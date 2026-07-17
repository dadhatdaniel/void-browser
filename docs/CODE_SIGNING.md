# Code signing (macOS & Windows)

OS identity signing (SmartScreen / Gatekeeper) is **separate** from the Tauri updater signature model — see [SECURITY.md](./SECURITY.md) and [UPDATE.md](./UPDATE.md).

Void Browser builds are **unsigned by default** at the OS level. That is expected until paid certificates are configured. Unsigned installs often trigger:

- **macOS:** Gatekeeper — “Apple cannot check it for malicious software”
- **Windows:** SmartScreen / Defender — “Windows protected your PC”

VirusTotal heuristics may also flag new unsigned Windows installers. Signing does not replace checksum verification; it reduces OS warnings and builds reputation over time.

## What you need (paid)

| Platform | Program | Approx. cost | Certificate / service |
|----------|---------|--------------|------------------------|
| macOS | [Apple Developer Program](https://developer.apple.com/programs/) | ~$99/year | **Developer ID Application** (+ notarization) |
| Windows | Authenticode EV/OV or [Azure Trusted Signing](https://learn.microsoft.com/en-us/azure/trusted-signing/) | Varies (often hundreds USD/year) | Code signing cert or Trusted Signing account |

There is no free path that fully removes Gatekeeper / SmartScreen for distribution outside the Mac App Store / Microsoft Store.

---

## macOS — codesign + notarize + staple

### 1. Apple setup
1. Enroll in Apple Developer Program.
2. Create a **Developer ID Application** certificate in Certificates, Identifiers & Profiles.
3. Export as `.p12` (keep password private).
4. Create an [app-specific password](https://support.apple.com/en-us/HT204397) for notarization.
5. Note your **Team ID** (Membership details).

### 2. Secrets (GitHub Actions — builds run there)

Add repository secrets (never commit these):

| Secret | Purpose |
|--------|---------|
| `APPLE_CERTIFICATE` | Base64 of the `.p12` file (`base64 -i cert.p12`) |
| `APPLE_CERTIFICATE_PASSWORD` | `.p12` password |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | Apple ID email |
| `APPLE_TEAM_ID` | 10-character Team ID |
| `APPLE_ID_PASSWORD` | App-specific password (not your login password) |

### 3. Local / CI flow (Tauri)

```bash
# After cargo tauri build produces a .app / .dmg
codesign --deep --force --options runtime \
  --sign "$APPLE_SIGNING_IDENTITY" \
  "src-tauri/target/release/bundle/macos/Void Browser.app"

# Notarize (notarytool)
xcrun notarytool submit path/to/Void*.dmg \
  --apple-id "$APPLE_ID" \
  --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_ID_PASSWORD" \
  --wait

xcrun stapler staple path/to/Void*.dmg
```

Tauri 2 also supports env-driven signing when `APPLE_*` vars are set — see the conditional steps in `.github/workflows/build.yml` (`Sign and notarize macOS` job step, runs only when secrets exist).

### 4. Users on unsigned builds (today)
Right-click → Open, or: System Settings → Privacy & Security → Open Anyway.

---

## Windows — Authenticode / Trusted Signing

### Option A — Standard code signing certificate
1. Buy an OV or EV Authenticode certificate from a public CA (or use Azure Trusted Signing).
2. Import into a `.pfx` (or use cloud HSM / Trusted Signing — preferred for new projects).
3. Sign with `signtool` after the MSI/NSIS build:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /f "$env:WINDOWS_CERT_PFX" /p "$env:WINDOWS_CERT_PASSWORD" `
  "path\to\Void.Browser_*.msi" "path\to\Void.Browser_*-setup.exe"
```

### Option B — Azure Trusted Signing
Microsoft’s cloud signing service; often easier than shipping a USB token. Configure an identity validation, then use the Trusted Signing action / `Invoke-TrustedSigning` in CI.

### Secrets (GitHub Actions)

| Secret | Purpose |
|--------|---------|
| `WINDOWS_CERT_PFX` | Base64 of `.pfx` (or use Trusted Signing instead) |
| `WINDOWS_CERT_PASSWORD` | PFX password |
| `WINDOWS_CERT_SHA1` | Optional thumbprint if using cert store |

The Windows job in `.github/workflows/build.yml` has a conditional `Sign Windows packages` step that runs only when `WINDOWS_CERT_PFX` is present.

### SmartScreen reputation
Even after signing, SmartScreen may warn until the certificate accumulates reputation (downloads over time). EV certs / Trusted Signing usually clear faster than cheap OV certs.

### Users on unsigned builds (today)
Click **More info** → **Run anyway**. Prefer the `.msi` if the `.exe` is more aggressively flagged.

---

## Website honesty

The landing page must **not** claim packages are signed until CI actually signs them. Safety scan scores come from VirusTotal and are separate from OS code-signing status.

## Checklist before claiming “signed releases”

- [ ] Apple Developer Program active; Developer ID cert exported
- [ ] Notarization succeeds and staple verifies (`spctl --assess -vv`)
- [ ] Windows packages signed; `signtool verify /pa` passes
- [ ] GitHub Actions secrets set; tag build logs show sign/notarize steps ran
- [ ] Website copy updated only after a signed release is published
