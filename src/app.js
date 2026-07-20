// ═══════════════════════════════════════════════════
// Void Browser — Frontend Controller
// Tabs, navigation, settings persistence, window chrome
// ═══════════════════════════════════════════════════

const tauri = window.__TAURI__;
const invoke = tauri?.core?.invoke ?? (async (cmd, args) => {
  console.log(`[dev] invoke: ${cmd}`, args);
  return null;
});
const listen = tauri?.event?.listen ?? (async () => () => {});
const getCurrentWindow = tauri?.window?.getCurrentWindow
  ? () => tauri.window.getCurrentWindow()
  : null;

let tabs = [];
let activeTabId = null;
let blockedCount = 0;
/** @type {object|null} full VoidConfig from Rust */
let appConfig = null;
let browsingActive = false;
/** Settings is an overlay — never mutates the active tab URL/title. */
let settingsOpen = false;

const tabsContainer = document.getElementById('tabs-container');
const newTabBtn = document.getElementById('new-tab-btn');
const urlBar = document.getElementById('url-bar');
const backBtn = document.getElementById('back-btn');
const forwardBtn = document.getElementById('forward-btn');
const reloadBtn = document.getElementById('reload-btn');
const shieldBtn = document.getElementById('shield-btn');
const settingsBtn = document.getElementById('settings-btn');
const searchInput = document.getElementById('search-input');
const blockCountEl = document.getElementById('block-count');
const statBlocked = document.getElementById('stat-blocked');
const newtabPage = document.getElementById('newtab-page');
const settingsPage = document.getElementById('settings-page');
const chromeEl = document.getElementById('chrome');
const securityIndicator = document.getElementById('security-indicator');

const SEARCH_URLS = {
  DuckDuckGo: (q) => `https://duckduckgo.com/?q=${encodeURIComponent(q)}`,
  Brave: (q) => `https://search.brave.com/search?q=${encodeURIComponent(q)}`,
  Startpage: (q) => `https://www.startpage.com/sp/search?query=${encodeURIComponent(q)}`,
  SearXNG: (q) => `https://searx.be/search?q=${encodeURIComponent(q)}`,
};

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

let systemThemeMql = null;
let systemThemeHandler = null;

function systemThemeCssName() {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  } catch (_) {
    return 'dark';
  }
}

function isSystemTheme(theme) {
  const name = typeof theme === 'string' ? theme : enumName(theme, '');
  return String(name).toLowerCase() === 'system';
}

function themeCssName(theme) {
  if (!theme || typeof theme === 'string') {
    const t = String(theme || 'Dark').toLowerCase();
    if (t === 'system') return systemThemeCssName();
    if (t === 'midnight') return 'midnight';
    if (t === 'light') return 'light';
    return 'dark';
  }
  if (theme.Custom) return 'dark';
  if ('System' in theme || theme === 'System') return systemThemeCssName();
  if ('Midnight' in theme || theme === 'Midnight') return 'midnight';
  if ('Light' in theme || theme === 'Light') return 'light';
  return 'dark';
}

function bindSystemThemeListener(enabled) {
  if (systemThemeMql && systemThemeHandler) {
    try {
      systemThemeMql.removeEventListener('change', systemThemeHandler);
    } catch (_) { /* older engines */ }
    systemThemeMql = null;
    systemThemeHandler = null;
  }
  if (!enabled) return;
  try {
    systemThemeMql = window.matchMedia('(prefers-color-scheme: light)');
    systemThemeHandler = () => {
      document.documentElement.setAttribute('data-theme', systemThemeCssName());
    };
    systemThemeMql.addEventListener('change', systemThemeHandler);
  } catch (_) { /* no matchMedia */ }
}

function enumName(value, fallback) {
  if (value == null) return fallback;
  if (typeof value === 'string') return value;
  if (typeof value === 'object') {
    const keys = Object.keys(value);
    if (keys.length) return keys[0];
  }
  return fallback;
}

function applyAppearance(config) {
  if (!config) return;
  const theme = enumName(config.theme, 'Dark');
  document.documentElement.setAttribute('data-theme', themeCssName(theme));
  bindSystemThemeListener(isSystemTheme(theme));
  document.body.classList.toggle('compact', !!config.compact_mode);
  if (config.font_size) {
    document.body.style.fontSize = `${config.font_size}px`;
  }
  const engine = enumName(config.search_engine, 'DuckDuckGo');
  if (searchInput) {
    searchInput.placeholder = `Search with ${engine === 'Brave' ? 'Brave Search' : engine}`;
  }
}

