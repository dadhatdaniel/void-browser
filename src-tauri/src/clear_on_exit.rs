//! Apply `clear_on_exit` flags via the platform webview profile APIs on quit.
//!
//! - Windows: WebView2 `ICoreWebView2Profile2::ClearBrowsingData`
//! - Linux: WebKitGTK `WebsiteDataManager::clear`
//!
//! RPA / automation can set `VOID_DISABLE_CLEAR_ON_EXIT=1` to skip clearing
//! entirely (sessions survive forced kill/relaunch cycles). App restarts
//! (`RESTART_EXIT_CODE`) also skip clearing.

use crate::config::ClearOnExit;
use crate::AppState;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::{AppHandle, Manager, RunEvent};

const DISABLE_ENV: &str = "VOID_DISABLE_CLEAR_ON_EXIT";

static CLEARING: AtomicBool = AtomicBool::new(false);

fn env_disabled() -> bool {
    match std::env::var(DISABLE_ENV) {
        Ok(v) => {
            let v = v.trim();
            v == "1" || v.eq_ignore_ascii_case("true") || v.eq_ignore_ascii_case("yes")
        }
        Err(_) => false,
    }
}

fn load_flags(app: &AppHandle) -> Option<ClearOnExit> {
    let state = app.try_state::<AppState>()?;
    let cfg = state.config.lock().ok()?;
    Some(cfg.clear_on_exit.clone())
}

fn pick_webview(app: &AppHandle) -> Option<tauri::Webview> {
    // Chrome + content tabs share one WebView2/WebKit profile — any handle works.
    app.get_webview("main")
        .or_else(|| app.webviews().into_values().next())
}

/// Hook for `App::run` — clears browsing data then exits when flags are set.
pub fn on_run_event(app: &AppHandle, event: &RunEvent) {
    let RunEvent::ExitRequested { api, code, .. } = event else {
        return;
    };

    if env_disabled() {
        return;
    }
    // Updater / programmatic restart must keep profile data.
    if *code == Some(tauri::RESTART_EXIT_CODE) {
        return;
    }

    let Some(flags) = load_flags(app) else {
        return;
    };
    if !flags.any_enabled() {
        return;
    }

    // Re-entrancy: we call app.exit() after clear, which re-emits ExitRequested.
    if CLEARING.swap(true, Ordering::SeqCst) {
        return;
    }

    api.prevent_exit();
    let exit_code = code.unwrap_or(0);
    eprintln!(
        "[void] clear_on_exit: cookies={} cache={} history={} local_storage={} downloads_list={}",
        flags.cookies, flags.cache, flags.history, flags.local_storage, flags.downloads_list
    );

    if let Some(webview) = pick_webview(app) {
        if let Err(e) = clear_webview_data(&webview, &flags) {
            eprintln!("[void] clear_on_exit failed: {e}");
        }
    } else {
        eprintln!("[void] clear_on_exit: no webview available");
    }

    app.exit(exit_code);
}

fn clear_webview_data(webview: &tauri::Webview, flags: &ClearOnExit) -> Result<(), String> {
    #[cfg(windows)]
    {
        return clear_webview2(webview, flags);
    }
    #[cfg(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    {
        return clear_webkit(webview, flags);
    }
    #[cfg(not(any(
        windows,
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    )))]
    {
        // Best-effort on other platforms (e.g. macOS WKWebView): only when the
        // user asked for a broad wipe; selective kinds are Windows/Linux only.
        if flags.cookies && flags.cache && flags.local_storage {
            webview
                .clear_all_browsing_data()
                .map_err(|e| e.to_string())?;
        } else {
            eprintln!(
                "[void] clear_on_exit: selective clear not implemented on this platform; skipping"
            );
        }
        let _ = flags;
        Ok(())
    }
}

