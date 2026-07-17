// ═══════════════════════════════════════════════════
// Void Browser — Frontend Controller
// Handles tabs, navigation, settings, and Tauri IPC
// ═══════════════════════════════════════════════════

const { invoke } = window.__TAURI__?.core ?? {
  // Fallback for development without Tauri
  invoke: async (cmd, args) => {
    console.log(`[dev] invoke: ${cmd}`, args);
    return null;
  }
};

// ── State ────────────────────────────────────────

let tabs = [];
let activeTabId = null;
let blockedCount = 0;

// ── DOM References ───────────────────────────────

const tabsContainer = document.getElementById('tabs-container');
const newTabBtn = document.getElementById('new-tab-btn');
const urlBar = document.getElementById('url-bar');
const backBtn = document.getElementById('back-btn');
const forwardBtn = document.getElementById('forward-btn');
const reloadBtn = document.getElementById('reload-btn');
const shieldBtn = document.getElementById('shield-btn');
const settingsBtn = document.getElementById('settings-btn');
const searchInput = document.getElementById('search-input');
const blockCount = document.getElementById('block-count');
const statBlocked = document.getElementById('stat-blocked');
const newtabPage = document.getElementById('newtab-page');
const settingsPage = document.getElementById('settings-page');

// ── Tab Management ───────────────────────────────

function renderTabs() {
  tabsContainer.innerHTML = '';
  tabs.forEach(tab => {
    const el = document.createElement('div');
    el.className = `tab${tab.active ? ' active' : ''}`;
    el.innerHTML = `
      <span class="tab-title">${escapeHtml(tab.title)}</span>
      <span class="tab-close" data-id="${tab.id}">&times;</span>
    `;
    el.addEventListener('click', (e) => {
      if (e.target.classList.contains('tab-close')) {
        closeTab(e.target.dataset.id);
      } else {
        switchTab(tab.id);
      }
    });
    tabsContainer.appendChild(el);
  });
}

async function createTab(url) {
  try {
    const tab = await invoke('create_tab', { url: url || null });
    if (tab) {
      tabs.push(tab);
      activeTabId = tab.id;
      tabs.forEach(t => t.active = t.id === activeTabId);
      renderTabs();
      showPage('newtab');
      urlBar.value = '';
      if (searchInput) searchInput.focus();
    }
  } catch (e) {
    // Fallback for dev mode
    const id = 'tab-' + Date.now();
    const tab = { id, title: 'New Tab', url: url || 'void://newtab', active: true };
    tabs.forEach(t => t.active = false);
    tabs.push(tab);
    activeTabId = id;
    renderTabs();
    showPage('newtab');
    urlBar.value = '';
    if (searchInput) searchInput.focus();
  }
}

async function closeTab(id) {
  if (tabs.length <= 1) return;

  try {
    const newActiveId = await invoke('close_tab', { id });
    tabs = tabs.filter(t => t.id !== id);
    if (newActiveId) {
      activeTabId = newActiveId;
      tabs.forEach(t => t.active = t.id === activeTabId);
    }
  } catch (e) {
    tabs = tabs.filter(t => t.id !== id);
    if (activeTabId === id && tabs.length > 0) {
      activeTabId = tabs[tabs.length - 1].id;
      tabs[tabs.length - 1].active = true;
    }
  }
  renderTabs();
}

function switchTab(id) {
  tabs.forEach(t => t.active = t.id === id);
  activeTabId = id;
  renderTabs();

  const tab = tabs.find(t => t.id === id);
  if (tab) {
    urlBar.value = tab.url === 'void://newtab' ? '' : tab.url;
    if (tab.url === 'void://newtab') showPage('newtab');
    else if (tab.url === 'void://settings') showPage('settings');
  }
}

// ── Navigation ───────────────────────────────────

function navigate(url) {
  if (!url) return;

  // Handle internal pages
  if (url === 'void://newtab') {
    showPage('newtab');
    return;
  }
  if (url === 'void://settings') {
    showPage('settings');
    return;
  }

  // Auto-add protocol
  if (!url.includes('://')) {
    if (url.includes('.') && !url.includes(' ')) {
      url = 'https://' + url;
    } else {
      // Search query
      url = `https://duckduckgo.com/?q=${encodeURIComponent(url)}`;
    }
  }

  // Update tab
  const tab = tabs.find(t => t.id === activeTabId);
  if (tab) {
    tab.url = url;
    tab.title = new URL(url).hostname;
    renderTabs();
  }
  urlBar.value = url;

  // In a full build, this would navigate the webview
  showPage('webview');
  console.log(`[void] navigate: ${url}`);
}

