// Void Browser — main entry point
// A minimal, secure, privacy-first browser

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod adblock;
mod browser;
mod config;
mod gpu;
mod privacy;
mod smoke;
mod tabs;
mod updater;

use browser::BrowserState;
use config::VoidConfig;
use serde::Serialize;
use std::sync::Mutex;
use tauri::State;

/// Global browser state
pub struct AppState {
    pub config: Mutex<VoidConfig>,
    pub blocker: Mutex<adblock::AdBlocker>,
    pub tabs: Mutex<tabs::TabManager>,
}

#[derive(Serialize)]
struct BlockResult {
    blocked: bool,
    url: String,
    filter: Option<String>,
}

#[tauri::command]
fn check_url(url: &str, source_url: &str, state: State<AppState>) -> BlockResult {
    let blocker = state.blocker.lock().unwrap();
    let result = blocker.check(url, source_url);
    BlockResult {
        blocked: result.matched,
        url: url.to_string(),
        filter: result.filter,
    }
}

#[tauri::command]
fn get_stats(state: State<AppState>) -> adblock::BlockStats {
    let blocker = state.blocker.lock().unwrap();
    blocker.stats()
}

#[tauri::command]
fn get_privacy_headers() -> privacy::PrivacyHeaders {
    privacy::get_privacy_headers()
}

#[tauri::command]
fn get_config(state: State<AppState>) -> VoidConfig {
    state.config.lock().unwrap().clone()
}

#[tauri::command]
fn get_config_path() -> String {
    config::config_path().display().to_string()
}

#[tauri::command]
fn update_config(new_config: VoidConfig, state: State<AppState>) -> Result<(), String> {
    let rebuild_blocker = {
        let current = state.config.lock().map_err(|e| e.to_string())?;
        current.adblock_enabled != new_config.adblock_enabled
            || current.tracker_blocking != new_config.tracker_blocking
            || current.custom_filters != new_config.custom_filters
    };

    // Flush to disk first so a crash after save still keeps user changes.
    config::save_config(&new_config).map_err(|e| e.to_string())?;

    {
        let mut config = state.config.lock().map_err(|e| e.to_string())?;
        *config = new_config.clone();
    }

    if rebuild_blocker {
        let mut blocker = state.blocker.lock().map_err(|e| e.to_string())?;
        *blocker = adblock::AdBlocker::new(&new_config);
    }
    Ok(())
}

#[tauri::command]
fn create_tab(url: Option<String>, state: State<AppState>) -> tabs::Tab {
    state.tabs.lock().unwrap().create(url)
}

#[tauri::command]
fn close_tab(id: String, state: State<AppState>) -> Option<String> {
    state.tabs.lock().unwrap().close(&id)
}

#[tauri::command]
fn list_tabs(state: State<AppState>) -> Vec<tabs::Tab> {
    state.tabs.lock().unwrap().list()
}

#[tauri::command]
fn set_active_tab(id: String, state: State<AppState>) -> bool {
    state.tabs.lock().unwrap().set_active(&id)
}

fn main() {
    gpu::configure_hardware_acceleration();

    let config = config::load_config().unwrap_or_default();
    let blocker = adblock::AdBlocker::new(&config);
    let tab_manager = tabs::TabManager::new();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(
            // Pubkey pinned in Rust as well as tauri.conf.json — never disable verification.
            tauri_plugin_updater::Builder::new()
                .pubkey(updater::UPDATER_PUBKEY)
                .build(),
        )
        .manage(AppState {
            config: Mutex::new(config),
            blocker: Mutex::new(blocker),
            tabs: Mutex::new(tab_manager),
        })
        .manage(BrowserState::default())
        .setup(|app| {
            browser::attach_resize_handler(app.handle())?;
            if smoke::requested() {
                smoke::spawn(app.handle().clone());
            } else {
                updater::spawn_startup_check(app.handle().clone());
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            check_url,
            get_stats,
            get_privacy_headers,
            get_config,
            get_config_path,
            update_config,
            create_tab,
            close_tab,
            list_tabs,
            set_active_tab,
            updater::check_for_updates,
            browser::set_chrome_height,
            browser::navigate_browser,
            browser::show_browser_content,
            browser::hide_browser_content,
            browser::close_browser_content,
            browser::browser_reload,
            browser::browser_go_back,
            browser::browser_go_forward,
            browser::get_webview_info,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Void Browser");
}
