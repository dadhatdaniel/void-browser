// Void Browser — Content webview management (Tauri 2 multi-webview)
//
// Architecture (Windows-critical):
// The default WebviewWindow creates a full-window "main" webview for chrome UI.
// Child content webviews added with add_child can end up *under* that opaque
// surface on WebView2, which looks like a solid black (or, after a partial
// shrink, solid white Win32 client) content area while the URL bar still
// updates (navigation works, pixels never appear).
//
// Fix while browsing:
// 1) Shrink the shell ("main") webview to the chrome strip only.
// 2) Place content webviews in the remaining bounds (atomic set_bounds).
// 3) Force WebView2 controller visibility + a short deferred reflow (Windows
//    applies SetWindowPos asynchronously via SWP_ASYNCWINDOWPOS).
// Expand the shell again for internal pages (newtab / settings).

use crate::gpu;
use crate::AppState;
use serde::Serialize;
use std::collections::HashSet;
use std::sync::Mutex;
use std::time::Duration;
use tauri::webview::{PageLoadEvent, WebviewBuilder};
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, Rect, State, WebviewUrl,
    WindowEvent,
};
use url::Url;

const MAIN_WINDOW: &str = "main";
const MAIN_WEBVIEW: &str = "main";
const DEFAULT_CHROME_HEIGHT: f64 = 78.0;
/// Guard against a flex/layout glitch reporting the full window as "chrome".
const MAX_CHROME_HEIGHT: f64 = 160.0;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TabNavEvent {
    pub tab_id: String,
    pub url: String,
    pub title: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WebviewInfo {
    pub tab_id: String,
    pub label: String,
    pub url: String,
    pub visible: bool,
    pub width: f64,
    pub height: f64,
    pub x: f64,
    pub y: f64,
    pub chrome_height: f64,
    pub shell_browsing: bool,
    pub shell_width: f64,
    pub shell_height: f64,
}

pub struct BrowserState {
    pub chrome_height: Mutex<f64>,
    pub content_tabs: Mutex<HashSet<String>>,
    /// True when a content webview should own the area below chrome.
    pub shell_browsing: Mutex<bool>,
}

impl Default for BrowserState {
    fn default() -> Self {
        Self {
            chrome_height: Mutex::new(DEFAULT_CHROME_HEIGHT),
            content_tabs: Mutex::new(HashSet::new()),
            shell_browsing: Mutex::new(false),
        }
    }
}

fn content_label(tab_id: &str) -> String {
    format!("content-{tab_id}")
}

fn clamp_chrome_height(height: f64) -> f64 {
    height.clamp(40.0, MAX_CHROME_HEIGHT)
}

/// Compute content-area bounds under the chrome strip (logical pixels).
pub(crate) fn content_bounds_for(
    window_width: f64,
    window_height: f64,
    chrome_height: f64,
) -> (LogicalPosition<f64>, LogicalSize<f64>) {
    let chrome = clamp_chrome_height(chrome_height);
    let content_h = (window_height - chrome).max(1.0);
    (
        LogicalPosition::new(0.0, chrome),
        LogicalSize::new(window_width.max(1.0), content_h),
    )
}

fn window_logical_size(window: &tauri::Window) -> Result<(f64, f64), String> {
    let scale = window.scale_factor().map_err(|e| e.to_string())?;
    let size = window.inner_size().map_err(|e| e.to_string())?;
    Ok((
        f64::from(size.width) / scale,
        f64::from(size.height) / scale,
    ))
}

fn content_bounds(
    window: &tauri::Window,
    chrome_height: f64,
) -> Result<(LogicalPosition<f64>, LogicalSize<f64>), String> {
    let (width, height) = window_logical_size(window)?;
    Ok(content_bounds_for(width, height, chrome_height))
}

fn apply_webview_bounds(
    webview: &tauri::Webview,
    pos: LogicalPosition<f64>,
    size: LogicalSize<f64>,
) -> Result<(), String> {
    let _ = webview.set_auto_resize(false);
    // Prefer atomic set_bounds; fall back to position+size if the runtime rejects it.
    if webview
        .set_bounds(Rect {
            position: pos.into(),
            size: size.into(),
        })
        .is_err()
    {
        webview
            .set_position(pos)
            .map_err(|e| e.to_string())?;
        webview.set_size(size).map_err(|e| e.to_string())?;
    }
    let _ = webview.show();
    Ok(())
}

/// Ensure the child webview HWND is shown and focused after layout.
fn force_webview_visible(webview: &tauri::Webview) {
    let _ = webview.show();
    let _ = webview.set_focus();
}

/// Resize the chrome ("main") webview: strip-only while browsing, full window otherwise.
fn layout_shell(app: &AppHandle, chrome_height: f64, browsing: bool) -> Result<(), String> {
    let Some(window) = app.get_window(MAIN_WINDOW) else {
        return Ok(());
    };
    let (width, height) = window_logical_size(&window)?;
    let Some(shell) = app.get_webview(MAIN_WEBVIEW) else {
        return Ok(());
    };

    // Manual layout — do not let the shell auto-fill the window over content.
    // Use set_position/set_size (not set_bounds) on the WebviewWindow primary
    // webview — set_bounds has been observed to break its dispatcher on Windows.
    let _ = shell.set_auto_resize(false);
    let _ = shell.set_position(LogicalPosition::new(0.0, 0.0));
    if browsing {
        let h = clamp_chrome_height(chrome_height).clamp(1.0, height.max(1.0));
        shell
            .set_size(LogicalSize::new(width.max(1.0), h))
            .map_err(|e| e.to_string())?;
    } else {
        shell
            .set_size(LogicalSize::new(width.max(1.0), height.max(1.0)))
            .map_err(|e| e.to_string())?;
    }
    Ok(())
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
            apply_webview_bounds(&webview, pos, size)?;
            force_webview_visible(&webview);
        }
    }
    Ok(())
}