function showPage(page) {
  newtabPage.classList.remove('active');
  settingsPage.classList.remove('active');
  document.getElementById('webview-container').classList.remove('active');

  switch (page) {
    case 'newtab':
      newtabPage.classList.add('active');
      break;
    case 'settings':
      settingsPage.classList.add('active');
      break;
    case 'webview':
      document.getElementById('webview-container').classList.add('active');
      break;
  }
}

// ── Privacy Shield Panel ─────────────────────────

shieldBtn.addEventListener('click', async () => {
  try {
    const stats = await invoke('get_stats');
    if (stats) {
      blockedCount = stats.total_blocked;
      updateBlockCount();
    }
  } catch (e) {
    // Dev mode — just toggle visual state
    shieldBtn.classList.toggle('active');
  }
});

function updateBlockCount() {
  blockCount.textContent = blockedCount;
  statBlocked.textContent = blockedCount;
}

// ── Event Listeners ──────────────────────────────

// URL bar — navigate on Enter
urlBar.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    navigate(urlBar.value.trim());
    urlBar.blur();
  }
});

// URL bar — select all on focus
urlBar.addEventListener('focus', () => urlBar.select());

// Search input — navigate on Enter
searchInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const query = searchInput.value.trim();
    if (query) {
      navigate(query);
      searchInput.value = '';
    }
  }
});

// New tab button
newTabBtn.addEventListener('click', () => createTab());

// Settings button
settingsBtn.addEventListener('click', () => {
  const tab = tabs.find(t => t.id === activeTabId);
  if (tab) {
    tab.url = 'void://settings';
    tab.title = 'Settings';
    renderTabs();
  }
  urlBar.value = '';
  showPage('settings');
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  // Ctrl+T — New tab
  if (e.ctrlKey && e.key === 't') {
    e.preventDefault();
    createTab();
  }
  // Ctrl+W — Close tab
  if (e.ctrlKey && e.key === 'w') {
    e.preventDefault();
    if (activeTabId) closeTab(activeTabId);
  }
  // Ctrl+L — Focus URL bar
  if (e.ctrlKey && e.key === 'l') {
    e.preventDefault();
    urlBar.focus();
  }
  // Ctrl+, — Settings
  if (e.ctrlKey && e.key === ',') {
    e.preventDefault();
    settingsBtn.click();
  }
  // Ctrl+Tab — Next tab
  if (e.ctrlKey && e.key === 'Tab') {
    e.preventDefault();
    const idx = tabs.findIndex(t => t.id === activeTabId);
    const nextIdx = e.shiftKey
      ? (idx - 1 + tabs.length) % tabs.length
      : (idx + 1) % tabs.length;
    switchTab(tabs[nextIdx].id);
  }
});

// ── Theme & Settings ─────────────────────────────

const themeSelect = document.getElementById('theme-select');
themeSelect?.addEventListener('change', () => {
  document.documentElement.setAttribute('data-theme', themeSelect.value);
  localStorage.setItem('void-theme', themeSelect.value);
});

// Load saved theme
const savedTheme = localStorage.getItem('void-theme');
if (savedTheme) {
  document.documentElement.setAttribute('data-theme', savedTheme);
  if (themeSelect) themeSelect.value = savedTheme;
}

const compactToggle = document.getElementById('compact-mode');
compactToggle?.addEventListener('change', () => {
  document.body.classList.toggle('compact', compactToggle.checked);
});

const fontSizeSlider = document.getElementById('font-size');
const fontSizeDisplay = document.getElementById('font-size-display');
fontSizeSlider?.addEventListener('input', () => {
  fontSizeDisplay.textContent = fontSizeSlider.value + 'px';
  document.body.style.fontSize = fontSizeSlider.value + 'px';
});

// ── Utilities ────────────────────────────────────

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── Init ─────────────────────────────────────────

(async function init() {
  await createTab();
  shieldBtn.classList.add('active');
  console.log('[void] Browser initialized — No tracking. No AI. Just browsing.');
})();
