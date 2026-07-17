// Void Browser — Privacy & Security Engine
// Strips tracking headers, hardens requests, prevents fingerprinting

use serde::Serialize;

/// Security level presets
#[derive(Clone, Default, Serialize, serde::Deserialize, PartialEq)]
pub enum SecurityLevel {
    Standard, // Block ads + trackers, strip referrers
    #[default]
    Strict, // + block third-party cookies, disable WebRTC leak
    Paranoid, // + resist fingerprinting, block all JS by default
}

/// Generate hardened HTTP headers for all requests
pub fn get_hardened_headers() -> Vec<(String, String)> {
    let mut headers = vec![
        // Prevent referrer leaking
        (
            "Referrer-Policy".into(),
            "strict-origin-when-cross-origin".into(),
        ),
        // Deny iframe embedding (clickjacking protection)
        ("X-Frame-Options".into(), "DENY".into()),
        // Prevent MIME sniffing
        ("X-Content-Type-Options".into(), "nosniff".into()),
        // XSS protection
        ("X-XSS-Protection".into(), "1; mode=block".into()),
        // Strict transport security
        (
            "Strict-Transport-Security".into(),
            "max-age=31536000; includeSubDomains".into(),
        ),
        // Permissions policy — disable dangerous APIs
        (
            "Permissions-Policy".into(),
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()".into(),
        ),
        // DNT (Do Not Track)
        ("DNT".into(), "1".into()),
        // Global Privacy Control
        ("Sec-GPC".into(), "1".into()),
    ];

    // Document which request headers we strip — exposed for the UI / future proxy hook
    let _ = stripped_request_headers();
    let _ = blocked_telemetry_domains();
    headers
}

/// Headers to strip from outgoing requests (tracking vectors)
pub fn stripped_request_headers() -> Vec<&'static str> {
    vec![
        "X-Client-Data",      // Chrome tracking header
        "X-Chrome-Connected", // Chrome account linking
        "X-Chrome-ID-Consistency-Request",
        "X-Requested-With", // Can leak app identity
    ]
}

/// Domains that should never be contacted (telemetry endpoints)
pub fn blocked_telemetry_domains() -> Vec<&'static str> {
    vec![
        // Google
        "clients1.google.com",
        "update.googleapis.com",
        "safebrowsing.googleapis.com",
        "accounts.google.com",
        "ssl.gstatic.com",
        // Mozilla
        "telemetry.mozilla.org",
        "incoming.telemetry.mozilla.org",
        "crash-stats.mozilla.org",
        // Microsoft
        "vortex.data.microsoft.com",
        "settings-win.data.microsoft.com",
        "watson.telemetry.microsoft.com",
        // Generic
        "ocsp.digicert.com", // Can be used for tracking via OCSP
    ]
}

/// WebRTC policy to prevent IP leaks
#[derive(Clone, Default, Serialize, serde::Deserialize)]
pub enum WebRtcPolicy {
    Default, // Allow WebRTC (may leak local IP)
    #[default]
    DisableNonProxied, // Only allow through proxy/VPN
    Disabled,          // Fully disable WebRTC
}

/// Fingerprint resistance measures
#[derive(Clone, Serialize, serde::Deserialize)]
pub struct FingerprintResistance {
    pub spoof_canvas: bool,      // Randomize canvas fingerprint
    pub spoof_webgl: bool,       // Randomize WebGL renderer info
    pub spoof_audio: bool,       // Randomize AudioContext fingerprint
    pub uniform_navigator: bool, // Generic navigator properties
    pub resist_font_enum: bool,  // Limit font enumeration
    pub spoof_timezone: bool,    // Report UTC timezone
    pub limit_screen_info: bool, // Generic screen dimensions
}

impl Default for FingerprintResistance {
    fn default() -> Self {
        FingerprintResistance {
            spoof_canvas: true,
            spoof_webgl: true,
            spoof_audio: true,
            uniform_navigator: false, // Can break sites, off by default
            resist_font_enum: true,
            spoof_timezone: false,    // Can break sites, off by default
            limit_screen_info: false, // Can break layouts, off by default
        }
    }
}

/// Cookie policy
#[derive(Clone, Default, Serialize, serde::Deserialize)]
pub enum CookiePolicy {
    AllowAll,
    #[default]
    BlockThirdParty, // Default — blocks cross-site cookies
    BlockAll,    // Nuclear option
    SessionOnly, // Allow but clear on exit
}

/// HTTPS upgrade policy
#[derive(Clone, Default, Serialize, serde::Deserialize)]
pub enum HttpsPolicy {
    Prefer, // Upgrade when possible, allow HTTP fallback
    #[default]
    Strict, // Block HTTP entirely (default)
    Off,    // No enforcement
}