fn reflow_all(app: &AppHandle) -> Result<(), String> {
    let browser = app.state::<BrowserState>();
    let chrome_height =
        clamp_chrome_height(*browser.chrome_height.lock().map_err(|e| e.to_string())?);
    let browsing = *browser.shell_browsing.lock().map_err(|e| e.to_string())?;
    // Content first, then shell strip last so chrome stays above the page region.
    if browsing {
        reflow_content_webviews(app, chrome_height)?;
    }
    layout_shell(app, chrome_height, browsing)?;
    Ok(())
}

fn schedule_deferred_reflow(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        // WebView2/tao apply child HWND moves asynchronously; a second pass
        // fixes the white-client blank that survives the first layout.
        tokio::time::sleep(Duration::from_millis(50)).await;
        let _ = reflow_all(&app);
        tokio::time::sleep(Duration::from_millis(200)).await;
        let _ = reflow_all(&app);
    });
}

fn set_shell_browsing(app: &AppHandle, browsing: bool) -> Result<(), String> {
    {
        let browser = app.state::<BrowserState>();
        *browser.shell_browsing.lock().map_err(|e| e.to_string())? = browsing;
    }
    reflow_all(app)?;
    if browsing {
        schedule_deferred_reflow(app);
    }
    Ok(())
}

