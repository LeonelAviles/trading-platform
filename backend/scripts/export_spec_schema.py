"""Export the Spec v2 JSON Schema + primitive docs for the frontend (PLATFORM-SPEC.md §5 Phase 3 task 1).

    python scripts/export_spec_schema.py            # writes frontend/src/spec/schema.json
    python scripts/export_spec_schema.py --check    # exit 1 if the file is stale (CI)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from engine import spec as S  # noqa: E402

OUT = BACKEND_DIR.parent / "frontend" / "src" / "spec" / "schema.json"


def payload() -> dict:
    return {"schema": S.json_schema(), "primitives": S.primitive_docs(), "operators": sorted(S.expr_mod.OPS),
            "fields": list(S.expr_mod.FIELDS), "timeframes": list(S.TIMEFRAMES), "structures": list(S.STRUCTURES),
            "levels": list(S.LEVELS)}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    text = json.dumps(payload(), indent=2, sort_keys=True) + "\n"
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is stale — run scripts/export_spec_schema.py")
            return 1
        print("schema.json up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text)} bytes, {len(payload()['primitives'])} primitives)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
