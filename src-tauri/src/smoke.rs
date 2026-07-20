// Void Browser — smoke harness for CI (Windows / local)
//
// Launch: void-browser.exe --smoke-test
// Or:     VOID_SMOKE_TEST=1 void-browser.exe
//
// Creates a tab, navigates to example.com, asserts content webview bounds + URL,
// then exits 0/1. Logs to %TEMP%/void-browser-smoke.log (no console on Windows GUI).

use crate::browser::{self, BrowserState};
use crate::AppState;
use std::fs;
use std::path::PathBuf;
use std::time::Duration;
use tauri::{AppHandle, Manager};

const SMOKE_URL: &str = "https://example.com/";

pub fn requested() -> bool {
    std::env::args().any(|a| a == "--smoke-test") || std::env::var_os("VOID_SMOKE_TEST").is_some()
}

fn log_path() -> PathBuf {
    std::env::temp_dir().join("void-browser-smoke.log")
}

fn smoke_log(msg: &str) {
    let line = format!("{msg}\n");
    let _ = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path())
        .and_then(|mut f| {
            use std::io::Write;
            f.write_all(line.as_bytes())
        });
    eprintln!("[void-smoke] {msg}");
}

/// Spawn the smoke sequence on Tauri's async runtime (same path as invoke handlers).
pub fn spawn(app: AppHandle) {
    let _ = fs::write(log_path(), "void-browser smoke starting\n");
    smoke_log("scheduled");

    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(1500)).await;

        if let Some(browser) = app.try_state::<BrowserState>() {
            if let Ok(mut h) = browser.chrome_height.lock() {
                if *h < 40.0 {
                    *h = 78.0;
                }
            }
        }

        let tab_id = {
            let Some(state) = app.try_state::<AppState>() else {
                smoke_log("FAIL: AppState missing");
                std::process::exit(1);
            };
            let id = match state.tabs.lock() {
                Ok(mut tabs) => tabs.create(Some(SMOKE_URL.to_string())).id,
                Err(e) => {
                    smoke_log(&format!("FAIL: tabs lock: {e}"));
                    std::process::exit(1);
                }
            };
            id
        };
        smoke_log(&format!("tab_id={tab_id}"));

        let nav = {
            let browser = app.state::<BrowserState>();
            let state = app.state::<AppState>();
            browser::navigate_browser(
                tab_id.clone(),
                SMOKE_URL.to_string(),
                app.clone(),
                browser,
                state,
            )
            .await
        };

        match &nav {
            Ok(()) => smoke_log("navigate_browser ok"),
            Err(e) => {
                smoke_log(&format!("FAIL: navigate_browser: {e}"));
                std::process::exit(1);
            }
        }

        // Give WebView2 time to paint; retry — some hosts drop the first IPC.
        // Chrome JS init can race and call hide_browser_content after navigate;
        // re-show browsing mode each attempt before measuring.
        let mut info = None;
        let mut last_err = String::new();
        for attempt in 1..=10 {
            tokio::time::sleep(Duration::from_millis(if attempt == 1 { 2500 } else { 750 })).await;

            {
                let browser = app.state::<BrowserState>();
                if let Err(e) =
                    browser::show_browser_content(tab_id.clone(), app.clone(), browser).await
                {
                    smoke_log(&format!("show_browser_content attempt {attempt}: {e}"));
                }
            }

            let label = format!("content-{tab_id}");
            let present = app.get_webview(&label).is_some();
            smoke_log(&format!(
                "attempt {attempt}: content webview present={present}"
            ));

            let result = {
                let browser = app.state::<BrowserState>();
                let state = app.state::<AppState>();
                browser::get_webview_info(Some(tab_id.clone()), app.clone(), browser, state)
            };
            match result {
                Ok(i) => {
                    let shell_ok = i.shell_height <= i.chrome_height + 40.0;
                    if i.shell_browsing
                        && i.width >= 100.0
                        && i.height >= 100.0
                        && shell_ok
                        && i.url.contains("example.com")
                    {
                        info = Some(i);
                        break;
                    }
                    smoke_log(&format!(
                        "attempt {attempt}: not ready url={} visible={} {}x{} shell_browsing={} shell_h={} chrome_h={}",
                        i.url,
                        i.visible,
                        i.width,
                        i.height,
                        i.shell_browsing,
                        i.shell_height,
                        i.chrome_height
                    ));
                    last_err = format!(
                        "shell_browsing={} bounds={}x{} shell_h={}",
                        i.shell_browsing, i.width, i.height, i.shell_height
                    );
                }
                Err(e) => {
                    last_err = e;
                    smoke_log(&format!("get_webview_info attempt {attempt}: {last_err}"));
                }
            }
        }

        let info = match info {
            Some(i) => i,
            None => {
                smoke_log(&format!("FAIL: get_webview_info: {last_err}"));
                std::process::exit(1);
            }
        };

        smoke_log(&format!(
            "info url={} visible={} {}x{} @{},{} shell_browsing={} shell={}x{} chrome_h={}",
            info.url,
            info.visible,
            info.width,
            info.height,
            info.x,
            info.y,
            info.shell_browsing,
            info.shell_width,
            info.shell_height,
            info.chrome_height
        ));

        if !info.shell_browsing {
            smoke_log("FAIL: shell_browsing is false — chrome still covering content");
            std::process::exit(1);
        }
        if info.width < 100.0 || info.height < 100.0 {
            smoke_log(&format!(
                "FAIL: content webview bounds too small: {}x{} (black-screen regression)",
                info.width, info.height
            ));
            std::process::exit(1);
        }
        if info.y + 1.0 < info.chrome_height * 0.5 {
            smoke_log(&format!(
                "FAIL: content y={} looks wrong vs chrome_height={}",
                info.y, info.chrome_height
            ));
            std::process::exit(1);
        }
        if info.shell_height > info.chrome_height + 40.0 {
            smoke_log(&format!(
                "FAIL: shell height {} still near full window (chrome_height={}) — cover risk",
                info.shell_height, info.chrome_height
            ));
            std::process::exit(1);
        }
        if !info.url.contains("example.com") {
            smoke_log(&format!(
                "FAIL: expected example.com in url, got '{}'",
                info.url
            ));
            std::process::exit(1);
        }

        smoke_log("PASS: content webview visible with example.com");
        // Use process::exit so Windows GUI subsystem reports a real exit code.
        std::process::exit(0);
    });
}
