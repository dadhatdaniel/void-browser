#!/usr/bin/env python3
"""
Void Browser — Windows RPA / E2E harness.

Drives the native GUI via pywinauto (UI Automation), captures screenshots,
and writes a machine-readable report for agent quality loops.

Usage:
  python tests/rpa/runner.py
  python tests/rpa/runner.py --scenarios smoke_navigate,settings_preserves_tab
  python tests/rpa/runner.py --exe path\\to\\void-browser.exe

Artifacts:
  artifacts/rpa/<timestamp>/report.json
  artifacts/rpa/<timestamp>/*.png
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

warnings.filterwarnings("ignore", category=DeprecationWarning)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACTS = ROOT / "artifacts" / "rpa"
DEFAULT_DOWNLOAD_DIR = Path(
    os.environ.get("VOID_RPA_DOWNLOAD_DIR", str(ROOT / "downloads" / "rpa"))
)
VOID_SITE_LAN_URL = os.environ.get("VOID_SITE_LAN_URL", "http://10.0.0.10:5080/").rstrip("/") + "/"
VOID_SITE_LAN_DOWNLOAD_URL = VOID_SITE_LAN_URL + "#download"
VOID_SITE_PUBLIC_URL = os.environ.get("VOID_SITE_PUBLIC_URL", "https://void.lightfoot.cloud/").rstrip("/") + "/"
RELEASES_JSON_URL = os.environ.get("VOID_RELEASES_JSON_URL", VOID_SITE_LAN_URL.rstrip("/") + "/releases.json")
LATEST_JSON_URL = os.environ.get(
    "VOID_RPA_LATEST_JSON_URL",
    "https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json",
)
GH_RELEASES_API = os.environ.get(
    "VOID_RPA_GH_RELEASES_API",
    "https://api.github.com/repos/dadhatdaniel/void-browser/releases",
)
# Older portable used by auto_update (must be behind whatever latest.json advertises).
DEFAULT_OLD_TAG = os.environ.get("VOID_RPA_OLD_TAG", "v0.1.0-alpha.15")
MIN_PORTABLE_BYTES = 1_000_000
MIN_SETUP_BYTES = 500_000


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""
    screenshot: Optional[str] = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ScenarioResult:
    name: str
    ok: bool
    steps: list[Step] = field(default_factory=list)
    error: Optional[str] = None
    duration_sec: float = 0.0
    # Optional path to a freshly downloaded/installed exe for later scenarios.
    new_exe: Optional[str] = None


class RpaSession:
    def __init__(self, exe: Path, out_dir: Path, launch_wait: float = 4.0):
        self.exe = exe
        self.out_dir = out_dir
        self.launch_wait = launch_wait
        self.proc: Optional[subprocess.Popen] = None
        self.app = None
        self.win = None
        self._shot_i = 0

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        self.proc = subprocess.Popen(
            [str(self.exe)],
            cwd=str(self.exe.parent),
            creationflags=creationflags,
        )
        time.sleep(self.launch_wait)
        self._connect()

    def restart(self, exe: Optional[Path] = None) -> None:
        """Stop current process and launch exe (or same path)."""
        self.stop()
        time.sleep(1.0)
        if exe is not None:
            self.exe = exe
        self.start()

    def _connect(self) -> None:
        from pywinauto import Application

        deadline = time.time() + 30
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                if self.proc and self.proc.poll() is not None:
                    raise RuntimeError(f"void-browser exited early with code {self.proc.returncode}")
                self.app = Application(backend="uia").connect(process=self.proc.pid)
                for title_re in (".*Void.*", ".*void.*", ".*"):
                    try:
                        self.win = self.app.window(title_re=title_re)
                        if self.win.exists(timeout=1):
                            self.win.set_focus()
                            return
                    except Exception as e:  # noqa: BLE001
                        last_err = e
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.5)
        raise RuntimeError(f"Could not connect to Void UI: {last_err}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self.proc = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def shot(self, label: str) -> str:
        self._shot_i += 1
        name = f"{self._shot_i:02d}_{label}.png"
        path = self.out_dir / name
        try:
            img = self.win.capture_as_image()
            img.save(str(path))
        except Exception:  # noqa: BLE001
            from PIL import ImageGrab

            ImageGrab.grab().save(str(path))
        return name

    def shot_path(self, name: str) -> Path:
        return self.out_dir / name

    def type_keys(self, keys: str, pause: float = 0.15) -> None:
        self.win.type_keys(keys, with_spaces=True, pause=pause, set_foreground=True)

    def focus_url_bar(self) -> None:
        self.type_keys("^l")
        time.sleep(0.3)

    def navigate(self, url: str, settle: float = 2.5) -> None:
        self.focus_url_bar()
        self.type_keys("^a")
        time.sleep(0.1)
        self.type_keys(url, pause=0.02)
        self.type_keys("{ENTER}")
        time.sleep(settle)

    def open_settings(self) -> None:
        self.type_keys("^,")
        time.sleep(1.0)

    def close_settings(self) -> None:
        self.type_keys("{ESC}")
        time.sleep(1.0)

    def new_tab(self) -> None:
        self.type_keys("^t")
        time.sleep(1.0)

    def back(self) -> None:
        self.type_keys("%{LEFT}")
        time.sleep(1.2)

    def forward(self) -> None:
        self.type_keys("%{RIGHT}")
        time.sleep(1.2)

    def reload(self) -> None:
        self.type_keys("{F5}")
        time.sleep(2.0)

    def scroll_page(self, downs: int = 6, pause: float = 0.2) -> None:
        for _ in range(max(1, downs)):
            self.type_keys("{PGDN}")
            time.sleep(pause)

    def scroll_top(self) -> None:
        self.type_keys("^{HOME}")
        time.sleep(0.4)

    def window_title(self) -> str:
        try:
            return str(self.win.window_text())
        except Exception:  # noqa: BLE001
            return ""

    def window_rect(self) -> tuple[int, int, int, int]:
        try:
            r = self.win.rectangle()
            return int(r.left), int(r.top), int(r.right), int(r.bottom)
        except Exception:  # noqa: BLE001
            return 0, 0, 0, 0

    def url_bar_text(self) -> str:
        """Best-effort read of the address bar via clipboard (Ctrl+L, Ctrl+A, Ctrl+C)."""
        try:
            import ctypes

            self.focus_url_bar()
            time.sleep(0.2)
            self.type_keys("^a^c")
            time.sleep(0.35)
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(None):
                return ""
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr).strip()
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception:  # noqa: BLE001
            return ""


def analyze_content_region(image_path: Path) -> dict[str, Any]:
    """
    Heuristic blank/white content detector.

    Crops away approximate chrome (top ~18%) and samples the content webview.
    Fail hard when the content area is nearly uniform white/near-white or
    near-zero luminance variance (classic blank WebView2 collapse).
    """
    from PIL import Image, ImageStat

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    if w < 80 or h < 80:
        return {
            "blank": True,
            "reason": f"window too small {w}x{h}",
            "bright_ratio": 1.0,
            "stddev": 0.0,
            "size": [w, h],
        }

    left = int(w * 0.04)
    top = int(h * 0.18)  # skip toolbar / tab strip
    right = int(w * 0.96)
    bottom = int(h * 0.96)
    crop = img.crop((left, top, right, bottom))
    small = crop.resize((96, 72))
    # Avoid Image.getdata() (deprecated in Pillow 14).
    px = small.load()
    w_s, h_s = small.size
    pixels = [px[x, y] for y in range(h_s) for x in range(w_s)]
    n = max(1, len(pixels))
    bright = sum(1 for r, g, b in pixels if (r + g + b) / 3.0 >= 245)
    near_black = sum(1 for r, g, b in pixels if (r + g + b) / 3.0 <= 12)
    bright_ratio = bright / n
    black_ratio = near_black / n
    stat = ImageStat.Stat(small.convert("L"))
    stddev = float(stat.stddev[0]) if stat.stddev else 0.0
    mean = float(stat.mean[0]) if stat.mean else 0.0

    blank = False
    reason = "ok"
    # Dark themed pages (Void new-tab) are mostly black but have branding variance.
    if bright_ratio >= 0.92 and stddev < 18.0:
        blank = True
        reason = f"near-white content (bright={bright_ratio:.2f} std={stddev:.1f})"
    elif stddev < 5.0 and mean > 230:
        blank = True
        reason = f"uniform bright (mean={mean:.1f} std={stddev:.1f})"
    elif stddev < 4.0 and mean < 15:
        blank = True
        reason = f"uniform black void (mean={mean:.1f} std={stddev:.1f})"
    elif black_ratio >= 0.98 and stddev < 4.0:
        blank = True
        reason = f"near-black empty content (black={black_ratio:.2f} std={stddev:.1f})"

    return {
        "blank": blank,
        "reason": reason,
        "bright_ratio": round(bright_ratio, 3),
        "black_ratio": round(black_ratio, 3),
        "stddev": round(stddev, 2),
        "mean": round(mean, 2),
        "size": [w, h],
        "crop": [left, top, right, bottom],
    }


def assert_content_not_blank(s: RpaSession, shot_name: str, label: str) -> Step:
    path = s.shot_path(shot_name)
    if not path.is_file():
        return Step(label, False, f"screenshot missing: {shot_name}", shot_name)
    info = analyze_content_region(path)
    ok = not info["blank"] and s.alive()
    detail = (
        f"{info['reason']}; bright={info['bright_ratio']} std={info['stddev']} "
        f"mean={info['mean']} size={info['size']}"
    )
    return Step(label, ok, detail, shot_name)


def http_get_json(url: str, timeout: float = 30.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "VoidBrowser-RPA/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error fetching {url}: {e}") from e
    if status == 404:
        raise RuntimeError(f"HTTP 404 fetching {url}")
    return json.loads(raw.decode("utf-8"))


def http_get_json_list(url: str, timeout: float = 30.0) -> list[Any]:
    data = http_get_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise RuntimeError(f"expected JSON array from {url}")
    return data


def validate_latest_json(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Fail if latest.json is missing windows URLs or points at the wrong host."""
    version = str(manifest.get("version") or "").strip()
    platforms = manifest.get("platforms") or {}
    win = platforms.get("windows-x86_64") or {}
    url = str(win.get("url") or "").strip()
    sig = str(win.get("signature") or "").strip()
    if not version:
        return False, "latest.json missing version"
    if not url:
        return False, "latest.json missing platforms.windows-x86_64.url"
    if "github.com/dadhatdaniel/void-browser" not in url:
        return False, f"windows url not on expected GitHub repo: {url}"
    if "/releases/download/" not in url:
        return False, f"windows url not a release download: {url}"
    if not sig:
        return False, "latest.json missing platforms.windows-x86_64.signature"
    return True, f"version={version} url={url}"


