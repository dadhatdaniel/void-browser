// Void Browser — Tab Management

use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Clone, Serialize, Deserialize)]
pub struct Tab {
    pub id: String,
    pub title: String,
    pub url: String,
    pub active: bool,
    pub loading: bool,
    pub pinned: bool,
}

pub struct TabManager {
    tabs: Vec<Tab>,
    active_id: Option<String>,
}

impl Default for TabManager {
    fn default() -> Self {
        Self::new()
    }
}

impl TabManager {
    pub fn new() -> Self {
        TabManager {
            tabs: Vec::new(),
            active_id: None,
        }
    }

    pub fn create(&mut self, url: Option<String>) -> Tab {
        let id = Uuid::new_v4().to_string();
        let url = url.unwrap_or_else(|| "void://newtab".to_string());
        let title = if url == "void://newtab" {
            "New Tab".to_string()
        } else if url == "void://settings" {
            "Settings".to_string()
        } else {
            UrlHost::from(&url)
        };

        let tab = Tab {
            id: id.clone(),
            title,
            url,
            active: true,
            loading: false,
            pinned: false,
        };

        if let Some(ref active) = self.active_id {
            if let Some(t) = self.tabs.iter_mut().find(|t| t.id == *active) {
                t.active = false;
            }
        }

        self.active_id = Some(id);
        self.tabs.push(tab.clone());
        tab
    }

    pub fn close(&mut self, id: &str) -> Option<String> {
        if self.tabs.len() <= 1 {
            return None;
        }

        let pos = self.tabs.iter().position(|t| t.id == id)?;
        self.tabs.remove(pos);

        if self.active_id.as_deref() == Some(id) {
            let new_pos = if pos >= self.tabs.len() {
                self.tabs.len() - 1
            } else {
                pos
            };
            self.tabs[new_pos].active = true;
            self.active_id = Some(self.tabs[new_pos].id.clone());
        }

        self.active_id.clone()
    }

    pub fn set_active(&mut self, id: &str) -> bool {
        if let Some(ref active) = self.active_id {
            if let Some(t) = self.tabs.iter_mut().find(|t| t.id == *active) {
                t.active = false;
            }
        }

        if let Some(t) = self.tabs.iter_mut().find(|t| t.id == id) {
            t.active = true;
            self.active_id = Some(id.to_string());
            true
        } else {
            false
        }
    }

    pub fn set_url(&mut self, id: &str, url: &str) {
        if let Some(t) = self.tabs.iter_mut().find(|t| t.id == id) {
            t.url = url.to_string();
        }
    }

    pub fn set_title(&mut self, id: &str, title: &str) {
        if let Some(t) = self.tabs.iter_mut().find(|t| t.id == id) {
            let trimmed = title.trim();
            if !trimmed.is_empty() {
                t.title = trimmed.to_string();
            }
        }
    }

    pub fn set_loading(&mut self, id: &str, loading: bool) {
        if let Some(t) = self.tabs.iter_mut().find(|t| t.id == id) {
            t.loading = loading;
        }
    }

    pub fn get(&self, id: &str) -> Option<&Tab> {
        self.tabs.iter().find(|t| t.id == id)
    }

    pub fn list(&self) -> Vec<Tab> {
        self.tabs.clone()
    }

    pub fn active_id(&self) -> Option<String> {
        self.active_id.clone()
    }
}

struct UrlHost;

impl UrlHost {
    fn from(url: &str) -> String {
        url::Url::parse(url)
            .ok()
            .and_then(|u| u.host_str().map(|h| h.to_string()))
            .unwrap_or_else(|| url.to_string())
    }
}
