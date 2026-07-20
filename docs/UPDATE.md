# In-app updates (Tauri updater)

Void checks for updates against **GitHub Releases** only:

```
https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json
```

That URL is pinned in `tauri.conf.json` and again in Rust (`src-tauri/src/updater.rs`). The webview cannot change the endpoint. Artifacts are **signature-verified** against the public key embedded in the binary before install — unsigned or wrongly-signed payloads are rejected.

No analytics, crash reporting, or other “phone home” — just HTTPS to that manifest and (if you accept) the signed installer URL listed inside it.

**Threat model & enterprise checklist:** [SECURITY.md](./SECURITY.md)

## How it works

1. **Build & Release** (GitHub Actions on tag `v*`) builds installers with `createUpdaterArtifacts: true` when the signing secret is present.
2. When `TAURI_SIGNING_PRIVATE_KEY` is set on the **`release`** environment, Tauri produces `.sig` files next to updater bundles (AppImage / NSIS / `.app.tar.gz`).
3. The release job (tags only) runs `scripts/generate-updater-manifest.sh` and uploads `latest.json` to the GitHub Release.
4. On launch (quiet) and via **Settings → Check for updates**, the app fetches `latest.json`, compares SemVer, shows version + notes, and offers **Install & Relaunch**. Set `VOID_DISABLE_UPDATER=1` or pass `--disable-updater` to skip the quiet startup check (Settings check still works; used by RPA for non-`auto_update` runs).
5. The downloaded artifact is verified with the **public key** (pinned in config + Rust) before install. There is no disable-verify path in production builds.

Source of truth for git remains **GitLab** (`lightfootcloud/void-browser`). Push tags to GitLab; the mirror syncs refs, and GitLab CI `trigger-github-build` dispatches Actions (`workflow_dispatch`) because mirror-only often skips tag `PushEvent`s. Set GitLab CI variable `GITHUB_TOKEN` (classic `repo`+`workflow`).

**Full release → site → RPA automation:** [RELEASE_PIPELINE.md](./RELEASE_PIPELINE.md).

### Prereleases / “latest”

GitHub’s `/releases/latest` API **ignores prereleases entirely** — even when
`make_latest: true` is set. If every release is marked `prerelease: true`, then

```
https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json
```

returns **404** and in-app updates cannot run.

The release workflow therefore sets **`prerelease: false`** (version string still
carries `alpha.N`) plus `make_latest: true`, so the pinned updater URL resolves.
Asset URLs inside `latest.json` must use GitHub’s stored names (`Void.Browser_…`,
spaces → `.`); `scripts/generate-updater-manifest.sh` sanitizes that.

### Website `releases.json`

Previously updated by hand before `deploy-site`. Now:

1. **Primary:** GitHub `release` job generates `artifacts/releases.json` and, when
   Environment secret `GITLAB_TOKEN` is set, commits `website/releases.json` to
   GitLab `main` (`scripts/sync-website-releases-to-gitlab.sh`) so deploy-site runs.
   GitHub-hosted runners **cannot** reach private LAN hosts (`10.0.0.10`); the sync
   step soft-fails in that case and does **not** fail the Build & Release workflow.
2. **Fallback:** GitLab job `sync-releases-from-github` (schedule / manual /
   `SYNC_RELEASES=1` pipeline) polls GitHub and commits the same file. Use this
   when Actions cannot reach GitLab (normal for LAN-only GitLab).

## Required GitHub secrets (user action)

Prefer **Environment secrets** on environment name **`release`** (Settings → Environments → `release`), not broad repository secrets:

| Secret | Required | Purpose |
|--------|----------|---------|
| `TAURI_SIGNING_PRIVATE_KEY` | **Yes for auto-updates** | Full contents of the minisign/ed25519 private key (paste key body; path is not used in CI) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Optional | Only if the private key was generated with a password |
| `GITLAB_TOKEN` | Recommended | PAT with `api` + `write_repository` so the release job can commit `website/releases.json` to GitLab. **Note:** GitHub-hosted runners cannot reach LAN-only GitLab (`10.0.0.10`); sync soft-fails and falls back to GitLab `sync-releases-from-github`. |
| `GITLAB_HOST` | Optional | Default `http://10.0.0.10:8929` (unreachable from GitHub runners without a tunnel) |
| `GITLAB_PROJECT_ID` | Optional | Default `lightfootcloud%2Fvoid-browser` |
| `GH_WORKFLOW_TOKEN` | **Required for GHA RPA after release** | Classic PAT (`repo` + `workflow`). Releases created with the default `GITHUB_TOKEN` do **not** fire `on.release` for sibling workflows — Build & Release dispatches **RPA Windows** with this PAT instead. |

Also configure on Environment **`release`**:

- [ ] Required reviewers (dual control before signed publish)
- [ ] Limit who can approve

Without the private key secret, CI still builds normal installers but **disables** updater artifacts and publishes an empty-platforms `latest.json` (clients will not update).

Signing secrets are only injected on `refs/tags/v*`. `workflow_dispatch` on a branch does not receive them. Do **not** add `pull_request` triggers that pass these secrets.

### Keypair for this project

A keypair was generated for Void Browser. The **public** key is already in `tauri.conf.json` (`plugins.updater.pubkey`) and pinned in Rust.

### Local private key risk

A maintainer copy may exist at:

- `%USERPROFILE%\.void-browser-updater.key` (Windows agent host)

**Risk:** on a shared machine this is equivalent to leaving the enterprise root of trust on disk. Recommended:

1. Upload the file contents to GitHub Environment secret `TAURI_SIGNING_PRIVATE_KEY` (environment `release`).
2. **Delete** the local copy after upload, **or** keep a single offline backup under dual control (encrypted USB / password manager) — not on the shared agent.
3. Never commit the private key.

Copy that file’s **entire contents** into the secret (never print it in CI logs or chat).

To generate a **new** keypair (invalidates old clients’ ability to verify future updates unless you also rotate `pubkey` and ship a transitional release):

```bash
npm exec --yes @tauri-apps/cli@2 -- signer generate -w ./void-browser.key
# Put void-browser.key.pub contents into tauri.conf.json plugins.updater.pubkey
# and src-tauri/src/updater.rs UPDATER_PUBKEY
# Put void-browser.key contents into GitHub Environment secret TAURI_SIGNING_PRIVATE_KEY
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

- Update threat model: [SECURITY.md](./SECURITY.md)
- OS code signing (SmartScreen / Gatekeeper): [CODE_SIGNING.md](./CODE_SIGNING.md)
- Updater plugin docs: https://v2.tauri.app/plugin/updater/
