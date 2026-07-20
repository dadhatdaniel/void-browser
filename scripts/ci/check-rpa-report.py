#!/usr/bin/env python3
"""Print RPA report summary; exit 1 if overall ok is false.

Scenarios marked soft_fail (e.g. live Google WebView2 challenge) count as ok
for the suite exit code when report.ok is True. Hard failures still fail CI.
"""
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
    soft_meta = (data.get("meta") or {}).get("soft_fail_scenarios") or []
    for sc in data.get("scenarios") or []:
        if sc.get("soft_fail"):
            mark = "SOFT"
        else:
            mark = "PASS" if sc.get("ok") else "FAIL"
        print(mark, sc.get("name"), f"({sc.get('duration_sec')}s)", sc.get("error") or "")
    if soft_meta:
        print("soft_fail_scenarios=", ",".join(soft_meta))
    teardown = (data.get("meta") or {}).get("teardown_uninstall") or {}
    if teardown:
        tmark = "PASS" if teardown.get("ok") else "FAIL"
        print(tmark, "teardown_uninstall", teardown.get("detail", ""))
    scenarios_ok = bool(data.get("ok"))
    teardown_ok = True if not teardown else bool(teardown.get("ok"))
    return 0 if scenarios_ok and teardown_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
