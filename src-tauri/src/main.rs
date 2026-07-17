// Void Browser — main entry point
// A minimal, secure, privacy-first browser

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod adblock;
mod config;
mod privacy;
mod tabs;

use config::VoidConfig;
use serde::Serialize;
use std::sync::Mutex;
use tauri::State;

/// Global browser state
struct AppState {
    config: Mutex<VoidConfig>,
    blocker: Mutex<adblock::AdBlocker>,
    tabs: Mutex<tabs::TabManager>,
}

// ── Tauri Commands ──────────────────────────────────────────────

#[derive(Serialize)]
struct BlockResult {
    blocked: bool,
    url: String,
    filter: Option<String>,
}

/// Check if a URL should be blocked (ads/trackers)
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

/// Get current block stats
#[tauri::command]
fn get_stats(state: State<AppState>) -> adblock::BlockStats {
    let blocker = state.blocker.lock().unwrap();
    blocker.stats()
}

/// Apply privacy headers to a request
#[tauri::command]
fn get_privacy_headers() -> Vec<(String, String)> {
    privacy::get_hardened_headers()
}

/// Get user config
#[tauri::command]
fn get_config(state: State<AppState>) -> VoidConfig {
    let config = state.config.lock().unwrap();
    config.clone()
}

/// Update user config
#[tauri::command]
fn update_config(new_config: VoidConfig, state: State<AppState>) -> Result<(), String> {
    let mut config = state.config.lock().unwrap();
    *config = new_config.clone();
    config::save_config(&new_config).map_err(|e| e.to_string())
}

// ── Tab Management Commands ─────────────────────────────────────

#[tauri::command]
fn create_tab(url: Option<String>, state: State<AppState>) -> tabs::Tab {
    let mut manager = state.tabs.lock().unwrap();
    manager.create(url)
}

#[tauri::command]
fn close_tab(id: String, state: State<AppState>) -> Option<String> {
    let mut manager = state.tabs.lock().unwrap();
    manager.close(&id)
}

#[tauri::command]
fn list_tabs(state: State<AppState>) -> Vec<tabs::Tab> {
    let manager = state.tabs.lock().unwrap();
    manager.list()
}

#[tauri::command]
fn set_active_tab(id: String, state: State<AppState>) -> bool {
    let mut manager = state.tabs.lock().unwrap();
    manager.set_active(&id)
}

// ── Main ────────────────────────────────────────────────────────

fn main() {
    let config = config::load_config().unwrap_or_default();
    let blocker = adblock::AdBlocker::new(&config);
    let tab_manager = tabs::TabManager::new();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            config: Mutex::new(config),
            blocker: Mutex::new(blocker),
            tabs: Mutex::new(tab_manager),
        })
        .invoke_handler(tauri::generate_handler![
            check_url,
            get_stats,
            get_privacy_headers,
            get_config,
            update_config,
            create_tab,
            close_tab,
            list_tabs,
            set_active_tab,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Void Browser");
}
