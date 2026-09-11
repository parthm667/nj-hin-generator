#!/usr/bin/env python3
"""Compatibility entry point for the official NJDOT Accidents loader.

The former Socrata implementation targeted a summary dataset and is retired.
Use ``ingest_njdot_crashes.py`` directly for new automation.
"""

import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ingest_njdot_crashes import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger(__name__).warning(
        "ingest_nj_crash_data.py is retired; forwarding to ingest_njdot_crashes.py"
    )
    raise SystemExit(main())
