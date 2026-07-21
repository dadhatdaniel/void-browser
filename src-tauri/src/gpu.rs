// Void Browser — Hardware acceleration / GPU defaults
//
// Driven by Settings → Performance mode (`performance` | `efficiency` in config.toml).
// Env override: VOID_SOFTWARE_RENDERING=1 forces the efficiency/software path (RPA guests).
//
// Platform notes:
// - Windows (WebView2): Performance keeps Chromium GPU compositing ON (never --disable-gpu).
//   Efficiency prefers software / power-saving flags. Transparent windows can force software
//   paths — keep app.windows[].transparent = false.
// - Linux (WebKitGTK): Performance prefers hardware compositing when the GPU looks healthy.
//   QXL / bochs / cirrus / missing DRI render nodes force the software path even in
//   Performance mode (otherwise WebKit paints a solid black upper webview band).
//   Never set WEBKIT_FORCE_COMPOSITING_MODE — it breaks broken/partial GPUs harder.
// - macOS (WKWebView): Metal/GPU by default; Efficiency is best-effort (no public SW force).

use crate::config::PerformanceMode;
use std::sync::atomic::{AtomicBool, Ordering};

/// Last applied software/efficiency preference (for WebView2 arg builders + WebKit policy).
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

/// Runtime snapshot after `configure_hardware_acceleration` (WebKit settings helpers).
pub fn software_rendering_active() -> bool {
    SOFTWARE_PREFERRED.load(Ordering::SeqCst) || env_forces_software()
}

/// Detect GPU backends that break WebKitGTK accelerated compositing (QXL black band).
/// Pure sysfs/`/proc` — no shelling out.
#[cfg(target_os = "linux")]
fn linux_gpu_needs_software() -> bool {
    // Kernel modules that commonly lack working DRI3 / GBM for WebKit AC.
    if let Ok(modules) = std::fs::read_to_string("/proc/modules") {
        for line in modules.lines() {
            let name = line.split_whitespace().next().unwrap_or("");
            if matches!(
                name,
                "qxl" | "bochs_drm" | "cirrus" | "vboxvideo" | "vmwgfx"
            ) {
                return true;
            }
        }
    }

    // DRM driver symlink (e.g. /sys/class/drm/card0/device/driver → …/qxl).
    if let Ok(entries) = std::fs::read_dir("/sys/class/drm") {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if !name.starts_with("card") || name.contains('-') {
                continue;
            }
            let driver_link = entry.path().join("device/driver");
            if let Ok(target) = std::fs::read_link(&driver_link) {
                let driver = target
                    .file_name()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default()
                    .to_ascii_lowercase();
                if matches!(
                    driver.as_str(),
                    "qxl" | "bochs-drm" | "cirrus" | "vboxvideo" | "vmwgfx"
                ) {
                    return true;
                }
            }
        }
    }

    // Render node present but not usable by this user → DRI3 "Could not get DRI3
    // device" and WebKit paints a solid black upper band. Prefer software.
    let mut saw_render = false;
    let mut render_readable = false;
    if let Ok(rd) = std::fs::read_dir("/dev/dri") {
        for e in rd.flatten() {
            let fname = e.file_name();
            let fname = fname.to_string_lossy().to_ascii_lowercase();
            if fname.starts_with("render") {
                saw_render = true;
                if std::fs::File::open(e.path()).is_ok() {
                    render_readable = true;
                }
            }
        }
    }
    if !saw_render || !render_readable {
        return true;
    }

    false
}

