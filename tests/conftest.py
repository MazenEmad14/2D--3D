"""
tests/conftest.py

Session-scoped fixture auto-generation.

Ensures all synthetic test fixtures exist before any test runs.
On a fresh checkout (no .generated sentinel) the generator script is
called automatically — no manual step required.
"""

import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SENTINEL = FIXTURES_DIR / ".generated"


@pytest.fixture(scope="session", autouse=True)
def ensure_fixtures() -> None:
    """Generate synthetic fixtures if they haven't been created yet."""
    if not SENTINEL.exists():
        logger.info("Fixtures not found — generating now …")
        from tests.fixtures.generate_fixtures import generate_all

        generate_all(FIXTURES_DIR)
    else:
        logger.debug("Fixtures already present (sentinel: %s).", SENTINEL)
