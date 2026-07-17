# In-app updates (Tauri updater)

Void checks for updates against **GitHub Releases** only:

```
https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json
```

No analytics, crash reporting, or other “phone home” — just HTTPS to that manifest and (if you accept) the signed installer URL listed inside it.

## How it works

1. **Build & Release** (GitHub Actions on tag `v*`) builds installers with `createUpdaterArtifacts: true`.
2. When `TAURI_SIGNING_PRIVATE_KEY` is set, Tauri produces `.sig` files next to updater bundles (AppImage / NSIS / `.app.tar.gz`).
3. The release job runs `scripts/generate-updater-manifest.sh` and uploads `latest.json` to the GitHub Release.
4. On launch (quiet) and via **Settings → Check for updates**, the app fetches `latest.json`, compares SemVer, shows version + notes, and offers **Install & Relaunch**.
5. The downloaded artifact is verified with the **public key** embedded in `src-tauri/tauri.conf.json` before install.

Source of truth for git remains **GitLab** (`lightfootcloud/void-browser`). Push tags to GitLab; the GitHub mirror triggers Actions.

### Prereleases / “latest”

GitHub’s `/releases/latest` prefers non-draft releases marked as the latest. The release workflow sets `make_latest: true` so alpha tags still publish a resolvable `latest.json` URL for early-access testing.

## Required GitHub secrets (user action)

Add these on **https://github.com/dadhatdaniel/void-browser/settings/secrets/actions** (never commit private keys):

| Secret | Required | Purpose |
|--------|----------|---------|
| `TAURI_SIGNING_PRIVATE_KEY` | **Yes for auto-updates** | Full contents of the minisign/ed25519 private key (or path is not used in CI — paste key body) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Optional | Only if the private key was generated with a password |

Without the private key secret, CI still builds normal installers but **disables** updater artifacts and publishes an empty-platforms `latest.json` (clients will not update).

### Keypair for this project

A keypair was generated for Void Browser. The **public** key is already in `tauri.conf.json` (`plugins.updater.pubkey`).

The matching private key was saved locally for the maintainer (not in git), e.g.:

- `%USERPROFILE%\.void-browser-updater.key` (Windows agent host)

Copy that file’s **entire contents** into the `TAURI_SIGNING_PRIVATE_KEY` secret.

To generate a **new** keypair (invalidates old clients’ ability to verify future updates unless you also rotate `pubkey` and ship a transitional release):

```bash
npm exec --yes @tauri-apps/cli@2 -- signer generate -w ./void-browser.key
# Put void-browser.key.pub contents into tauri.conf.json plugins.updater.pubkey
# Put void-browser.key contents into GitHub secret TAURI_SIGNING_PRIVATE_KEY
# Never commit void-browser.key
```

## Local builds

Updater artifacts require the signing env vars:

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $env:USERPROFILE\.void-browser-updater.key -Raw
# optional: $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "..."
cargo tauri build
```

To build installers without updater signing, temporarily set `bundle.createUpdaterArtifacts` to `false` in `tauri.conf.json`.

## Related

- OS code signing (SmartScreen / Gatekeeper): [CODE_SIGNING.md](./CODE_SIGNING.md)
- Updater plugin docs: https://v2.tauri.app/plugin/updater/
