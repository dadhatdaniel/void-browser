// Void Browser — User Configuration
// Local-only TOML config, no cloud sync, no telemetry

use crate::privacy::{
    CookiePolicy, FingerprintResistance, HttpsPolicy, SecurityLevel, WebRtcPolicy,
};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

/// Main configuration struct — serialized to TOML
#[derive(Clone, Serialize, Deserialize)]
pub struct VoidConfig {
    // ── General ──
    pub homepage: String,
    pub search_engine: SearchEngine,
    pub restore_tabs: bool,
    pub new_tab_page: NewTabPage,

    // ── Appearance ──
    pub theme: Theme,
    pub font_size: u8,
    pub show_bookmarks_bar: bool,
    pub compact_mode: bool,
    pub custom_css: Option<String>,

    /// GPU / power preference: `performance` (HW accel) or `efficiency` (software / power-saving).
    /// Missing in older config.toml → Performance (serde default).
    #[serde(default)]
    pub performance_mode: PerformanceMode,

    // ── Privacy & Security ──
    pub security_level: SecurityLevel,
    pub adblock_enabled: bool,
    pub tracker_blocking: bool,
    pub cookie_policy: CookiePolicy,
    pub https_policy: HttpsPolicy,
    pub webrtc_policy: WebRtcPolicy,
    pub fingerprint_resistance: FingerprintResistance,
    pub clear_on_exit: ClearOnExit,
    pub dns_over_https: Option<String>,

    // ── Custom Filters ──
    pub custom_filters: Option<Vec<String>>,
    pub custom_blocklists: Option<Vec<String>>,

    // ── Keybinds ──
    pub keybinds: Keybinds,
}

impl Default for VoidConfig {
    fn default() -> Self {
        VoidConfig {
            homepage: "void://newtab".to_string(),
            search_engine: SearchEngine::DuckDuckGo,
            restore_tabs: true,
            new_tab_page: NewTabPage::Blank,
            theme: Theme::Dark,
            font_size: 14,
            show_bookmarks_bar: false,
            compact_mode: false,
            custom_css: None,
            performance_mode: PerformanceMode::Performance,
            security_level: SecurityLevel::Strict,
            adblock_enabled: true,
            tracker_blocking: true,
            cookie_policy: CookiePolicy::BlockThirdParty,
            https_policy: HttpsPolicy::Strict,
            webrtc_policy: WebRtcPolicy::DisableNonProxied,
            fingerprint_resistance: FingerprintResistance::default(),
            clear_on_exit: ClearOnExit::default(),
            dns_over_https: Some("https://dns.quad9.net/dns-query".to_string()),
            custom_filters: None,
            custom_blocklists: None,
            keybinds: Keybinds::default(),
        }
    }
}

// ── Sub-configs ─────────────────────────────────────────────────

#[derive(Clone, Serialize, Deserialize)]
pub enum SearchEngine {
    DuckDuckGo,
    Brave,
    Startpage,
    SearXNG,
    Custom(String),
}

#[derive(Clone, Serialize, Deserialize)]
pub enum NewTabPage {
    Blank,
    Homepage,
    SpeedDial,
}

#[derive(Clone, Serialize, Deserialize)]
pub enum Theme {
    Dark,
    Light,
    Midnight, // OLED black
    System,   // follow OS prefers-color-scheme
    Custom(String),
}

/// Rendering / power preference persisted as `performance_mode = "performance" | "efficiency"`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PerformanceMode {
    /// Prefer GPU / hardware acceleration (WebView2 / WebKit defaults).
    #[default]
    Performance,
    /// Prefer power-saving / software rendering (aligns with VOID_SOFTWARE_RENDERING).
    Efficiency,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct ClearOnExit {
    pub cookies: bool,
    pub cache: bool,
    pub history: bool,
    pub local_storage: bool,
    pub downloads_list: bool,
}

impl Default for ClearOnExit {
    fn default() -> Self {
        ClearOnExit {
            // Keep cookies/local_storage off by default so signed-in sessions
            // (and RPA) survive restarts unless the user opts in.
            cookies: false,
            cache: true,
            history: false,
            local_storage: false,
            downloads_list: false,
        }
    }
}

impl ClearOnExit {
    pub fn any_enabled(&self) -> bool {
        self.cookies || self.cache || self.history || self.local_storage || self.downloads_list
    }
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Keybinds {
    pub new_tab: String,
    pub close_tab: String,
    pub reload: String,
    pub hard_reload: String,
    pub find: String,
    pub address_bar: String,
    pub dev_tools: String,
    pub zoom_in: String,
    pub zoom_out: String,
    pub zoom_reset: String,
    pub fullscreen: String,
    pub settings: String,
    pub next_tab: String,
    pub prev_tab: String,
}

impl Default for Keybinds {
    fn default() -> Self {
        Keybinds {
            new_tab: "Ctrl+T".into(),
            close_tab: "Ctrl+W".into(),
            reload: "Ctrl+R".into(),
            hard_reload: "Ctrl+Shift+R".into(),
            find: "Ctrl+F".into(),
            address_bar: "Ctrl+L".into(),
            dev_tools: "F12".into(),
            zoom_in: "Ctrl+=".into(),
            zoom_out: "Ctrl+-".into(),
            zoom_reset: "Ctrl+0".into(),
            fullscreen: "F11".into(),
            settings: "Ctrl+,".into(),
            next_tab: "Ctrl+Tab".into(),
            prev_tab: "Ctrl+Shift+Tab".into(),
        }
    }
}

// ── Config I/O ──────────────────────────────────────────────────

fn config_dir() -> PathBuf {
    let base = dirs::config_dir().unwrap_or_else(|| PathBuf::from("."));
    base.join("void-browser")
}

pub fn config_path() -> PathBuf {
    config_dir().join("config.toml")
}

/// Load user config. Creates defaults once on first run; never overwrites an existing file.
pub fn load_config() -> Result<VoidConfig, Box<dyn std::error::Error>> {
    let path = config_path();
    if !path.exists() {
        let config = VoidConfig::default();
        save_config(&config)?;
        return Ok(config);
    }
    let content = fs::read_to_string(&path)?;
    let config: VoidConfig = toml::from_str(&content)?;
    Ok(config)
}

/// Persist config atomically (temp file → sync → rename) so a crash mid-save
/// does not truncate the user's settings.
pub fn save_config(config: &VoidConfig) -> Result<(), Box<dyn std::error::Error>> {
    use std::io::Write;

    let dir = config_dir();
    fs::create_dir_all(&dir)?;
    let path = config_path();
    let tmp = dir.join("config.toml.tmp");
    let content = toml::to_string_pretty(config)?;

    {
        let mut file = fs::File::create(&tmp)?;
        file.write_all(content.as_bytes())?;
        file.sync_all()?;
    }
    fs::rename(&tmp, &path)?;

    // Best-effort directory sync so the rename itself is durable on crash.
    if let Ok(dir_file) = fs::File::open(&dir) {
        let _ = dir_file.sync_all();
    }
    Ok(())
}
