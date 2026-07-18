// Void Browser — Privacy & Security Engine
// Strips tracking headers, hardens requests, prevents fingerprinting

use serde::Serialize;

/// Security level presets
#[derive(Clone, Default, Serialize, serde::Deserialize, PartialEq, Eq)]
pub enum SecurityLevel {
    Standard,
    #[default]
    Strict,
    Paranoid,
}

/// Hardened response/request policy exposed to the UI and network hooks
#[derive(Serialize)]
pub struct PrivacyHeaders {
    pub set: Vec<(String, String)>,
    pub strip: Vec<&'static str>,
}

/// Generate hardened HTTP headers for all requests
pub fn get_hardened_headers() -> Vec<(String, String)> {
    vec![
        (
            "Referrer-Policy".into(),
            "strict-origin-when-cross-origin".into(),
        ),
        ("X-Frame-Options".into(), "DENY".into()),
        ("X-Content-Type-Options".into(), "nosniff".into()),
        ("X-XSS-Protection".into(), "1; mode=block".into()),
        (
            "Strict-Transport-Security".into(),
            "max-age=31536000; includeSubDomains".into(),
        ),
        (
            "Permissions-Policy".into(),
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()".into(),
        ),
        ("DNT".into(), "1".into()),
        ("Sec-GPC".into(), "1".into()),
    ]
}

/// Full privacy header policy: headers to set + headers to strip
pub fn get_privacy_headers() -> PrivacyHeaders {
    PrivacyHeaders {
        set: get_hardened_headers(),
        strip: stripped_request_headers(),
    }
}

/// Headers to strip from outgoing requests (tracking vectors)
pub fn stripped_request_headers() -> Vec<&'static str> {
    vec![
        "X-Client-Data",
        "X-Chrome-Connected",
        "X-Chrome-ID-Consistency-Request",
        "X-Requested-With",
    ]
}

/// Domains that should never be contacted (telemetry endpoints).
/// Wired into the adblock engine as network filters / main-frame blocks.
///
/// IMPORTANT: Do NOT list Google account / CDN hosts here (`accounts.google.com`,
/// `ssl.gstatic.com`, `www.google.com`, `youtube.com`). Those are required for
/// YouTube/Gmail sign-in. Blocking them makes OAuth appear broken.
pub const BLOCKED_TELEMETRY_DOMAINS: &[&str] = &[
    "clients1.google.com",
    "update.googleapis.com",
    "safebrowsing.googleapis.com",
    "telemetry.mozilla.org",
    "incoming.telemetry.mozilla.org",
    "crash-stats.mozilla.org",
    "vortex.data.microsoft.com",
    "settings-win.data.microsoft.com",
    "watson.telemetry.microsoft.com",
];

#[derive(Clone, Default, Serialize, serde::Deserialize, PartialEq, Eq)]
pub enum WebRtcPolicy {
    Default, // Allow WebRTC (may leak local IP)
    #[default]
    DisableNonProxied, // Only allow through proxy/VPN
    Disabled, // Fully disable WebRTC
}

#[derive(Clone, Serialize, serde::Deserialize, PartialEq)]
pub struct FingerprintResistance {
    pub spoof_canvas: bool,
    pub spoof_webgl: bool,
    pub spoof_audio: bool,
    pub uniform_navigator: bool,
    pub resist_font_enum: bool,
    pub spoof_timezone: bool,
    pub limit_screen_info: bool,
}

impl Default for FingerprintResistance {
    fn default() -> Self {
        FingerprintResistance {
            spoof_canvas: true,
            spoof_webgl: true,
            spoof_audio: true,
            uniform_navigator: false,
            resist_font_enum: true,
            spoof_timezone: false,
            limit_screen_info: false,
        }
    }
}

#[derive(Clone, Default, Serialize, serde::Deserialize, PartialEq, Eq)]
pub enum CookiePolicy {
    AllowAll,
    #[default]
    BlockThirdParty,
    BlockAll,
    SessionOnly,
}

#[derive(Clone, Default, Serialize, serde::Deserialize, PartialEq, Eq)]
pub enum HttpsPolicy {
    Prefer,
    #[default]
    Strict,
    Off,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn google_auth_hosts_are_not_telemetry_blocked() {
        let blocked: Vec<String> = BLOCKED_TELEMETRY_DOMAINS
            .iter()
            .map(|s| s.to_ascii_lowercase())
            .collect();
        for host in [
            "accounts.google.com",
            "ssl.gstatic.com",
            "www.youtube.com",
            "youtube.com",
            "mail.google.com",
        ] {
            assert!(
                !blocked
                    .iter()
                    .any(|b| host == b.as_str() || host.ends_with(&format!(".{b}"))),
                "{host} must not be in BLOCKED_TELEMETRY_DOMAINS (breaks sign-in)"
            );
        }
    }
}
