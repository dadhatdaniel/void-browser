// Void Browser — Content webview management (Tauri 2 multi-webview)
//
// Root cause (Windows WebView2 + Tauri 2):
// A config-created WebviewWindow builds the chrome UI as WebviewKind::WindowContent
// (wry `is_child = false`). That path attaches a parent WM_SIZE subclass that
// ALWAYS stretches the chrome HWND back to the full client area. Shrinking the
// shell then either:
//   - gets undone on the next resize → opaque chrome covers the page (black), or
//   - reveals the Win32 client while the page child never paints on top (white),
// while URL/title events still fire (navigation works, pixels do not).
//
// Additionally, wry `set_bounds` on Windows uses SWP_ASYNCWINDOWPOS and has been
// observed to break the webview dispatcher ("failed to receive message"), so we
// only use set_position + set_size.
//
// Fix:
// 1) Create a plain Window and attach chrome as a child webview (same as content).
// 2) While browsing, size chrome to the strip and content to the area below.
// 3) On Windows, force the content controller visible and HWND_TOP after layout.

use crate::gpu;
use crate::AppState;
use serde::Serialize;
use std::collections::HashSet;
use std::sync::Mutex;
use std::time::Duration;
use tauri::webview::{PageLoadEvent, WebviewBuilder};
use tauri::window::WindowBuilder;
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, State, WebviewUrl, WindowEvent,
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

/// Position/size a webview. Never use set_bounds on Windows — it can kill the
/// dispatcher ("failed to receive message from webview").
fn apply_webview_bounds(
    webview: &tauri::Webview,
    pos: LogicalPosition<f64>,
    size: LogicalSize<f64>,
) -> Result<(), String> {
    let _ = webview.set_auto_resize(false);
    webview.set_position(pos).map_err(|e| e.to_string())?;
    webview.set_size(size).map_err(|e| e.to_string())?;
    let _ = webview.show();
    Ok(())
}

fn webview_dispatcher_ok(webview: &tauri::Webview) -> bool {
    webview.size().is_ok() || webview.bounds().is_ok() || webview.url().is_ok()
}

/// Ensure WebView2 default context menus, accelerator keys, and status-bar
/// settings stay enabled. Also softens Google-OAuth friction where the COM
/// surface allows it (popups still constrained by Edge WebView2 policy).
fn ensure_editing_features(webview: &tauri::Webview) {
    #[cfg(windows)]
    {
        let _ = webview.with_webview(|platform| {
            use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Settings3;
            use windows::core::Interface;

            let controller = platform.controller();
            unsafe {
                if let Ok(core) = controller.CoreWebView2() {
                    if let Ok(settings) = core.Settings() {
                        let _ = settings.SetAreDefaultContextMenusEnabled(true);
                        let _ = settings.SetAreDefaultScriptDialogsEnabled(true);
                        let _ = settings.SetIsStatusBarEnabled(true);
                        let _ = settings.SetIsZoomControlEnabled(true);
                        if let Ok(settings3) = settings.cast::<ICoreWebView2Settings3>() {
                            let _ = settings3.SetAreBrowserAcceleratorKeysEnabled(true);
                        }
                    }
                }
            }
        });
    }
    #[cfg(not(windows))]
    {
        let _ = webview;
    }
}

/// Show + z-order the child HWND. `focus` only when the user is actively browsing
/// that pane — reflow must not steal focus from the address bar.
fn raise_webview_hwnd(webview: &tauri::Webview, focus: bool) {
    ensure_editing_features(webview);
    let _ = webview.show();
    if focus {
        let _ = webview.set_focus();
    }

    #[cfg(windows)]
    {
        let focus = focus;
        let _ = webview.with_webview(move |platform| {
            use windows::Win32::Foundation::HWND;
            use windows::Win32::UI::WindowsAndMessaging::{
                SetWindowPos, ShowWindow, HWND_TOP, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
                SWP_SHOWWINDOW, SW_SHOW,
            };

            let controller = platform.controller();
            unsafe {
                let _ = controller.SetIsVisible(true);
                let mut hwnd = HWND::default();
                if controller.ParentWindow(&mut hwnd).is_ok() && !hwnd.is_invalid() {
                    let _ = ShowWindow(hwnd, SW_SHOW);
                    let flags = if focus {
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
                    } else {
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE
                    };
                    let _ = SetWindowPos(hwnd, Some(HWND_TOP), 0, 0, 0, 0, flags);
                }
            }
        });
    }
}

fn force_webview_visible(webview: &tauri::Webview) {
    raise_webview_hwnd(webview, true);
}

/// Resize the chrome child webview: strip-only while browsing, full window otherwise.
fn layout_shell(app: &AppHandle, chrome_height: f64, browsing: bool) -> Result<(), String> {
    let Some(window) = app.get_window(MAIN_WINDOW) else {
        return Ok(());
    };
    let (width, height) = window_logical_size(&window)?;
    let Some(shell) = app.get_webview(MAIN_WEBVIEW) else {
        return Ok(());
    };

    let _ = shell.set_auto_resize(false);
    let pos = LogicalPosition::new(0.0, 0.0);
    let size = if browsing {
        let h = clamp_chrome_height(chrome_height).clamp(1.0, height.max(1.0));
        LogicalSize::new(width.max(1.0), h)
    } else {
        LogicalSize::new(width.max(1.0), height.max(1.0))
    };
    apply_webview_bounds(&shell, pos, size)?;
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
            // Reflow must not steal focus (breaks address-bar Ctrl+V / typing).
            raise_webview_hwnd(&webview, false);
        }
    }
    Ok(())
}

