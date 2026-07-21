//! Network decision / HAR-like capture for RPA and diagnosis.
//!
//! Enabled when either:
//! - `VOID_NETWORK_CAPTURE=1` (general RPA suite capture), or
//! - `VOID_ADBLOCK_DEBUG=1` (adblock scenario / legacy alias)
//!
//! Writes:
//! - JSONL decision stream → `VOID_NETWORK_CAPTURE_LOG` or `VOID_ADBLOCK_DEBUG_LOG`
//!   (default: `%TEMP%/void-network-capture.jsonl`)
//! - Capped JSON snapshot beside the JSONL (`.json`)
//! - Live HAR 1.2 → `VOID_NETWORK_HAR_PATH` or `<jsonl>.har`
//!
//! WebView2 does not expose a reliable browser HAR export to RPA. This module
//! synthesizes HAR 1.2 from the WebResourceRequested / navigation decision stream
//! (blocked → HTTP 403; allowed → status 0 / unknown).

use once_cell::sync::Lazy;
use serde::Serialize;
use serde_json::{json, Value};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_SNAPSHOT: usize = 2000;
const HAR_FLUSH_EVERY: usize = 25;

#[derive(Clone, Serialize)]
pub struct NetworkDecision {
    pub ts_ms: u64,
    pub url: String,
    pub source_url: String,
    pub resource_type: String,
    pub method: String,
    pub decision: &'static str,
    pub filter: Option<String>,
    /// Synthetic or known status: 403 when blocked; None when allowed/unknown.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<i32>,
}

struct DebugState {
    enabled: bool,
    jsonl_path: PathBuf,
    snapshot_path: PathBuf,
    har_path: PathBuf,
    recent: Vec<NetworkDecision>,
    total_written: usize,
}

