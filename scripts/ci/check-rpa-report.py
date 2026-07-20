#!/usr/bin/env python3
"""Print RPA report summary; exit 1 if overall ok is false."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check-rpa-report.py <report.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    print("ok=", data.get("ok"))
    for sc in data.get("scenarios") or []:
        mark = "PASS" if sc.get("ok") else "FAIL"
        print(mark, sc.get("name"), f"({sc.get('duration_sec')}s)")
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
