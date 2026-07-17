// Void Browser — Hardware acceleration / GPU defaults
//
// Platform notes:
// - Windows (WebView2): Chromium GPU compositing is ON by default. We never pass
//   --disable-gpu. We strip it if present in the environment and optionally enable
//   Canvas OOP rasterization (low risk). Transparent windows can force software
//   paths — keep app.windows[].transparent = false.
// - Linux (WebKitGTK): Prefer hardware compositing; clear WEBKIT_DISABLE_COMPOSITING_MODE
//   unless VOID_SOFTWARE_RENDERING=1.
// - macOS (WKWebView): Uses Metal/GPU by default; no flags required.

/// Browser args applied to WebView2 (main + child webviews must match).
#[cfg(target_os = "windows")]
pub fn webview2_browser_args() -> String {
    const EXTRA: &str = "--enable-features=CanvasOopRasterization";

    let existing = std::env::var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS").unwrap_or_default();
    let cleaned: Vec<&str> = existing
        .split_whitespace()
        .filter(|a| {
            let lower = a.to_ascii_lowercase();
            !lower.contains("disable-gpu")
                && !lower.contains("disable-gpu-compositing")
                && !lower.contains("use-gl=swiftshader")
                && *a != EXTRA
        })
        .collect();

    if cleaned.is_empty() {
        EXTRA.to_string()
    } else {
        format!("{} {}", cleaned.join(" "), EXTRA)
    }
}

#[cfg(not(target_os = "windows"))]
pub fn webview2_browser_args() -> String {
    String::new()
}

/// Call before any WebView is created.
pub fn configure_hardware_acceleration() {
    #[cfg(target_os = "windows")]
    {
        let args = webview2_browser_args();
        // Safety: called once at process start before other threads spawn webviews.
        unsafe {
            std::env::set_var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", &args);
        }
        eprintln!("[void] WebView2 GPU: hardware accel default (args: {args})");
    }

    #[cfg(target_os = "linux")]
    {
        let software = std::env::var_os("VOID_SOFTWARE_RENDERING").is_some();
        if software {
            eprintln!(
                "[void] WebKitGTK: VOID_SOFTWARE_RENDERING set — leaving compositing defaults"
            );
        } else {
            unsafe {
                std::env::remove_var("WEBKIT_DISABLE_COMPOSITING_MODE");
                // Prefer HW compositing when the driver supports it; WebKit falls back if not.
                std::env::set_var("WEBKIT_FORCE_COMPOSITING_MODE", "1");
            }
            eprintln!("[void] WebKitGTK: hardware compositing preferred");
        }
    }

    #[cfg(target_os = "macos")]
    {
        eprintln!("[void] WKWebView: Metal GPU acceleration (system default)");
    }
}
