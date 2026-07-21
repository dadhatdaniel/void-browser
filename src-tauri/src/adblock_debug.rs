//! Network decision log for RPA / diagnosis.
//!
//! Enabled when `VOID_ADBLOCK_DEBUG=1` (or `true` / `yes`).
//! Writes JSONL to `VOID_ADBLOCK_DEBUG_LOG` or `%TEMP%/void-adblock-debug.jsonl`.
//! Also keeps a capped JSON snapshot at the same path with `.json` suffix.

use once_cell::sync::Lazy;
use serde::Serialize;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_SNAPSHOT: usize = 500;

#[derive(Clone, Serialize)]
pub struct NetworkDecision {
    pub ts_ms: u64,
    pub url: String,
    pub source_url: String,
    pub resource_type: String,
    pub decision: &'static str,
    pub filter: Option<String>,
}

struct DebugState {
    enabled: bool,
    jsonl_path: PathBuf,
    snapshot_path: PathBuf,
    recent: Vec<NetworkDecision>,
}

static STATE: Lazy<Mutex<DebugState>> = Lazy::new(|| {
    let enabled = env_truthy("VOID_ADBLOCK_DEBUG");
    let jsonl_path = std::env::var_os("VOID_ADBLOCK_DEBUG_LOG")
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::temp_dir().join("void-adblock-debug.jsonl"));
    let snapshot_path = jsonl_path.with_extension("json");
    if enabled {
        let _ = fs::write(&jsonl_path, "");
        let _ = fs::write(
            &snapshot_path,
            serde_json::json!({
                "ok": true,
                "entries": [],
                "blocked": 0,
                "allowed": 0,
            })
            .to_string(),
        );
        eprintln!(
            "[void-adblock] debug log -> {} (snapshot {})",
            jsonl_path.display(),
            snapshot_path.display()
        );
    }
    Mutex::new(DebugState {
        enabled,
        jsonl_path,
        snapshot_path,
        recent: Vec::new(),
    })
});

fn env_truthy(key: &str) -> bool {
    match std::env::var(key) {
        Ok(v) => {
            let v = v.trim().to_ascii_lowercase();
            matches!(v.as_str(), "1" | "true" | "yes" | "on")
        }
        Err(_) => false,
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[allow(dead_code)]
pub fn enabled() -> bool {
    STATE.lock().map(|s| s.enabled).unwrap_or(false)
}

#[allow(dead_code)]
pub fn log_path() -> Option<PathBuf> {
    let s = STATE.lock().ok()?;
    if s.enabled {
        Some(s.jsonl_path.clone())
    } else {
        None
    }
}

#[allow(dead_code)]
pub fn snapshot_path() -> Option<PathBuf> {
    let s = STATE.lock().ok()?;
    if s.enabled {
        Some(s.snapshot_path.clone())
    } else {
        None
    }
}

pub fn record(
    url: &str,
    source_url: &str,
    resource_type: &str,
    blocked: bool,
    filter: Option<String>,
) {
    let Ok(mut state) = STATE.lock() else {
        return;
    };
    if !state.enabled {
        return;
    }

    let entry = NetworkDecision {
        ts_ms: now_ms(),
        url: url.to_string(),
        source_url: source_url.to_string(),
        resource_type: resource_type.to_string(),
        decision: if blocked { "blocked" } else { "allowed" },
        filter,
    };

    if let Ok(mut f) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&state.jsonl_path)
    {
        if let Ok(line) = serde_json::to_string(&entry) {
            let _ = writeln!(f, "{line}");
        }
    }

    state.recent.push(entry);
    if state.recent.len() > MAX_SNAPSHOT {
        let drain = state.recent.len() - MAX_SNAPSHOT;
        state.recent.drain(0..drain);
    }

    let blocked_n = state
        .recent
        .iter()
        .filter(|e| e.decision == "blocked")
        .count();
    let allowed_n = state.recent.len().saturating_sub(blocked_n);
    let body = serde_json::json!({
        "ok": true,
        "log_path": state.jsonl_path,
        "entries": state.recent,
        "blocked": blocked_n,
        "allowed": allowed_n,
    });
    if let Ok(mut f) = File::create(&state.snapshot_path) {
        let _ = f.write_all(body.to_string().as_bytes());
    }
}
