#!/usr/bin/env bash
# Defensive static checks for the Void Browser marketing site (website/).
#
# Analyzes in-repo HTML/CSS/JSON + nginx Dockerfile hardening.
# Does NOT: run nikto/nmap/sqlmap, scan third-party sites, or generate exploits.
# Optional live header probe against our own origin (:5080) is read-only.
#
# Usage (repo root): bash scripts/ci/site-security-check.sh
# Env:
#   SITE_SECURITY_BASE  — default http://10.0.0.10:5080 (own deploy only)
#   SITE_SECURITY_LIVE  — set to 1 to require live header checks (default: try, soft-fail)
# Exit 0 = pass, 1 = findings.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

FAIL=0
warn() { echo "WARN: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }
ok() { echo "OK: $*"; }

SITE_DIR="website"
BASE="${SITE_SECURITY_BASE:-http://10.0.0.10:5080}"

echo "=== Site security check (defensive / static) ==="

if [[ ! -d "$SITE_DIR" ]]; then
  fail "missing $SITE_DIR/"
  echo "=== Site security check FAILED ==="
  exit 1
fi

# ── 1. Dockerfile must declare core security headers ──────────────────────
DF="$SITE_DIR/Dockerfile"
if [[ ! -f "$DF" ]]; then
  fail "missing $DF"
else
  REQUIRED_HEADERS=(
    'X-Frame-Options'
    'X-Content-Type-Options'
    'Referrer-Policy'
    'Permissions-Policy'
    'Content-Security-Policy'
    'Cross-Origin-Opener-Policy'
  )
  for h in "${REQUIRED_HEADERS[@]}"; do
    if ! grep -Fq "$h" "$DF"; then
      fail "$DF missing add_header for $h"
    fi
  done
  if grep -Eq "Content-Security-Policy[^\"]*unsafe-eval" "$DF"; then
    fail "$DF CSP allows 'unsafe-eval'"
  fi
  if grep -Eq "frame-ancestors[[:space:]]+\*" "$DF"; then
    fail "$DF CSP frame-ancestors is wildcard"
  fi
  ok "Dockerfile declares required security headers"
fi

# ── 2. No third-party tracking / analytics in static HTML ─────────────────
if grep -REiq --include='*.html' --include='*.js' \
  'google-analytics|googletagmanager|gtag\(|facebook\.net|hotjar|segment\.com|mixpanel' \
  "$SITE_DIR" >/tmp/site-sec-track.txt 2>/dev/null; then
  fail "tracking / analytics markers found:"
  sed -n '1,30p' /tmp/site-sec-track.txt
else
  ok "no tracking / analytics scripts"
fi

# ── 3. No eval / new Function in site assets ──────────────────────────────
if grep -REn --include='*.html' --include='*.js' \
  '\beval\s*\(|new\s+Function\s*\(|document\.write\s*\(' \
  "$SITE_DIR" >/tmp/site-sec-eval.txt 2>/dev/null; then
  fail "eval / new Function / document.write in website assets:"
  cat /tmp/site-sec-eval.txt
else
  ok "no eval / new Function / document.write"
fi

# ── 4. Mixed content: avoid http:// asset URLs in HTML (allow XML NS) ─────
# Flag http:// in href/src/action attributes pointing at remote hosts.
if grep -REn --include='*.html' \
  '(href|src|action)=["'\'']http://' \
  "$SITE_DIR" >/tmp/site-sec-mixed.txt 2>/dev/null; then
  # Allow localhost / 127.0.0.1 only if somehow present; everything else fails
  if grep -Ev 'http://(localhost|127\.0\.0\.1)([:/'\''"]|$)' /tmp/site-sec-mixed.txt >/tmp/site-sec-mixed-bad.txt; then
    if [[ -s /tmp/site-sec-mixed-bad.txt ]]; then
      fail "cleartext http:// resource URLs (mixed content risk):"
      cat /tmp/site-sec-mixed-bad.txt
    fi
  fi
else
  ok "no cleartext http:// href/src/action URLs"
fi

