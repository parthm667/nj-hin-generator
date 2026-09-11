#!/usr/bin/env python3
"""Import every API module without substituting SQLite for PostgreSQL."""

import importlib
import os
import pkgutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL is required. Import checks do not replace PostgreSQL with a SQLite fake."
        )

    app_package = importlib.import_module("app")
    module_names = [
        module_info.name
        for module_info in pkgutil.walk_packages(app_package.__path__, prefix="app.")
    ]
    for module_name in sorted(module_names):
        importlib.import_module(module_name)
        print(f"imported {module_name}")

    print(f"Imported {len(module_names)} API modules successfully without connecting to the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
