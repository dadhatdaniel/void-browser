#!/usr/bin/env python3
"""Scan Void Browser release artifacts (and optionally the website URL) via VirusTotal.

Privacy-first design:
  - Runs in CI only (server-side). The public site never calls VirusTotal.
  - Writes static website/scan-results.json that the landing page loads same-origin.

Requires env:
  VIRUSTOTAL_API_KEY  — VirusTotal API v3 key (GitLab CI/CD variable, masked)

Optional env:
  VT_RELEASE_TAG / RELEASE_TAG — preferred GitHub release tag (tried first)
  VT_GITHUB_REPO      — owner/repo (default: dadhatdaniel/void-browser)
  VT_WEBSITE_URL      — site to URL-scan (default: https://void.lightfoot.cloud)
  VT_OUT              — output JSON path (default: website/scan-results.json)
  GITHUB_TOKEN        — optional; raises GitHub API rate limits for asset download

Release resolution (packages are best-effort):
  1. VT_RELEASE_TAG / RELEASE_TAG if that GitHub Release has package assets
  2. Newest non-draft GitHub Release that has .deb/.AppImage/.msi/.exe/.dmg
  3. Tag from website/releases.json, then v0.1.0-alpha.3
  Website URL scan always runs even when no package release exists.

Usage:
  python3 scripts/virustotal-scan.py
  python3 scripts/virustotal-scan.py --dry-run   # write pending JSON without API calls
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "website" / "scan-results.json"
VT_API = "https://www.virustotal.com/api/v3"
GH_API = "https://api.github.com"

LIMITATIONS = [
    "macOS builds are unsigned — Gatekeeper may warn until notarized.",
    "Windows builds are unsigned — SmartScreen / Defender may warn on first run.",
    "VirusTotal aggregates many engines; a single unknown/heuristic hit is common for new unsigned software and is not proof of malware.",
]

WANTED_EXT = (".deb", ".AppImage", ".msi", ".exe", ".dmg")
FALLBACK_RELEASE_TAG = "v0.1.0-alpha.3"


def http_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    data: bytes | None = None,
    timeout: int = 120,
    retries: int = 5,
) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            # Rate limited or transient — wait and retry (do not log response bodies / keys)
            if e.code in (429, 502, 503, 504) and attempt < retries - 1:
                wait = 20 * (attempt + 1)
                print(f"  VT HTTP {e.code}; backing off {wait}s...")
                time.sleep(wait)
                continue
            raise
        except TimeoutError as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(15)
                continue
            raise
    if last_err:
        raise last_err
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _gh_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "void-browser-vt-scan"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_release(repo: str, tag: str, token: str | None) -> dict[str, Any]:
    url = f"{GH_API}/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}"
    return http_json(url, headers=_gh_headers(token))


def github_list_releases(repo: str, token: str | None, *, per_page: int = 30) -> list[dict[str, Any]]:
    url = f"{GH_API}/repos/{repo}/releases?per_page={per_page}"
    data = http_json(url, headers=_gh_headers(token))
    return data if isinstance(data, list) else []


def package_assets(release: dict[str, Any]) -> list[dict[str, Any]]:
    assets = release.get("assets") or []
    return [
        a
        for a in assets
        if (a.get("name") or "").endswith(WANTED_EXT) and a.get("browser_download_url")
    ]


def tag_from_releases_json() -> str | None:
    path = ROOT / "website" / "releases.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tag = data.get("tag")
    return tag if isinstance(tag, str) and tag else None


def resolve_release(
    repo: str,
    preferred_tag: str | None,
    token: str | None,
) -> tuple[dict[str, Any] | None, str]:
    """Return (release, note). release is None when no scannable package assets exist."""
    tried: list[str] = []

    def try_tag(tag: str, why: str) -> dict[str, Any] | None:
        if not tag or tag in tried:
            return None
        tried.append(tag)
        try:
            rel = github_release(repo, tag, token)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"No GitHub release for tag {tag} (404) — {why}")
                return None
            raise
        assets = package_assets(rel)
        if not assets:
            print(f"GitHub release {tag} has no package assets — {why}")
            return None
        print(f"Using GitHub release {tag} ({len(assets)} package asset(s)) — {why}")
        return rel

    if preferred_tag:
        rel = try_tag(preferred_tag, "preferred VT_RELEASE_TAG/RELEASE_TAG")
        if rel is not None:
            return rel, f"preferred tag {preferred_tag}"

    print("Listing GitHub releases for one with package assets...")
    try:
        for rel in github_list_releases(repo, token):
            if rel.get("draft"):
                continue
            tag = rel.get("tag_name") or ""
            if not tag or tag in tried:
                continue
            assets = package_assets(rel)
            if assets:
                print(f"Using GitHub release {tag} ({len(assets)} package asset(s)) — newest with assets")
                return rel, f"newest published release with assets ({tag})"
            tried.append(tag)
    except Exception as e:
        print(f"Failed to list GitHub releases: {e}", file=sys.stderr)

    for tag, why in (
        (tag_from_releases_json(), "website/releases.json"),
        (FALLBACK_RELEASE_TAG, "built-in fallback"),
    ):
        if not tag:
            continue
        rel = try_tag(tag, why)
        if rel is not None:
            return rel, why

    note = (
        "No GitHub Release with scannable package assets "
        f"(tried: {', '.join(tried) or 'none'}). Website scan only."
    )
    print(note)
    return None, note


def download(url: str, dest: Path, token: str | None = None) -> None:
    headers = {"User-Agent": "void-browser-vt-scan"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def vt_headers(api_key: str) -> dict[str, str]:
    return {"x-apikey": api_key, "Accept": "application/json", "User-Agent": "void-browser-vt-scan"}


def vt_file_report(api_key: str, digest: str) -> dict[str, Any] | None:
    try:
        return http_json(f"{VT_API}/files/{digest}", headers=vt_headers(api_key))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def vt_upload_file(api_key: str, path: Path) -> str:
    """Upload file; returns analysis id. Uses /files (small) or /files/upload_url (large)."""
    size = path.stat().st_size
    upload_url = f"{VT_API}/files"
    if size > 32 * 1024 * 1024:
        meta = http_json(f"{VT_API}/files/upload_url", headers=vt_headers(api_key))
        upload_url = meta["data"]

    boundary = f"----VoidBoundary{int(time.time())}"
    file_bytes = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = vt_headers(api_key)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    result = http_json(upload_url, headers=headers, method="POST", data=body, timeout=600)
    return result["data"]["id"]


def vt_wait_analysis(api_key: str, analysis_id: str, timeout_s: int = 300) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = http_json(f"{VT_API}/analyses/{analysis_id}", headers=vt_headers(api_key))
        status = data.get("data", {}).get("attributes", {}).get("status")
        if status == "completed":
            return data
        time.sleep(15)
    raise TimeoutError(f"VirusTotal analysis timed out: {analysis_id}")


def vt_url_scan(api_key: str, url: str) -> dict[str, Any]:
    body = urllib.parse.urlencode({"url": url}).encode("utf-8")
    headers = vt_headers(api_key)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    submitted = http_json(f"{VT_API}/urls", headers=headers, method="POST", data=body)
    analysis_id = submitted["data"]["id"]
    vt_wait_analysis(api_key, analysis_id)
    # URL id is base64url of the URL without padding
    url_id = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return http_json(f"{VT_API}/urls/{url_id}", headers=vt_headers(api_key))


def summarize_stats(stats: dict[str, Any] | None) -> tuple[int | None, int | None]:
    if not stats:
        return None, None
    malicious = int(stats.get("malicious", 0) or 0)
    suspicious = int(stats.get("suspicious", 0) or 0)
    positives = malicious + suspicious
    total = sum(int(stats.get(k, 0) or 0) for k in ("malicious", "suspicious", "undetected", "harmless", "timeout", "failure", "type-unsupported", "confirmed-timeout"))
    if total == 0:
        total = positives
    return positives, total


def pending_payload(tag: str, message: str) -> dict[str, Any]:
    return {
        "status": "pending",
        "provider": "VirusTotal",
        "message": message,
        "website": {
            "url": os.environ.get("VT_WEBSITE_URL", "https://void.lightfoot.cloud"),
            "status": "not_scanned",
            "permalink": None,
            "positives": None,
            "total": None,
        },
        "release_tag": tag,
        "scanned_at": None,
        "limitations": LIMITATIONS,
        "artifacts": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Write pending JSON without API calls")
    parser.add_argument("--out", default=os.environ.get("VT_OUT", str(DEFAULT_OUT)))
    args = parser.parse_args()

    repo = os.environ.get("VT_GITHUB_REPO", "dadhatdaniel/void-browser")
    preferred_tag = (
        os.environ.get("VT_RELEASE_TAG")
        or os.environ.get("RELEASE_TAG")
        or None
    )
    if preferred_tag is not None:
        preferred_tag = preferred_tag.strip() or None
    website_url = os.environ.get("VT_WEBSITE_URL", "https://void.lightfoot.cloud")
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    api_key = os.environ.get("VIRUSTOTAL_API_KEY")
    out_path = Path(args.out)

    if args.dry_run or not api_key:
        msg = (
            "Dry-run placeholder."
            if args.dry_run
            else "Automated scans are not configured yet. Add VIRUSTOTAL_API_KEY as a GitLab CI/CD variable, then re-run the virus-scan job (or publish a release tag)."
        )
        payload = pending_payload(preferred_tag or FALLBACK_RELEASE_TAG, msg)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote pending scan results to {out_path}")
        return 0

    # Website URL first — always runs even when GitHub Release/assets are missing.
    website_block: dict[str, Any] = {
        "url": website_url,
        "status": "error",
        "permalink": None,
        "positives": None,
        "total": None,
    }
    try:
        print(f"Scanning website URL {website_url}...")
        url_report = vt_url_scan(api_key, website_url)
        attrs = url_report.get("data", {}).get("attributes", {})
        positives, total = summarize_stats(attrs.get("last_analysis_stats"))
        vt_id = url_report.get("data", {}).get("id")
        website_block = {
            "url": website_url,
            "status": "clean" if positives == 0 else ("flagged" if (positives or 0) > 0 else "unknown"),
            "permalink": f"https://www.virustotal.com/gui/url/{vt_id}" if vt_id else None,
            "positives": positives,
            "total": total,
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        print(f"  website: {positives}/{total}")
        time.sleep(16)
    except Exception as e:
        print(f"Website URL scan failed: {e}", file=sys.stderr)
        website_block["error"] = str(e)

    artifacts_out: list[dict[str, Any]] = []
    tag_name = preferred_tag or FALLBACK_RELEASE_TAG
    release_note = ""
    try:
        release, release_note = resolve_release(repo, preferred_tag, gh_token)
    except Exception as e:
        print(f"Failed to resolve GitHub release (continuing with website-only): {e}", file=sys.stderr)
        release = None
        release_note = str(e)

    if release is not None:
        tag_name = release.get("tag_name") or tag_name
        scan_assets = package_assets(release)
        scan_assets.sort(key=lambda a: int(a.get("size") or 0))

        with tempfile.TemporaryDirectory(prefix="void-vt-") as tmp:
            tmpdir = Path(tmp)
            for asset in scan_assets:
                name = asset.get("name") or ""
                url = asset.get("browser_download_url")
                print(f"Scanning {name} ({asset.get('size', '?')} bytes)...")
                local = tmpdir / name
                try:
                    download(url, local, gh_token)
                    digest = sha256_file(local)
                    report = vt_file_report(api_key, digest)
                    if report is None:
                        print(f"  Not on VT yet — uploading {name} ({local.stat().st_size} bytes)")
                        analysis_id = vt_upload_file(api_key, local)
                        vt_wait_analysis(api_key, analysis_id, timeout_s=600)
                        report = vt_file_report(api_key, digest)
                    attrs = (report or {}).get("data", {}).get("attributes", {})
                    positives, total = summarize_stats(attrs.get("last_analysis_stats"))
                    permalink = f"https://www.virustotal.com/gui/file/{digest}"
                    artifacts_out.append(
                        {
                            "name": name,
                            "sha256": digest,
                            "positives": positives,
                            "total": total,
                            "status": "clean" if positives == 0 else ("flagged" if (positives or 0) > 0 else "unknown"),
                            "permalink": permalink,
                            "download_url": url,
                            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }
                    )
                    print(f"  {name}: {positives}/{total} -> {permalink}")
                    # Free-tier: ~4 requests/min
                    time.sleep(20)
                except Exception as e:
                    print(f"  ERROR scanning {name}: {e}", file=sys.stderr)
                    artifacts_out.append(
                        {
                            "name": name,
                            "sha256": None,
                            "positives": None,
                            "total": None,
                            "status": "error",
                            "permalink": None,
                            "download_url": url,
                            "error": str(e),
                        }
                    )
                    time.sleep(20)

    clean_count = sum(1 for a in artifacts_out if a.get("status") == "clean")
    flagged = sum(1 for a in artifacts_out if a.get("status") == "flagged")
    site_ok = website_block.get("status") in ("clean", "flagged", "unknown")
    if not artifacts_out:
        status = "partial" if site_ok else "pending"
        message = (
            "No scannable release package assets on GitHub yet. "
            f"Website was scanned. ({release_note})"
            if site_ok
            else f"No release artifacts found to scan. {release_note}"
        ).strip()
    elif flagged:
        status = "attention"
        message = f"{flagged} artifact(s) have at least one engine detection. Review VirusTotal reports before claiming clean."
    elif clean_count == len(artifacts_out):
        status = "clean"
        message = f"All {clean_count} scanned artifacts had 0 malicious/suspicious detections at scan time."
    else:
        status = "partial"
        message = "Some artifacts scanned; others failed or are incomplete."

    payload = {
        "status": status,
        "provider": "VirusTotal",
        "message": message,
        "website": website_block,
        "release_tag": tag_name,
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limitations": LIMITATIONS,
        "artifacts": artifacts_out,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    # Always succeed after writing results — missing packages must not fail the pipeline.
    return 0


if __name__ == "__main__":
    sys.exit(main())
