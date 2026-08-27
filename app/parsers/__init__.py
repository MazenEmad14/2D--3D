# app/parsers/__init__.py
# Parsers package — one module per input format.
#
# Phase 1 modules (implemented):
#   exceptions.py          — Custom exception hierarchy for all parsers
#   layer_config.py        — DXF layer-name patterns and default dimensions
#   dxf_to_ir.py           — Parse DXF files into the JSON IR
#   pdf_content_detector.py — Classify PDF as vector vs. scanned (metadata only)
#   pdf_to_ir.py           — Render PDF page to image, hand off to Phase 2
#
# Future modules (Phase 2+):
#   image_to_ir.py  — Parse raster images into the JSON IR via AI pipeline