#[cfg(windows)]
fn clear_webview2(webview: &tauri::Webview, flags: &ClearOnExit) -> Result<(), String> {
    use std::sync::mpsc;
    use webview2_com::ClearBrowsingDataCompletedHandler;
    use webview2_com::Microsoft::Web::WebView2::Win32::{ICoreWebView2Profile2, ICoreWebView2_13};
    use windows::core::Interface;

    let kinds = browsing_data_kinds(flags);
    if kinds.0 == 0 {
        return Ok(());
    }

    let (tx, rx) = mpsc::channel::<Result<(), String>>();
    webview
        .with_webview(move |platform| {
            let controller = platform.controller();
            let result = (|| {
                unsafe {
                    let core = controller.CoreWebView2().map_err(|e| e.to_string())?;
                    let core13: ICoreWebView2_13 = core.cast().map_err(|e| e.to_string())?;
                    let profile = core13.Profile().map_err(|e| e.to_string())?;
                    let profile2: ICoreWebView2Profile2 =
                        profile.cast().map_err(|e| e.to_string())?;
                    let tx_done = tx.clone();
                    profile2
                        .ClearBrowsingData(
                            kinds,
                            &ClearBrowsingDataCompletedHandler::create(Box::new(move |hr| {
                                let _ = tx_done.send(if hr.is_ok() {
                                    Ok(())
                                } else {
                                    Err(format!("ClearBrowsingData HRESULT {hr:?}"))
                                });
                                Ok(())
                            })),
                        )
                        .map_err(|e| e.to_string())?;
                }
                Ok(())
            })();
            if let Err(e) = result {
                let _ = tx.send(Err(e));
            }
        })
        .map_err(|e| e.to_string())?;

    // WebView2 posts the completion callback onto this UI thread — pump briefly.
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    loop {
        match rx.try_recv() {
            Ok(r) => return r,
            Err(mpsc::TryRecvError::Disconnected) => {
                return Err("clear_on_exit: completion channel closed".into());
            }
            Err(mpsc::TryRecvError::Empty) => {
                if std::time::Instant::now() >= deadline {
                    eprintln!(
                        "[void] clear_on_exit: timed out waiting for WebView2; continuing exit"
                    );
                    return Ok(());
                }
                pump_webview2_messages_briefly();
                std::thread::sleep(Duration::from_millis(20));
            }
        }
    }
}

#[cfg(windows)]
fn browsing_data_kinds(
    flags: &ClearOnExit,
) -> webview2_com::Microsoft::Web::WebView2::Win32::COREWEBVIEW2_BROWSING_DATA_KINDS {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_BROWSING_DATA_KINDS, COREWEBVIEW2_BROWSING_DATA_KINDS_BROWSING_HISTORY,
        COREWEBVIEW2_BROWSING_DATA_KINDS_CACHE_STORAGE, COREWEBVIEW2_BROWSING_DATA_KINDS_COOKIES,
        COREWEBVIEW2_BROWSING_DATA_KINDS_DISK_CACHE,
        COREWEBVIEW2_BROWSING_DATA_KINDS_DOWNLOAD_HISTORY,
        COREWEBVIEW2_BROWSING_DATA_KINDS_INDEXED_DB,
        COREWEBVIEW2_BROWSING_DATA_KINDS_LOCAL_STORAGE,
    };

    let mut kinds = COREWEBVIEW2_BROWSING_DATA_KINDS(0);
    if flags.cookies {
        kinds |= COREWEBVIEW2_BROWSING_DATA_KINDS_COOKIES;
    }
    if flags.cache {
        kinds |= COREWEBVIEW2_BROWSING_DATA_KINDS_DISK_CACHE
            | COREWEBVIEW2_BROWSING_DATA_KINDS_CACHE_STORAGE;
    }
    if flags.history {
        kinds |= COREWEBVIEW2_BROWSING_DATA_KINDS_BROWSING_HISTORY;
    }
    if flags.local_storage {
        kinds |= COREWEBVIEW2_BROWSING_DATA_KINDS_LOCAL_STORAGE
            | COREWEBVIEW2_BROWSING_DATA_KINDS_INDEXED_DB;
    }
    if flags.downloads_list {
        kinds |= COREWEBVIEW2_BROWSING_DATA_KINDS_DOWNLOAD_HISTORY;
    }
    kinds
}

