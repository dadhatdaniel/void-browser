#!/usr/bin/env bash
# Defensive static checks for the Void Browser desktop app (Rust / Tauri / chrome UI).
#
# Catches high-signal misconfigurations and secret leaks in-repo.
# Does NOT: write exploits, run offensive scanners, or scan third-party sites.
# Windows .exe / VirusTotal remains the manual `virus-scan` job after release.
#
# Usage (repo root): bash scripts/ci/app-security-check.sh
# Exit 0 = pass, 1 = findings.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

FAIL=0
warn() { echo "WARN: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }
ok() { echo "OK: $*"; }

echo "=== App security check (defensive / static) ==="

# ── 1. Required config files ──────────────────────────────────────────────
for f in src-tauri/tauri.conf.json src-tauri/capabilities/default.json; do
  if [[ ! -f "$f" ]]; then
    fail "missing $f"
  else
    ok "found $f"
  fi
done

# ── 2. CSP must be present on the chrome webview ──────────────────────────
if [[ -f src-tauri/tauri.conf.json ]]; then
  if ! grep -Eq '"csp"[[:space:]]*:[[:space:]]*"[^"]+' src-tauri/tauri.conf.json; then
    fail "tauri.conf.json: app.security.csp is missing or empty"
  else
    ok "tauri.conf.json has CSP"
  fi
  if grep -Eq 'dangerousDisableAssetCspModification[[:space:]]*:[[:space:]]*true' src-tauri/tauri.conf.json; then
    fail "tauri.conf.json: dangerousDisableAssetCspModification enabled"
  else
    ok "no dangerousDisableAssetCspModification"
  fi
fi

# ── 3. Updater endpoint must stay HTTPS + pinned GitHub path ──────────────
if [[ -f src-tauri/tauri.conf.json ]]; then
  if grep -Eq 'http://[^"]*latest\.json' src-tauri/tauri.conf.json; then
    fail "updater endpoint uses cleartext http://"
  fi
  if ! grep -Eq 'https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest\.json' src-tauri/tauri.conf.json; then
    fail "updater endpoints must pin the official GitHub latest.json URL"
  else
    ok "updater endpoint pinned to HTTPS GitHub latest.json"
  fi
fi

# ── 4. Deny high-risk Tauri capability grants (capability JSON) ───────────
# These would let chrome IPC reach shell execute / broad filesystem / raw HTTP.
CAP_FILES=()
while IFS= read -r -d '' f; do
  CAP_FILES+=("$f")
done < <(find src-tauri/capabilities -type f -name '*.json' -print0 2>/dev/null || true)

DANGEROUS_PERMS=(
  'shell:allow-execute'
  'shell:allow-spawn'
  'shell:allow-stdin-write'
  'fs:allow-write-file'
  'fs:allow-write-text-file'
  'fs:allow-remove'
  'fs:allow-rename'
  'fs:allow-mkdir'
  'fs:allow-copy-file'
  'fs:scope-app-index'
  'http:default'
  'http:allow-fetch'
  'updater:allow-download'
  'updater:allow-install'
  'updater:default'
)

for cap in "${CAP_FILES[@]:-}"; do
  [[ -z "${cap:-}" ]] && continue
  for p in "${DANGEROUS_PERMS[@]}"; do
    if grep -Fq "\"$p\"" "$cap"; then
      fail "$cap grants dangerous permission: $p"
    fi
  done
done
ok "capabilities: no high-risk shell/fs/http/updater IPC grants found"

# shell:allow-open is intentional (open external links) — note only
for cap in "${CAP_FILES[@]:-}"; do
  [[ -z "${cap:-}" ]] && continue
  if grep -Fq '"shell:allow-open"' "$cap"; then
    warn "$cap includes shell:allow-open (open external URLs) — expected for a browser"
  fi
done

# ── 5. No private key / cleartext secret material in app sources ──────────
SECRET_GLOBS=(src-tauri/src src-tauri/capabilities src)
SECRET_PATTERNS=(
  'BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY'
  'TAURI_SIGNING_PRIVATE_KEY[[:space:]]*='
  '-----BEGIN PRIVATE KEY-----'
  'ghp_[A-Za-z0-9]{20,}'
  'glpat-[A-Za-z0-9._-]{20,}'
  'AKIA[0-9A-Z]{16}'
)

for dir in "${SECRET_GLOBS[@]}"; do
  [[ -d "$dir" ]] || continue
  for pat in "${SECRET_PATTERNS[@]}"; do
    if grep -REn --exclude-dir=target --exclude='*.lock' -E "$pat" "$dir" >/tmp/app-sec-secrets.txt 2>/dev/null; then
      fail "possible secret material under $dir matching /$pat/:"
      sed -n '1,20p' /tmp/app-sec-secrets.txt
    fi
  done
done
ok "no cleartext private-key / token patterns in app sources"

# ── 6. Chrome frontend: no eval / new Function (untrusted-page abuse surface) ─
# Rust Webview::eval for history nav is out of scope (src-tauri/).
CHROME_JS=(src/app.js src/index.html)
for f in "${CHROME_JS[@]}"; do
  [[ -f "$f" ]] || continue
  if grep -En '\beval\s*\(|new\s+Function\s*\(' "$f" >/tmp/app-sec-eval.txt 2>/dev/null; then
    fail "$f uses eval/new Function (dangerous in chrome UI):"
    cat /tmp/app-sec-eval.txt
  fi
  if grep -En 'document\.write\s*\(' "$f" >/tmp/app-sec-write.txt 2>/dev/null; then
    fail "$f uses document.write:"
    cat /tmp/app-sec-write.txt
  fi
done
ok "chrome UI: no eval / new Function / document.write"

# ── 7. Remind: binary VT is manual ────────────────────────────────────────
echo "NOTE: VirusTotal of release .exe/.msi/.dmg remains Manual job virus-scan (stage scan)."
echo "      This job does not analyze binaries."

if [[ "$FAIL" -ne 0 ]]; then
  echo "=== App security check FAILED ==="
  exit 1
fi
echo "=== App security check PASSED ==="
exit 0
