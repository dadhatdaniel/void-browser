// Void Browser — Content webview management (Tauri 2 multi-webview)

use crate::gpu;
use crate::AppState;
use serde::Serialize;
use std::collections::HashSet;
use std::sync::Mutex;
use tauri::webview::{PageLoadEvent, WebviewBuilder};
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, State, WebviewUrl, WindowEvent,
};
use url::Url;

const MAIN_WINDOW: &str = "main";
const DEFAULT_CHROME_HEIGHT: f64 = 78.0;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TabNavEvent {
    pub tab_id: String,
    pub url: String,
    pub title: Option<String>,
}

pub struct BrowserState {
    pub chrome_height: Mutex<f64>,
    pub content_tabs: Mutex<HashSet<String>>,
}

impl Default for BrowserState {
    fn default() -> Self {
        Self {
            chrome_height: Mutex::new(DEFAULT_CHROME_HEIGHT),
            content_tabs: Mutex::new(HashSet::new()),
        }
    }
}

fn content_label(tab_id: &str) -> String {
    format!("content-{tab_id}")
}

fn content_bounds(
    window: &tauri::Window,
    chrome_height: f64,
) -> Result<(LogicalPosition<f64>, LogicalSize<f64>), String> {
    let scale = window.scale_factor().map_err(|e| e.to_string())?;
    let size = window.inner_size().map_err(|e| e.to_string())?;
    let width = f64::from(size.width) / scale;
    let height = f64::from(size.height) / scale;
    let content_h = (height - chrome_height).max(1.0);
    Ok((
        LogicalPosition::new(0.0, chrome_height),
        LogicalSize::new(width.max(1.0), content_h),
    ))
}

fn reflow_content_webviews(app: &AppHandle, chrome_height: f64) -> Result<(), String> {
    let Some(window) = app.get_window(MAIN_WINDOW) else {
        return Ok(());
    };
    let (pos, size) = content_bounds(&window, chrome_height)?;
    let browser = app.state::<BrowserState>();
    let tabs = browser
        .content_tabs
        .lock()
        .map_err(|e| e.to_string())?
        .clone();
    for tab_id in tabs {
        let label = content_label(&tab_id);
        if let Some(webview) = app.get_webview(&label) {
            let _ = webview.set_position(pos);
            let _ = webview.set_size(size);
        }
    }
    Ok(())
}

pub fn attach_resize_handler(app: &AppHandle) -> Result<(), String> {
    let Some(window) = app.get_window(MAIN_WINDOW) else {
        return Ok(());
    };
    let app_handle = app.clone();
    window.on_window_event(move |event| {
        if matches!(
            event,
            WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. }
        ) {
            let height = app_handle
                .state::<BrowserState>()
                .chrome_height
                .lock()
                .map(|h| *h)
                .unwrap_or(DEFAULT_CHROME_HEIGHT);
            let _ = reflow_content_webviews(&app_handle, height);
        }
    });
    Ok(())
}

/// Block main-frame loads only for known tracker/telemetry hosts (not full EasyList).
fn should_block_main_frame(url: &str) -> bool {
    let Ok(parsed) = Url::parse(url) else {
        return false;
    };
    let Some(host) = parsed.host_str() else {
        return false;
    };
    let host = host.to_ascii_lowercase();
    crate::adblock::TRACKER_DOMAINS
        .iter()
        .chain(crate::privacy::BLOCKED_TELEMETRY_DOMAINS.iter())
        .any(|domain| {
            let d = domain.to_ascii_lowercase();
            host == d || host.ends_with(&format!(".{d}"))
        })
}

#[tauri::command]
pub fn set_chrome_height(
    height: f64,
    app: AppHandle,
    browser: State<BrowserState>,
) -> Result<(), String> {
    let h = height.max(40.0);
    *browser.chrome_height.lock().map_err(|e| e.to_string())? = h;
    reflow_content_webviews(&app, h)
}

fn hide_all_content(app: &AppHandle, browser: &BrowserState) -> Result<(), String> {
    let tabs = browser
        .content_tabs
        .lock()
        .map_err(|e| e.to_string())?
        .clone();
    for tab_id in tabs {
        if let Some(webview) = app.get_webview(&content_label(&tab_id)) {
            let _ = webview.hide();
        }
    }
    Ok(())
}

#[tauri::command]
pub async fn show_browser_content(
    tab_id: String,
    app: AppHandle,
    browser: State<'_, BrowserState>,
) -> Result<(), String> {
    hide_all_content(&app, &browser)?;
    if let Some(webview) = app.get_webview(&content_label(&tab_id)) {
        webview.show().map_err(|e| e.to_string())?;
        let height = *browser.chrome_height.lock().map_err(|e| e.to_string())?;
        reflow_content_webviews(&app, height)?;
    }
    Ok(())
}

#[tauri::command]
pub async fn hide_browser_content(
    app: AppHandle,
    browser: State<'_, BrowserState>,
) -> Result<(), String> {
    hide_all_content(&app, &browser)
}

