"""
HAR 1.2 helpers for Void RPA network capture.

WebView2 has no reliable HAR export for automation. Void writes a JSONL
decision stream (VOID_NETWORK_CAPTURE / VOID_ADBLOCK_DEBUG); this module
converts that stream into HAR 1.2-compatible JSON for quality-loop review.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse


CREATOR = {
    "name": "void-browser-rpa",
    "version": "1.0",
    "comment": (
        "HAR 1.2 synthesized from Void network decision JSONL "
        "(WebResourceRequested / navigation). Not a Chromium DevTools export."
    ),
}


def _iso_from_ms(ts_ms: int | float | None) -> str:
    if ts_ms is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    try:
        sec = float(ts_ms) / 1000.0
        return (
            datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z"
        )
    except (OverflowError, OSError, ValueError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def load_jsonl_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def decision_to_har_entry(e: dict[str, Any]) -> dict[str, Any]:
    decision = str(e.get("decision") or "").lower()
    blocked = decision == "blocked"
    status = e.get("status")
    if status is None:
        status = 403 if blocked else 0
    try:
        status_i = int(status)
    except (TypeError, ValueError):
        status_i = 403 if blocked else 0
    method = str(e.get("method") or "GET") or "GET"
    url = str(e.get("url") or "")
    return {
        "startedDateTime": _iso_from_ms(e.get("ts_ms")),
        "time": 0,
        "request": {
            "method": method,
            "url": url,
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": [],
            "queryString": [],
            "headersSize": -1,
            "bodySize": -1,
        },
        "response": {
            "status": status_i,
            "statusText": "Blocked" if blocked else ("" if status_i == 0 else "OK"),
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": [],
            "content": {"size": 0, "mimeType": "x-unknown"},
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": 0,
            "_transferSize": 0,
        },
        "cache": {},
        "timings": {"send": 0, "wait": 0, "receive": 0},
        "pageref": "void-page",
        "_void": {
            "decision": decision or ("blocked" if blocked else "allowed"),
            "filter": e.get("filter"),
            "resourceType": e.get("resource_type") or e.get("resourceType"),
            "sourceUrl": e.get("source_url") or e.get("sourceUrl"),
        },
    }


def build_har(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    entries_list = list(entries)
    har_entries = [decision_to_har_entry(e) for e in entries_list]
    pages: list[dict[str, Any]] = []
    if entries_list:
        pages.append(
            {
                "startedDateTime": _iso_from_ms(entries_list[0].get("ts_ms")),
                "id": "void-page",
                "title": "void-browser network capture",
                "pageTimings": {"onContentLoad": -1, "onLoad": -1},
            }
        )
    return {
        "log": {
            "version": "1.2",
            "creator": CREATOR,
            "browser": {"name": "void-browser", "version": "rpa"},
            "pages": pages,
            "entries": har_entries,
            "comment": (
                "status 403 = adblock/privacy block; status 0 = allowed "
                "(response status unknown without CDP). Custom _void fields carry "
                "decision / filter / resourceType / sourceUrl."
            ),
        }
    }


def write_har(path: Path, entries: Iterable[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_har(entries), indent=2), encoding="utf-8")
    return path


def write_har_from_jsonl(jsonl_path: Path, har_path: Path) -> dict[str, Any]:
    entries = load_jsonl_entries(jsonl_path)
    write_har(har_path, entries)
    blocked = sum(1 for e in entries if str(e.get("decision") or "").lower() == "blocked")
    return {
        "ok": True,
        "jsonl": str(jsonl_path),
        "har": str(har_path),
        "entries": len(entries),
        "blocked": blocked,
        "allowed": len(entries) - blocked,
    }


def summarize_har_for_failures(har_path: Path, *, max_samples: int = 12) -> dict[str, Any]:
    """Extract review hints for quality-loop agents from a HAR file."""
    if not har_path.is_file():
        return {"ok": False, "error": f"missing {har_path}"}
    try:
        data = json.loads(har_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    entries = list((data.get("log") or {}).get("entries") or [])
    failed: list[dict[str, Any]] = []
    blocked_trackers: list[str] = []
    documents: list[str] = []
    blankish: list[str] = []
    for ent in entries:
        req = ent.get("request") or {}
        resp = ent.get("response") or {}
        void = ent.get("_void") or {}
        url = str(req.get("url") or "")
        status = int(resp.get("status") or 0)
        decision = str(void.get("decision") or "").lower()
        rtype = str(void.get("resourceType") or "").lower()
        host = ""
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:  # noqa: BLE001
            host = ""
        if rtype == "document" or url.endswith("/") and decision == "allowed":
            documents.append(url)
        if status in (0,) and decision == "allowed" and rtype == "document":
            # status 0 on document is normal for our synthetic HAR; flag about:blank
            if "about:blank" in url.lower() or not url:
                blankish.append(url or "(empty)")
        if decision == "blocked" or status == 403:
            blocked_trackers.append(host or url)
        if status >= 400 and status != 403:
            failed.append({"url": url, "status": status, "decision": decision})
        elif status == 0 and decision not in ("allowed", "blocked", ""):
            failed.append({"url": url, "status": status, "decision": decision})

    def _uniq(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in seq:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    return {
        "ok": True,
        "har": str(har_path),
        "entry_count": len(entries),
        "blocked_host_samples": _uniq(blocked_trackers)[:max_samples],
        "document_navigations": _uniq(documents)[:max_samples],
        "blankish_navigations": _uniq(blankish)[:max_samples],
        "failed_or_odd_requests": failed[:max_samples],
        "review_hints": [
            "On scenario failure: open network.har (or network-*.har) and check "
            "_void.decision, response.status, and document navigations.",
            "Look for unexpected tracker hosts that were allowed (decision=allowed).",
            "Look for about:blank / empty document navigations correlating with blank screenshots.",
            "status 403 with decision=blocked is expected adblock; status 0 means allowed "
            "(real HTTP status unknown without CDP).",
        ],
    }


def enable_suite_capture(out_dir: Path) -> dict[str, str]:
    """
    Set env so Void writes network JSONL + live HAR under the RPA artifact dir.
    Returns paths used (also stored in os.environ).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "network.jsonl"
    har = out_dir / "network.har"
    snap = out_dir / "network.json"
    jsonl.write_text("", encoding="utf-8")
    snap.write_text(
        json.dumps({"ok": True, "entries": [], "blocked": 0, "allowed": 0}),
        encoding="utf-8",
    )
    write_har(har, [])
    os.environ["VOID_NETWORK_CAPTURE"] = "1"
    os.environ["VOID_NETWORK_CAPTURE_LOG"] = str(jsonl)
    os.environ["VOID_NETWORK_HAR_PATH"] = str(har)
    # Keep adblock debug alias on so older binaries that only check VOID_ADBLOCK_DEBUG
    # still emit a decision stream into the same JSONL path.
    os.environ["VOID_ADBLOCK_DEBUG"] = "1"
    os.environ["VOID_ADBLOCK_DEBUG_LOG"] = str(jsonl)
    return {
        "jsonl": str(jsonl),
        "har": str(har),
        "snapshot": str(snap),
    }


