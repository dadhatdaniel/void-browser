//! In-app updates via GitHub Releases + tauri-plugin-updater.
//!
//! Trust model: signature verification is mandatory (minisign/ed25519). The
//! public key and endpoint are pinned in Rust so the webview cannot redirect
//! checks or disable verification. See docs/SECURITY.md.
//!
//! Privacy: the only network call is HTTPS to the pinned updater endpoint
//! (GitHub Releases `latest.json`) and, if the user accepts, the signed update
//! asset URL. No telemetry beyond that.

use tauri::{AppHandle, Runtime};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;
use url::Url;

/// Must match `plugins.updater.pubkey` in tauri.conf.json.
pub const UPDATER_PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDM0NENDRTA4RTlEOTVEMjcKUldRblhkbnBDTTVNTkZWTGpNL0xKVEo5UGU5LzBLSVVQRzV5MmFlUEY4SGpQRSt5RVpTTUpmdEYK";

/// Only trusted manifest URL — not configurable from JS.
pub const UPDATER_ENDPOINT: &str =
    "https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json";

/// Result returned to the Settings UI.
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateCheckResult {
    pub update_available: bool,
    pub current_version: String,
    pub latest_version: Option<String>,
    pub notes: Option<String>,
    pub message: String,
}

/// Quiet startup check: prompt only when an update exists.
pub fn spawn_startup_check<R: Runtime>(app: AppHandle<R>) {
    tauri::async_runtime::spawn(async move {
        // Let the main window paint before any dialog.
        tokio::time::sleep(std::time::Duration::from_secs(4)).await;
        if let Err(e) = check_and_prompt(app, false).await {
            eprintln!("[void] update check skipped: {e}");
        }
    });
}

/// Settings / manual check. Shows a dialog when already up to date.
#[tauri::command]
pub async fn check_for_updates<R: Runtime>(app: AppHandle<R>) -> Result<UpdateCheckResult, String> {
    check_and_prompt(app, true).await
}

async fn check_and_prompt<R: Runtime>(
    app: AppHandle<R>,
    notify_up_to_date: bool,
) -> Result<UpdateCheckResult, String> {
    let current = app.package_info().version.to_string();

    let endpoint = Url::parse(UPDATER_ENDPOINT)
        .map_err(|e| format!("invalid pinned updater endpoint: {e}"))?;

    // Pin endpoint + pubkey at check time. Never call dangerous* overrides.
    let updater = app
        .updater_builder()
        .pubkey(UPDATER_PUBKEY)
        .endpoints(vec![endpoint])
        .map_err(|e| format!("updater endpoints rejected: {e}"))?
        .build()
        .map_err(|e| format!("updater unavailable: {e}"))?;

    let update = updater
        .check()
        .await
        .map_err(|e| format!("update check failed: {e}"))?;

    let Some(update) = update else {
        let message = "You're on the latest version.".to_string();
        if notify_up_to_date {
            show_info(&app, "Void Browser", &message);
        }
        return Ok(UpdateCheckResult {
            update_available: false,
            current_version: current,
            latest_version: None,
            notes: None,
            message,
        });
    };

    let latest = update.version.clone();
    let notes = update.body.clone().unwrap_or_default();
    let notes_trimmed = notes.trim().to_string();

    let body = if notes_trimmed.is_empty() {
        format!(
            "Version {latest} is available (you have {current}).\n\n\
             Download, install, and relaunch now?\n\n\
             Updates are signature-verified before install. \
             Update checks contact GitHub Releases over HTTPS only — no telemetry."
        )
    } else {
        format!(
            "Version {latest} is available (you have {current}).\n\n\
             {notes_trimmed}\n\n\
             Download, install, and relaunch now?\n\n\
             Updates are signature-verified before install. \
             Update checks contact GitHub Releases over HTTPS only — no telemetry."
        )
    };

    let install = ask_install(&app, "Update available", &body);
    if !install {
        return Ok(UpdateCheckResult {
            update_available: true,
            current_version: current,
            latest_version: Some(latest),
            notes: if notes_trimmed.is_empty() {
                None
            } else {
                Some(notes_trimmed)
            },
            message: "Update available — install deferred.".into(),
        });
    }

    // download_and_install verifies the minisign signature against the pinned pubkey.
    update
        .download_and_install(|_chunk, _total| {}, || {})
        .await
        .map_err(|e| format!("update install failed: {e}"))?;

    app.restart();
}

fn show_info<R: Runtime>(app: &AppHandle<R>, title: &str, message: &str) {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog()
        .message(message.to_string())
        .title(title.to_string())
        .kind(MessageDialogKind::Info)
        .show(move |_| {
            let _ = tx.send(());
        });
    let _ = rx.recv();
}

fn ask_install<R: Runtime>(app: &AppHandle<R>, title: &str, message: &str) -> bool {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog()
        .message(message.to_string())
        .title(title.to_string())
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install & Relaunch".into(),
            "Later".into(),
        ))
        .show(move |answer| {
            let _ = tx.send(answer);
        });
    rx.recv().unwrap_or(false)
}