fn reflow_all(app: &AppHandle) -> Result<(), String> {
    let browser = app.state::<BrowserState>();
    let chrome_height =
        clamp_chrome_height(*browser.chrome_height.lock().map_err(|e| e.to_string())?);
    let browsing = *browser.shell_browsing.lock().map_err(|e| e.to_string())?;
    // Content first (under), then chrome strip last so the toolbar stays on top
    // of any accidental overlap at the strip boundary.
    if browsing {
        reflow_content_webviews(app, chrome_height)?;
    }
    layout_shell(app, chrome_height, browsing)?;
    if browsing {
        // Bring the active content pane above any sibling after chrome layout.
        reflow_content_webviews(app, chrome_height)?;
    }
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

/// Create the host window + chrome as a *child* webview (not WebviewWindow).
/// Must run from setup before any navigation.
pub fn create_main_window(app: &AppHandle) -> Result<(), String> {
    // opaque by default — WindowBuilder::transparent is macOS-gated
    let window = WindowBuilder::new(app, MAIN_WINDOW)
        .title("Void")
        .inner_size(1280.0, 900.0)
        .min_inner_size(640.0, 480.0)
        .resizable(true)
        .decorations(false)
        .shadow(true)
        .build()
        .map_err(|e| e.to_string())?;

    let (width, height) = window_logical_size(&window)?;
    // Clipboard + native edit menu for address bar / settings fields.
    let mut builder = WebviewBuilder::new(MAIN_WEBVIEW, WebviewUrl::App("index.html".into()))
        .enable_clipboard_access();

    let args = gpu::webview2_browser_args();
    if !args.is_empty() {
        builder = builder.additional_browser_args(&args);
    }

    let chrome = window
        .add_child(
            builder,
            LogicalPosition::new(0.0, 0.0),
            LogicalSize::new(width.max(1.0), height.max(1.0)),
        )
        .map_err(|e| e.to_string())?;
    let _ = chrome.set_auto_resize(false);
    ensure_editing_features(&chrome);
    Ok(())
}

pub fn attach_resize_handler(app: &AppHandle) -> Result<(), String> {
    let Some(window) = app.get_window(MAIN_WINDOW) else {
        return Ok(());
    };
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

fn attach_content_webview(
    app: &AppHandle,
    browser: &BrowserState,
    tab_id: &str,
    url: &str,
    parsed: Url,
) -> Result<(), String> {
    let chrome_height = *browser.chrome_height.lock().map_err(|e| e.to_string())?;
    let window = app
        .get_window(MAIN_WINDOW)
        .ok_or_else(|| "main window not found".to_string())?;
    let (pos, size) = content_bounds(&window, chrome_height)?;

    let tab_id_for_nav = tab_id.to_string();
    let tab_id_for_title = tab_id.to_string();
    let tab_id_for_load = tab_id.to_string();
    let app_for_nav = app.clone();
    let app_for_title = app.clone();
    let app_for_load = app.clone();
    let label = content_label(tab_id);

    // Clipboard permission + (via ensure_editing_features) WebView2 Cut/Copy/Paste menu.
    let mut builder = WebviewBuilder::new(&label, WebviewUrl::External(parsed))
        .enable_clipboard_access()
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

    hide_all_content(app, browser)?;
    set_shell_browsing(app, true)?;

    let webview = window
        .add_child(builder, pos, size)
        .map_err(|e| e.to_string())?;
    apply_webview_bounds(&webview, pos, size)?;
    force_webview_visible(&webview);

    browser
        .content_tabs
        .lock()
        .map_err(|e| e.to_string())?
        .insert(tab_id.to_string());

    reflow_all(app)?;
    schedule_deferred_reflow(app);

    let _ = app.emit(
        "tab-navigated",
        TabNavEvent {
            tab_id: tab_id.to_string(),
            url: url.to_string(),
            title: None,
        },
    );
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

    if let Some(webview) = app.get_webview(&label) {
        // Stale / broken dispatcher after a bad set_bounds — recreate the child.
        if !webview_dispatcher_ok(&webview) {
            let _ = webview.close();
            browser
                .content_tabs
                .lock()
                .map_err(|e| e.to_string())?
                .remove(&tab_id);
        } else {
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
    }

    attach_content_webview(&app, &browser, &tab_id, &url, parsed)
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

    let scale = app
        .get_window(MAIN_WINDOW)
        .and_then(|w| w.scale_factor().ok())
        .unwrap_or(1.0);

    let (shell_width, shell_height) = if let Some(shell) = app.get_webview(MAIN_WEBVIEW) {
        match shell.size() {
            Ok(size) => (
                f64::from(size.width) / scale,
                f64::from(size.height) / scale,
            ),
            Err(_) => match shell.bounds() {
                Ok(bounds) => {
                    let size = bounds.size.to_logical::<f64>(scale);
                    (size.width, size.height)
                }
                Err(_) => (0.0, 0.0),
            },
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

    let (width, height, x, y) = match webview.size().and_then(|size| {
        webview.position().map(|pos| {
            (
                f64::from(size.width) / scale,
                f64::from(size.height) / scale,
                f64::from(pos.x) / scale,
                f64::from(pos.y) / scale,
            )
        })
    }) {
        Ok(v) => v,
        Err(_) => match webview.bounds() {
            Ok(bounds) => {
                let size = bounds.size.to_logical::<f64>(scale);
                let pos = bounds.position.to_logical::<f64>(scale);
                (size.width, size.height, pos.x, pos.y)
            }
            Err(e) => {
                // Still surface URL if possible — helps diagnose white screens.
                let url = webview.url().map(|u| u.to_string()).unwrap_or_default();
                return Err(format!("content bounds unavailable (url={url}): {e}"));
            }
        },
    };
    let url = webview.url().map(|u| u.to_string()).unwrap_or_default();
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