def finalize_suite_capture(
    out_dir: Path,
    *,
    scenario_names: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Rebuild network.har from full JSONL; write a short network-summary.json."""
    jsonl = out_dir / "network.jsonl"
    har = out_dir / "network.har"
    # Prefer suite JSONL; fall back to live Void paths if suite file empty/missing.
    alt = Path(os.environ.get("VOID_NETWORK_CAPTURE_LOG") or "")
    src = jsonl if jsonl.is_file() and jsonl.stat().st_size > 0 else alt
    if not src or not src.is_file():
        src = jsonl
    meta = write_har_from_jsonl(src, har)
    summary = summarize_har_for_failures(har)
    summary_path = out_dir / "network-summary.json"
    payload = {
        **meta,
        "summary": summary,
        "scenarios": scenario_names or [],
        "note": (
            "Suite-level HAR for automation/quality-loop review. "
            "Per-scenario copies may also appear as network-<scenario>.har."
        ),
    }
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Optional per-scenario marker file listing (actual split is best-effort by wall clock
    # in the runner; if absent, suite HAR is the source of truth).
    return payload


def copy_scenario_har(out_dir: Path, scenario: str) -> Optional[Path]:
    """Snapshot current suite HAR as network-<scenario>.har after a scenario ends."""
    src = out_dir / "network.har"
    if not src.is_file():
        jsonl = out_dir / "network.jsonl"
        if jsonl.is_file():
            write_har_from_jsonl(jsonl, src)
        else:
            return None
    dest = out_dir / f"network-{scenario}.har"
    try:
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return dest
    except OSError:
        return None
