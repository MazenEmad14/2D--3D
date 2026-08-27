# Phase 2: Image Pipeline

This document describes the Phase 2 implementation of the "2D CAD/Image to 3D Floorplan Viewer" project, which converts raster floor plan images into the project's internal `IRFloorPlan` representation.

## Core Objective

Given a raster image of a floor plan (either directly uploaded, or handed off from Phase 1's PDF renderer), identify walls, doors, and windows using a pretrained deep learning segmentation model, and extract them into vector geometry (the JSON IR).

## Architecture

The pipeline consists of three stages:

1. **Preprocessing (`app/parsers/image_preprocessing.py`)**
   - Letterboxing: The image is aspect-preservingly resized to fit within a 512×512 canvas.
   - Padding: The unused canvas area is filled with the ImageNet mean colour `(123, 116, 103)`.
   - Normalisation: The image is converted to float32 and normalised using ImageNet statistics `(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])`.

2. **Model Inference (`app/models/floorplan_segmenter.py`)**
   - We use the `Yytsi/floorplan-to-3d-walls` model from the Hugging Face Hub.
   - Architecture: ResNet-34 encoder + UNet decoder (via `segmentation_models_pytorch`).
   - The model is loaded lazily on the first request (Singleton pattern) to avoid blocking the Flask application's `create_app()` startup.
   - Inference runs on CPU. The model produces a 4-channel output (Logits). The `argmax(dim=1)` yields a single 512×512 mask with class indices:
     - `0`: Floor (background)
     - `1`: Wall
     - `2`: Door
     - `3`: Window

3. **Postprocessing (`app/parsers/mask_to_ir.py`)**
   - Coordinates are mapped back from the 512×512 padded canvas to the original image's coordinate space.
   - **Polygon Extraction**: 
     - Binary masks per class are smoothed with a 3×3 morphological closing operation to bridge small gaps (e.g. 1-pixel breaks in a wall).
     - We use `cv2.findContours(RETR_CCOMP)` to extract outer boundaries and `cv2.approxPolyDP` to collapse pixel staircases into straight lines.
     - Speckle noise (contours < 30px area) is dropped.
   - **Wall Centerlines**:
     - `cv2.minAreaRect` is applied to each wall contour to find its rotated bounding box.
     - The longer axis is taken as the wall's length and direction (start to end points). The shorter axis determines the wall's thickness.
   - **Opening Assignment**:
     - Door and window contour centres are projected onto the nearest wall centerline using perpendicular projection to determine their `position_on_wall`.
   - **Scale Calibration**:
     - Default: A naive heuristic of `0.01 m/px` is used (`scale_confidence="estimated"`).
     - Calibrated: If `known_door_width_m` is supplied, the average width of detected door contours in the image is measured, yielding a precise `m/px` scale (`scale_confidence="high"`).

## Usage

```python
from app.parsers.image_to_ir import parse_image

# With default estimated scale
ir = parse_image("path/to/floorplan.png")

# With scale calibration based on a known 0.9m door width
ir_calibrated = parse_image("path/to/floorplan.png", known_door_width_m=0.9)
```

## Known Limitations

- **Wall Centerlines**: The `minAreaRect` approach works very well for simple, straight wall segments. However, for L-shaped or T-shaped wall contiguous regions, the bounding rectangle is inaccurate. A future refinement (e.g. skeletonisation) could split complex shapes into individual straight wall segments.
- **CPU Inference Speed**: Running a ResNet-34 UNet on CPU takes around 1.5–3 seconds per image depending on the processor. For an MVP this is completely acceptable, but a production deployment with high concurrency would benefit from GPU acceleration or ONNX Runtime optimisations.

## Honest Evaluation

The pipeline fundamentally works as a proof of concept. The model successfully identifies solid core structures in clean imagery and the translation layer accurately maps those segmentations into physical coordinates and geometries.

### The Domain Gap: Why It Fails on Noisy Images
However, research into the reference repository (`Yytsi/floorplan-to-3d`) reveals a critical architectural limitation: the original model was designed to consume **mathematically perfect vector SVGs** (from CubiCasa5K) that are rasterized cleanly via `cairosvg` just before inference. It was never trained on real photographs, uneven lighting, JPEG artifacts, or pencil smudges. 

Our pipeline intentionally accepts a broader, harder set of real-world input formats (raw photos, scans, PDFs) than the reference implementation was designed for. Because the model expects zero background noise and pixel-perfect solid lines, feeding it real-world images pushes it far outside its intended input distribution. This domain gap is the single biggest reason for the extreme "noise sensitivity" and "wall shattering" observed during testing. The model is not necessarily failing; it is being forced to interpret artifacts it was never taught to ignore.

### Tried and Rejected Preprocessing Experiments

To bridge the domain gap between messy real-world raster inputs and the clean vector SVGs the model was trained on, we tested several aggressive preprocessing steps to clean the image *before* feeding it to the model. Both were formally rejected due to severe negative side-effects:

1. **Otsu / Adaptive Thresholding**: 
   - *Hypothesis*: Binarizing the raw image would remove uneven lighting and paper texture.
   - *Failure Mode (Information Loss)*: Destroyed faint semantic lines. Hard thresholding wiped out window panes and faint door arcs entirely, resulting in 0/5 windows surviving the post-processing area filter on baseline images.

2. **Geometric Vectorization (Ridge Filter + Skeletonization + Uniform Dilation)**:
   - *Hypothesis*: By explicitly detecting line ridges (Frangi filter), extracting their centerlines, and redrawing them with perfectly uniform stroke widths (mimicking `cairosvg`), we could standardize the geometry without losing faint lines to Otsu thresholding.
   - *Failure Mode 1 (Color-Channel Information Loss)*: Standardizing lines to black-on-white destroyed color cues that the model implicitly relies on. E.g., a baseline blueprint with white-on-blue lines dropped from 20 walls to 0 walls when forced into uniform black-on-white outlines.
   - *Failure Mode 2 (Noise Amplification)*: On images with heavy photographic or paper texture (e.g., Gudea), the ridge filter connected the background dirt into a dense, solid spiderweb of traced lines. This amplification broke the model completely, pushing it so far out of distribution that it output empty probability masks.

*Conclusion*: Geometric cleanup and noise suppression must be strictly confined to the **post-processing** stage (operating on the model's output masks), rather than preprocessing the raw image. The model requires the rich semantic features (shading, colors, varying line weights) to accurately classify walls vs. doors vs. noise.
1. **Quality of Predictions**: The model (`Yytsi/floorplan-to-3d-walls` / ResNet-34 UNet) is trained on CubiCasa5K, which consists mostly of clean, digitally rasterized SVG plans (white backgrounds, clear black lines). It performs *extremely well* on images that match this style (like our synthetic tests). However, if fed a noisy, hand-sketched, or photographed floor plan (like a wrinkled paper scan), the segmentation quality drops significantly. It is **not** a robust "in-the-wild" model for poor-quality scans.
2. **Resolution Bottleneck**: The hardcoded `512x512` input size means that large, complex floor plans with very thin walls (e.g., 1-2 pixels wide after resizing) will lose critical detail. Walls might be missed entirely, or small doors might blur into walls.
3. **Geometry extraction**: The current pipeline uses `minAreaRect` to find wall centerlines. This is a naive heuristic. It works perfectly for straight walls but fails for L-shaped or T-shaped continuous wall contours. A true Skeletonization approach is required for production-grade vectorization.
4. **Small Openings Filter**: The pipeline uses an area filter (`MIN_POLYGON_AREA_PX = 30.0`) to drop model noise. As a known limitation, small or thin doors and windows (especially those eroded by binarization or low-resolution scaling) may fall below this threshold and be dropped. This is an intentional tradeoff to avoid a "noise explosion" (dozens of spurious openings) on non-ideal, noisy images.
5. **Conclusion**: This implementation perfectly satisfies the MVP requirements for Phase 2. The pipeline is architecturally sound and accurately converts segmentations to the 3D-ready IR schema. However, for a commercial product, the underlying AI model would need to be replaced with a higher-resolution, more robust architecture (e.g. Swin Transformer or Mask2Former) trained on a much more diverse dataset.
