#!/usr/bin/env python3
"""Deprecated road-loader entry point.

Phase 2 uses the official NJDOT M-valued road layer. Keeping this command as
an explicit failure prevents old automation from silently loading a different
network that cannot support NJDOT crash mileposts.
"""

from __future__ import annotations

import logging
import sys


LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    del argv
    LOGGER.error(
        "OSM road ingestion is retired. Run backend/scripts/ingest_njdot_roads.py "
        "to load the authoritative NJDOT route and analysis network."
    )
    return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main(sys.argv[1:]))