#[cfg(not(target_os = "linux"))]
fn linux_gpu_needs_software() -> bool {
    false
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

#[cfg(target_os = "linux")]
fn apply_linux_software_env() {
    // Safety: called at process start / settings change before webviews spawn.
    unsafe {
        std::env::remove_var("WEBKIT_FORCE_COMPOSITING_MODE");
        std::env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "1");
        // DMA-BUF renderer fails on QXL / broken GBM even when AC is "disabled".
        if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
            std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        }
        if std::env::var_os("LIBGL_ALWAYS_SOFTWARE").is_none() {
            std::env::set_var("LIBGL_ALWAYS_SOFTWARE", "1");
        }
        if std::env::var_os("GALLIUM_DRIVER").is_none() {
            std::env::set_var("GALLIUM_DRIVER", "llvmpipe");
        }
        if std::env::var_os("MESA_LOADER_DRIVER_OVERRIDE").is_none() {
            std::env::set_var("MESA_LOADER_DRIVER_OVERRIDE", "llvmpipe");
        }
        if std::env::var_os("GSK_RENDERER").is_none() {
            // GTK4: avoid GL/Vulkan shell renderer fighting QXL.
            std::env::set_var("GSK_RENDERER", "cairo");
        }
        if std::env::var_os("GDK_BACKEND").is_none() {
            // Prefer X11 on guests where Wayland+GBM is broken.
            std::env::set_var("GDK_BACKEND", "x11");
        }
    }
}

/// Force WebKitGTK hardware-acceleration policy on an existing webview.
/// Env vars alone are not always enough on QXL; the Settings API is authoritative.
#[cfg(target_os = "linux")]
pub fn apply_webkit_acceleration_policy(webview: &tauri::Webview) {
    let software = software_rendering_active();
    let _ = webview.with_webview(move |platform| {
        use webkit2gtk::{HardwareAccelerationPolicy, SettingsExt, WebViewExt};
        let wk = platform.inner();
        if let Some(settings) = wk.settings() {
            let policy = if software {
                HardwareAccelerationPolicy::Never
            } else {
                HardwareAccelerationPolicy::OnDemand
            };
            settings.set_hardware_acceleration_policy(policy);
        }
    });
}

#[cfg(not(target_os = "linux"))]
pub fn apply_webkit_acceleration_policy(_webview: &tauri::Webview) {}

/// Call before any WebView is created, and again when Settings → Performance mode changes.
pub fn configure_hardware_acceleration(mode: PerformanceMode) {
    configure_webview2_profile();

    let mut software = software_preferred(mode);
    let mut auto_reason: Option<&'static str> = None;

    #[cfg(target_os = "linux")]
    {
        if !software && linux_gpu_needs_software() {
            software = true;
            auto_reason = Some("QXL/bochs/cirrus/no-render-node auto soft-render");
        }
    }

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
            apply_linux_software_env();
            if let Some(reason) = auto_reason {
                eprintln!(
                    "[void] WebKitGTK: software forced ({reason}) — \
                     WEBKIT_DISABLE_COMPOSITING_MODE=1, DMABUF off, llvmpipe/Cairo"
                );
            } else {
                eprintln!(
                    "[void] WebKitGTK: efficiency / software — \
                     WEBKIT_DISABLE_COMPOSITING_MODE=1, DMABUF off, software GL/Cairo"
                );
            }
        } else {
            // Prefer HW when the driver looks healthy — but do NOT force AC.
            // WEBKIT_FORCE_COMPOSITING_MODE=1 caused solid black upper bands on
            // partial/broken GPUs that still passed the heuristic.
            // Also disable DMA-BUF by default: it is the most common WebKitGTK
            // blank/black failure mode on NVIDIA and flaky VM GPUs (Tauri docs).
            unsafe {
                std::env::remove_var("WEBKIT_FORCE_COMPOSITING_MODE");
                if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
                    std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
                }
            }
            eprintln!(
                "[void] WebKitGTK: performance / hardware preferred \
                 (no FORCE; DMABUF off; WebKit decides AC)"
            );
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_forces_software_parses() {
        // Do not mutate process env in unit tests — just exercise the helper shape.
        let _ = software_preferred(PerformanceMode::Performance);
        let _ = software_preferred(PerformanceMode::Efficiency);
        assert!(matches!(PerformanceMode::default(), PerformanceMode::Performance));
    }
}