#[tauri::command]
pub async fn navigate_browser(
    tab_id: String,
    url: String,
    app: AppHandle,
    browser: State<'_, BrowserState>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let parsed = Url::parse(&url).map_err(|e| format!("invalid url: {e}"))?;

    {
        let engine = state.blocker.lock().map_err(|e| e.to_string())?;
        let _ = engine.check(&url, &url); // count toward stats
        if should_block_main_frame(&url) {
            let _ = app.emit("block-stats-updated", engine.stats());
            return Err("Blocked tracker/telemetry destination".into());
        }
        let _ = app.emit("block-stats-updated", engine.stats());
    }

    {
        let mut mgr = state.tabs.lock().map_err(|e| e.to_string())?;
        mgr.set_url(&tab_id, &url);
        mgr.set_loading(&tab_id, true);
    }

    let label = content_label(&tab_id);
    let chrome_height = *browser.chrome_height.lock().map_err(|e| e.to_string())?;

    if let Some(webview) = app.get_webview(&label) {
        hide_all_content(&app, &browser)?;
        webview.navigate(parsed).map_err(|e| e.to_string())?;
        webview.show().map_err(|e| e.to_string())?;
        reflow_content_webviews(&app, chrome_height)?;
        let _ = app.emit(
            "tab-navigated",
            TabNavEvent {
                tab_id,
                url,
                title: None,
            },
        );
        return Ok(());
    }

    let window = app
        .get_window(MAIN_WINDOW)
        .ok_or_else(|| "main window not found".to_string())?;
    let (pos, size) = content_bounds(&window, chrome_height)?;

    let tab_id_for_nav = tab_id.clone();
    let tab_id_for_title = tab_id.clone();
    let tab_id_for_load = tab_id.clone();
    let app_for_nav = app.clone();
    let app_for_title = app.clone();
    let app_for_load = app.clone();

    let mut builder = WebviewBuilder::new(&label, WebviewUrl::External(parsed))
        .auto_resize()
        .on_navigation(move |nav_url| {
            let url_str = nav_url.to_string();
            if url_str == "about:blank" {
                return true;
            }
            if should_block_main_frame(&url_str) {
                if let Some(state) = app_for_nav.try_state::<AppState>() {
                    if let Ok(engine) = state.blocker.lock() {
                        let _ = engine.check(&url_str, &url_str);
                        let _ = app_for_nav.emit("block-stats-updated", engine.stats());
                    }
                }
                return false;
            }
            if let Some(state) = app_for_nav.try_state::<AppState>() {
                if let Ok(mut mgr) = state.tabs.lock() {
                    mgr.set_url(&tab_id_for_nav, &url_str);
                }
                if let Ok(engine) = state.blocker.lock() {
                    let _ = engine.check(&url_str, &url_str);
                    let _ = app_for_nav.emit("block-stats-updated", engine.stats());
                }
            }
            let _ = app_for_nav.emit(
                "tab-navigated",
                TabNavEvent {
                    tab_id: tab_id_for_nav.clone(),
                    url: url_str,
                    title: None,
                },
            );
            true
        })
        .on_document_title_changed(move |_webview, title| {
            if let Some(state) = app_for_title.try_state::<AppState>() {
                if let Ok(mut mgr) = state.tabs.lock() {
                    mgr.set_title(&tab_id_for_title, &title);
                }
            }
            let url = app_for_title
                .get_webview(&content_label(&tab_id_for_title))
                .and_then(|w| w.url().ok())
                .map(|u| u.to_string())
                .unwrap_or_default();
            let _ = app_for_title.emit(
                "tab-title-changed",
                TabNavEvent {
                    tab_id: tab_id_for_title.clone(),
                    url,
                    title: Some(title),
                },
            );
        })
        .on_page_load(move |_webview, payload| {
            if matches!(payload.event(), PageLoadEvent::Finished) {
                let url_str = payload.url().to_string();
                if let Some(state) = app_for_load.try_state::<AppState>() {
                    if let Ok(mut mgr) = state.tabs.lock() {
                        mgr.set_url(&tab_id_for_load, &url_str);
                        mgr.set_loading(&tab_id_for_load, false);
                    }
                }
                let _ = app_for_load.emit(
                    "tab-navigated",
                    TabNavEvent {
                        tab_id: tab_id_for_load.clone(),
                        url: url_str,
                        title: None,
                    },
                );
            }
        });

    let args = gpu::webview2_browser_args();
    if !args.is_empty() {
        builder = builder.additional_browser_args(&args);
    }

    hide_all_content(&app, &browser)?;

    window
        .add_child(builder, pos, size)
        .map_err(|e| e.to_string())?;

    browser
        .content_tabs
        .lock()
        .map_err(|e| e.to_string())?
        .insert(tab_id.clone());

    let _ = app.emit(
        "tab-navigated",
        TabNavEvent {
            tab_id,
            url,
            title: None,
        },
    );

    Ok(())
}

#[tauri::command]
pub async fn close_browser_content(
    tab_id: String,
    app: AppHandle,
    browser: State<'_, BrowserState>,
) -> Result<(), String> {
    let label = content_label(&tab_id);
    if let Some(webview) = app.get_webview(&label) {
        webview.close().map_err(|e| e.to_string())?;
    }
    browser
        .content_tabs
        .lock()
        .map_err(|e| e.to_string())?
        .remove(&tab_id);
    Ok(())
}

#[tauri::command]
pub async fn browser_reload(tab_id: String, app: AppHandle) -> Result<(), String> {
    let webview = app
        .get_webview(&content_label(&tab_id))
        .ok_or_else(|| "no content webview".to_string())?;
    webview.reload().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn browser_go_back(tab_id: String, app: AppHandle) -> Result<(), String> {
    let webview = app
        .get_webview(&content_label(&tab_id))
        .ok_or_else(|| "no content webview".to_string())?;
    webview
        .eval("window.history.back()")
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn browser_go_forward(tab_id: String, app: AppHandle) -> Result<(), String> {
    let webview = app
        .get_webview(&content_label(&tab_id))
        .ok_or_else(|| "no content webview".to_string())?;
    webview
        .eval("window.history.forward()")
        .map_err(|e| e.to_string())
}