function updateNavButtons(enabled) {
  backBtn.disabled = !enabled;
  forwardBtn.disabled = !enabled;
  reloadBtn.disabled = !enabled;
}

function updateBlockCount(n) {
  blockedCount = n ?? blockedCount;
  if (blockCountEl) blockCountEl.textContent = String(blockedCount);
  if (statBlocked) statBlocked.textContent = String(blockedCount);
}

function setSecurityIndicator(url) {
  if (!securityIndicator) return;
  const secure = !url || url.startsWith('https://') || url.startsWith('void://');
  securityIndicator.classList.toggle('insecure', !secure);
}

async function reportChromeHeight() {
  // Measure the fixed chrome strips only — never the flex-grown shell height.
  // A full-window "chrome" value collapses the content webview to ~0px (white void).
  const tabBar = document.getElementById('tab-bar');
  const navBar = document.getElementById('nav-bar');
  let height = 0;
  if (tabBar) height += tabBar.getBoundingClientRect().height;
  if (navBar) height += navBar.getBoundingClientRect().height;
  if (!height && chromeEl) height = chromeEl.getBoundingClientRect().height;
  height = Math.ceil(height || 78);
  height = Math.min(160, Math.max(40, height));
  try {
    await invoke('set_chrome_height', { height });
  } catch (_) { /* dev */ }
}

async function hideContent() {
  browsingActive = false;
  document.body.classList.remove('browsing');
  try {
    await invoke('hide_browser_content');
  } catch (_) { /* dev */ }
  updateNavButtons(false);
}

async function showContentFor(tabId) {
  browsingActive = true;
  document.body.classList.add('browsing');
  try {
    await invoke('show_browser_content', { tabId });
  } catch (_) { /* dev */ }
  updateNavButtons(true);
}

function showPage(page) {
  newtabPage.classList.remove('active');
  settingsPage.classList.remove('active');
  document.getElementById('webview-container')?.classList.remove('active');

  if (page === 'newtab') {
    newtabPage.classList.add('active');
  } else if (page === 'settings') {
    settingsPage.classList.add('active');
  } else if (page === 'webview') {
    document.getElementById('webview-container')?.classList.add('active');
  }
}

