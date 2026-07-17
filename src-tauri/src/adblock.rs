// Void Browser — Ad & Tracker Blocking Engine
// Uses adblock-rust (same engine as Brave) with EasyList + EasyPrivacy

use adblock::lists::ParseOptions;
use adblock::request::Request;
use adblock::{Engine, FilterSet};
use serde::Serialize;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::config::VoidConfig;
use crate::privacy::BLOCKED_TELEMETRY_DOMAINS;

/// Stats tracked per session
#[derive(Clone, Serialize, Default)]
pub struct BlockStats {
    pub total_checked: u64,
    pub total_blocked: u64,
    pub ads_blocked: u64,
    pub trackers_blocked: u64,
}

pub struct MatchResult {
    pub matched: bool,
    pub filter: Option<String>,
}

pub struct AdBlocker {
    engine: Engine,
    total_checked: AtomicU64,
    total_blocked: AtomicU64,
}

impl AdBlocker {
    pub fn new(config: &VoidConfig) -> Self {
        let mut filter_set = FilterSet::new(true);

        if config.adblock_enabled {
            // Load bundled minimal filter lists (compiled into the binary)
            let easylist = include_str!("../../filters/easylist-minimal.txt");
            filter_set.add_filters(easylist.lines(), ParseOptions::default());

            let privacy = include_str!("../../filters/privacy-filters.txt");
            filter_set.add_filters(privacy.lines(), ParseOptions::default());

            // Domain-level tracker + telemetry fallbacks as network filters
            let domain_rules: Vec<String> = TRACKER_DOMAINS
                .iter()
                .chain(BLOCKED_TELEMETRY_DOMAINS.iter())
                .map(|d| format!("||{d}^"))
                .collect();
            filter_set.add_filters(domain_rules, ParseOptions::default());

            // Load full filter lists from runtime data directory if available
            if let Some(data_dir) = dirs::data_dir() {
                let filters_dir = data_dir.join("void-browser").join("filters");
                load_filter_file(&mut filter_set, &filters_dir.join("easylist-full.txt"));
                load_filter_file(&mut filter_set, &filters_dir.join("easyprivacy-full.txt"));
            }
        }

        // Load user custom filters
        if let Some(ref custom) = config.custom_filters {
            filter_set.add_filters(custom.iter().map(String::as_str), ParseOptions::default());
        }

        let engine = Engine::from_filter_set(filter_set, true);

        AdBlocker {
            engine,
            total_checked: AtomicU64::new(0),
            total_blocked: AtomicU64::new(0),
        }
    }

    /// Check if a URL should be blocked
    pub fn check(&self, url: &str, source_url: &str) -> MatchResult {
        self.total_checked.fetch_add(1, Ordering::Relaxed);

        let Ok(request) = Request::new(url, source_url, "other") else {
            return MatchResult {
                matched: false,
                filter: None,
            };
        };
        let result = self.engine.check_network_request(&request);

        if result.matched {
            self.total_blocked.fetch_add(1, Ordering::Relaxed);
        }

        MatchResult {
            matched: result.matched,
            filter: result.filter,
        }
    }

    /// Get current session stats
    pub fn stats(&self) -> BlockStats {
        BlockStats {
            total_checked: self.total_checked.load(Ordering::Relaxed),
            total_blocked: self.total_blocked.load(Ordering::Relaxed),
            ads_blocked: 0,
            trackers_blocked: 0,
        }
    }
}

/// Load a filter list file at runtime if it exists
fn load_filter_file(filter_set: &mut FilterSet, path: &std::path::Path) {
    if let Ok(content) = std::fs::read_to_string(path) {
        filter_set.add_filters(content.lines(), ParseOptions::default());
    }
}

/// Known tracker domains (hardcoded fallback for domain-level blocking)
pub const TRACKER_DOMAINS: &[&str] = &[
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net",
    "facebook.com/tr",
    "analytics.google.com",
    "connect.facebook.net",
    "pixel.facebook.com",
    "bat.bing.com",
    "analytics.twitter.com",
    "t.co",
    "clarity.ms",
    "hotjar.com",
    "mixpanel.com",
    "segment.io",
    "amplitude.com",
    "fullstory.com",
    "mouseflow.com",
    "crazyegg.com",
    "optimizely.com",
    "newrelic.com",
    "sentry.io",
    "bugsnag.com",
    "pingdom.net",
    "statcounter.com",
    "quantserve.com",
    "scorecardresearch.com",
    "outbrain.com",
    "taboola.com",
    "criteo.com",
    "adroll.com",
];