pub fn attach_resize_handler(app: &AppHandle) -> Result<(), String> {
    let Some(window) = app.get_window(MAIN_WINDOW) else {
        return Ok(());
    };
    // Main webview starts full-window for newtab; never auto-stretch over children.
    if let Some(shell) = app.get_webview(MAIN_WEBVIEW) {
        let _ = shell.set_auto_resize(false);
    }
    let app_handle = app.clone();
    window.on_window_event(move |event| {
        if matches!(
            event,
            WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. }
        ) {
            let _ = reflow_all(&app_handle);
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
    let h = clamp_chrome_height(height);
    *browser.chrome_height.lock().map_err(|e| e.to_string())? = h;
    reflow_all(&app)
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
    set_shell_browsing(&app, true)?;
    if let Some(webview) = app.get_webview(&content_label(&tab_id)) {
        force_webview_visible(&webview);
        reflow_all(&app)?;
        schedule_deferred_reflow(&app);
    }
    Ok(())
}

#[tauri::command]
pub async fn hide_browser_content(
    app: AppHandle,
    browser: State<'_, BrowserState>,
) -> Result<(), String> {
    hide_all_content(&app, &browser)?;
    set_shell_browsing(&app, false)?;
    Ok(())
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
        set_shell_browsing(&app, true)?;
        webview.navigate(parsed).map_err(|e| e.to_string())?;
        force_webview_visible(&webview);
        reflow_all(&app)?;
        schedule_deferred_reflow(&app);
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

    // No auto_resize: we own bounds so the content pane cannot expand over chrome.
    let mut builder = WebviewBuilder::new(&label, WebviewUrl::External(parsed))
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
    // Shrink chrome shell *before* attaching so the child is not covered at creation.
    set_shell_browsing(&app, true)?;

    let webview = window
        .add_child(builder, pos, size)
        .map_err(|e| e.to_string())?;
    apply_webview_bounds(&webview, pos, size)?;
    force_webview_visible(&webview);

    browser
        .content_tabs
        .lock()
        .map_err(|e| e.to_string())?
        .insert(tab_id.clone());

    reflow_all(&app)?;
    schedule_deferred_reflow(&app);

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

    let remaining = browser
        .content_tabs
        .lock()
        .map_err(|e| e.to_string())?
        .len();
    if remaining == 0 {
        set_shell_browsing(&app, false)?;
    }
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

/// Debug / CI: bounds + URL for the active content webview (and chrome shell).
#[tauri::command]
pub fn get_webview_info(
    tab_id: Option<String>,
    app: AppHandle,
    browser: State<BrowserState>,
    state: State<AppState>,
) -> Result<WebviewInfo, String> {
    let chrome_height = *browser.chrome_height.lock().map_err(|e| e.to_string())?;
    let shell_browsing = *browser.shell_browsing.lock().map_err(|e| e.to_string())?;

    let (shell_width, shell_height) = if let Some(shell) = app.get_webview(MAIN_WEBVIEW) {
        let scale = app
            .get_window(MAIN_WINDOW)
            .and_then(|w| w.scale_factor().ok())
            .unwrap_or(1.0);
        match shell.size() {
            Ok(size) => (
                f64::from(size.width) / scale,
                f64::from(size.height) / scale,
            ),
            Err(_) => (0.0, 0.0),
        }
    } else {
        (0.0, 0.0)
    };

    let tab_id = match tab_id {
        Some(id) => id,
        None => state
            .tabs
            .lock()
            .map_err(|e| e.to_string())?
            .active_id()
            .ok_or_else(|| "no active tab".to_string())?,
    };

    let label = content_label(&tab_id);
    let Some(webview) = app.get_webview(&label) else {
        return Ok(WebviewInfo {
            tab_id,
            label,
            url: String::new(),
            visible: false,
            width: 0.0,
            height: 0.0,
            x: 0.0,
            y: 0.0,
            chrome_height,
            shell_browsing,
            shell_width,
            shell_height,
        });
    };

    let scale = app
        .get_window(MAIN_WINDOW)
        .and_then(|w| w.scale_factor().ok())
        .unwrap_or(1.0);
    // Prefer bounds() (one IPC) — separate size/position can fail mid-reflow on WebView2.
    let (width, height, x, y) = match webview.bounds() {
        Ok(bounds) => {
            let size = bounds.size.to_logical::<f64>(scale);
            let pos = bounds.position.to_logical::<f64>(scale);
            (size.width, size.height, pos.x, pos.y)
        }
        Err(_) => {
            let size = webview.size().map_err(|e| e.to_string())?;
            let pos = webview.position().map_err(|e| e.to_string())?;
            (
                f64::from(size.width) / scale,
                f64::from(size.height) / scale,
                f64::from(pos.x) / scale,
                f64::from(pos.y) / scale,
            )
        }
    };
    let url = webview.url().map(|u| u.to_string()).unwrap_or_default();

    // Webview2 has no reliable is_visible on Webview; treat "tracked + non-zero" as visible.
    let visible = width >= 1.0 && height >= 1.0 && shell_browsing;

    Ok(WebviewInfo {
        tab_id,
        label,
        url,
        visible,
        width,
        height,
        x,
        y,
        chrome_height,
        shell_browsing,
        shell_width,
        shell_height,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_bounds_sit_below_chrome() {
        let (pos, size) = content_bounds_for(1280.0, 900.0, 78.0);
        assert_eq!(pos.x, 0.0);
        assert_eq!(pos.y, 78.0);
        assert_eq!(size.width, 1280.0);
        assert_eq!(size.height, 822.0);
    }

    #[test]
    fn content_bounds_never_zero_height() {
        let (_pos, size) = content_bounds_for(800.0, 50.0, 78.0);
        assert!(size.height >= 1.0);
        assert!(size.width >= 1.0);
    }

    #[test]
    fn chrome_height_is_capped() {
        let (pos, size) = content_bounds_for(1280.0, 900.0, 900.0);
        assert_eq!(pos.y, MAX_CHROME_HEIGHT);
        assert_eq!(size.height, 900.0 - MAX_CHROME_HEIGHT);
        assert_eq!(clamp_chrome_height(12.0), 40.0);
        assert_eq!(clamp_chrome_height(500.0), MAX_CHROME_HEIGHT);
    }

    #[test]
    fn content_label_format() {
        assert_eq!(content_label("abc"), "content-abc");
    }
}