function renderTabs() {
  tabsContainer.innerHTML = '';
  tabs.forEach((tab) => {
    const el = document.createElement('div');
    el.className = `tab${tab.active ? ' active' : ''}${tab.loading ? ' loading' : ''}`;
    el.title = tab.url || tab.title;
    el.innerHTML = `
      <span class="tab-title">${escapeHtml(tab.title)}</span>
      <span class="tab-close" data-id="${tab.id}" title="Close">&times;</span>
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

function isInternalUrl(url) {
  return !url || url.startsWith('void://');
}

async function openSettings() {
  settingsOpen = true;
  // Hide content webviews but keep tab list + URLs intact.
  await hideContent();
  showPage('settings');
  await populateSettingsForm();
  document.getElementById('settings-close-btn')?.focus();
}

async function closeSettings() {
  if (!settingsOpen) return;
  settingsOpen = false;
  const tab = tabs.find((t) => t.id === activeTabId);
  await activateTabView(tab);
}

async function activateTabView(tab) {
  if (!tab) return;
  // Closing/switching while settings was open clears the overlay flag.
  settingsOpen = false;
  urlBar.value = isInternalUrl(tab.url) ? '' : tab.url;
  setSecurityIndicator(tab.url);

  // Legacy: an old session may still have void://settings as a tab URL.
  // Treat it as new-tab chrome and open the overlay instead of destroying state.
  if (tab.url === 'void://settings') {
    tab.url = 'void://newtab';
    tab.title = 'New Tab';
    renderTabs();
    await hideContent();
    showPage('newtab');
    await openSettings();
    return;
  }

  if (isInternalUrl(tab.url)) {
    await hideContent();
    showPage('newtab');
  } else {
    showPage('webview');
    await showContentFor(tab.id);
  }
  updateNavButtons(!isInternalUrl(tab.url));
}

async function createTab(url) {
  try {
    const tab = await invoke('create_tab', { url: url || null });
    if (tab) {
      tabs.forEach((t) => { t.active = false; });
      tabs.push(tab);
      activeTabId = tab.id;
      renderTabs();
      await activateTabView(tab);
      if (!url || url === 'void://newtab') {
        searchInput?.focus();
      }
      return;
    }
  } catch (_) { /* fall through */ }

  const id = `tab-${Date.now()}`;
  const tab = { id, title: 'New Tab', url: url || 'void://newtab', active: true, loading: false };
  tabs.forEach((t) => { t.active = false; });
  tabs.push(tab);
  activeTabId = id;
  renderTabs();
  await activateTabView(tab);
}

async function closeTab(id) {
  if (tabs.length <= 1) return;
  try {
    await invoke('close_browser_content', { tabId: id });
  } catch (_) { /* ok */ }

  let newActiveId = null;
  try {
    newActiveId = await invoke('close_tab', { id });
  } catch (_) {
    tabs = tabs.filter((t) => t.id !== id);
    if (activeTabId === id && tabs.length) {
      newActiveId = tabs[tabs.length - 1].id;
    }
  }

  tabs = tabs.filter((t) => t.id !== id);
  if (newActiveId) {
    activeTabId = newActiveId;
    tabs.forEach((t) => { t.active = t.id === activeTabId; });
  }
  renderTabs();
  const tab = tabs.find((t) => t.id === activeTabId);
  await activateTabView(tab);
}

async function switchTab(id) {
  try {
    await invoke('set_active_tab', { id });
  } catch (_) { /* ok */ }
  tabs.forEach((t) => { t.active = t.id === id; });
  activeTabId = id;
  renderTabs();
  const tab = tabs.find((t) => t.id === id);
  await activateTabView(tab);
}

function resolveNavigateUrl(input) {
  let url = (input || '').trim();
  if (!url) return null;
  if (url === 'void://newtab' || url === 'void://settings') return url;

  if (!url.includes('://')) {
    if (url.includes('.') && !url.includes(' ')) {
      const httpsOnly = enumName(appConfig?.https_policy, 'Strict') === 'Strict';
      url = (httpsOnly ? 'https://' : 'http://') + url;
    } else {
      const engine = enumName(appConfig?.search_engine, 'DuckDuckGo');
      const builder = SEARCH_URLS[engine] || SEARCH_URLS.DuckDuckGo;
      url = builder(url);
    }
  }
  return url;
}

async function navigate(raw) {
  const url = resolveNavigateUrl(raw);
  if (!url) return;

  const tab = tabs.find((t) => t.id === activeTabId);
  if (!tab) return;

  if (url === 'void://newtab') {
    tab.url = url;
    tab.title = 'New Tab';
    renderTabs();
    await activateTabView(tab);
    return;
  }
  if (url === 'void://settings') {
    // Overlay only — do not replace the browsing tab.
    await openSettings();
    return;
  }

  tab.url = url;
  tab.loading = true;
  try {
    tab.title = new URL(url).hostname;
  } catch (_) {
    tab.title = url;
  }
  renderTabs();
  urlBar.value = url;
  setSecurityIndicator(url);
  showPage('webview');
  document.body.classList.add('browsing');

  try {
    await reportChromeHeight();
    await invoke('navigate_browser', { tabId: tab.id, url });
    browsingActive = true;
    updateNavButtons(true);
  } catch (e) {
    console.error('[void] navigate failed', e);
    tab.loading = false;
    renderTabs();
    showPage('newtab');
    await hideContent();
  }
}

async function populateSettingsForm() {
  try {
    appConfig = await invoke('get_config');
  } catch (_) {
    return;
  }
  if (!appConfig) return;

  applyAppearance(appConfig);

  const security = enumName(appConfig.security_level, 'Strict');
  document.querySelectorAll('input[name="security"]').forEach((el) => {
    el.checked = el.value === security;
  });

  const theme = enumName(appConfig.theme, 'Dark');
  const themeSelect = document.getElementById('theme-select');
  if (themeSelect) themeSelect.value = theme;

  const fontSize = document.getElementById('font-size');
  const fontDisplay = document.getElementById('font-size-display');
  if (fontSize) {
    fontSize.value = appConfig.font_size ?? 14;
    if (fontDisplay) fontDisplay.textContent = `${fontSize.value}px`;
  }

  const compact = document.getElementById('compact-mode');
  if (compact) compact.checked = !!appConfig.compact_mode;

  const homepage = document.getElementById('homepage-input');
  if (homepage) homepage.value = appConfig.homepage || 'void://newtab';

  const searchEngine = document.getElementById('search-engine');
  if (searchEngine) searchEngine.value = enumName(appConfig.search_engine, 'DuckDuckGo');

  const adblock = document.getElementById('adblock-toggle');
  if (adblock) adblock.checked = !!appConfig.adblock_enabled;

  const tracker = document.getElementById('tracker-toggle');
  if (tracker) tracker.checked = !!appConfig.tracker_blocking;

  const https = document.getElementById('https-toggle');
  if (https) https.checked = enumName(appConfig.https_policy, 'Strict') === 'Strict';

  const fp = document.getElementById('fingerprint-toggle');
  if (fp) {
    const f = appConfig.fingerprint_resistance || {};
    fp.checked = !!(f.spoof_canvas || f.spoof_webgl || f.spoof_audio);
  }

  const doh = document.getElementById('doh-select');
  if (doh) doh.value = appConfig.dns_over_https || '';

  const clearCache = document.getElementById('clear-cache-toggle');
  if (clearCache) clearCache.checked = !!appConfig.clear_on_exit?.cache;

  const clearHistory = document.getElementById('clear-history-toggle');
  if (clearHistory) clearHistory.checked = !!appConfig.clear_on_exit?.history;

  try {
    const path = await invoke('get_config_path');
    const pathEl = document.getElementById('config-path-display');
    if (pathEl && path) pathEl.textContent = `Config: ${path}`;
  } catch (_) { /* ok */ }
}

async function saveSettings() {
  const status = document.getElementById('settings-status');
  const setStatus = (msg, ok) => {
    if (!status) return;
    status.textContent = msg;
    status.className = ok ? 'ok' : 'err';
  };

  let base;
  try {
    base = await invoke('get_config');
  } catch (e) {
    setStatus('Failed to read config', false);
    return;
  }
  if (!base) {
    setStatus('No config backend (dev mode)', false);
    return;
  }

  const security = document.querySelector('input[name="security"]:checked')?.value || 'Strict';
  const theme = document.getElementById('theme-select')?.value || 'Dark';
  const fontSize = Number(document.getElementById('font-size')?.value || 14);
  const compact = !!document.getElementById('compact-mode')?.checked;
  const homepage = document.getElementById('homepage-input')?.value?.trim() || 'void://newtab';
  const searchEngine = document.getElementById('search-engine')?.value || 'DuckDuckGo';
  const adblockOn = !!document.getElementById('adblock-toggle')?.checked;
  const trackerOn = !!document.getElementById('tracker-toggle')?.checked;
  const httpsOnly = !!document.getElementById('https-toggle')?.checked;
  const fpOn = !!document.getElementById('fingerprint-toggle')?.checked;
  const doh = document.getElementById('doh-select')?.value ?? '';
  const clearCache = !!document.getElementById('clear-cache-toggle')?.checked;
  const clearHistory = !!document.getElementById('clear-history-toggle')?.checked;

  const next = {
    ...base,
    homepage,
    search_engine: searchEngine,
    theme,
    font_size: fontSize,
    compact_mode: compact,
    security_level: security,
    adblock_enabled: adblockOn,
    tracker_blocking: trackerOn,
    https_policy: httpsOnly ? 'Strict' : 'Prefer',
    dns_over_https: doh || null,
    fingerprint_resistance: {
      ...(base.fingerprint_resistance || {}),
      spoof_canvas: fpOn,
      spoof_webgl: fpOn,
      spoof_audio: fpOn,
      resist_font_enum: fpOn,
    },
    clear_on_exit: {
      ...(base.clear_on_exit || {}),
      cache: clearCache,
      history: clearHistory,
    },
  };

  // Security level presets adjust related policies
  if (security === 'Standard') {
    next.cookie_policy = 'AllowAll';
    next.webrtc_policy = 'Default';
  } else if (security === 'Strict') {
    next.cookie_policy = 'BlockThirdParty';
    next.webrtc_policy = 'DisableNonProxied';
  } else if (security === 'Paranoid') {
    next.cookie_policy = 'BlockAll';
    next.webrtc_policy = 'Disabled';
    next.fingerprint_resistance = {
      ...next.fingerprint_resistance,
      spoof_canvas: true,
      spoof_webgl: true,
      spoof_audio: true,
      resist_font_enum: true,
      uniform_navigator: true,
    };
  }

  try {
    await invoke('update_config', { newConfig: next });
    appConfig = next;
    applyAppearance(appConfig);
    await reportChromeHeight();
    setStatus('Saved — will persist after restart', true);
    // Verify round-trip from disk-backed state
    const verified = await invoke('get_config');
    if (verified && enumName(verified.theme, '') === theme) {
      setStatus('Saved', true);
    }
  } catch (e) {
    console.error(e);
    setStatus(`Save failed: ${e}`, false);
  }
}

// ── Window controls ──────────────────────────────

function wireWindowControls() {
  const win = getCurrentWindow ? getCurrentWindow() : null;
  document.getElementById('win-min')?.addEventListener('click', () => win?.minimize());
  document.getElementById('win-max')?.addEventListener('click', () => win?.toggleMaximize());
  document.getElementById('win-close')?.addEventListener('click', () => win?.close());
}

// ── Events ───────────────────────────────────────

urlBar.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    navigate(urlBar.value);
    urlBar.blur();
  }
});
urlBar.addEventListener('focus', () => urlBar.select());

searchInput?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const q = searchInput.value.trim();
    if (q) {
      navigate(q);
      searchInput.value = '';
    }
  }
});

newTabBtn.addEventListener('click', () => createTab());
settingsBtn.addEventListener('click', async () => {
  if (settingsOpen) {
    await closeSettings();
  } else {
    await openSettings();
  }
});
document.getElementById('settings-close-btn')?.addEventListener('click', () => closeSettings());
document.getElementById('settings-done-btn')?.addEventListener('click', () => closeSettings());

backBtn.addEventListener('click', async () => {
  if (!activeTabId || !browsingActive) return;
  try { await invoke('browser_go_back', { tabId: activeTabId }); } catch (_) { /* ok */ }
});
forwardBtn.addEventListener('click', async () => {
  if (!activeTabId || !browsingActive) return;
  try { await invoke('browser_go_forward', { tabId: activeTabId }); } catch (_) { /* ok */ }
});
reloadBtn.addEventListener('click', async () => {
  if (!activeTabId) return;
  const tab = tabs.find((t) => t.id === activeTabId);
  if (tab && !isInternalUrl(tab.url)) {
    try { await invoke('browser_reload', { tabId: activeTabId }); } catch (_) { /* ok */ }
  }
});

shieldBtn.addEventListener('click', async () => {
  try {
    const stats = await invoke('get_stats');
    if (stats) updateBlockCount(stats.total_blocked ?? 0);
  } catch (_) { /* ok */ }
  shieldBtn.classList.toggle('active');
});

document.getElementById('settings-save-btn')?.addEventListener('click', saveSettings);

document.getElementById('check-updates-btn')?.addEventListener('click', async () => {
  const status = document.getElementById('update-status');
  const btn = document.getElementById('check-updates-btn');
  if (status) status.textContent = 'Checking…';
  if (btn) btn.disabled = true;
  try {
    const result = await invoke('check_for_updates');
    if (status) {
      status.textContent = result?.message
        || (result?.updateAvailable ? `Update ${result.latestVersion} available` : 'Up to date');
    }
  } catch (e) {
    console.error('[void] update check failed', e);
    if (status) status.textContent = `Check failed: ${e}`;
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById('theme-select')?.addEventListener('change', (e) => {
  const value = e.target.value;
  document.documentElement.setAttribute('data-theme', themeCssName(value));
  bindSystemThemeListener(isSystemTheme(value));
});
document.getElementById('font-size')?.addEventListener('input', (e) => {
  const v = e.target.value;
  const display = document.getElementById('font-size-display');
  if (display) display.textContent = `${v}px`;
  document.body.style.fontSize = `${v}px`;
});
document.getElementById('compact-mode')?.addEventListener('change', (e) => {
  document.body.classList.toggle('compact', e.target.checked);
  reportChromeHeight();
});

document.addEventListener('keydown', (e) => {
  // Never intercept clipboard shortcuts — let the focused input / WebView2 handle them.
  const key = e.key.toLowerCase();
  if (e.ctrlKey && (key === 'c' || key === 'v' || key === 'x' || key === 'a')) {
    return;
  }
  if (e.key === 'Escape' && settingsOpen) {
    e.preventDefault();
    closeSettings();
    return;
  }
  if (e.ctrlKey && e.key === 't') {
    e.preventDefault();
    createTab();
  }
  if (e.ctrlKey && e.key === 'w') {
    e.preventDefault();
    if (activeTabId) closeTab(activeTabId);
  }
  if (e.ctrlKey && e.key === 'l') {
    e.preventDefault();
    urlBar.focus();
    urlBar.select();
  }
  if (e.ctrlKey && e.key === ',') {
    e.preventDefault();
    settingsBtn.click();
  }
  if (e.ctrlKey && e.key === 'r') {
    e.preventDefault();
    reloadBtn.click();
  }
  if (e.ctrlKey && e.key === 'Tab') {
    e.preventDefault();
    const idx = tabs.findIndex((t) => t.id === activeTabId);
    if (idx < 0 || !tabs.length) return;
    const nextIdx = e.shiftKey
      ? (idx - 1 + tabs.length) % tabs.length
      : (idx + 1) % tabs.length;
    switchTab(tabs[nextIdx].id);
  }
});

// ── Chrome context menu (Cut / Copy / Paste / Select All) ──

function isEditableTarget(el) {
  if (!el || el.nodeType !== 1) return null;
  if (el.matches?.('input:not([type=checkbox]):not([type=range]):not([type=button]):not([type=submit]), textarea')) {
    return el;
  }
  if (el.isContentEditable) return el;
  return el.closest?.('input, textarea, [contenteditable="true"]') || null;
}

function wireChromeContextMenu() {
  let menu = document.getElementById('chrome-ctx-menu');
  if (!menu) {
    menu = document.createElement('div');
    menu.id = 'chrome-ctx-menu';
    menu.hidden = true;
    menu.innerHTML = `
      <button type="button" data-cmd="cut">Cut</button>
      <button type="button" data-cmd="copy">Copy</button>
      <button type="button" data-cmd="paste">Paste</button>
      <button type="button" data-cmd="selectAll">Select All</button>
      <hr>
      <button type="button" data-cmd="reload">Reload</button>
    `;
    document.body.appendChild(menu);
  }

  let targetEl = null;

  const hide = () => {
    menu.hidden = true;
    targetEl = null;
  };

  const showAt = (x, y, el) => {
    targetEl = el;
    menu.hidden = false;
    const pad = 6;
    const rect = menu.getBoundingClientRect();
    const left = Math.min(x, window.innerWidth - rect.width - pad);
    const top = Math.min(y, window.innerHeight - rect.height - pad);
    menu.style.left = `${Math.max(pad, left)}px`;
    menu.style.top = `${Math.max(pad, top)}px`;
  };

  document.addEventListener('contextmenu', (e) => {
    const editable = isEditableTarget(e.target);
    if (!editable) {
      hide();
      return; // non-editable chrome: no custom menu
    }
    e.preventDefault();
    showAt(e.clientX, e.clientY, editable);
  });

  document.addEventListener('click', (e) => {
    if (!menu.hidden && !menu.contains(e.target)) hide();
  });
  window.addEventListener('blur', hide);
  window.addEventListener('resize', hide);

  menu.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-cmd]');
    if (!btn) return;
    const cmd = btn.dataset.cmd;
    const el = targetEl;
    hide();

    if (cmd === 'reload') {
      reloadBtn.click();
      return;
    }
    if (!el) return;
    el.focus();

    try {
      if (cmd === 'cut') {
        if (typeof el.selectionStart === 'number') {
          const start = el.selectionStart;
          const end = el.selectionEnd;
          const selected = el.value.slice(start, end);
          if (selected) {
            await navigator.clipboard.writeText(selected);
            el.setRangeText('', start, end, 'start');
            el.dispatchEvent(new Event('input', { bubbles: true }));
          }
        } else {
          document.execCommand('cut');
        }
      } else if (cmd === 'copy') {
        if (typeof el.selectionStart === 'number') {
          const selected = el.value.slice(el.selectionStart, el.selectionEnd);
          if (selected) await navigator.clipboard.writeText(selected);
        } else {
          document.execCommand('copy');
        }
      } else if (cmd === 'paste') {
        const text = await navigator.clipboard.readText();
        if (typeof el.selectionStart === 'number') {
          const start = el.selectionStart;
          const end = el.selectionEnd;
          el.setRangeText(text, start, end, 'end');
          el.dispatchEvent(new Event('input', { bubbles: true }));
        } else {
          document.execCommand('insertText', false, text);
        }
      } else if (cmd === 'selectAll') {
        if (typeof el.select === 'function') el.select();
        else document.execCommand('selectAll');
      }
    } catch (err) {
      console.warn('[void] context menu action failed', err);
      // Fallback to legacy execCommand when clipboard API is blocked.
      try {
        if (cmd === 'cut') document.execCommand('cut');
        else if (cmd === 'copy') document.execCommand('copy');
        else if (cmd === 'paste') document.execCommand('paste');
        else if (cmd === 'selectAll') document.execCommand('selectAll');
      } catch (_) { /* ok */ }
    }
  });
}

async function wireBackendEvents() {
  await listen('tab-navigated', async (event) => {
    const { tabId, url } = event.payload || {};
    let tab = tabs.find((t) => t.id === tabId);
    // Backend may create/navigate tabs before chrome JS knows about them (smoke / races).
    if (!tab && tabId) {
      try {
        const listed = await invoke('list_tabs');
        if (Array.isArray(listed) && listed.length) {
          tabs = listed;
          if (!activeTabId || !tabs.some((t) => t.id === activeTabId)) {
            activeTabId = (listed.find((t) => t.active) || listed[0]).id;
          }
          tab = tabs.find((t) => t.id === tabId);
        }
      } catch (_) { /* ok */ }
    }
    if (!tab) return;
    tab.url = url;
    tab.loading = false;
    if (tabId === activeTabId) {
      urlBar.value = isInternalUrl(url) ? '' : url;
      setSecurityIndicator(url);
      if (url && !isInternalUrl(url) && !settingsOpen) {
        showPage('webview');
        await showContentFor(tabId);
      }
    }
    try {
      tab.title = new URL(url).hostname || tab.title;
    } catch (_) { /* keep */ }
    renderTabs();
  });

  await listen('tab-title-changed', (event) => {
    const { tabId, title, url } = event.payload || {};
    const tab = tabs.find((t) => t.id === tabId);
    if (!tab) return;
    if (title) tab.title = title;
    if (url) tab.url = url;
    if (tabId === activeTabId && url) {
      urlBar.value = url;
      setSecurityIndicator(url);
    }
    renderTabs();
  });

  await listen('block-stats-updated', (event) => {
    const stats = event.payload;
    if (stats && typeof stats.total_blocked === 'number') {
      updateBlockCount(stats.total_blocked);
    }
  });
}

(async function init() {
  wireWindowControls();
  wireChromeContextMenu();
  await wireBackendEvents();

  try {
    appConfig = await invoke('get_config');
    applyAppearance(appConfig);
  } catch (_) {
    applyAppearance({ theme: 'Dark', font_size: 14, compact_mode: false });
  }

  await reportChromeHeight();
  window.addEventListener('resize', () => reportChromeHeight());
  if (typeof ResizeObserver !== 'undefined' && chromeEl) {
    new ResizeObserver(() => reportChromeHeight()).observe(chromeEl);
  }

  // Prefer tabs already created by the backend (e.g. --smoke-test) so chrome
  // init does not clobber a navigated content webview with hide_browser_content.
  let existing = null;
  try {
    existing = await invoke('list_tabs');
  } catch (_) { /* ok */ }

  if (Array.isArray(existing) && existing.length > 0) {
    tabs = existing;
    const active = existing.find((t) => t.active) || existing[0];
    activeTabId = active.id;
    renderTabs();
    await activateTabView(active);
  } else {
    const homepage = appConfig?.homepage;
    if (homepage && homepage !== 'void://newtab' && !homepage.startsWith('void://')) {
      await createTab(homepage);
      await navigate(homepage);
    } else {
      await createTab();
    }
  }

  shieldBtn.classList.add('active');
  try {
    const stats = await invoke('get_stats');
    if (stats) updateBlockCount(stats.total_blocked ?? 0);
  } catch (_) { /* ok */ }

  console.log('[void] Browser ready — settings persist to OS config dir');
})();
