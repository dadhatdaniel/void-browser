//! WebView2 subresource request interception (Windows).
//!
//! Root cause of "adblock doesn't work": adblock-rust was only consulted on
//! main-frame navigations (`on_navigation` / `navigate_browser`). Ads load as
//! script/image/XHR subresources, which WebView2 fetched freely.
//!
//! Fix: register `WebResourceRequested` + URI filter `*` on content webviews and
//! return a synthetic empty response when the engine matches.

use tauri::{AppHandle, Emitter, Manager};

/// Attach adblock interception to a content webview (no-op on non-Windows).
pub fn attach(webview: &tauri::Webview, app: AppHandle) {
    #[cfg(windows)]
    {
        attach_windows(webview, app);
    }
    #[cfg(not(windows))]
    {
        let _ = (webview, app);
    }
}

#[cfg(windows)]
fn attach_windows(webview: &tauri::Webview, app: AppHandle) {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        ICoreWebView2_2, ICoreWebView2_22, COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,
        COREWEBVIEW2_WEB_RESOURCE_REQUEST_SOURCE_KINDS_ALL,
    };
    use webview2_com::WebResourceRequestedEventHandler;
    use windows::core::{Interface, PCWSTR};

    let result = webview.with_webview(move |platform| {
        let controller = platform.controller();
        unsafe {
            let Ok(core) = controller.CoreWebView2() else {
                eprintln!("[void-adblock] CoreWebView2 unavailable");
                return;
            };

            // Filter every URI/context so script/image/XHR ads are visible to us.
            if let Ok(wv22) = core.cast::<ICoreWebView2_22>() {
                let _ = wv22.AddWebResourceRequestedFilterWithRequestSourceKinds(
                    PCWSTR(windows::core::w!("*").as_ptr()),
                    COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,
                    COREWEBVIEW2_WEB_RESOURCE_REQUEST_SOURCE_KINDS_ALL,
                );
            } else {
                let _ = core.AddWebResourceRequestedFilter(
                    PCWSTR(windows::core::w!("*").as_ptr()),
                    COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,
                );
            }

            let env = match core.cast::<ICoreWebView2_2>().and_then(|c2| c2.Environment()) {
                Ok(e) => e,
                Err(e) => {
                    eprintln!("[void-adblock] Environment unavailable: {e}");
                    return;
                }
            };

            let mut token = 0i64;
            let handler = WebResourceRequestedEventHandler::create(Box::new(move |sender, args| {
                handle_request(sender, args, &app, &env);
                Ok(())
            }));
            if let Err(e) = core.add_WebResourceRequested(&handler, &mut token) {
                eprintln!("[void-adblock] add_WebResourceRequested failed: {e}");
            } else {
                eprintln!("[void-adblock] WebResourceRequested hook attached");
            }
        }
    });

    if let Err(e) = result {
        eprintln!("[void-adblock] with_webview failed: {e}");
    }
}

#[cfg(windows)]
fn handle_request(
    sender: Option<webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2>,
    args: Option<
        webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2WebResourceRequestedEventArgs,
    >,
    app: &AppHandle,
    env: &webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Environment,
) {
    use crate::AppState;
    use webview2_com::Microsoft::Web::WebView2::Win32::COREWEBVIEW2_WEB_RESOURCE_CONTEXT;
    use webview2_com::take_pwstr;
    use windows::core::{HSTRING, PWSTR};
    use windows::Win32::UI::Shell::SHCreateMemStream;

    let Some(args) = args else {
        return;
    };

    let (url, resource_type, method) = unsafe {
        let Ok(req) = args.Request() else {
            return;
        };
        let mut uri = PWSTR::null();
        if req.Uri(&mut uri).is_err() {
            return;
        }
        let url = take_pwstr(uri);

        let mut method_pw = PWSTR::null();
        let method = if req.Method(&mut method_pw).is_ok() {
            take_pwstr(method_pw)
        } else {
            "GET".to_string()
        };

        let mut ctx = COREWEBVIEW2_WEB_RESOURCE_CONTEXT::default();
        let _ = args.ResourceContext(&mut ctx);
        let resource_type = map_resource_context(ctx);
        (url, resource_type, method)
    };

    // Only decide on real network schemes; leave custom protocols to wry/tauri.
    let lower = url.to_ascii_lowercase();
    if !(lower.starts_with("http://") || lower.starts_with("https://")) {
        return;
    }

    // Main-frame documents are gated in `on_navigation` / `navigate_browser`.
    // Skipping here avoids double-counting stats for every page load.
    if resource_type == "document" {
        return;
    }

    let source_url = unsafe {
        sender
            .as_ref()
            .and_then(|wv| {
                let mut src = PWSTR::null();
                if wv.Source(&mut src).is_ok() {
                    Some(take_pwstr(src))
                } else {
                    None
                }
            })
            .unwrap_or_else(|| url.clone())
    };

    let Some(state) = app.try_state::<AppState>() else {
        return;
    };
    let Ok(engine) = state.blocker.try_lock() else {
        return;
    };

    let result = engine.check_typed_method(&url, &source_url, resource_type, &method);
    if !result.matched {
        // Allowed: already in decision log / HAR; avoid synthetic response.
        return;
    }

    // Block: synthetic empty response (browser treats as failed/empty load).
    unsafe {
        let stream = SHCreateMemStream(Some(b""));
        let headers = HSTRING::from("Content-Type: text/plain\r\nContent-Length: 0\r\n");
        if let Ok(response) =
            env.CreateWebResourceResponse(stream.as_ref(), 403, &HSTRING::from("Blocked"), &headers)
        {
            let _ = args.SetResponse(&response);
        }
    }
    let _ = app.emit("block-stats-updated", engine.stats());
}

#[cfg(windows)]
fn map_resource_context(
    ctx: webview2_com::Microsoft::Web::WebView2::Win32::COREWEBVIEW2_WEB_RESOURCE_CONTEXT,
) -> &'static str {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_DOCUMENT,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FETCH,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FONT,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_IMAGE,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_MEDIA,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_OTHER,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_PING,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_SCRIPT,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_STYLESHEET,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_WEBSOCKET,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_XML_HTTP_REQUEST,
    };

    // Compare via .0 since these are newtype i32 wrappers.
    let v = ctx.0;
    if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_DOCUMENT.0 {
        "document"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_STYLESHEET.0 {
        "stylesheet"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_IMAGE.0 {
        "image"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_MEDIA.0 {
        "media"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_SCRIPT.0 {
        "script"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_XML_HTTP_REQUEST.0
        || v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FETCH.0
    {
        "xmlhttprequest"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FONT.0 {
        "font"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_PING.0 {
        "ping"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_WEBSOCKET.0 {
        "websocket"
    } else if v == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_OTHER.0 {
        "other"
    } else {
        "other"
    }
}
