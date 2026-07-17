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

impl TabManager {
    pub fn new() -> Self {
        let mut manager = TabManager {
            tabs: Vec::new(),
            active_id: None,
        };
        // Open with one blank tab
        manager.create(None);
        manager
    }

    pub fn create(&mut self, url: Option<String>) -> Tab {
        let id = Uuid::new_v4().to_string();
        let tab = Tab {
            id: id.clone(),
            title: "New Tab".to_string(),
            url: url.unwrap_or_else(|| "void://newtab".to_string()),
            active: true,
            loading: false,
            pinned: false,
        };

        // Deactivate current tab
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
            return None; // Don't close last tab
        }

        let pos = self.tabs.iter().position(|t| t.id == id)?;
        self.tabs.remove(pos);

        // If we closed the active tab, activate the nearest one
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

    pub fn list(&self) -> Vec<Tab> {
        self.tabs.clone()
    }
}
