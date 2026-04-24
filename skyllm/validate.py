"""Load every models/*/model.yaml and report schema errors.

Run via `pixi run -e cli validate`. Exits 1 on any load or schema failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from skyllm import schema

CATALOG = Path(__file__).resolve().parent.parent / "models"


def main() -> int:
    if not CATALOG.is_dir():
        print(f"catalog directory not found: {CATALOG}", file=sys.stderr)
        return 1

    failed = 0
    specs: dict[str, schema.ModelSpec] = {}
    for child in sorted(CATALOG.iterdir()):
        if not (child / "model.yaml").is_file():
            continue
        try:
            specs[child.name] = schema.load(child)
        except (ValidationError, ValueError) as e:
            print(f"FAIL {child.name}: {e}", file=sys.stderr)
            failed += 1

    for name, spec in specs.items():
        print(f"OK   {name:30s}  {spec.engine:8s}  {spec.tier:8s}  {spec.hf_repo}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
