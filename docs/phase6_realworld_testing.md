# Phase 6: Real-World Testing & Known-Limits Report

## 1. Test Corpus & Methodology

A diverse set of 12 test inputs was generated/collected across 7 distinct real-world categories. Each was processed via the live Flask API (`/api/convert`). Results were automatically parsed for sanity limits (HTTP errors, zero walls, massive dimensions, missing scale). The `known_door_width_m=0.9` calibration was provided for image inputs to avoid estimated-scale warnings.

## 2. Results Matrix

| Input File | Category | Status | Wall Count | Openings | Scale Confidence | Warnings | Time |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `floor-plan...illustration.jpg` | Baseline (Digital) | **PASS** | 21 | 18 | high | None | 7.8s |
| `A_hand_drawn_1.png` | A. Hand-Drawn | **PASS** | 16 | 19 | high | None | 1.0s |
| `A_hand_drawn_2.png` | A. Hand-Drawn (Messy) | **FAIL** | 132 | 57 | high | High wall count | 1.4s |
| `B_real_estate_1.png` | B. Real Estate | **PARTIAL** | 64 | 44 | high | High wall count | 2.5s |
| `B_real_estate_2.png` | B. Real Estate | **PASS** | 27 | 22 | high | None | 1.4s |
| `C_phone_photo_1.png` | C. Phone Camera | **PARTIAL** | 49 | 18 | high | High wall count | 1.1s |
| `C_phone_photo_2.png` | C. Phone (Angled/Distorted) | **FAIL** | 95 | 19 | high | High wall count | 1.2s |
| `D_low_quality_scan_1.png` | D. Low-Quality Scan | **PARTIAL** | 49 | 30 | high | High wall count | 1.7s |
| `D_low_quality_scan_2.png` | D. Low-Quality Scan | **FAIL** | 158 | 39 | high | High wall count | 2.1s |
| `G_near_miss_circuit.png` | G. Out-of-Domain (Circuit) | **FAIL** | 43 | 31 | high | High wall count | 0.9s |
| `G_near_miss_assembly.png` | G. Out-of-Domain (Assembly)| **FAIL** | 45 | 65 | high | High wall count | 0.6s |

### Synthetic CAD/Vector Controls
| Input File | Category | Status | Wall Count | Openings | Scale Confidence | Warnings | Time |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `E_synthetic_complex.dxf` | E. DXF CAD (Synthetic) | **PASS** | 8 | 0 | high | None | 0.05s |
| `F_synthetic_complex.pdf` | F. PDF Vector (Synthetic) | **PASS** | 7 | 0 | estimated | Scale estimated | 1.5s |

> [!WARNING]
> **DXF/PDF Robustness Unverified:** The 100% pass rate on synthetic DXF and PDF files (generated via `ezdxf` and `reportlab`) only proves the parsers work functionally on clean data. It does **not** demonstrate the parsers can handle real-world architectural CAD complexity (e.g. diverse layer naming conventions, messy drafting quirks, blocks, exploded geometry). Robustness against real-world CAD files remains **UNVERIFIED**.

**Overall Pass Rates:**
*   Clean Digital Image (Baseline): 1/1 PASS
*   Hand-Drawn Sketches: 1/2 PASS, 1/2 FAIL
*   Real Estate Style: 1/2 PASS, 1/2 PARTIAL
*   Phone Photos / Scans: 0/4 PASS, 2/4 PARTIAL, 2/4 FAIL
*   Out-of-Domain (Negative Control): 2/2 FAIL (Model hallucinated geometry on both)

## 3. Root Cause Analysis (PARTIAL & FAIL Cases)

### A. Shattering on Noise & Sketch Artifacts (Failures A, D)
**Root Cause:** The segmentation model was trained heavily on clean, machine-generated layouts (like CubiCasa5K). When it encounters messy ink strokes, high-frequency scan noise, or pencil smudges, it produces a patchy, fragmented mask rather than a continuous wall blob. The `mask_to_ir.py` pipeline then fits a distinct `Wall` segment to every single patch of noise, creating an explosion of tiny, useless walls (130+).
![Debug Hand Drawn 2](file:///C:/Users/mazen/.gemini/antigravity-ide/brain/b18fb503-c675-4754-8cbb-4b8c6ba81615/debug_A_hand_drawn_2.png)
![Debug Scan Noise](file:///C:/Users/mazen/.gemini/antigravity-ide/brain/b18fb503-c675-4754-8cbb-4b8c6ba81615/debug_D_low_quality_scan_2.png)

### B. Perspective Distortion (Failures C)
**Root Cause:** The model expects top-down orthographic images. If a phone photo has heavy perspective warping or page curvature, the model still blindly finds "walls" but maps them to the distorted 2D pixel space. When extruded into 3D, the building is physically skewed/canted and often shatters because curved parallel walls break the minimum-area rectangle logic.
![Debug Phone Photo](file:///C:/Users/mazen/.gemini/antigravity-ide/brain/b18fb503-c675-4754-8cbb-4b8c6ba81615/debug_C_phone_photo_2.png)

### C. False Positives / Domain Blindness (Failures G)
**Root Cause:** The model does not know what a floor plan is; it only knows how to classify pixels. When fed an electrical circuit or a furniture assembly diagram (black lines on a white background with rectangular shapes), it confidently misclassifies capacitors, table legs, and circuit traces as walls, doors, and windows, producing a completely nonsensical architectural layout.
![Debug Circuit](file:///C:/Users/mazen/.gemini/antigravity-ide/brain/b18fb503-c675-4754-8cbb-4b8c6ba81615/debug_G_near_miss_circuit.png)

## 4. Fixes Applied During Testing
During the test run, **Failure F (Synthetic PDF)** triggered a `422 Unprocessable Entity`. 
*   **The Issue:** The API routing in Phase 4 `convert.py` forgot to catch the `PDFRenderedToImage` exception from Phase 1, causing the PDF processing to crash instead of routing to the image pipeline.
*   **The Fix:** Updated `convert.py` to `except PDFRenderedToImage as p_exc:` and pass the image path directly to `parse_image`. 
*   **Regression Check:** Re-ran `run_corpus.py` and validated that the PDF now reliably converts to a 3D model, and the baseline image test was unaffected.

## 5. Known Limitations (For Production Documentation)

If deciding to deploy this system, be aware of these strict boundaries:

*   **Strict Orthographic Requirement:** The system cannot auto-correct perspective. Phone photos or skewed scans will produce physically distorted 3D geometry.
*   **Low Noise Tolerance:** Pencil sketches, muddy scans, and highly detailed CAD blueprints with hatching will cause the walls to "shatter" into hundreds of micro-segments. The model demands clean, digital-style imagery.
*   **No "Not A Floor Plan" Rejection:** The system will aggressively attempt to build a house out of *any* black-and-white technical drawing (e.g., an electrical circuit). Upstream user-validation or a separate binary classifier is required to reject bad inputs before parsing.
*   **Scale Estimation is Blind:** Unless a known door width is provided by the user, the 3D dimensions are entirely hallucinated (defaulting to 0.01 m/px). No internal reference objects are used to establish ground truth scale.
*   **Calibration Amplifies Hallucinations:** The `known_door_width_m` calibration assumes the detected reference opening is real. On noisy/out-of-domain images where the model hallucinates false openings, calibration can produce a confidently-scaled ("high" confidence) but still physically meaningless result — potentially worse than leaving scale as "estimated", since the user has no signal that something is wrong. A future improvement could involve calibrating only when scale_confidence would otherwise already look plausible, or flagging when the calibration reference itself falls in a suspicious region (e.g., overlapping heavy noise/high wall density).
