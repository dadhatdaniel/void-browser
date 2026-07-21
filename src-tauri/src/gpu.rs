// Void Browser — Hardware acceleration / GPU defaults
//
// Driven by Settings → Performance mode (`performance` | `efficiency` in config.toml).
// Env override: VOID_SOFTWARE_RENDERING=1 forces the efficiency/software path (RPA guests).
//
// Platform notes:
// - Windows (WebView2): Performance keeps Chromium GPU compositing ON (never --disable-gpu).
//   Efficiency prefers software / power-saving flags. Transparent windows can force software
//   paths — keep app.windows[].transparent = false.
// - Linux (WebKitGTK): Performance prefers hardware compositing; Efficiency matches the
//   VOID_SOFTWARE_RENDERING software path.
// - macOS (WKWebView): Metal/GPU by default; Efficiency is best-effort (no public SW force).

use crate::config::PerformanceMode;
use std::sync::atomic::{AtomicBool, Ordering};

/// Last applied software/efficiency preference (for WebView2 arg builders).
static SOFTWARE_PREFERRED: AtomicBool = AtomicBool::new(false);

fn env_forces_software() -> bool {
    match std::env::var("VOID_SOFTWARE_RENDERING") {
        Ok(v) => {
            let t = v.trim().to_ascii_lowercase();
            !(t.is_empty() || t == "0" || t == "false" || t == "no")
        }
        Err(_) => false,
    }
}

/// True when Efficiency mode is selected, or VOID_SOFTWARE_RENDERING forces software.
pub fn software_preferred(mode: PerformanceMode) -> bool {
    env_forces_software() || matches!(mode, PerformanceMode::Efficiency)
}

/// Browser args applied to WebView2 (main + child webviews must match).
#[cfg(target_os = "windows")]
pub fn webview2_browser_args() -> String {
    let software = SOFTWARE_PREFERRED.load(Ordering::SeqCst) || env_forces_software();

    if software {
        // Power-saving / software path — strip HW extras, add disable-gpu flags.
        const EFFICIENCY: &str =
            "--disable-gpu --disable-gpu-compositing --disable-features=CanvasOopRasterization";

        let existing = std::env::var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS").unwrap_or_default();
        let cleaned: Vec<&str> = existing
            .split_whitespace()
            .filter(|a| {
                let lower = a.to_ascii_lowercase();
                !lower.contains("disable-gpu")
                    && !lower.contains("disable-gpu-compositing")
                    && !lower.contains("use-gl=swiftshader")
                    && !lower.contains("canvasooprasterization")
                    && *a != EFFICIENCY
            })
            .collect();

        if cleaned.is_empty() {
            EFFICIENCY.to_string()
        } else {
            format!("{} {}", cleaned.join(" "), EFFICIENCY)
        }
    } else {
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
}

#[cfg(not(target_os = "windows"))]
pub fn webview2_browser_args() -> String {
    String::new()
}

/// Persistent WebView2 profile (cookies, localStorage, Google sessions).
/// Respects an existing `WEBVIEW2_USER_DATA_FOLDER` override.
#[cfg(target_os = "windows")]
pub fn configure_webview2_profile() {
    if std::env::var_os("WEBVIEW2_USER_DATA_FOLDER").is_some() {
        eprintln!("[void] WebView2 profile: using WEBVIEW2_USER_DATA_FOLDER from env");
        return;
    }
    if let Some(data) = dirs::data_dir() {
        let profile = data.join("void-browser").join("webview2-profile");
        if let Err(e) = std::fs::create_dir_all(&profile) {
            eprintln!("[void] WebView2 profile: mkdir failed: {e}");
            return;
        }
        // Safety: called once at process start before webviews spawn.
        unsafe {
            std::env::set_var("WEBVIEW2_USER_DATA_FOLDER", &profile);
        }
        eprintln!("[void] WebView2 profile: {}", profile.display());
    }
}

#[cfg(not(target_os = "windows"))]
pub fn configure_webview2_profile() {}

/// Call before any WebView is created, and again when Settings → Performance mode changes.
pub fn configure_hardware_acceleration(mode: PerformanceMode) {
    configure_webview2_profile();

    let software = software_preferred(mode);
    SOFTWARE_PREFERRED.store(software, Ordering::SeqCst);

    #[cfg(target_os = "windows")]
    {
        let args = webview2_browser_args();
        // Safety: called at process start / settings change before new webviews spawn.
        unsafe {
            std::env::set_var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", &args);
        }
        if software {
            eprintln!("[void] WebView2 GPU: efficiency / software preferred (args: {args})");
        } else {
            eprintln!("[void] WebView2 GPU: performance / hardware accel (args: {args})");
        }
    }

    #[cfg(target_os = "linux")]
    {
        if software {
            // QXL/no-DRI3 guests black out under WebKit AC — force the software path.
            unsafe {
                std::env::remove_var("WEBKIT_FORCE_COMPOSITING_MODE");
                std::env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "1");
                if std::env::var_os("LIBGL_ALWAYS_SOFTWARE").is_none() {
                    std::env::set_var("LIBGL_ALWAYS_SOFTWARE", "1");
                }
                if std::env::var_os("GALLIUM_DRIVER").is_none() {
                    std::env::set_var("GALLIUM_DRIVER", "llvmpipe");
                }
                if std::env::var_os("GSK_RENDERER").is_none() {
                    // GTK4: avoid GL/Vulkan shell renderer fighting QXL.
                    std::env::set_var("GSK_RENDERER", "cairo");
                }
            }
            eprintln!(
                "[void] WebKitGTK: efficiency / software — \
                 WEBKIT_DISABLE_COMPOSITING_MODE=1, software GL/Cairo"
            );
        } else {
            unsafe {
                std::env::remove_var("WEBKIT_DISABLE_COMPOSITING_MODE");
                // Prefer HW compositing when the driver supports it; WebKit falls back if not.
                std::env::set_var("WEBKIT_FORCE_COMPOSITING_MODE", "1");
            }
            eprintln!("[void] WebKitGTK: performance / hardware compositing preferred");
        }
    }

    #[cfg(target_os = "macos")]
    {
        if software {
            eprintln!(
                "[void] WKWebView: efficiency requested — Metal still used (no public SW force)"
            );
        } else {
            eprintln!("[void] WKWebView: performance / Metal GPU acceleration (system default)");
        }
    }
}
