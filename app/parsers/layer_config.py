"""
app/parsers/layer_config.py

Configuration constants for DXF layer-name matching and default geometry values.

How to extend
-------------
To support additional layer-name conventions (e.g. from a new client's CAD
standard), simply add the pattern string to the relevant list below.
Matching is case-insensitive and supports both exact matches and substring
matches (e.g. "A-WALL" matches a layer called "A-WALL-EXT").

All physical dimensions are in **metres**.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Layer name patterns
# ---------------------------------------------------------------------------

#: Layer names recognised as carrying wall geometry.
#: Note: Layer matching is heuristic-based (e.g. prefix checking). Unconventional
#: layer names (like "A-WALLPAPER") might cause false positives/negatives.
#: For non-standard naming, the `custom_wall_layers` argument in `parse_dxf`
#: should be used to explicitly override these defaults.
WALL_LAYERS: list[str] = [
    "WALLS",
    "WALL",
    "A-WALL",
    "S-WALL",
    "MWALL",
    "PARTI",      # partition walls common in older AutoCAD standards
    "STR-WALL",
]

#: Layer names recognised as carrying door geometry (INSERT entities).
DOOR_LAYERS: list[str] = [
    "DOORS",
    "DOOR",
    "A-DOOR",
    "A-GLAZ-DOOR",
]

#: Layer names recognised as carrying window geometry (INSERT entities).
WINDOW_LAYERS: list[str] = [
    "WINDOWS",
    "WINDOW",
    "A-GLAZ",
    "A-WIND",
    "GLAZING",
]

# ---------------------------------------------------------------------------
# Default geometry (used when exact values cannot be extracted from the file)
# ---------------------------------------------------------------------------

#: Default wall thickness in metres — used when parallel-pair detection fails.
DEFAULT_WALL_THICKNESS_M: float = 0.2

#: Default storey height in metres.
DEFAULT_WALL_HEIGHT_M: float = 3.0

#: Default door clear opening width in metres.
DEFAULT_DOOR_WIDTH_M: float = 0.9

#: Default door clear opening height in metres.
DEFAULT_DOOR_HEIGHT_M: float = 2.1

#: Default window clear opening width in metres.
DEFAULT_WINDOW_WIDTH_M: float = 1.2

#: Default window clear opening height in metres.
DEFAULT_WINDOW_HEIGHT_M: float = 1.4

# ---------------------------------------------------------------------------
# Parallel-pair detection thresholds (wall thickness estimation)
# ---------------------------------------------------------------------------

#: Maximum angular difference (degrees) for two segments to be treated as parallel.
PARALLEL_ANGLE_TOLERANCE_DEG: float = 10.0

#: Two parallel segments whose perpendicular distance is ≤ this factor ×
#: DEFAULT_WALL_THICKNESS_M are considered a wall pair.
PARALLEL_DISTANCE_FACTOR: float = 4.0

# ---------------------------------------------------------------------------
# DXF $INSUNITS → metres conversion factors
# Source: AutoCAD DXF Reference, INSUNITS variable codes 0–20.
# ---------------------------------------------------------------------------

#: Maps AutoCAD $INSUNITS code → scale factor to convert document units to metres.
#: Code 0 (Unitless) is treated as metres with ``scale_confidence = "estimated"``.
INSUNITS_TO_METRES: dict[int, float] = {
    0:  1.0,        # Unitless — assumed metres, confidence "estimated"
    1:  0.0254,     # Inches
    2:  0.3048,     # Feet
    3:  1609.344,   # Miles
    4:  0.001,      # Millimetres
    5:  0.01,       # Centimetres
    6:  1.0,        # Metres
    7:  1_000.0,    # Kilometres
    8:  0.000_025_4,# Microinches
    9:  0.000_001,  # Millimicrons (µm)
    10: 0.000_1,    # Decimetres
    11: 10.0,       # Decametres
    12: 100.0,      # Hectometres
    13: 1_000_000.0,# Gigametres
    14: 0.9144,     # Yards
    15: 149_597_870_700.0,  # Astronomical units
    16: 9.461e15,   # Light years
    17: 3.086e16,   # Parsecs
}