# ── 5. javascript: / data: navigation sinks ───────────────────────────────
if grep -REn --include='*.html' --include='*.js' \
  'href=["'\'']javascript:|location\s*=\s*["'\'']javascript:' \
  "$SITE_DIR" >/tmp/site-sec-jsurl.txt 2>/dev/null; then
  fail "javascript: URL sinks found:"
  cat /tmp/site-sec-jsurl.txt
else
  ok "no javascript: URL sinks"
fi

# ── 6. target=_blank should use rel noopener ──────────────────────────────
# Soft: warn if target=_blank without noopener somewhere on the same tag.
while IFS= read -r -d '' html; do
  if grep -En 'target=["'\'']_blank["'\'']' "$html" >/tmp/site-sec-blank.txt 2>/dev/null; then
    while IFS= read -r line; do
      if ! echo "$line" | grep -Eq 'rel=["'\''][^"'\'']*noopener'; then
        # Many multiline tags; check a window around the line in the file
        ln=$(echo "$line" | cut -d: -f1)
        ctx=$(sed -n "$((ln > 2 ? ln - 2 : 1)),$((ln + 2))p" "$html")
        if ! echo "$ctx" | grep -Eq 'noopener'; then
          warn "$html:$ln target=_blank without nearby rel=noopener"
        fi
      fi
    done < /tmp/site-sec-blank.txt
  fi
done < <(find "$SITE_DIR" -type f -name '*.html' -print0)

# ── 7. Secret / credential patterns in website/ ───────────────────────────
SECRET_PATTERNS=(
  'BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY'
  '-----BEGIN PRIVATE KEY-----'
  'ghp_[A-Za-z0-9]{20,}'
  'glpat-[A-Za-z0-9._-]{20,}'
  'AKIA[0-9A-Z]{16}'
  'VIRUSTOTAL_API_KEY[[:space:]]*='
  'password[[:space:]]*=[[:space:]]*["'\''][^"'\'']{8,}'
)
for pat in "${SECRET_PATTERNS[@]}"; do
  if grep -REn --exclude='*.png' --exclude='*.ico' --exclude='*.woff*' \
    -E "$pat" "$SITE_DIR" >/tmp/site-sec-secrets.txt 2>/dev/null; then
    fail "possible secret material in website/ matching /$pat/:"
    sed -n '1,20p' /tmp/site-sec-secrets.txt
  fi
done
ok "no cleartext secret patterns in website/"

# ── 8. Open-redirect style query sinks in inline JS (static heuristic) ────
if grep -REn --include='*.html' --include='*.js' \
  'location\.(href|replace|assign)\s*=\s*[^;]*(location\.search|URLSearchParams|document\.URL)' \
  "$SITE_DIR" >/tmp/site-sec-redir.txt 2>/dev/null; then
  fail "possible open-redirect sink (location assigned from query/URL):"
  cat /tmp/site-sec-redir.txt
else
  ok "no obvious open-redirect sinks from query params"
fi

# ── 9. Optional live header probe (own origin only) ───────────────────────
# Read-only HEAD/GET of our deploy host. Never scans third parties.
probe_live() {
  local url="$1"
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl missing — skip live header probe"
    return 0
  fi
  echo "=== Live header probe (own site only): $url ==="
  local hdr
  if ! hdr=$(curl -sS -D - -o /dev/null --max-time 15 "$url/" 2>/tmp/site-sec-curl.err); then
    if [[ "${SITE_SECURITY_LIVE:-0}" == "1" ]]; then
      fail "live origin unreachable: $url ($(cat /tmp/site-sec-curl.err 2>/dev/null || true))"
    else
      warn "live origin unreachable — static checks only ($(cat /tmp/site-sec-curl.err 2>/dev/null || true))"
    fi
    return 0
  fi
  local need
  for need in "x-frame-options" "x-content-type-options" "content-security-policy" "referrer-policy"; do
    if ! echo "$hdr" | grep -Eiq "^${need}:"; then
      if [[ "${SITE_SECURITY_LIVE:-0}" == "1" ]]; then
        fail "live response missing header: $need"
      else
        warn "live response missing header: $need"
      fi
    fi
  done
  if echo "$hdr" | grep -Eiq '^content-security-policy:.*unsafe-eval'; then
    fail "live CSP includes unsafe-eval"
  fi
  ok "live header probe completed for $url"
}

# Only probe RFC1918 / known void host — refuse anything else
case "$BASE" in
  http://10.0.0.10:5080|https://void.lightfoot.cloud|http://127.0.0.1:*|http://localhost:*)
    probe_live "$BASE"
    ;;
  *)
    warn "SITE_SECURITY_BASE=$BASE not in allowlist — skipping live probe (static only)"
    ;;
esac

if [[ "$FAIL" -ne 0 ]]; then
  echo "=== Site security check FAILED ==="
  exit 1
fi
echo "=== Site security check PASSED ==="
exit 0