static STATE: Lazy<Mutex<DebugState>> = Lazy::new(|| {
    let enabled = env_truthy("VOID_NETWORK_CAPTURE") || env_truthy("VOID_ADBLOCK_DEBUG");
    let jsonl_path = std::env::var_os("VOID_NETWORK_CAPTURE_LOG")
        .or_else(|| std::env::var_os("VOID_ADBLOCK_DEBUG_LOG"))
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::temp_dir().join("void-network-capture.jsonl"));
    let snapshot_path = jsonl_path.with_extension("json");
    let har_path = std::env::var_os("VOID_NETWORK_HAR_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|| jsonl_path.with_extension("har"));
    if enabled {
        let _ = fs::write(&jsonl_path, "");
        let _ = fs::write(
            &snapshot_path,
            json!({
                "ok": true,
                "entries": [],
                "blocked": 0,
                "allowed": 0,
                "har_path": har_path,
            })
            .to_string(),
        );
        let _ = write_har_file(&har_path, &[]);
        eprintln!(
            "[void-network] capture log -> {} (snapshot {}, har {})",
            jsonl_path.display(),
            snapshot_path.display(),
            har_path.display()
        );
    }
    Mutex::new(DebugState {
        enabled,
        jsonl_path,
        snapshot_path,
        har_path,
        recent: Vec::new(),
        total_written: 0,
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

fn iso8601_from_ms(ts_ms: u64) -> String {
    // HAR wants ISO 8601; avoid chrono dependency — format UTC from epoch ms.
    let secs = (ts_ms / 1000) as i64;
    let millis = ts_ms % 1000;
    let days = secs.div_euclid(86_400);
    let tod = secs.rem_euclid(86_400) as u32;
    let hour = tod / 3600;
    let min = (tod % 3600) / 60;
    let sec = tod % 60;
    // Civil date from Unix day (proleptic Gregorian), algorithm from Howard Hinnant.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = (z - era * 146_097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{y:04}-{m:02}-{d:02}T{hour:02}:{min:02}:{sec:02}.{millis:03}Z"
    )
}

fn entry_to_har(e: &NetworkDecision) -> Value {
    let blocked = e.decision == "blocked";
    let status = e.status.unwrap_or(if blocked { 403 } else { 0 });
    let status_text = if blocked {
        "Blocked"
    } else if status == 0 {
        ""
    } else {
        "OK"
    };
    json!({
        "startedDateTime": iso8601_from_ms(e.ts_ms),
        "time": 0,
        "request": {
            "method": if e.method.is_empty() { "GET" } else { &e.method },
            "url": e.url,
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": [],
            "queryString": [],
            "headersSize": -1,
            "bodySize": -1
        },
        "response": {
            "status": status,
            "statusText": status_text,
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": [],
            "content": { "size": 0, "mimeType": "x-unknown" },
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": 0,
            "_transferSize": 0
        },
        "cache": {},
        "timings": { "send": 0, "wait": 0, "receive": 0 },
        "pageref": "void-page",
        "_void": {
            "decision": e.decision,
            "filter": e.filter,
            "resourceType": e.resource_type,
            "sourceUrl": e.source_url
        }
    })
}

fn build_har(entries: &[NetworkDecision]) -> Value {
    let har_entries: Vec<Value> = entries.iter().map(entry_to_har).collect();
    let pages = if let Some(first) = entries.first() {
        vec![json!({
            "startedDateTime": iso8601_from_ms(first.ts_ms),
            "id": "void-page",
            "title": "void-browser network capture",
            "pageTimings": { "onContentLoad": -1, "onLoad": -1 }
        })]
    } else {
        vec![]
    };
    json!({
        "log": {
            "version": "1.2",
            "creator": {
                "name": "void-browser",
                "version": env!("CARGO_PKG_VERSION"),
                "comment": "HAR 1.2 synthesized from Void WebResourceRequested / navigation decision log (not Chromium DevTools export)"
            },
            "browser": {
                "name": "void-browser-webview2",
                "version": env!("CARGO_PKG_VERSION")
            },
            "pages": pages,
            "entries": har_entries,
            "comment": "status 403 = adblock/privacy block; status 0 = allowed (response status unknown without CDP)"
        }
    })
}

fn write_har_file(path: &PathBuf, entries: &[NetworkDecision]) -> std::io::Result<()> {
    let body = serde_json::to_vec_pretty(&build_har(entries))?;
    let mut f = File::create(path)?;
    f.write_all(&body)
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

#[allow(dead_code)]
pub fn har_path() -> Option<PathBuf> {
    let s = STATE.lock().ok()?;
    if s.enabled {
        Some(s.har_path.clone())
    } else {
        None
    }
}

/// Force-flush live HAR from the in-memory ring (also runs periodically on record).
#[allow(dead_code)]
pub fn flush_har() {
    let Ok(state) = STATE.lock() else {
        return;
    };
    if !state.enabled {
        return;
    }
    let _ = write_har_file(&state.har_path, &state.recent);
}

pub fn record(
    url: &str,
    source_url: &str,
    resource_type: &str,
    blocked: bool,
    filter: Option<String>,
) {
    record_ex(url, source_url, resource_type, "GET", blocked, filter, None);
}

pub fn record_ex(
    url: &str,
    source_url: &str,
    resource_type: &str,
    method: &str,
    blocked: bool,
    filter: Option<String>,
    status: Option<i32>,
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
        method: if method.is_empty() {
            "GET".to_string()
        } else {
            method.to_string()
        },
        decision: if blocked { "blocked" } else { "allowed" },
        filter,
        status: status.or(if blocked { Some(403) } else { None }),
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
    state.total_written = state.total_written.saturating_add(1);

    let blocked_n = state
        .recent
        .iter()
        .filter(|e| e.decision == "blocked")
        .count();
    let allowed_n = state.recent.len().saturating_sub(blocked_n);
    let body = json!({
        "ok": true,
        "log_path": state.jsonl_path,
        "har_path": state.har_path,
        "entries": state.recent,
        "blocked": blocked_n,
        "allowed": allowed_n,
        "total_written": state.total_written,
    });
    if let Ok(mut f) = File::create(&state.snapshot_path) {
        let _ = f.write_all(body.to_string().as_bytes());
    }

    if state.total_written == 1 || state.total_written % HAR_FLUSH_EVERY == 0 {
        let _ = write_har_file(&state.har_path, &state.recent);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_epoch() {
        assert_eq!(iso8601_from_ms(0), "1970-01-01T00:00:00.000Z");
        assert_eq!(iso8601_from_ms(1_000), "1970-01-01T00:00:01.000Z");
    }

    #[test]
    fn har_blocked_status() {
        let e = NetworkDecision {
            ts_ms: 0,
            url: "https://ads.example/x.js".into(),
            source_url: "https://example.com/".into(),
            resource_type: "script".into(),
            method: "GET".into(),
            decision: "blocked",
            filter: Some("||ads.example^".into()),
            status: Some(403),
        };
        let har = build_har(&[e]);
        let status = har["log"]["entries"][0]["response"]["status"]
            .as_i64()
            .unwrap();
        assert_eq!(status, 403);
    }
}