#[cfg(windows)]
fn pump_webview2_messages_briefly() {
    use windows::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE,
    };
    unsafe {
        let mut msg = MSG::default();
        for _ in 0..32 {
            if PeekMessageW(&mut msg, None, 0, 0, PM_REMOVE).as_bool() {
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            } else {
                break;
            }
        }
    }
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn clear_webkit(webview: &tauri::Webview, flags: &ClearOnExit) -> Result<(), String> {
    use gtk::gio::Cancellable;
    use gtk::glib::TimeSpan;
    use webkit2gtk::{WebContextExt, WebViewExt, WebsiteDataManagerExtManual, WebsiteDataTypes};

    let mut types = WebsiteDataTypes::empty();
    if flags.cookies {
        types |= WebsiteDataTypes::COOKIES;
    }
    if flags.cache {
        types |= WebsiteDataTypes::DISK_CACHE
            | WebsiteDataTypes::MEMORY_CACHE
            | WebsiteDataTypes::DOM_CACHE;
    }
    if flags.local_storage {
        types |= WebsiteDataTypes::LOCAL_STORAGE
            | WebsiteDataTypes::SESSION_STORAGE
            | WebsiteDataTypes::INDEXEDDB_DATABASES;
    }
    // WebKitGTK has no direct browsing-history / downloads-list kinds; those
    // flags are honored on Windows WebView2 only.
    let _ = (flags.history, flags.downloads_list);

    if types.is_empty() {
        return Ok(());
    }

    let (tx, rx) = std::sync::mpsc::channel::<()>();
    webview
        .with_webview(move |platform| {
            let wk = platform.inner();
            if let Some(context) = wk.context() {
                if let Some(manager) = context.website_data_manager() {
                    manager.clear(
                        types,
                        TimeSpan::from_seconds(0),
                        None::<&Cancellable>,
                        move |_| {
                            let _ = tx.send(());
                        },
                    );
                    return;
                }
            }
            let _ = tx.send(());
        })
        .map_err(|e| e.to_string())?;

    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        match rx.try_recv() {
            Ok(()) => return Ok(()),
            Err(std::sync::mpsc::TryRecvError::Disconnected) => return Ok(()),
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                // Iterate the GLib context so the clear callback can fire.
                let _ = gtk::glib::MainContext::default().iteration(false);
                std::thread::sleep(Duration::from_millis(20));
            }
        }
    }
    eprintln!("[void] clear_on_exit: timed out waiting for WebKit; continuing exit");
    Ok(())
}

#[cfg(test)]
mod tests {
    use crate::config::ClearOnExit;

    #[test]
    fn any_enabled_respects_defaults() {
        let d = ClearOnExit::default();
        assert!(d.any_enabled()); // cache=true
        assert!(!d.cookies);
        assert!(!d.local_storage);
    }

    #[cfg(windows)]
    #[test]
    fn kinds_map_flags() {
        use webview2_com::Microsoft::Web::WebView2::Win32::{
            COREWEBVIEW2_BROWSING_DATA_KINDS_COOKIES, COREWEBVIEW2_BROWSING_DATA_KINDS_DISK_CACHE,
        };
        let flags = ClearOnExit {
            cookies: true,
            cache: true,
            history: false,
            local_storage: false,
            downloads_list: false,
        };
        let kinds = super::browsing_data_kinds(&flags);
        assert!(kinds.0 & COREWEBVIEW2_BROWSING_DATA_KINDS_COOKIES.0 != 0);
        assert!(kinds.0 & COREWEBVIEW2_BROWSING_DATA_KINDS_DISK_CACHE.0 != 0);
    }
}