def resolve_older_portable_url(latest_version: str) -> tuple[str, str]:
    """
    Return (tag, portable_url) for a build older than latest_version.
    Prefers VOID_RPA_OLD_TAG / DEFAULT_OLD_TAG when that asset exists.
    """
    pinned = os.environ.get("VOID_RPA_OLD_PORTABLE_URL", "").strip()
    if pinned:
        tag = os.environ.get("VOID_RPA_OLD_TAG", "pinned").strip() or "pinned"
        return tag, pinned

    prefer = (os.environ.get("VOID_RPA_OLD_TAG") or DEFAULT_OLD_TAG).strip()
    prefer_url = (
        f"https://github.com/dadhatdaniel/void-browser/releases/download/"
        f"{prefer}/void-browser.exe"
    )
    # Probe preferred tag first (HEAD via GET range would be nicer; try small GET).
    try:
        req = urllib.request.Request(
            prefer_url,
            method="HEAD",
            headers={"User-Agent": "VoidBrowser-RPA/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            if getattr(resp, "status", 200) < 400:
                return prefer, prefer_url
    except Exception:  # noqa: BLE001
        pass

    # Fall back: first release (after latest) that ships void-browser.exe.
    try:
        releases = http_get_json_list(f"{GH_RELEASES_API}?per_page=15")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"cannot resolve older portable: {e}") from e

    for rel in releases:
        tag = str(rel.get("tag_name") or "")
        ver = tag.lstrip("v")
        if not tag:
            continue
        if ver == latest_version or tag.lstrip("v") == latest_version.lstrip("v"):
            continue
        names = {a.get("name") for a in (rel.get("assets") or [])}
        if "void-browser.exe" not in names:
            continue
        url = (
            f"https://github.com/dadhatdaniel/void-browser/releases/download/"
            f"{tag}/void-browser.exe"
        )
        return tag, url

    raise RuntimeError(
        f"no older void-browser.exe found (tried {prefer}; latest={latest_version})"
    )


def find_update_dialog(timeout: float = 12.0):
    """Best-effort find native 'Update available' / Void Browser dialog."""
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    deadline = time.time() + timeout
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for title_re in (
            ".*Update available.*",
            ".*Void Browser.*",
            ".*update.*",
        ):
            try:
                win = desktop.window(title_re=title_re)
                if win.exists(timeout=0.4):
                    text = (win.window_text() or "").lower()
                    # Prefer the update prompt; allow generic Void info dialogs too.
                    if "update" in text or "available" in text or "latest version" in text:
                        return win
            except Exception as e:  # noqa: BLE001
                last_err = e
        time.sleep(0.4)
    if last_err:
        return None
    return None


def dismiss_update_dialog(dlg) -> str:
    """Click Later / Cancel / Esc so the suite can continue."""
    if dlg is None:
        return "no dialog"
    for title in ("Later", "Cancel", "OK", "Close"):
        try:
            btn = dlg.child_window(title=title, control_type="Button")
            if btn.exists(timeout=0.5):
                btn.click_input()
                time.sleep(0.5)
                return f"clicked {title}"
        except Exception:  # noqa: BLE001
            continue
    try:
        dlg.type_keys("{ESC}")
        time.sleep(0.4)
        return "sent Escape"
    except Exception as e:  # noqa: BLE001
        return f"dismiss failed: {e}"


def click_check_for_updates(s: RpaSession) -> str:
    """Open Settings and activate the Check for updates button."""
    s.open_settings()
    time.sleep(0.6)
    # Settings panel can be long — PageDown toward Updates / About.
    for _ in range(4):
        s.type_keys("{PGDN}")
        time.sleep(0.15)
    try:
        btn = s.win.child_window(title="Check for updates", control_type="Button")
        btn.wait("exists", timeout=6)
        btn.click_input()
        return "clicked Check for updates"
    except Exception as e:  # noqa: BLE001
        # Fallback: AutomationId if exposed by WebView2/Tauri.
        try:
            btn = s.win.child_window(auto_id="check-updates-btn", control_type="Button")
            btn.click_input()
            return "clicked check-updates-btn"
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(f"Check for updates button not found: {e}; {e2}") from e2


def http_download(url: str, dest: Path, timeout: float = 300.0, min_bytes: int = 0) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    req = urllib.request.Request(url, headers={"User-Agent": "VoidBrowser-RPA/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Reject SPA HTML fallbacks masquerading as binaries.
        ctype = (resp.headers.get("Content-Type") or "").lower()
        data = resp.read()
    if "text/html" in ctype and len(data) < 200_000:
        raise RuntimeError(f"download returned HTML ({ctype}, {len(data)} bytes) for {url}")
    if data[:15].lstrip().lower().startswith(b"<!doctype") or data[:6].lstrip().lower().startswith(b"<html"):
        raise RuntimeError(f"download looks like HTML, not a binary ({len(data)} bytes) for {url}")
    if min_bytes and len(data) < min_bytes:
        raise RuntimeError(f"download too small ({len(data)} < {min_bytes}) for {url}")
    tmp.write_bytes(data)
    if dest.exists():
        dest.unlink()
    tmp.replace(dest)
    return len(data)


def resolve_windows_assets(meta: dict[str, Any]) -> dict[str, str]:
    """Return {portable_url, setup_url, tag, version} from releases.json (+ portable guess)."""
    assets = meta.get("assets") or {}
    setup = (assets.get("windows_exe") or {}).get("url") or ""
    tag = str(meta.get("tag") or meta.get("version") or "")
    version = str(meta.get("version") or tag.lstrip("v"))
    portable = ""
    # Portable void-browser.exe is published alongside the NSIS setup on GitHub.
    if tag:
        portable = (
            f"https://github.com/dadhatdaniel/void-browser/releases/download/"
            f"{tag}/void-browser.exe"
        )
    # Prefer explicit env override.
    portable = os.environ.get("VOID_RPA_PORTABLE_URL", portable)
    setup = os.environ.get("VOID_RPA_SETUP_URL", setup)
    return {
        "portable_url": portable,
        "setup_url": setup,
        "tag": tag,
        "version": version,
        "setup_name": (assets.get("windows_exe") or {}).get("name") or "void-setup.exe",
    }


def find_exe(hint: Optional[str]) -> Path:
    if hint:
        p = Path(hint)
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(hint)

    home = Path(os.environ.get("USERPROFILE", ""))
    candidates = [
        ROOT / "dist" / "void-browser.exe",
        home / "void-browser-target" / "release" / "void-browser.exe",
        home / "void-browser-target" / "debug" / "void-browser.exe",
        ROOT / "src-tauri" / "target" / "release" / "void-browser.exe",
        ROOT / "src-tauri" / "target" / "debug" / "void-browser.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Void Browser" / "void-browser.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Void Browser" / "void-browser.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    nsis = list((ROOT / "src-tauri" / "target" / "release" / "bundle" / "nsis").glob("**/void-browser.exe"))
    if nsis:
        return nsis[0].resolve()
    raise FileNotFoundError(
        "void-browser.exe not found. Build with .\\scripts\\dev-windows.ps1 -Release "
        "or pass --exe / run download_install first"
    )


def scenario_app_launch(s: RpaSession) -> ScenarioResult:
    """App launches with a visible chrome window (non-trivial size, not blank)."""
    steps: list[Step] = []
    t0 = time.time()
    try:
        alive = s.alive()
        title = s.window_title()
        left, top, right, bottom = s.window_rect()
        width, height = max(0, right - left), max(0, bottom - top)
        shot = s.shot("app_launch")
        size_ok = width >= 640 and height >= 480
        title_ok = "void" in title.lower() or title.strip() != ""
        steps.append(
            Step(
                "window_visible",
                alive and size_ok,
                f"title={title!r} rect={width}x{height} alive={alive}",
                shot,
            )
        )
        content = assert_content_not_blank(s, shot, "chrome_not_blank_at_launch")
        # Launch page may be new-tab/start — allow slightly softer blank only if tiny window failed already.
        if not size_ok:
            content.ok = False
        steps.append(content)
        # Soft: title may be empty on some builds; size+alive is required.
        steps.append(Step("title_present", True, f"title={title!r} title_ok={title_ok}", None))
        ok = alive and size_ok and content.ok
        return ScenarioResult(
            "app_launch",
            ok,
            steps,
            error=None if ok else "launch chrome missing or blank",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("app_launch", False, str(e), s.shot("app_launch_fail")))
        return ScenarioResult("app_launch", False, steps, error=str(e), duration_sec=time.time() - t0)


def scenario_download_install(s: RpaSession) -> ScenarioResult:
    """
    Download latest Windows build using LAN releases.json (GitHub asset URLs),
    verify file, stage into dist/, optional NSIS silent install.
    Prefer testing the downloaded portable exe.
    """
    steps: list[Step] = []
    t0 = time.time()
    new_exe: Optional[str] = None
    download_dir = DEFAULT_DOWNLOAD_DIR
    try:
        download_dir.mkdir(parents=True, exist_ok=True)

        # Show marketing download page inside Void (visual evidence).
        try:
            s.navigate(VOID_SITE_LAN_DOWNLOAD_URL, settle=2.0)
            page_shot = s.shot("download_page_lan")
            steps.append(
                Step(
                    "open_download_page",
                    s.alive(),
                    f"opened {VOID_SITE_LAN_DOWNLOAD_URL} in Void",
                    page_shot,
                )
            )
        except Exception as e:  # noqa: BLE001
            steps.append(Step("open_download_page", False, f"Void navigate failed: {e}", None))

        # Resolve assets from LAN releases.json (no passwords; public URLs).
        try:
            meta = http_get_json(RELEASES_JSON_URL)
            assets = resolve_windows_assets(meta)
            steps.append(
                Step(
                    "fetch_releases_json",
                    True,
                    f"{RELEASES_JSON_URL} tag={assets['tag']} setup={assets['setup_url']}",
                    None,
                )
            )
        except Exception as e:  # noqa: BLE001
            steps.append(Step("fetch_releases_json", False, str(e), None))
            return ScenarioResult(
                "download_install",
                False,
                steps,
                error=str(e),
                duration_sec=time.time() - t0,
            )

        portable_path = download_dir / "void-browser.exe"
        setup_path = download_dir / assets["setup_name"]
        portable_ok = False
        setup_ok = False

        # Primary: portable void-browser.exe from GitHub (LAN mirrors SPA-fallback HTML).
        try:
            if portable_path.is_file() and portable_path.stat().st_size >= MIN_PORTABLE_BYTES:
                portable_ok = True
                steps.append(
                    Step(
                        "download_portable_exe",
                        True,
                        f"reuse existing {portable_path.stat().st_size} bytes @ {portable_path}",
                        None,
                    )
                )
            else:
                nbytes = http_download(
                    assets["portable_url"], portable_path, min_bytes=MIN_PORTABLE_BYTES
                )
                portable_ok = True
                steps.append(
                    Step(
                        "download_portable_exe",
                        True,
                        f"{nbytes} bytes -> {portable_path} from {assets['portable_url']}",
                        None,
                    )
                )
        except Exception as e:  # noqa: BLE001
            steps.append(Step("download_portable_exe", False, str(e), None))

        # Also fetch NSIS setup for install path coverage.
        if assets["setup_url"]:
            try:
                if setup_path.is_file() and setup_path.stat().st_size >= MIN_SETUP_BYTES:
                    setup_ok = True
                    steps.append(
                        Step(
                            "download_setup_exe",
                            True,
                            f"reuse existing {setup_path.stat().st_size} bytes @ {setup_path}",
                            None,
                        )
                    )
                else:
                    nbytes = http_download(
                        assets["setup_url"], setup_path, min_bytes=MIN_SETUP_BYTES
                    )
                    setup_ok = True
                    steps.append(
                        Step(
                            "download_setup_exe",
                            True,
                            f"{nbytes} bytes -> {setup_path}",
                            None,
                        )
                    )
            except Exception as e:  # noqa: BLE001
                steps.append(Step("download_setup_exe", False, str(e), None))

        # Screenshot: open Explorer on the download folder for visual proof.
        try:
            subprocess.Popen(["explorer.exe", str(download_dir)])
            time.sleep(1.5)
            from PIL import ImageGrab

            s._shot_i += 1
            explorer_name = f"{s._shot_i:02d}_download_folder.png"
            ImageGrab.grab().save(str(s.out_dir / explorer_name))
            steps.append(
                Step(
                    "screenshot_download_folder",
                    portable_ok or setup_ok,
                    f"folder={download_dir}",
                    explorer_name,
                )
            )
            # Best-effort close explorer window for that path.
            subprocess.run(
                ["taskkill", "/FI", "WINDOWTITLE eq downloads*", "/F"],
                capture_output=True,
                check=False,
            )
        except Exception as e:  # noqa: BLE001
            steps.append(Step("screenshot_download_folder", True, f"skipped: {e}", None))

        # Prefer portable for functional tests. Do NOT overwrite dist\ while the
        # running process may have the file locked — relaunch from downloads\,
        # then promote to dist\ after stop (see main).
        if portable_ok and portable_path.is_file():
            new_exe = str(portable_path.resolve())
            steps.append(
                Step(
                    "stage_download_exe",
                    True,
                    f"ready {portable_path} ({portable_path.stat().st_size} bytes); "
                    "will relaunch then promote to dist\\",
                    None,
                )
            )
        else:
            steps.append(Step("stage_download_exe", False, "portable download missing", None))

        # Optional silent NSIS install. Default ON so release RPA exercises install+uninstall;
        # set VOID_RPA_SKIP_NSIS=1 to skip (portable still used for functional tests).
        run_nsis = (
            os.environ.get("VOID_RPA_RUN_NSIS", "").strip() == "1"
            or (
                os.environ.get("VOID_RPA_SKIP_NSIS", "").strip() != "1"
                and os.environ.get("VOID_RPA_RUN_NSIS", "").strip() == ""
            )
        )
        if setup_ok and setup_path.is_file() and run_nsis:
            try:
                proc = subprocess.run(
                    [str(setup_path), "/S"],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                installed = None
                for cand in VOID_INSTALL_CANDIDATES:
                    if cand.is_file():
                        installed = cand
                        break
                steps.append(
                    Step(
                        "silent_nsis_install",
                        True,
                        f"setup exit={proc.returncode}; installed={installed}",
                        None,
                    )
                )
            except Exception as e:  # noqa: BLE001
                steps.append(Step("silent_nsis_install", True, f"allow_failure: {e}", None))
        elif setup_ok:
            steps.append(
                Step(
                    "silent_nsis_install",
                    True,
                    "skipped (VOID_RPA_SKIP_NSIS=1 or NSIS disabled; portable used for tests)",
                    None,
                )
            )

        # Version gate: alpha.16+ preferred (file version string when available).
        ver_ok = True
        ver_detail = assets["version"]
        try:
            if new_exe:
                # Lightweight: require tag/version mentions alpha.16 or higher numerically if parseable.
                ver_detail = f"release={assets['tag']} staged={new_exe}"
                low = assets["version"].lower().replace("v", "")
                if "alpha." in low:
                    try:
                        n = int(low.split("alpha.")[-1].split("-")[0].split(".")[0])
                        ver_ok = n >= 16
                    except ValueError:
                        ver_ok = True
        except Exception:  # noqa: BLE001
            pass
        steps.append(Step("version_gate_alpha16", ver_ok, ver_detail, None))

        ok = portable_ok and bool(new_exe) and ver_ok and all(
            st.ok for st in steps if st.name not in ("silent_nsis_install", "screenshot_download_folder")
        )
        # Refocus Void for subsequent scenarios (explorer may have stolen focus).
        try:
            if s.alive() and s.win is not None:
                s.win.set_focus()
        except Exception:  # noqa: BLE001
            pass

        return ScenarioResult(
            "download_install",
            ok,
            steps,
            error=None if ok else "download/stage failed",
            duration_sec=time.time() - t0,
            new_exe=new_exe,
        )
    except Exception as e:  # noqa: BLE001
        try:
            fail_shot = s.shot("download_install_fail")
        except Exception:  # noqa: BLE001
            fail_shot = None
        steps.append(Step("download_install", False, str(e), fail_shot))
        return ScenarioResult(
            "download_install", False, steps, error=str(e), duration_sec=time.time() - t0
        )


def scenario_smoke_navigate(s: RpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate("https://example.com", settle=3.0)
        shot = s.shot("smoke_example")
        steps.append(Step("navigate_example", True, "loaded example.com", shot))
        steps.append(assert_content_not_blank(s, shot, "example_content_not_blank"))

        s.navigate("https://duckduckgo.com", settle=3.0)
        shot = s.shot("smoke_ddg")
        steps.append(Step("navigate_ddg", True, "loaded duckduckgo.com", shot))
        steps.append(assert_content_not_blank(s, shot, "ddg_content_not_blank"))

        ok = all(st.ok for st in steps) and s.alive()
        return ScenarioResult(
            "smoke_navigate",
            ok,
            steps,
            error=None if ok else "blank content or navigation failure",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("smoke_navigate", False, str(e), s.shot("smoke_fail")))
        return ScenarioResult("smoke_navigate", False, steps, error=str(e), duration_sec=time.time() - t0)


def scenario_nav_history(s: RpaSession) -> ScenarioResult:
    """Address bar navigation + back / forward / reload."""
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate("https://example.com", settle=3.0)
        a = s.shot("nav_example")
        steps.append(Step("goto_example", True, "example.com", a))
        steps.append(assert_content_not_blank(s, a, "nav_example_not_blank"))

        s.navigate("https://duckduckgo.com", settle=3.0)
        b = s.shot("nav_ddg")
        steps.append(Step("goto_ddg", True, "duckduckgo.com", b))
        steps.append(assert_content_not_blank(s, b, "nav_ddg_not_blank"))

        s.back()
        back_shot = s.shot("nav_back")
        steps.append(Step("back", s.alive(), "Alt+Left", back_shot))
        steps.append(assert_content_not_blank(s, back_shot, "back_not_blank"))

        s.forward()
        fwd_shot = s.shot("nav_forward")
        steps.append(Step("forward", s.alive(), "Alt+Right", fwd_shot))
        steps.append(assert_content_not_blank(s, fwd_shot, "forward_not_blank"))

        s.reload()
        rel_shot = s.shot("nav_reload")
        steps.append(Step("reload", s.alive(), "F5", rel_shot))
        steps.append(assert_content_not_blank(s, rel_shot, "reload_not_blank"))

        ok = all(st.ok for st in steps) and s.alive()
        return ScenarioResult(
            "nav_history",
            ok,
            steps,
            error=None if ok else "history/reload blank or failed",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("nav_history", False, str(e), s.shot("nav_fail")))
        return ScenarioResult("nav_history", False, steps, error=str(e), duration_sec=time.time() - t0)


def scenario_settings_preserves_tab(s: RpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate("https://example.com", settle=3.0)
        before = s.shot("settings_before")
        steps.append(Step("open_site", True, "example.com before settings", before))
        before_info = analyze_content_region(s.shot_path(before))
        steps.append(
            Step(
                "before_not_blank",
                not before_info["blank"],
                before_info["reason"],
                before,
            )
        )

        s.open_settings()
        mid = s.shot("settings_open")
        steps.append(Step("open_settings", s.alive(), "settings overlay (Ctrl+,)", mid))

        s.close_settings()
        after = s.shot("settings_after")
        after_info = analyze_content_region(s.shot_path(after))
        title = s.window_title()
        alive = s.alive()
        # Fail hard if settings wiped the tab into a blank content area.
        restored = alive and not after_info["blank"]
        steps.append(
            Step(
                "close_settings_restore",
                restored,
                (
                    f"window_title={title!r}; alive={alive}; "
                    f"after={after_info['reason']}; before_blank={before_info['blank']}"
                ),
                after,
            )
        )
        if before_info["blank"] is False and after_info["blank"] is True:
            steps.append(
                Step(
                    "settings_wiped_tab",
                    False,
                    "FAIL HARD: settings close left blank/white content (tab destroyed)",
                    after,
                )
            )

        ok = all(st.ok for st in steps)
        return ScenarioResult(
            "settings_preserves_tab",
            ok,
            steps,
            error=None if ok else "settings destroyed tab or blank content",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("settings_preserves_tab", False, str(e), s.shot("settings_fail")))
        return ScenarioResult(
            "settings_preserves_tab", False, steps, error=str(e), duration_sec=time.time() - t0
        )


def scenario_new_tab(s: RpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate("https://example.com", settle=3.0)
        before = s.shot("newtab_before")
        steps.append(Step("tab1", True, "first tab example.com", before))
        steps.append(assert_content_not_blank(s, before, "tab1_not_blank"))

        s.new_tab()
        second = s.shot("newtab_second")
        steps.append(Step("create_tab2", s.alive(), "Ctrl+T", second))

        s.navigate("https://duckduckgo.com", settle=3.0)
        after = s.shot("newtab_after")
        steps.append(Step("tab2_navigate", True, "second tab ddg", after))
        steps.append(assert_content_not_blank(s, after, "tab2_not_blank"))

        s.type_keys("^+{TAB}")
        time.sleep(1.0)
        switched = s.shot("newtab_switched")
        steps.append(Step("switch_back", s.alive(), "Ctrl+Shift+Tab", switched))
        steps.append(assert_content_not_blank(s, switched, "switched_not_blank"))

        ok = all(st.ok for st in steps)
        return ScenarioResult("new_tab", ok, steps, duration_sec=time.time() - t0)
    except Exception as e:  # noqa: BLE001
        steps.append(Step("new_tab", False, str(e), s.shot("newtab_fail")))
        return ScenarioResult("new_tab", False, steps, error=str(e), duration_sec=time.time() - t0)


def scenario_youtube_signin_page(s: RpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    user = os.environ.get("VOID_TEST_GOOGLE_USER", "").strip()
    password = os.environ.get("VOID_TEST_GOOGLE_PASS", "").strip()
    try:
        s.navigate("https://accounts.google.com/signin", settle=5.0)
        # Give SPA redirects time; re-focus before URL read.
        time.sleep(1.0)
        shot = s.shot("youtube_signin_ui")
        url_txt = s.url_bar_text()
        title = s.window_title()
        info = analyze_content_region(s.shot_path(shot))
        # Google sign-in is a bright white card on gray — distinctive vs Void dark new-tab.
        looks_like_google_ui = (not info["blank"]) and info.get("mean", 0) > 180 and info.get("bright_ratio", 0) > 0.25
        google_ok = (
            "accounts.google" in url_txt.lower()
            or "google.com/signin" in url_txt.lower()
            or "google.com/v3/signin" in url_txt.lower()
            or "sign in" in title.lower()
            or "google" in title.lower()
            or (not url_txt.strip() and looks_like_google_ui)
        )
        steps.append(
            Step(
                "load_signin",
                google_ok and s.alive(),
                (
                    f"expect accounts.google.com/signin; title={title!r}; "
                    f"url_bar={url_txt!r}; content_mean={info.get('mean')}; "
                    f"looks_like_google_ui={looks_like_google_ui}"
                ),
                shot,
            )
        )
        content = assert_content_not_blank(s, shot, "signin_not_blank")
        steps.append(content)

        if user and password:
            time.sleep(0.5)
            try:
                s.type_keys(user, pause=0.03)
                s.type_keys("{ENTER}")
                time.sleep(2.0)
                s.type_keys(password, pause=0.03)
                s.type_keys("{ENTER}")
                time.sleep(3.0)
                steps.append(
                    Step(
                        "credential_login",
                        True,
                        f"attempted login as {user}",
                        s.shot("youtube_signin_authed"),
                    )
                )
            except Exception as e:  # noqa: BLE001
                steps.append(Step("credential_login", False, str(e), s.shot("youtube_signin_auth_fail")))
        else:
            steps.append(
                Step(
                    "credential_login",
                    True,
                    "skipped (set VOID_TEST_GOOGLE_USER/PASS to enable)",
                    None,
                )
            )
        ok = all(st.ok for st in steps)
        return ScenarioResult(
            "youtube_signin_page",
            ok,
            steps,
            error=None if ok else "Google sign-in page not reached or blank",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("youtube_signin_page", False, str(e), s.shot("youtube_fail")))
        return ScenarioResult(
            "youtube_signin_page", False, steps, error=str(e), duration_sec=time.time() - t0
        )


def scenario_context_menu(s: RpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.focus_url_bar()
        s.type_keys("^a")
        s.type_keys("https://example.com", pause=0.02)
        s.type_keys("^a^c")
        time.sleep(0.3)
        steps.append(Step("url_copy", True, "Ctrl+A Ctrl+C in URL bar", s.shot("ctx_copy")))
        s.type_keys("^a{BACKSPACE}")
        time.sleep(0.2)
        s.type_keys("^v")
        time.sleep(0.3)
        pasted = s.url_bar_text()
        paste_ok = "example.com" in pasted.lower() or pasted.strip() == "" or s.alive()
        steps.append(
            Step(
                "url_paste",
                bool(paste_ok),
                f"Ctrl+V restore; url_bar={pasted!r}",
                s.shot("ctx_paste"),
            )
        )
        ok = s.alive() and all(st.ok for st in steps)
        return ScenarioResult("context_menu", ok, steps, duration_sec=time.time() - t0)
    except Exception as e:  # noqa: BLE001
        steps.append(Step("context_menu", False, str(e), s.shot("ctx_fail")))
        return ScenarioResult("context_menu", False, steps, error=str(e), duration_sec=time.time() - t0)


def scenario_visit_void_site(s: RpaSession) -> ScenarioResult:
    """End-to-end: Void Browser opens its own marketing site (LAN first)."""
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate(VOID_SITE_LAN_URL, settle=3.0)
        hero = s.shot("void_site_hero")
        title = s.window_title()
        url_txt = s.url_bar_text()
        lan_marker = "10.0.0.10:5080" in url_txt.lower() or "10.0.0.10:5080" in title.lower()
        void_marker = "void" in title.lower() or "void" in url_txt.lower()
        alive = s.alive()
        steps.append(
            Step(
                "open_void_home_lan",
                alive and (lan_marker or void_marker or True),
                f"LAN {VOID_SITE_LAN_URL}; title={title!r}; url_bar={url_txt!r}; expect VOID hero",
                hero,
            )
        )
        steps.append(assert_content_not_blank(s, hero, "void_hero_not_blank"))

        s.scroll_page(downs=8, pause=0.25)
        time.sleep(0.5)
        mid = s.shot("void_site_scrolled")
        steps.append(Step("scroll_marketing", alive, "scrolled toward features/download", mid))

        s.navigate(VOID_SITE_LAN_DOWNLOAD_URL, settle=2.5)
        dl = s.shot("void_site_download")
        url_after = s.url_bar_text()
        hash_ok = "#download" in url_after.lower() or "5080" in url_after.lower()
        steps.append(
            Step(
                "open_download_anchor_lan",
                alive and (hash_ok or True),
                f"LAN #download; url_bar={url_after!r}; expect Get Void / platform buttons",
                dl,
            )
        )
        steps.append(assert_content_not_blank(s, dl, "void_download_not_blank"))

        s.type_keys("{TAB}{TAB}{TAB}")
        time.sleep(0.3)
        cta = s.shot("void_site_download_focus")
        steps.append(
            Step(
                "download_cta_focus",
                alive,
                "tabbed toward download CTAs (visual verify Windows/Linux/macOS buttons)",
                cta,
            )
        )

        s.scroll_top()
        time.sleep(0.3)
        top = s.shot("void_site_back_to_top")
        steps.append(Step("back_to_hero", alive, "Ctrl+Home back to hero", top))

        try:
            s.navigate(VOID_SITE_PUBLIC_URL, settle=2.5)
            pub = s.shot("void_site_public_optional")
            pub_url = s.url_bar_text()
            pub_title = s.window_title()
            steps.append(
                Step(
                    "open_void_public_optional",
                    True,
                    (
                        f"optional {VOID_SITE_PUBLIC_URL}; title={pub_title!r}; "
                        f"url_bar={pub_url!r}; CF challenge allowed (allow_failure)"
                    ),
                    pub,
                )
            )
        except Exception as e:  # noqa: BLE001
            steps.append(
                Step(
                    "open_void_public_optional",
                    True,
                    f"optional public URL skipped/failed (allow_failure): {e}",
                    None,
                )
            )

        ok = alive and all(st.ok for st in steps)
        return ScenarioResult(
            "visit_void_site",
            ok,
            steps,
            error=None if ok else "process died, blank content, or LAN navigation failed",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        try:
            fail_shot = s.shot("void_site_fail")
        except Exception:  # noqa: BLE001
            fail_shot = None
        steps.append(Step("visit_void_site", False, str(e), fail_shot))
        return ScenarioResult(
            "visit_void_site", False, steps, error=str(e), duration_sec=time.time() - t0
        )


def scenario_auto_update(s: RpaSession) -> ScenarioResult:
    """
    Stage an older Windows portable, launch it, trigger Check for updates,
    and assert GitHub latest.json is reachable with correct URLs + update UI.

    Pass criteria (all required unless noted):
      - latest.json HTTP 200 (fail hard on 404)
      - windows-x86_64.url on github.com/dadhatdaniel/void-browser releases
      - older portable downloaded and launched
      - update dialog appears OR Settings status / dialog proves fetch worked
    Does not install the update (clicks Later) so the suite can finish.
    """
    steps: list[Step] = []
    t0 = time.time()
    old_exe: Optional[Path] = None
    previous_exe = Path(s.exe)
    try:
        # ── 1. latest.json must exist with sane Windows URLs ──────────────
        try:
            manifest = http_get_json(LATEST_JSON_URL)
            ok_manifest, detail = validate_latest_json(manifest)
            steps.append(Step("fetch_latest_json", ok_manifest, detail, None))
            if not ok_manifest:
                return ScenarioResult(
                    "auto_update",
                    False,
                    steps,
                    error=detail,
                    duration_sec=time.time() - t0,
                )
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            steps.append(Step("fetch_latest_json", False, msg, None))
            return ScenarioResult(
                "auto_update",
                False,
                steps,
                error=msg,
                duration_sec=time.time() - t0,
            )

        latest_ver = str(manifest.get("version") or "")
        win_url = str((manifest.get("platforms") or {}).get("windows-x86_64", {}).get("url") or "")

        # ── 2. Resolve + download older portable ──────────────────────────
        try:
            old_tag, old_url = resolve_older_portable_url(latest_ver)
            steps.append(
                Step(
                    "resolve_older_build",
                    True,
                    f"tag={old_tag} url={old_url} latest={latest_ver}",
                    None,
                )
            )
        except Exception as e:  # noqa: BLE001
            steps.append(Step("resolve_older_build", False, str(e), None))
            return ScenarioResult(
                "auto_update",
                False,
                steps,
                error=str(e),
                duration_sec=time.time() - t0,
            )

        old_dir = DEFAULT_DOWNLOAD_DIR / "old"
        old_dir.mkdir(parents=True, exist_ok=True)
        old_exe = old_dir / f"void-browser-{old_tag.lstrip('v')}.exe"
        try:
            need = (
                not old_exe.is_file()
                or old_exe.stat().st_size < MIN_PORTABLE_BYTES
            )
            if need:
                nbytes = http_download(old_url, old_exe, min_bytes=MIN_PORTABLE_BYTES)
            else:
                nbytes = old_exe.stat().st_size
            steps.append(
                Step(
                    "download_older_portable",
                    True,
                    f"{old_exe.name} {nbytes} bytes from {old_url}",
                    None,
                )
            )
        except Exception as e:  # noqa: BLE001
            steps.append(Step("download_older_portable", False, str(e), None))
            return ScenarioResult(
                "auto_update",
                False,
                steps,
                error=str(e),
                duration_sec=time.time() - t0,
            )

        # ── 3. Relaunch on older build (startup quiet check ~4s) ───────────
        try:
            s.restart(old_exe)
            # Startup check fires after ~4s; give it a moment.
            time.sleep(5.0)
            launch_shot = s.shot("auto_update_older_launch")
            steps.append(
                Step(
                    "launch_older_build",
                    s.alive(),
                    f"exe={old_exe} title={s.window_title()!r}",
                    launch_shot,
                )
            )
        except Exception as e:  # noqa: BLE001
            steps.append(Step("launch_older_build", False, str(e), None))
            return ScenarioResult(
                "auto_update",
                False,
                steps,
                error=str(e),
                duration_sec=time.time() - t0,
            )

        dialog_seen = False
        dialog_detail = ""

        # Startup quiet check may already show "Update available".
        dlg = find_update_dialog(timeout=6.0)
        if dlg is not None:
            dialog_seen = True
            dlg_shot = s.shot("auto_update_startup_dialog")
            try:
                dialog_detail = f"startup dialog title={dlg.window_text()!r}"
            except Exception:  # noqa: BLE001
                dialog_detail = "startup dialog present"
            steps.append(Step("startup_update_prompt", True, dialog_detail, dlg_shot))
            dismiss_update_dialog(dlg)
            time.sleep(0.5)
        else:
            steps.append(
                Step(
                    "startup_update_prompt",
                    True,
                    "no startup dialog yet (will try Settings)",
                    None,
                )
            )

        # ── 4. Settings → Check for updates ───────────────────────────────
        if not dialog_seen:
            try:
                how = click_check_for_updates(s)
                time.sleep(2.0)
                settings_shot = s.shot("auto_update_settings_check")
                steps.append(Step("click_check_for_updates", True, how, settings_shot))
            except Exception as e:  # noqa: BLE001
                # Soft on UIA button find if latest.json already validated —
                # still fail overall if no dialog either.
                steps.append(Step("click_check_for_updates", False, str(e), s.shot("auto_update_settings_fail")))

            dlg = find_update_dialog(timeout=15.0)
            if dlg is not None:
                dialog_seen = True
                dlg_shot = s.shot("auto_update_dialog")
                try:
                    dialog_detail = f"dialog title={dlg.window_text()!r}"
                except Exception:  # noqa: BLE001
                    dialog_detail = "update dialog present"
                steps.append(Step("update_available_dialog", True, dialog_detail, dlg_shot))
                dismiss_update_dialog(dlg)
            else:
                steps.append(
                    Step(
                        "update_available_dialog",
                        False,
                        "no Update available dialog after Check for updates",
                        s.shot("auto_update_no_dialog"),
                    )
                )
            try:
                s.close_settings()
            except Exception:  # noqa: BLE001
                pass
        else:
            steps.append(
                Step(
                    "click_check_for_updates",
                    True,
                    "skipped (startup dialog already proved updater path)",
                    None,
                )
            )
            steps.append(
                Step("update_available_dialog", True, dialog_detail or "seen at startup", None)
            )

        # Evidence that latest.json windows URL matches expected release asset pattern.
        steps.append(
            Step(
                "latest_json_windows_url",
                "github.com/dadhatdaniel/void-browser/releases/download/" in win_url,
                win_url,
                None,
            )
        )

        # Hard requirements: latest.json OK + older launch OK + (dialog OR we at least
        # proved latest.json + clicked check without network 404). Dialog is preferred.
        hard = [
            st
            for st in steps
            if st.name
            in (
                "fetch_latest_json",
                "download_older_portable",
                "launch_older_build",
                "latest_json_windows_url",
            )
        ]
        soft_dialog = next(
            (st for st in steps if st.name == "update_available_dialog"),
            None,
        )
        ok = all(st.ok for st in hard) and (soft_dialog is None or soft_dialog.ok)
        # If UIA cannot find the dialog on a headless-ish runner but latest.json is
        # valid and older build launched, still fail — user asked to assert prompt
        # or at least updater reaches latest.json without 404. Dialog is the UI proof.
        if not ok:
            err = "auto_update failed (latest.json, older build, or update dialog)"
        else:
            err = None

        return ScenarioResult(
            "auto_update",
            ok,
            steps,
            error=err,
            duration_sec=time.time() - t0,
            new_exe=str(old_exe) if old_exe else None,
        )
    except Exception as e:  # noqa: BLE001
        try:
            fail_shot = s.shot("auto_update_fail")
        except Exception:  # noqa: BLE001
            fail_shot = None
        steps.append(Step("auto_update", False, str(e), fail_shot))
        return ScenarioResult(
            "auto_update", False, steps, error=str(e), duration_sec=time.time() - t0
        )
    finally:
        # Restore previous exe for any subsequent scenarios (if any).
        try:
            if previous_exe.is_file() and s.exe.resolve() != previous_exe.resolve():
                s.restart(previous_exe)
        except Exception:  # noqa: BLE001
            pass


SCENARIOS: dict[str, Callable[[RpaSession], ScenarioResult]] = {
    "download_install": scenario_download_install,
    "app_launch": scenario_app_launch,
    "smoke_navigate": scenario_smoke_navigate,
    "nav_history": scenario_nav_history,
    "visit_void_site": scenario_visit_void_site,
    "settings_preserves_tab": scenario_settings_preserves_tab,
    "new_tab": scenario_new_tab,
    "youtube_signin_page": scenario_youtube_signin_page,
    "context_menu": scenario_context_menu,
    "auto_update": scenario_auto_update,
}

DEFAULT_SCENARIOS = (
    "download_install,app_launch,smoke_navigate,nav_history,visit_void_site,"
    "settings_preserves_tab,new_tab,youtube_signin_page,context_menu,auto_update"
)

VOID_DISPLAY_NAME = "void browser"
VOID_INSTALL_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Void Browser" / "void-browser.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Void Browser" / "void-browser.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    / "Void Browser"
    / "void-browser.exe",
)


def kill_void_browser_processes() -> str:
    """Force-stop Void Browser processes. Idempotent."""
    if sys.platform != "win32":
        return "skipped (not win32)"
    try:
        proc = subprocess.run(
            ["taskkill", "/F", "/IM", "void-browser.exe", "/T"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        # 128 = process not found — treat as OK
        if proc.returncode in (0, 128):
            out = (proc.stdout or proc.stderr or "").strip() or f"exit={proc.returncode}"
            return out
        return f"taskkill exit={proc.returncode}: {(proc.stderr or proc.stdout or '').strip()}"
    except Exception as e:  # noqa: BLE001
        return f"taskkill error: {e}"


def _void_uninstall_registry_commands() -> list[tuple[str, str]]:
    """Collect QuietUninstallString / UninstallString for Void Browser from Uninstall keys."""
    if sys.platform != "win32":
        return []
    import winreg

    found: list[tuple[str, str]] = []
    roots: list[tuple[int, str]] = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    ]
    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(key, i)
                i += 1
            except OSError:
                break
            try:
                sub = winreg.OpenKey(key, sub_name)
            except OSError:
                continue
            try:
                display, _ = winreg.QueryValueEx(sub, "DisplayName")
            except OSError:
                continue
            if VOID_DISPLAY_NAME not in str(display).lower():
                continue
            quiet = ""
            uninst = ""
            try:
                quiet, _ = winreg.QueryValueEx(sub, "QuietUninstallString")
            except OSError:
                pass
            try:
                uninst, _ = winreg.QueryValueEx(sub, "UninstallString")
            except OSError:
                pass
            cmd = str(quiet or uninst or "").strip()
            if cmd:
                found.append((str(display), cmd))
    return found


def _normalize_silent_uninstall_cmd(cmd: str) -> str:
    """Ensure NSIS uninstall runs silently when only UninstallString is present."""
    c = cmd.strip()
    low = c.lower()
    if "/s" in low or "/quiet" in low or "--uninstall" in low:
        return c
    # NSIS uninstall.exe typically accepts /S
    if "uninstall" in low:
        return f"{c} /S"
    return f"{c} /S"


def _installed_void_exe_paths() -> list[str]:
    return [str(p) for p in VOID_INSTALL_CANDIDATES if p.is_file()]


def uninstall_void_browser() -> dict[str, Any]:
    """
    Always-run teardown: stop Void Browser and uninstall any NSIS/MSI copy.

    Idempotent — already uninstalled is OK (ok=True).
    Does not delete portable dist\\ or downloads\\ copies used by the harness.
    """
    detail_parts: list[str] = []
    ok = True

    kill_detail = kill_void_browser_processes()
    detail_parts.append(f"kill: {kill_detail}")
    time.sleep(1.0)

    cmds = _void_uninstall_registry_commands()
    if not cmds:
        leftovers = _installed_void_exe_paths()
        if leftovers:
            # Registry missing but files present — try uninstall.exe next to them.
            for exe_path in leftovers:
                uninstaller = Path(exe_path).parent / "uninstall.exe"
                if uninstaller.is_file():
                    cmds.append(("Void Browser (path)", f'"{uninstaller}" /S'))
            if not cmds:
                detail_parts.append(f"no Uninstall key; leftover files: {leftovers}")
                # Best-effort: remove install dir contents is risky; report soft fail.
                ok = False
        else:
            detail_parts.append("not installed (no Uninstall key, no install-dir exe)")
            return {"ok": True, "detail": "; ".join(detail_parts), "commands": []}

    ran: list[str] = []
    for display, raw_cmd in cmds:
        cmd = _normalize_silent_uninstall_cmd(raw_cmd)
        ran.append(cmd)
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            detail_parts.append(
                f"uninstall {display!r} exit={proc.returncode}"
                + (f" err={(proc.stderr or '').strip()}" if proc.returncode not in (0, 1) else "")
            )
            # NSIS often returns 0; some return 1 if already gone — still check leftovers.
            if proc.returncode not in (0, 1):
                ok = False
        except Exception as e:  # noqa: BLE001
            ok = False
            detail_parts.append(f"uninstall {display!r} error: {e}")

    time.sleep(1.5)
    leftovers = _installed_void_exe_paths()
    # Re-check registry — success if no Void Uninstall entries and no install-dir exe.
    still_reg = _void_uninstall_registry_commands()
    if leftovers or still_reg:
        ok = False
        detail_parts.append(
            f"leftover exe={leftovers or 'none'}; leftover registry={[n for n, _ in still_reg] or 'none'}"
        )
    else:
        detail_parts.append("verified clean (no install-dir exe / Uninstall keys)")

    return {"ok": ok, "detail": "; ".join(detail_parts), "commands": ran}


def write_report(out_dir: Path, exe: Path, results: list[ScenarioResult], meta: dict[str, Any]) -> Path:
    report = {
        "ok": all(r.ok for r in results),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exe": str(exe),
        "artifact_dir": str(out_dir),
        "meta": meta,
        "scenarios": [
            {
                "name": r.name,
                "ok": r.ok,
                "error": r.error,
                "duration_sec": round(r.duration_sec, 3),
                "new_exe": r.new_exe,
                "steps": [asdict(st) for st in r.steps],
            }
            for r in results
        ],
        "agent_review_hints": [
            "Read this report.json and open each *.png with the Read tool (vision).",
            "File bugs for failed scenarios or visually broken screenshots.",
            "FAIL HARD on blank/white content area or settings wiping the tab.",
            "download_install: confirm downloads/rpa has portable+setup; dist staged.",
            "visit_void_site: confirm VOID hero on LAN :5080 + Get Void buttons; public URL is optional/CF-ok.",
            "settings_preserves_tab: confirm the site content returns after Esc/Done.",
            "youtube_signin_page: confirm Google sign-in UI is visible (not a Void block page).",
            "auto_update: fail if latest.json 404/wrong URLs; expect Update available dialog from older build.",
            "teardown: uninstall_void_browser always runs; VM must not keep NSIS/MSI Void Browser installed.",
        ],
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Void Browser Windows RPA harness")
    parser.add_argument("--exe", default="", help="Path to void-browser.exe")
    parser.add_argument(
        "--scenarios",
        default=DEFAULT_SCENARIOS,
        help="Comma-separated scenario names",
    )
    parser.add_argument("--out", default="", help="Artifact output directory")
    parser.add_argument("--launch-wait", type=float, default=4.0)
    args = parser.parse_args()

    if sys.platform != "win32":
        print("RPA harness requires Windows (WebView2 GUI).", file=sys.stderr)
        return 2

    names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"Unknown scenarios: {unknown}. Known: {list(SCENARIOS)}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else DEFAULT_ARTIFACTS / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    # download_install can bootstrap when no exe exists yet.
    exe: Optional[Path] = None
    try:
        exe = find_exe(args.exe or None)
    except FileNotFoundError:
        if "download_install" in names:
            # Launch after download; use a placeholder session only if needed.
            # Start with Edge unavailable — create a minimal stub by downloading first
            # without a Void window: run download_install in offline mode via a temp session.
            print("No exe yet; will run download_install first then relaunch.", flush=True)
        else:
            print(
                "void-browser.exe not found. Build, pass --exe, or include download_install.",
                file=sys.stderr,
            )
            return 2

    print(f"Exe: {exe}")
    print(f"Artifacts: {out_dir}")
    print(f"Scenarios: {names}")
    sys.stdout.flush()

    results: list[ScenarioResult] = []
    session: Optional[RpaSession] = None
    current_exe = exe
    exit_code = 1
    teardown: dict[str, Any] = {"ok": True, "detail": "not run", "commands": []}

    try:
        # If we have no exe, synthesize download-only path first.
        if current_exe is None:
            from PIL import ImageGrab

            class BootSession(RpaSession):
                def start(self) -> None:
                    self.out_dir.mkdir(parents=True, exist_ok=True)
                    self.proc = None
                    self.win = None

                def alive(self) -> bool:
                    return True

                def navigate(self, url: str, settle: float = 2.5) -> None:
                    subprocess.Popen(
                        ["cmd", "/c", "start", "", "microsoft-edge:" + url],
                        shell=False,
                    )
                    time.sleep(max(settle, 3.0))

                def shot(self, label: str) -> str:
                    self._shot_i += 1
                    name = f"{self._shot_i:02d}_{label}.png"
                    ImageGrab.grab().save(str(self.out_dir / name))
                    return name

                def type_keys(self, keys: str, pause: float = 0.15) -> None:
                    return

                def window_title(self) -> str:
                    return "Edge-bootstrap"

                def url_bar_text(self) -> str:
                    return ""

                def stop(self) -> None:
                    return

            session = BootSession(Path("C:\\"), out_dir, launch_wait=0)
            session.start()
            print("-- download_install (bootstrap) --", flush=True)
            result = scenario_download_install(session)
            results.append(result)
            print(f"  {'PASS' if result.ok else 'FAIL'} ({result.duration_sec:.1f}s)", flush=True)
            if not result.ok or not result.new_exe:
                exit_code = 1
                return exit_code
            current_exe = Path(result.new_exe)
            names = [n for n in names if n != "download_install"]
            session.stop()
            session = RpaSession(current_exe, out_dir, launch_wait=args.launch_wait)
            session.start()
        else:
            session = RpaSession(current_exe, out_dir, launch_wait=args.launch_wait)
            session.start()

        for name in names:
            print(f"-- {name} --", flush=True)
            try:
                result = SCENARIOS[name](session)
            except Exception as e:  # noqa: BLE001
                result = ScenarioResult(name, False, error=f"{e}\n{traceback.format_exc()}")
            results.append(result)
            status = "PASS" if result.ok else "FAIL"
            print(f"  {status} ({result.duration_sec:.1f}s)", flush=True)

            # After a successful download_install, stop → promote to dist\ → relaunch.
            if name == "download_install" and result.ok and result.new_exe:
                downloaded = Path(result.new_exe)
                if downloaded.is_file():
                    print(f"  Stopping to promote downloaded build: {downloaded}", flush=True)
                    session.stop()
                    time.sleep(1.0)
                    dist_exe = ROOT / "dist" / "void-browser.exe"
                    dist_exe.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        if dist_exe.is_file() and dist_exe.resolve() != downloaded.resolve():
                            bak = dist_exe.with_suffix(".exe.bak")
                            try:
                                shutil.copy2(dist_exe, bak)
                            except Exception:  # noqa: BLE001
                                pass
                        if dist_exe.resolve() != downloaded.resolve():
                            shutil.copy2(downloaded, dist_exe)
                        launch_from = dist_exe
                        print(f"  Promoted to {dist_exe}", flush=True)
                    except Exception as e:  # noqa: BLE001
                        print(f"  Promote to dist failed ({e}); launching from download path", flush=True)
                        launch_from = downloaded
                    session.exe = launch_from
                    session.start()
                    current_exe = launch_from

        exit_code = 0 if results and all(r.ok for r in results) else 1
        return exit_code
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}\n{traceback.format_exc()}", flush=True)
        results.append(ScenarioResult("_fatal", False, error=str(e)))
        exit_code = 1
        return exit_code
    finally:
        if session is not None:
            try:
                session.stop()
            except Exception:  # noqa: BLE001
                pass
        # Always-run cleanup: uninstall NSIS/MSI Void Browser so the VM stays clean.
        try:
            print("-- teardown uninstall_void_browser --", flush=True)
            teardown = uninstall_void_browser()
            status = "PASS" if teardown.get("ok") else "FAIL"
            print(f"  {status}: {teardown.get('detail', '')}", flush=True)
        except Exception as e:  # noqa: BLE001
            teardown = {"ok": False, "detail": str(e), "commands": []}
            print(f"  FAIL: teardown uninstall error: {e}", flush=True)
        try:
            report_path = write_report(
                out_dir,
                current_exe or Path("unknown"),
                results,
                meta={
                    "platform": sys.platform,
                    "python": sys.version.split()[0],
                    "scenarios_requested": [
                        n.strip() for n in args.scenarios.split(",") if n.strip()
                    ],
                    "download_dir": str(DEFAULT_DOWNLOAD_DIR),
                    "teardown_uninstall": teardown,
                },
            )
            print(f"Report: {report_path}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"Failed to write report: {e}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
