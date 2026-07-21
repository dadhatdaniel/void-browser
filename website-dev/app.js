/* VOID OS — theatrical boot + desktop shell + easter eggs */

(function () {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const params = new URLSearchParams(window.location.search);
  const skipBoot =
    params.has("skip") ||
    params.get("boot") === "0" ||
    window.location.hash === "#desktop" ||
    window.location.hash === "#skip";

  // Prod builds set <body class="is-prod">
  if (document.body.classList.contains("is-prod") || params.get("prod") === "1") {
    document.body.classList.add("is-prod");
  }

  const stages = {
    bios: document.getElementById("stage-bios"),
    load: document.getElementById("stage-load"),
    login: document.getElementById("stage-login"),
    desktop: document.getElementById("stage-desktop"),
  };

  const order = ["bios", "load", "login", "desktop"];
  let current = "bios";
  let advancing = false;
  let timers = [];
  let zTop = 40;

  function clearTimers() {
    timers.forEach((t) => clearTimeout(t));
    timers = [];
  }

  function later(fn, ms) {
    const id = setTimeout(fn, ms);
    timers.push(id);
    return id;
  }

  function showStage(name) {
    current = name;
    clearTimers();
    advancing = false;
    order.forEach((key) => {
      const el = stages[key];
      if (!el) return;
      const on = key === name;
      el.classList.toggle("is-active", on);
      if (on) el.removeAttribute("hidden");
      else el.setAttribute("hidden", "");
    });
    if (name === "desktop") {
      document.body.style.overflow = "hidden";
      openWindow("welcome");
      updateClock();
      maybeShowAssistantOnce();
    }
  }

  function nextStage() {
    if (advancing) return;
    const idx = order.indexOf(current);
    if (idx < 0 || idx >= order.length - 1) return;
    advancing = true;
    const next = order[idx + 1];
    if (next === "load") startLoad();
    else if (next === "login") showStage("login");
    else if (next === "desktop") showStage("desktop");
    else showStage(next);
  }

  function skipToDesktop() {
    clearTimers();
    advancing = false;
    showStage("desktop");
  }

  function skipOrAdvance() {
    if (current === "desktop") return;
    if (current === "login") {
      showStage("desktop");
      return;
    }
    nextStage();
  }

  /* ---------- BIOS (slow / readable) ---------- */
  const biosLines = [
    { t: "VOID BIOS v1.0 — Theatrical Edition", cls: "hi", d: 0 },
    { t: "Copyright (C) 2026 Void Labs — fictional firmware", cls: "dim", d: 420 },
    { t: "", d: 220 },
    { t: "CPU: Local Wrapper Core @ honest MHz", d: 520 },
    { t: "Memory Test:  ", d: 580 },
    { t: "memcheck", special: "mem", d: 0 },
    { t: "", d: 280 },
    { t: "Detecting Primary Master... VOID HD", d: 480 },
    { t: "Detecting Primary Slave... None", cls: "dim", d: 400 },
    { t: "Detecting Secondary Master... Optical (empty)", cls: "dim", d: 400 },
    { t: "Detecting Secondary Slave... None", cls: "dim", d: 360 },
    { t: "", d: 220 },
    { t: "Initializing privacy stack........... OK", cls: "ok", d: 620 },
    { t: "Telemetry module..................... ABSENT", cls: "ok", d: 560 },
    { t: "AI assistant......................... NOT FOUND", cls: "ok", d: 560 },
    { t: "Phone-home continuum................. SEVERED", cls: "ok", d: 580 },
    { t: "Crypto wallet........................ NOT INSTALLED", cls: "ok", d: 520 },
    { t: "News feed / sponsored junk........... BLOCKED", cls: "ok", d: 520 },
    { t: "", d: 240 },
    { t: "Boot Device Priority:", d: 420 },
    { t: "  1. VOID OS (local shell)", d: 360 },
    { t: "  2. Network (disabled)", cls: "dim", d: 320 },
    { t: "  3. Removable (none)", cls: "dim", d: 300 },
    { t: "", d: 240 },
    { t: "Press Esc or click to skip POST", cls: "dim", d: 480 },
    { t: "", d: 200 },
    { t: "Loading VOID OS...", cls: "hi", d: 900 },
  ];

  function runBios() {
    showStage("bios");
    const pre = document.getElementById("bios-text");
    if (!pre) {
      later(() => startLoad(), 600);
      return;
    }
    pre.innerHTML = "";

    if (reduceMotion) {
      biosLines.forEach((line) => {
        if (line.special === "mem") {
          const mem = document.createElement("span");
          mem.className = "ok";
          mem.textContent = "65536 KB OK\n";
          pre.appendChild(mem);
          return;
        }
        const span = document.createElement("span");
        if (line.cls) span.className = line.cls;
        span.textContent = (line.t || "") + "\n";
        pre.appendChild(span);
      });
      later(() => startLoad(), 900);
      return;
    }

    let i = 0;
    function step() {
      if (current !== "bios") return;
      if (i >= biosLines.length) {
        later(() => startLoad(), 1100);
        return;
      }
      const line = biosLines[i++];
      if (line.special === "mem") {
        runMemCheck(pre, () => later(step, 400));
        return;
      }
      const span = document.createElement("span");
      if (line.cls) span.className = line.cls;
      span.textContent = (line.t || "") + "\n";
      pre.appendChild(span);
      later(step, line.d != null ? line.d : 350);
    }
    step();
  }

  function runMemCheck(pre, done) {
    const span = document.createElement("span");
    span.className = "ok";
    pre.appendChild(span);
    let kb = 0;
    const target = 65536;
    const tick = reduceMotion ? 65536 : 2048;
    function bump() {
      if (current !== "bios") return;
      kb = Math.min(target, kb + tick);
      span.textContent = kb + " KB OK";
      if (kb >= target) {
        span.textContent = target + " KB OK\n";
        done();
        return;
      }
      later(bump, 70);
    }
    bump();
  }

  /* ---------- LOAD ---------- */
  function startLoad() {
    showStage("load");
    const fill = document.getElementById("load-bar-fill");
    const bar = document.getElementById("load-bar");
    if (!fill) {
      later(() => showStage("login"), 600);
      return;
    }
    fill.style.width = "0%";
    if (bar) bar.setAttribute("aria-valuenow", "0");

    if (reduceMotion) {
      fill.style.width = "100%";
      later(() => showStage("login"), 600);
      return;
    }

    let pct = 0;
    function tick() {
      if (current !== "load") return;
      pct += pct < 65 ? 1.6 + Math.random() * 2.2 : 0.6 + Math.random() * 1.2;
      if (pct >= 100) {
        pct = 100;
        fill.style.width = "100%";
        if (bar) bar.setAttribute("aria-valuenow", "100");
        later(() => showStage("login"), 800);
        return;
      }
      fill.style.width = pct.toFixed(1) + "%";
      if (bar) bar.setAttribute("aria-valuenow", String(Math.round(pct)));
      later(tick, 110);
    }
    later(tick, 350);
  }

  /* ---------- Login ---------- */
  const loginBtn = document.getElementById("login-enter");
  if (loginBtn) {
    loginBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      showStage("desktop");
    });
  }

  /* ---------- Skip ---------- */
  document.addEventListener("keydown", (e) => {
    if (document.getElementById("overlay-bsod") && !document.getElementById("overlay-bsod").hidden) {
      hideBsod();
      e.preventDefault();
      return;
    }
    if (e.key === "Escape") {
      if (current !== "desktop") {
        e.preventDefault();
        skipToDesktop();
      } else {
        closeStartMenu();
        hideOverlaysSoft();
      }
      return;
    }
    if (current === "login" && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      showStage("desktop");
      return;
    }
    // StickyKeys: Shift spam on desktop
    if (current === "desktop" && (e.key === "Shift" || e.code === "ShiftLeft" || e.code === "ShiftRight")) {
      onShiftSpam();
    }
    // Konami
    if (current === "desktop") onKonami(e);
  });

  ["stage-bios", "stage-load"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("click", (e) => {
      if (e.target.closest("a, button")) return;
      skipOrAdvance();
    });
  });

  const loginStage = stages.login;
  if (loginStage) {
    loginStage.addEventListener("click", (e) => {
      if (e.target.closest("button, a")) return;
      if (e.target === loginStage || e.target.classList.contains("login-bg") || e.target.classList.contains("login-stripe")) {
        showStage("desktop");
      }
    });
  }

  /* ---------- Desktop windows ---------- */
  const winMeta = {
    browser: { title: "Void Browser", el: document.getElementById("win-browser") },
    welcome: { title: "Welcome to VOID OS", el: document.getElementById("win-welcome") },
    wrapper: { title: "The wrapper is the product", el: document.getElementById("win-wrapper") },
    modes: { title: "Efficiency & Performance", el: document.getElementById("win-modes") },
    download: { title: "Acquire Void", el: document.getElementById("win-download") },
    mycomp: { title: "My Computer", el: document.getElementById("win-mycomp") },
    games: { title: "Games", el: document.getElementById("win-games") },
    cmd: { title: "Command Prompt", el: document.getElementById("win-cmd") },
  };

  const defaults = {
    browser: { top: 28, left: 90 },
    welcome: { top: 40, left: 120 },
    wrapper: { top: 52, left: 150 },
    modes: { top: 64, left: 180 },
    download: { top: 76, left: 200 },
    mycomp: { top: 48, left: 160 },
    games: { top: 70, left: 200 },
    cmd: { top: 90, left: 140 },
  };

  function openWindow(id) {
    const meta = winMeta[id];
    if (!meta || !meta.el) return;
    const win = meta.el;
    win.hidden = false;
    win.classList.remove("is-min");
    if (!win.dataset.placed) {
      const d = defaults[id] || { top: 40, left: 120 };
      win.style.top = d.top + "px";
      win.style.left = d.left + "px";
      win.dataset.placed = "1";
    }
    if (id === "cmd") fillCmdFortune(true);
    focusWindow(id);
    syncTaskTabs();
    closeStartMenu();
  }

  function closeWindow(id) {
    const meta = winMeta[id];
    if (!meta || !meta.el) return;
    meta.el.hidden = true;
    meta.el.classList.remove("is-min", "is-max", "is-front");
    syncTaskTabs();
  }

  function minimizeWindow(id) {
    const meta = winMeta[id];
    if (!meta || !meta.el) return;
    meta.el.classList.add("is-min");
    meta.el.classList.remove("is-front");
    syncTaskTabs();
  }

  function toggleMax(id) {
    const meta = winMeta[id];
    if (!meta || !meta.el) return;
    meta.el.classList.toggle("is-max");
    focusWindow(id);
  }

  function focusWindow(id) {
    Object.keys(winMeta).forEach((key) => {
      const el = winMeta[key].el;
      if (!el) return;
      el.classList.toggle("is-front", key === id && !el.hidden && !el.classList.contains("is-min"));
    });
    const el = winMeta[id] && winMeta[id].el;
    if (el) {
      zTop += 1;
      el.style.zIndex = String(zTop);
    }
    syncTaskTabs();
  }

  function syncTaskTabs() {
    const tabs = document.getElementById("task-tabs");
    if (!tabs) return;
    tabs.innerHTML = "";
    Object.keys(winMeta).forEach((id) => {
      const meta = winMeta[id];
      const el = meta.el;
      if (!el || el.hidden) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "task-tab";
      if (el.classList.contains("is-front") && !el.classList.contains("is-min")) {
        btn.classList.add("is-active");
      }
      btn.textContent = meta.title;
      btn.addEventListener("click", () => {
        if (el.classList.contains("is-min")) {
          el.classList.remove("is-min");
          focusWindow(id);
        } else if (el.classList.contains("is-front")) {
          minimizeWindow(id);
        } else {
          focusWindow(id);
        }
      });
      tabs.appendChild(btn);
    });
  }

  document.querySelectorAll("[data-open]").forEach((el) => {
    el.addEventListener("click", (e) => {
      const id = el.getAttribute("data-open");
      if (!id) return;
      e.preventDefault();
      openWindow(id);
    });
  });

  Object.keys(winMeta).forEach((id) => {
    const win = winMeta[id].el;
    if (!win) return;

    win.addEventListener("mousedown", () => focusWindow(id));

    const closeBtn = win.querySelector("[data-close]");
    const minBtn = win.querySelector("[data-min]");
    const maxBtn = win.querySelector("[data-max]");
    if (closeBtn) closeBtn.addEventListener("click", (e) => { e.stopPropagation(); closeWindow(id); });
    if (minBtn) minBtn.addEventListener("click", (e) => { e.stopPropagation(); minimizeWindow(id); });
    if (maxBtn) maxBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleMax(id); });

    const bar = win.querySelector("[data-drag]");
    if (!bar) return;
    let dragging = false;
    let ox = 0, oy = 0;

    bar.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".win-btn")) return;
      if (win.classList.contains("is-max")) return;
      dragging = true;
      focusWindow(id);
      const rect = win.getBoundingClientRect();
      ox = e.clientX - rect.left;
      oy = e.clientY - rect.top;
      bar.setPointerCapture(e.pointerId);
      e.preventDefault();
    });

    bar.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const desk = stages.desktop.getBoundingClientRect();
      let left = e.clientX - desk.left - ox;
      let top = e.clientY - desk.top - oy;
      const maxL = desk.width - 80;
      const maxT = desk.height - 60;
      left = Math.max(-40, Math.min(left, maxL));
      top = Math.max(0, Math.min(top, maxT));
      win.style.left = left + "px";
      win.style.top = top + "px";
    });

    bar.addEventListener("pointerup", () => { dragging = false; });
    bar.addEventListener("pointercancel", () => { dragging = false; });
  });

  /* Browser tabs */
  function showBrowserTab(name) {
    const addrs = { home: "about:void", download: "about:void/download", honest: "about:void/honest.txt" };
    document.querySelectorAll(".browser-tab").forEach((tab) => {
      const on = tab.getAttribute("data-tab") === name;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".browser-page").forEach((page) => {
      const on = page.getAttribute("data-page") === name;
      if (on) page.removeAttribute("hidden");
      else page.setAttribute("hidden", "");
      page.classList.toggle("is-active", on);
    });
    const addr = document.getElementById("browser-addr");
    if (addr) addr.value = addrs[name] || "about:void";
  }

  document.querySelectorAll(".browser-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      showBrowserTab(tab.getAttribute("data-tab") || "home");
      focusWindow("browser");
    });
  });
  document.querySelectorAll("[data-browser-tab]").forEach((el) => {
    el.addEventListener("click", () => {
      openWindow("browser");
      showBrowserTab(el.getAttribute("data-browser-tab") || "home");
    });
  });
  const refreshBtn = document.getElementById("browser-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      const body = document.querySelector("#win-browser .browser-body");
      if (!body) return;
      body.style.opacity = "0.4";
      setTimeout(() => { body.style.opacity = "1"; }, 180);
    });
  }

  /* Start menu */
  const startBtn = document.getElementById("start-btn");
  const startMenu = document.getElementById("start-menu");
  let startClicks = 0;

  function closeStartMenu() {
    if (!startMenu) return;
    startMenu.hidden = true;
    if (startBtn) startBtn.setAttribute("aria-expanded", "false");
  }

  function toggleStartMenu() {
    if (!startMenu) return;
    const open = startMenu.hidden;
    startMenu.hidden = !open;
    if (startBtn) startBtn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  if (startBtn) {
    startBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      startClicks += 1;
      if (startClicks === 7) {
        startClicks = 0;
        showToast("Start menu secret: Konami still works. So does Shift×5.");
        showBsod();
        return;
      }
      if (startClicks > 12) startClicks = 0;
      toggleStartMenu();
    });
  }

  document.addEventListener("click", (e) => {
    if (!startMenu || startMenu.hidden) return;
    if (e.target.closest("#start-menu") || e.target.closest("#start-btn")) return;
    closeStartMenu();
  });

  const rebootBtn = document.getElementById("reboot-btn");
  if (rebootBtn) {
    rebootBtn.addEventListener("click", () => {
      closeStartMenu();
      Object.keys(winMeta).forEach(closeWindow);
      if (window.location.hash === "#desktop" || window.location.hash === "#skip") {
        history.replaceState(null, "", window.location.pathname + window.location.search);
      }
      runBios();
    });
  }

  const startCmd = document.getElementById("start-cmd");
  if (startCmd) startCmd.addEventListener("click", () => openWindow("cmd"));

  const startAsst = document.getElementById("start-assistant");
  if (startAsst) {
    startAsst.addEventListener("click", () => {
      closeStartMenu();
      showAssistant(true);
    });
  }

  /* Clock */
  function updateClock() {
    const el = document.getElementById("tray-clock");
    if (!el) return;
    const d = new Date();
    el.textContent = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  setInterval(updateClock, 15000);
  updateClock();

  /* ---------- Easter eggs ---------- */
  const fortunes = [
    "Fortune: The best telemetry is no telemetry.",
    "Fortune: Chromium forks multiply. Wrappers stay lean.",
    "Fortune: Your data left the building in 2005. Void locks the door.",
    "Fortune: AI cannot help you browse if it isn't invited.",
    "Fortune: 65536 KB of honesty loaded successfully.",
    "Fortune: StickyKeys called. It wants its parody back.",
    "Fortune: Recycle Bin contains 0 trackers, 0 regrets.",
  ];

  function fillCmdFortune(fresh) {
    const out = document.getElementById("cmd-out");
    if (!out) return;
    const f = fortunes[Math.floor(Math.random() * fortunes.length)];
    const lines = [
      "Microsoft Windows XP [Version VOID.1.0]",
      "(C) Void Labs — not affiliated with Microsoft.",
      "",
      "C:\\Users\\Guest> fortune",
      f,
      "",
      "C:\\Users\\Guest> echo %TELEMETRY%",
      "ABSENT",
      "",
      "C:\\Users\\Guest>_",
    ];
    out.textContent = lines.join("\n");
    if (fresh) out.scrollTop = 0;
  }

  // Recycle bin double-click
  const recycle = document.getElementById("recycle-bin");
  if (recycle) {
    recycle.addEventListener("dblclick", (e) => {
      e.preventDefault();
      showEmptyVoid();
    });
    recycle.addEventListener("click", (e) => {
      // single click: mild toast
      if (e.detail === 1) {
        setTimeout(() => {
          if (e.detail === 1) showToast("Recycle Bin — double-click to empty the void");
        }, 280);
      }
    });
  }

  function showEmptyVoid() {
    const el = document.getElementById("overlay-empty");
    if (el) el.hidden = false;
  }
  function hideEmptyVoid() {
    const el = document.getElementById("overlay-empty");
    if (el) el.hidden = true;
  }
  const emptyYes = document.getElementById("empty-yes");
  const emptyNo = document.getElementById("empty-no");
  if (emptyYes) {
    emptyYes.addEventListener("click", () => {
      hideEmptyVoid();
      showToast("Void emptied. 0 bytes freed. Conscience: +1.");
    });
  }
  if (emptyNo) emptyNo.addEventListener("click", hideEmptyVoid);

  // Volume tray
  const trayVol = document.getElementById("tray-vol");
  if (trayVol) {
    let volClicks = 0;
    trayVol.addEventListener("click", () => {
      volClicks += 1;
      if (volClicks === 1) showToast("Volume: muted trackers, unmuted honesty.");
      else if (volClicks === 3) {
        showToast("System sound: tada.wav not found — playing silence.exe");
        volClicks = 0;
      }
    });
  }

  function showToast(msg) {
    const el = document.getElementById("tray-toast");
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => { el.hidden = true; }, 3200);
  }

  // BSOD
  function showBsod() {
    const el = document.getElementById("overlay-bsod");
    if (el) el.hidden = false;
  }
  function hideBsod() {
    const el = document.getElementById("overlay-bsod");
    if (el) el.hidden = true;
  }
  const bsod = document.getElementById("overlay-bsod");
  if (bsod) {
    bsod.addEventListener("click", hideBsod);
  }

  // StickyKeys
  let shiftCount = 0;
  let shiftTimer = null;
  let stickyShown = false;
  function onShiftSpam() {
    if (stickyShown) return;
    shiftCount += 1;
    clearTimeout(shiftTimer);
    shiftTimer = setTimeout(() => { shiftCount = 0; }, 2000);
    if (shiftCount >= 5) {
      shiftCount = 0;
      stickyShown = true;
      const el = document.getElementById("overlay-sticky");
      if (el) el.hidden = false;
    }
  }
  function hideSticky() {
    const el = document.getElementById("overlay-sticky");
    if (el) el.hidden = true;
  }
  const stickyYes = document.getElementById("sticky-yes");
  const stickyNo = document.getElementById("sticky-no");
  if (stickyYes) {
    stickyYes.addEventListener("click", () => {
      hideSticky();
      showToast("StickyKeys: enabled in spirit. Keys remain unsticky.");
      setTimeout(() => { stickyShown = false; }, 8000);
    });
  }
  if (stickyNo) {
    stickyNo.addEventListener("click", () => {
      hideSticky();
      setTimeout(() => { stickyShown = false; }, 8000);
    });
  }

  // Konami → games + confetti-free toast
  const konami = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "b", "a"];
  let konamiIdx = 0;
  function onKonami(e) {
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const expect = konami[konamiIdx];
    if (key === expect || (expect.length === 1 && key === expect)) {
      konamiIdx += 1;
      if (konamiIdx >= konami.length) {
        konamiIdx = 0;
        openWindow("games");
        showToast("Konami accepted. Games folder unlocked (parody).");
      }
    } else {
      konamiIdx = key === konami[0] ? 1 : 0;
    }
  }

  // VOID Assistant (paperclip-adjacent, no Clippy IP)
  function showAssistant(force) {
    const el = document.getElementById("void-assistant");
    if (!el) return;
    const text = document.getElementById("assistant-text");
    const lines = [
      "It looks like you’re browsing without tracking. Want help staying that way?",
      "I see you’re about to open a browser. Have you tried… just opening Void?",
      "Tip: Esc skips the boot. Your time is finite. Telemetry isn’t invited.",
    ];
    if (text) text.textContent = lines[Math.floor(Math.random() * lines.length)];
    el.hidden = false;
    if (!force) {
      try { sessionStorage.setItem("void-asst-shown", "1"); } catch (_) {}
    }
  }
  function maybeShowAssistantOnce() {
    try {
      if (sessionStorage.getItem("void-asst-shown")) return;
    } catch (_) {}
    later(() => showAssistant(false), 4500);
  }
  const asstDismiss = document.getElementById("assistant-dismiss");
  if (asstDismiss) {
    asstDismiss.addEventListener("click", () => {
      const el = document.getElementById("void-assistant");
      if (el) el.hidden = true;
    });
  }

  function hideOverlaysSoft() {
    hideEmptyVoid();
    hideSticky();
    const asst = document.getElementById("void-assistant");
    if (asst) asst.hidden = true;
  }

  /* releases.json */
  function applyReleases(data) {
    if (!data || !data.assets) return;
    ["download-release-meta", "browser-release-meta"].forEach((id) => {
      const meta = document.getElementById(id);
      if (meta && data.tag) {
        const page = data.page || data.latest_page || "https://github.com/dadhatdaniel/void-browser/releases/latest";
        meta.innerHTML = 'Latest: <a href="' + page + '" rel="noopener noreferrer">' + data.tag + "</a>";
      }
    });
    document.querySelectorAll("[data-asset]").forEach((el) => {
      const key = el.getAttribute("data-asset");
      const asset = data.assets[key];
      if (asset && asset.url) {
        el.setAttribute("href", asset.url);
        el.setAttribute("download", asset.name || "");
      }
    });
    document.querySelectorAll("[data-size-for]").forEach((el) => {
      const key = el.getAttribute("data-size-for");
      const asset = data.assets[key];
      if (asset && asset.size_label) el.textContent = asset.size_label;
    });
  }

  fetch("/releases.json")
    .then((r) => (r.ok ? r.json() : null))
    .then(applyReleases)
    .catch(() => {});

  /* Boot */
  if (skipBoot) {
    showStage("desktop");
  } else {
    runBios();
  }
})();
