# Update security & threat model

Void’s in-app updater is designed so **GitHub login ≠ update trust**. Clients only install artifacts that verify against a **public key baked into the binary** (`src-tauri/tauri.conf.json` + Rust pin). The matching **private signing key** must stay out of the git repo and off shared disks whenever possible.

See also: [UPDATE.md](./UPDATE.md) (how releases work), [CODE_SIGNING.md](./CODE_SIGNING.md) (OS Authenticode / Apple — separate from updater signatures).

## How a fake update gets installed

### Without signature verification (or if verification is disabled)

| # | Path | What happens |
|---|------|----------------|
| 1 | Compromised GitHub account / stolen PAT / session | Attacker edits Release assets or `latest.json` → clients download malware labeled as Void |
| 2 | Compromised GitHub Actions secrets or malicious workflow | CI builds attacker code as “Void” and publishes it |
| 3 | Supply-chain injection | Dependency or workflow change redirects the updater endpoint to an attacker CDN |
| 4 | Insider with release permissions | Same as (1)/(2) with legitimate access |
| 5 | MITM (rare if TLS intact) | Tampered CDN/cache edge; signing still blocks install of altered bytes |

### With signature verification (Void’s design)

The client **refuses** any update whose `.sig` does not match the pinned pubkey. An attacker who only has GitHub or Actions access **still cannot** ship a trusted update unless they also have the **private updater signing key**.

That is the enterprise control: **dual control** — repository/release access and the signing key are separate trust domains.

| Attacker has | Can publish Release assets? | Can clients install as trusted update? |
|--------------|----------------------------|----------------------------------------|
| GitHub login / PAT only | Yes | **No** (signature fails) |
| Actions `contents: write` only | Yes | **No** |
| Private signing key only | No (unless they also have publish access) | Would need a channel to host `latest.json` + assets |
| GitHub **and** private key | Yes | **Yes** — this is the full compromise case |

## What Void locks down in-repo

1. **Pinned pubkey** in `tauri.conf.json` and again in Rust (`main.rs` / `updater.rs`) — no `dangerous*` updater flags enabled.
2. **Pinned endpoint** — only  
   `https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json`  
   (hardcoded in Rust check path; frontend cannot override).
3. **No JS updater IPC** — `updater:default` is not granted to the webview; only the Rust `check_for_updates` command runs updates (avoids proxy/header knobs on the plugin IPC surface).
4. **CI** — least-privilege permissions; signing secrets only on `v*` tags via the `release` environment; release publish only on tags; never wire `pull_request` to signing secrets.

## Enterprise checklist (maintainers)

### Keys

- [ ] Private key lives only as GitHub Environment secret `TAURI_SIGNING_PRIVATE_KEY` on environment **`release`** (preferred), or offline HSM/USB — **never** in git.
- [ ] If a copy exists at `%USERPROFILE%\.void-browser-updater.key` on a shared agent machine: treat as **high risk** — upload to the Environment secret, then **delete the local file** (or keep an offline-only backup under dual control).
- [ ] Document who holds the private key / who can approve `release` environment jobs (minimum two people if possible).
- [ ] Rotate keys on suspected leak: generate new pair → ship transitional release with new pubkey → revoke old key material → invalidate old CI secrets.
- [ ] Optional: `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` if the key is password-protected.

### GitHub / GitLab

- [ ] Enable **2FA** on all accounts with write access to `dadhatdaniel/void-browser` and the GitLab source of truth.
- [ ] **Branch protection** on `main` (required reviews, no force-push).
- [ ] Create GitHub Environment **`release`** with **required reviewers** before jobs that sign or publish.
- [ ] Store `TAURI_SIGNING_*` as **environment** secrets on `release`, not broad repository secrets used by PR workflows (this repo must not add `pull_request` jobs that receive those secrets).
- [ ] Restrict who can push `v*` tags (GitLab + mirrored GitHub).
- [ ] Review Actions workflows on every change that touches `.github/workflows/` (treat as privileged code).

### On leak

1. Revoke GitHub PATs / sessions; rotate account passwords.
2. Remove/replace `TAURI_SIGNING_PRIVATE_KEY` in the `release` environment.
3. Generate a new updater keypair; update pubkey in tree; cut a new signed release.
4. Announce that older unsigned or wrongly-signed manifests must not be trusted.

## Related docs

- [UPDATE.md](./UPDATE.md) — CI secrets, `latest.json`, local signed builds  
- [CODE_SIGNING.md](./CODE_SIGNING.md) — Windows/macOS OS identity (SmartScreen / Gatekeeper)
