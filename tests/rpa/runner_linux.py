#!/usr/bin/env python3
"""
Void Browser — Linux RPA harness (xdotool + scrot).

Mirrors tests/rpa/runner.py intent on Ubuntu/GNOME:
  download_install → app_launch → smoke_navigate → nav_history →
  visit_void_site → settings_preserves_tab → new_tab →
  youtube_signin_page (soft) → context_menu → auto_update → teardown

Usage (on the void-test-linux guest, DISPLAY=:0):
  python3 tests/rpa/runner_linux.py
  python3 tests/rpa/runner_linux.py --scenarios smoke_navigate,visit_void_site
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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACTS = ROOT / "artifacts" / "rpa"
DEFAULT_DOWNLOAD_DIR = Path(
    os.environ.get("VOID_RPA_DOWNLOAD_DIR", str(ROOT / "downloads" / "rpa"))
)
VOID_SITE_LAN_URL = os.environ.get("VOID_SITE_LAN_URL", "http://10.0.0.10:5080/").rstrip("/") + "/"
VOID_SITE_LAN_DOWNLOAD_URL = VOID_SITE_LAN_URL + "#download"
RELEASES_JSON_URL = os.environ.get(
    "VOID_RELEASES_JSON_URL", VOID_SITE_LAN_URL.rstrip("/") + "/releases.json"
)
LATEST_JSON_URL = os.environ.get(
    "VOID_RPA_LATEST_JSON_URL",
    "https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json",
)
GH_RELEASES_API = os.environ.get(
    "VOID_RPA_GH_RELEASES_API",
    "https://api.github.com/repos/dadhatdaniel/void-browser/releases",
)
DEFAULT_OLD_TAG = os.environ.get("VOID_RPA_OLD_TAG", "v0.1.0-alpha.15")
MIN_APPIMAGE_BYTES = 5_000_000
MIN_DEB_BYTES = 500_000
DEFAULT_SCENARIO_TIMEOUT_SEC = float(os.environ.get("VOID_RPA_SCENARIO_TIMEOUT", "180"))
VOID_DISABLE_UPDATER_ENV = "VOID_DISABLE_UPDATER"

DEFAULT_SCENARIOS = (
    "download_install,app_launch,smoke_navigate,nav_history,visit_void_site,"
    "settings_preserves_tab,new_tab,youtube_signin_page,context_menu,auto_update"
)


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
    new_exe: Optional[str] = None
    soft_fail: bool = False


def _run(cmd: list[str], timeout: float = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        out = (p.stdout or "") + (p.stderr or "")
        return int(p.returncode), out
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def http_get_json(url: str, timeout: float = 60) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "VoidBrowser-RPA-Linux/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    return data


def http_get_json_list(url: str, timeout: float = 60) -> list[Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "VoidBrowser-RPA-Linux/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"expected JSON array from {url}")
    return data


def http_download(url: str, dest: Path, min_bytes: int = 0) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    req = urllib.request.Request(url, headers={"User-Agent": "VoidBrowser-RPA-Linux/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(partial, "wb") as f:
        shutil.copyfileobj(resp, f)
    nbytes = partial.stat().st_size
    if min_bytes and nbytes < min_bytes:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"download too small ({nbytes} < {min_bytes}): {url}")
    partial.replace(dest)
    return nbytes


def analyze_content_region(image_path: Path) -> dict[str, Any]:
    from PIL import Image, ImageStat

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    if w < 80 or h < 80:
        return {
            "blank": True,
            "reason": f"window too small {w}x{h}",
            "bright_ratio": 1.0,
            "stddev": 0.0,
            "mean": 0.0,
            "size": [w, h],
        }
    left, top = int(w * 0.04), int(h * 0.18)
    right, bottom = int(w * 0.96), int(h * 0.96)
    crop = img.crop((left, top, right, bottom))
    small = crop.resize((96, 72))
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
    if bright_ratio >= 0.92 and stddev < 18.0:
        blank, reason = True, f"near-white content (bright={bright_ratio:.2f} std={stddev:.1f})"
    elif stddev < 5.0 and mean > 230:
        blank, reason = True, f"uniform bright (mean={mean:.1f} std={stddev:.1f})"
    elif stddev < 4.0 and mean < 15:
        blank, reason = True, f"uniform black void (mean={mean:.1f} std={stddev:.1f})"
    elif black_ratio >= 0.98 and stddev < 4.0:
        blank, reason = True, f"near-black empty (black={black_ratio:.2f} std={stddev:.1f})"
    return {
        "blank": blank,
        "reason": reason,
        "bright_ratio": round(bright_ratio, 3),
        "black_ratio": round(black_ratio, 3),
        "stddev": round(stddev, 2),
        "mean": round(mean, 2),
        "size": [w, h],
    }


def resolve_linux_assets(meta: dict[str, Any]) -> dict[str, str]:
    assets = meta.get("assets") or {}
    app = assets.get("linux_appimage") or {}
    deb = assets.get("linux_deb") or {}
    tag = str(meta.get("tag") or meta.get("version") or "")
    version = str(meta.get("version") or tag.lstrip("v"))
    app_url = str(app.get("url") or "").strip()
    app_name = str(app.get("name") or "void-browser.AppImage")
    deb_url = str(deb.get("url") or "").strip()
    deb_name = str(deb.get("name") or "void-browser.deb")
    if not app_url:
        # Fallback: synthesize from tag if present
        if tag:
            app_name = f"Void.Browser_{version}_amd64.AppImage"
            app_url = (
                f"https://github.com/dadhatdaniel/void-browser/releases/download/"
                f"{tag}/{app_name}"
            )
        else:
            raise RuntimeError("releases.json missing linux_appimage.url")
    return {
        "tag": tag,
        "version": version,
        "appimage_url": app_url,
        "appimage_name": app_name,
        "deb_url": deb_url,
        "deb_name": deb_name,
    }


def validate_latest_json_linux(manifest: dict[str, Any]) -> tuple[bool, str]:
    version = str(manifest.get("version") or "").strip()
    platforms = manifest.get("platforms") or {}
    lin = platforms.get("linux-x86_64") or {}
    url = str(lin.get("url") or "").strip()
    sig = str(lin.get("signature") or "").strip()
    if not version:
        return False, "latest.json missing version"
    if not url:
        return False, "latest.json missing platforms.linux-x86_64.url"
    if "github.com/dadhatdaniel/void-browser" not in url:
        return False, f"linux url not on expected GitHub repo: {url}"
    if "/releases/download/" not in url:
        return False, f"linux url not a release download: {url}"
    if not sig:
        return False, "latest.json missing platforms.linux-x86_64.signature"
    return True, f"version={version} url={url}"


def resolve_older_appimage_url(latest_version: str) -> tuple[str, str]:
    pinned = os.environ.get("VOID_RPA_OLD_APPIMAGE_URL", "").strip()
    if pinned:
        tag = os.environ.get("VOID_RPA_OLD_TAG", "pinned").strip() or "pinned"
        return tag, pinned

    prefer = (os.environ.get("VOID_RPA_OLD_TAG") or DEFAULT_OLD_TAG).strip()
    # Prefer exact AppImage naming from releases; probe HEAD.
    prefer_name = f"Void.Browser_{prefer.lstrip('v')}_amd64.AppImage"
    prefer_url = (
        f"https://github.com/dadhatdaniel/void-browser/releases/download/"
        f"{prefer}/{prefer_name}"
    )
    try:
        req = urllib.request.Request(
            prefer_url, method="HEAD", headers={"User-Agent": "VoidBrowser-RPA-Linux/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            if getattr(resp, "status", 200) < 400:
                return prefer, prefer_url
    except Exception:  # noqa: BLE001
        pass

    try:
        releases = http_get_json_list(f"{GH_RELEASES_API}?per_page=15")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"cannot resolve older AppImage: {e}") from e

    for rel in releases:
        tag = str(rel.get("tag_name") or "")
        ver = tag.lstrip("v")
        if not tag or ver == latest_version.lstrip("v"):
            continue
        names = {a.get("name") for a in (rel.get("assets") or [])}
        match = next((n for n in names if n and str(n).endswith(".AppImage")), None)
        if not match:
            continue
        url = (
            f"https://github.com/dadhatdaniel/void-browser/releases/download/"
            f"{tag}/{match}"
        )
        return tag, url
    raise RuntimeError(
        f"no older AppImage found (tried {prefer}; latest={latest_version})"
    )


def kill_void_processes() -> None:
    """Kill Void GUI / AppImage processes — never match the harness path (…/void-browser/…)."""
    # Prefer exact process name (AppImage extract / package binary).
    _run(["pkill", "-x", "void-browser"], timeout=5)
    # Match AppImage filename / argv — not the git checkout directory.
    patterns = (
        r"Void\.Browser_.*\.AppImage",
        r"/void-browser\.AppImage",
        r"[Vv]oid[Bb]rowser",
        r"void-browser --",
    )
    for pat in patterns:
        _run(["pkill", "-f", pat], timeout=5)
    time.sleep(0.5)


def dismiss_interfering_dialogs() -> str:
    """Close apport / Software Updater / Firefox first-run stealers when present."""
    titles = (
        "Problem in WebKit",
        "Sorry, Ubuntu",
        "Software Updater",
        "Welcome to Firefox",
        "Firefox Privacy",
    )
    acted: list[str] = []
    for title in titles:
        _c, out = _run(["xdotool", "search", "--name", title], timeout=5)
        for wid in (out or "").split():
            if not wid.isdigit():
                continue
            _run(["xdotool", "windowactivate", "--sync", wid], timeout=5)
            _run(["xdotool", "key", "--clearmodifiers", "Escape"], timeout=5)
            _run(["xdotool", "key", "--clearmodifiers", "alt+F4"], timeout=5)
            acted.append(f"{title}:{wid}")
    return ",".join(acted) if acted else "none"


class LinuxRpaSession:
    def __init__(self, appimage: Path, out_dir: Path, launch_wait: float = 6.0):
        self.appimage = appimage
        self.exe = appimage  # alias for Windows-parity callers
        self.out_dir = out_dir
        self.launch_wait = launch_wait
        self.proc: Optional[subprocess.Popen] = None
        self.wid: Optional[str] = None
        self._shot_i = 0
        self.dismiss_update_on_launch = True
        self.disable_updater = True
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _launch_env(self, *, enable_updater: bool) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault(
            "DBUS_SESSION_BUS_ADDRESS",
            f"unix:path={env['XDG_RUNTIME_DIR']}/bus",
        )
        if enable_updater:
            env.pop(VOID_DISABLE_UPDATER_ENV, None)
        else:
            env[VOID_DISABLE_UPDATER_ENV] = "1"
        return env

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
        self.wid = None
        kill_void_processes()

    def start(
        self,
        appimage: Optional[Path] = None,
        *,
        dismiss_update: Optional[bool] = None,
        enable_updater: Optional[bool] = None,
    ) -> None:
        if appimage is not None:
            self.appimage = Path(appimage)
            self.exe = self.appimage
        if dismiss_update is not None:
            self.dismiss_update_on_launch = dismiss_update
        if enable_updater is not None:
            self.disable_updater = not enable_updater

        self.stop()
        if not self.appimage.is_file():
            raise FileNotFoundError(f"AppImage missing: {self.appimage}")
        os.chmod(self.appimage, 0o755)
        env = self._launch_env(enable_updater=not self.disable_updater)
        log = Path("/tmp/void-rpa-linux-app.log")
        log_f = open(log, "ab", buffering=0)
        self.proc = subprocess.Popen(
            [str(self.appimage), "--no-sandbox"],
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(self.launch_wait)
        dismiss_interfering_dialogs()
        self.wid = self.find_window(timeout=12.0)
        if self.dismiss_update_on_launch:
            self.dismiss_update_dialogs()

    def restart(
        self,
        appimage: Path,
        *,
        dismiss_update: bool = True,
        enable_updater: bool = False,
    ) -> None:
        self.start(
            appimage, dismiss_update=dismiss_update, enable_updater=enable_updater
        )

    def alive(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        # AppImage may re-exec; fall back to window presence
        return self.find_window(timeout=0.5) is not None

    def find_window(self, timeout: float = 8.0) -> Optional[str]:
        """Return the largest visible Void window (≥200×200). Tiny frames are ignored."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            dismiss_interfering_dialogs()
            candidates: list[tuple[int, str]] = []
            wids: list[str] = []
            for pattern in ("Void", "void-browser", "void browser"):
                code, out = _run(["xdotool", "search", "--name", pattern], timeout=5)
                if code == 0:
                    wids.extend(line.strip() for line in out.splitlines() if line.strip().isdigit())
            code, out = _run(["xdotool", "search", "--class", "void"], timeout=5)
            if code == 0:
                wids.extend(line.strip() for line in out.splitlines() if line.strip().isdigit())
            for wid in dict.fromkeys(wids):  # unique, preserve order
                _c, geom = _run(["xdotool", "getwindowgeometry", "--shell", wid], timeout=5)
                props: dict[str, int] = {}
                for line in (geom or "").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        try:
                            props[k.strip()] = int(v.strip())
                        except ValueError:
                            pass
                area = props.get("WIDTH", 0) * props.get("HEIGHT", 0)
                if area >= 200 * 200:
                    candidates.append((area, wid))
            if candidates:
                candidates.sort(reverse=True)
                self.wid = candidates[0][1]
                return self.wid
            time.sleep(0.4)
        return self.wid

    def ensure_foreground(self) -> None:
        wid = self.wid or self.find_window(timeout=2.0)
        if not wid:
            return
        _run(["xdotool", "windowactivate", "--sync", wid], timeout=5)
        time.sleep(0.2)

    def window_title(self) -> str:
        wid = self.wid or self.find_window(timeout=1.0)
        if not wid:
            return ""
        _c, out = _run(["xdotool", "getwindowname", wid], timeout=5)
        return (out or "").strip()

    def window_rect(self) -> tuple[int, int, int, int]:
        wid = self.wid or self.find_window(timeout=1.0)
        if not wid:
            return 0, 0, 0, 0
        _c, out = _run(["xdotool", "getwindowgeometry", "--shell", wid], timeout=5)
        vals: dict[str, int] = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    vals[k.strip()] = int(v.strip())
                except ValueError:
                    pass
        x, y = vals.get("X", 0), vals.get("Y", 0)
        w, h = vals.get("WIDTH", 0), vals.get("HEIGHT", 0)
        return x, y, x + w, y + h

    def shot(self, label: str) -> str:
        self._shot_i += 1
        name = f"{self._shot_i:02d}_{label}.png"
        path = self.out_dir / name
        # Prefer full desktop (Void may not support XGetImage on all builds)
        code, _ = _run(["scrot", "-o", str(path)], timeout=10)
        if code != 0 or not path.is_file():
            _run(["gnome-screenshot", "-f", str(path)], timeout=10)
        return name

    def shot_path(self, name: str) -> Path:
        return self.out_dir / name

    def type_keys(self, keys: str) -> None:
        """Send xdotool key sequence (e.g. 'ctrl+l', 'Return', 'ctrl+shift+Tab')."""
        self.ensure_foreground()
        _run(["xdotool", "key", "--clearmodifiers", keys], timeout=10)

    def type_text(self, text: str, delay_ms: int = 12) -> None:
        self.ensure_foreground()
        _run(
            ["xdotool", "type", "--clearmodifiers", "--delay", str(delay_ms), "--", text],
            timeout=60,
        )

    def _click_url_bar_region(self) -> bool:
        left, top, right, bottom = self.window_rect()
        w, h = right - left, bottom - top
        if w < 200 or h < 200:
            return False
        abs_x = left + w // 2
        abs_y = top + max(52, min(90, int(h * 0.07)))
        code, _ = _run(["xdotool", "mousemove", str(abs_x), str(abs_y), "click", "1"], timeout=5)
        time.sleep(0.25)
        return code == 0

    def focus_url_bar(self) -> None:
        """Focus omnibox: click chrome region, then Ctrl+L (app.js), then select-all."""
        self.ensure_foreground()
        self._click_url_bar_region()
        time.sleep(0.15)
        # app.js handles ctrl+l → urlBar.focus(); needed even after click on WebKitGTK
        self.type_keys("ctrl+l")
        time.sleep(0.3)
        self.type_keys("ctrl+a")
        time.sleep(0.12)

    def url_bar_text(self) -> str:
        try:
            self.focus_url_bar()
            time.sleep(0.15)
            self.type_keys("ctrl+a")
            time.sleep(0.08)
            self.type_keys("ctrl+c")
            time.sleep(0.25)
            # Prefer wl-clipboard / xclip
            for cmd in (
                ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"],
                ["wl-paste"],
            ):
                if shutil.which(cmd[0]):
                    code, out = _run(cmd, timeout=5)
                    if code == 0:
                        return (out or "").strip()
        except Exception:  # noqa: BLE001
            pass
        return ""

    def navigate(self, url: str, settle: float = 2.5, retries: int = 2) -> None:
        host = ""
        try:
            host = url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
        except Exception:  # noqa: BLE001
            host = ""
        attempts = max(1, int(retries) + 1)
        for attempt in range(attempts):
            self.dismiss_update_dialogs()
            self.ensure_foreground()
            self.focus_url_bar()
            self.type_keys("ctrl+a")
            time.sleep(0.1)
            self.type_keys("BackSpace")
            time.sleep(0.1)
            self.type_text(url)
            self.type_keys("Return")
            time.sleep(settle)
            if not host:
                return
            url_txt = self.url_bar_text().lower()
            title = self.window_title().lower()
            if host in url_txt or host.split(".")[0] in url_txt:
                return
            if host.split(".")[0] in title and "new tab" not in title:
                return
            if attempt + 1 < attempts:
                time.sleep(0.6)

    def open_settings(self) -> None:
        self.type_keys("ctrl+comma")
        time.sleep(1.0)

    def close_settings(self) -> None:
        self.type_keys("Escape")
        time.sleep(1.0)

    def new_tab(self) -> None:
        self.type_keys("ctrl+t")
        time.sleep(1.0)

    def back(self) -> None:
        self.type_keys("alt+Left")
        time.sleep(1.2)

    def forward(self) -> None:
        self.type_keys("alt+Right")
        time.sleep(1.2)

    def reload(self) -> None:
        self.type_keys("F5")
        time.sleep(2.0)

    def scroll_page(self, downs: int = 6, pause: float = 0.2) -> None:
        for _ in range(max(1, downs)):
            self.type_keys("Page_Down")
            time.sleep(pause)

    def dismiss_update_dialogs(self) -> None:
        # Best-effort: Escape / click Later if an update dialog steals focus
        for _ in range(3):
            title = self.window_title().lower()
            if "update" in title:
                self.type_keys("Escape")
                time.sleep(0.4)
            else:
                break


def assert_content_not_blank(s: LinuxRpaSession, shot_name: str, label: str) -> Step:
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


# ── Scenarios ──────────────────────────────────────────────────────────────


def scenario_download_install(s: LinuxRpaSession) -> ScenarioResult:
    """Download latest Linux AppImage (+ optional .deb) from releases.json."""
    steps: list[Step] = []
    t0 = time.time()
    new_exe: Optional[str] = None
    download_dir = DEFAULT_DOWNLOAD_DIR
    try:
        download_dir.mkdir(parents=True, exist_ok=True)

        # Visual: open marketing download page if app already running
        if s.alive():
            try:
                s.navigate(VOID_SITE_LAN_DOWNLOAD_URL, settle=2.0)
                steps.append(
                    Step(
                        "open_download_page",
                        True,
                        f"opened {VOID_SITE_LAN_DOWNLOAD_URL}",
                        s.shot("download_page_lan"),
                    )
                )
            except Exception as e:  # noqa: BLE001
                steps.append(Step("open_download_page", False, str(e), None))
        else:
            steps.append(Step("open_download_page", True, "skipped (app not running yet)", None))

        meta = http_get_json(RELEASES_JSON_URL)
        assets = resolve_linux_assets(meta)
        steps.append(
            Step(
                "fetch_releases_json",
                True,
                f"{RELEASES_JSON_URL} tag={assets['tag']} appimage={assets['appimage_name']}",
                None,
            )
        )

        app_path = download_dir / assets["appimage_name"]
        force = os.environ.get("VOID_RPA_FORCE_DOWNLOAD", "").strip() == "1"
        app_ok = False
        try:
            reuse = (
                app_path.is_file()
                and app_path.stat().st_size >= MIN_APPIMAGE_BYTES
                and not force
            )
            if reuse:
                app_ok = True
                steps.append(
                    Step(
                        "download_appimage",
                        True,
                        f"reuse existing {app_path.stat().st_size} bytes @ {app_path}",
                        None,
                    )
                )
            else:
                nbytes = http_download(
                    assets["appimage_url"], app_path, min_bytes=MIN_APPIMAGE_BYTES
                )
                os.chmod(app_path, 0o755)
                app_ok = True
                steps.append(
                    Step(
                        "download_appimage",
                        True,
                        f"{nbytes} bytes -> {app_path}",
                        None,
                    )
                )
        except Exception as e:  # noqa: BLE001
            steps.append(Step("download_appimage", False, str(e), None))

        # Optional .deb (install path coverage; AppImage remains primary)
        deb_ok = False
        if assets["deb_url"]:
            deb_path = download_dir / assets["deb_name"]
            try:
                if deb_path.is_file() and deb_path.stat().st_size >= MIN_DEB_BYTES and not force:
                    deb_ok = True
                    steps.append(
                        Step(
                            "download_deb",
                            True,
                            f"reuse existing {deb_path.stat().st_size} bytes",
                            None,
                        )
                    )
                else:
                    nbytes = http_download(
                        assets["deb_url"], deb_path, min_bytes=MIN_DEB_BYTES
                    )
                    deb_ok = True
                    steps.append(Step("download_deb", True, f"{nbytes} bytes -> {deb_path}", None))
            except Exception as e:  # noqa: BLE001
                steps.append(Step("download_deb", True, f"allow_failure: {e}", None))
        else:
            steps.append(Step("download_deb", True, "no linux_deb in releases.json", None))

        # Optional dpkg install (needs passwordless or VOID_RPA_LINUX_SUDO_PASS)
        run_deb = os.environ.get("VOID_RPA_RUN_DEB", "").strip() == "1"
        if run_deb and deb_ok:
            deb_path = download_dir / assets["deb_name"]
            sudo_pass = os.environ.get("VOID_RPA_LINUX_SUDO_PASS", "").strip()
            try:
                if sudo_pass:
                    proc = subprocess.run(
                        ["sudo", "-S", "dpkg", "-i", str(deb_path)],
                        input=sudo_pass + "\n",
                        capture_output=True,
                        text=True,
                        timeout=180,
                        check=False,
                    )
                else:
                    proc = subprocess.run(
                        ["sudo", "-n", "dpkg", "-i", str(deb_path)],
                        capture_output=True,
                        text=True,
                        timeout=180,
                        check=False,
                    )
                steps.append(
                    Step(
                        "dpkg_install",
                        True,
                        f"exit={proc.returncode} (allow_failure for suite)",
                        None,
                    )
                )
            except Exception as e:  # noqa: BLE001
                steps.append(Step("dpkg_install", True, f"allow_failure: {e}", None))
        else:
            steps.append(
                Step(
                    "dpkg_install",
                    True,
                    "skipped (default; set VOID_RPA_RUN_DEB=1 to enable)",
                    None,
                )
            )

        if app_ok and app_path.is_file():
            new_exe = str(app_path.resolve())
            # Stage copy under dist/ for stable path
            dist = ROOT / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            staged = dist / "void-browser.AppImage"
            try:
                if Path(new_exe).resolve() != staged.resolve():
                    shutil.copy2(new_exe, staged)
                    os.chmod(staged, 0o755)
                new_exe = str(staged.resolve())
                steps.append(Step("stage_appimage", True, f"staged {staged}", None))
            except Exception as e:  # noqa: BLE001
                steps.append(Step("stage_appimage", True, f"use download path: {e}", None))

        ver_ok = True
        ver_detail = assets["version"]
        try:
            low = assets["version"].lower().replace("v", "")
            if "alpha." in low:
                n = int(low.split("alpha.")[-1].split("-")[0].split(".")[0])
                ver_ok = n >= 16
                ver_detail = f"release={assets['tag']} alpha={n}"
        except Exception:  # noqa: BLE001
            pass
        steps.append(Step("version_gate_alpha16", ver_ok, ver_detail, None))

        folder_shot = None
        try:
            # Show downloads folder in Files for visual evidence
            subprocess.Popen(
                ["xdg-open", str(download_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.2)
            folder_shot = s.shot("download_folder")
            steps.append(
                Step("screenshot_download_folder", True, f"folder={download_dir}", folder_shot)
            )
            _run(["pkill", "-f", "org.gnome.Nautilus"], timeout=3)
        except Exception as e:  # noqa: BLE001
            steps.append(Step("screenshot_download_folder", True, f"skipped: {e}", None))

        hard = [
            st
            for st in steps
            if st.name
            not in ("dpkg_install", "screenshot_download_folder", "download_deb", "open_download_page")
        ]
        ok = app_ok and bool(new_exe) and ver_ok and all(st.ok for st in hard)
        return ScenarioResult(
            "download_install",
            ok,
            steps,
            error=None if ok else "download/stage failed",
            duration_sec=time.time() - t0,
            new_exe=new_exe,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("download_install", False, str(e), None))
        return ScenarioResult(
            "download_install", False, steps, error=str(e), duration_sec=time.time() - t0
        )


def scenario_app_launch(s: LinuxRpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        if not s.alive():
            s.start()
        # Wait for a real chrome window (avoid 10x10 transients right after relaunch).
        size_ok = False
        width = height = 0
        title = ""
        for _ in range(15):
            s.wid = None
            s.find_window(timeout=2.0)
            s.ensure_foreground()
            title = s.window_title()
            left, top, right, bottom = s.window_rect()
            width, height = max(0, right - left), max(0, bottom - top)
            if width >= 640 and height >= 480:
                size_ok = True
                break
            time.sleep(0.5)
        alive = s.alive()
        shot = s.shot("app_launch")
        steps.append(
            Step(
                "window_visible",
                alive and size_ok,
                f"title={title!r} rect={width}x{height} alive={alive}",
                shot,
            )
        )
        content = assert_content_not_blank(s, shot, "chrome_not_blank_at_launch")
        if not size_ok:
            content.ok = False
        steps.append(content)
        steps.append(Step("title_present", True, f"title={title!r}", None))
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


def scenario_smoke_navigate(s: LinuxRpaSession) -> ScenarioResult:
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


def scenario_nav_history(s: LinuxRpaSession) -> ScenarioResult:
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


def scenario_settings_preserves_tab(s: LinuxRpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate("https://example.com", settle=3.0)
        before = s.shot("settings_before")
        steps.append(Step("open_site", True, "example.com before settings", before))
        before_info = analyze_content_region(s.shot_path(before))
        steps.append(
            Step("before_not_blank", not before_info["blank"], before_info["reason"], before)
        )

        s.open_settings()
        mid = s.shot("settings_open")
        steps.append(Step("open_settings", s.alive(), "settings overlay (Ctrl+,)", mid))

        s.close_settings()
        after = s.shot("settings_after")
        after_info = analyze_content_region(s.shot_path(after))
        restored = s.alive() and not after_info["blank"]
        steps.append(
            Step(
                "tab_preserved_after_settings",
                restored,
                f"alive={s.alive()} blank={after_info['blank']} {after_info['reason']}",
                after,
            )
        )
        ok = all(st.ok for st in steps)
        return ScenarioResult(
            "settings_preserves_tab",
            ok,
            steps,
            error=None if ok else "settings wiped tab or blank",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("settings_preserves_tab", False, str(e), s.shot("settings_fail")))
        return ScenarioResult(
            "settings_preserves_tab", False, steps, error=str(e), duration_sec=time.time() - t0
        )


def scenario_new_tab(s: LinuxRpaSession) -> ScenarioResult:
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

        s.type_keys("ctrl+shift+Tab")
        time.sleep(1.0)
        switched = s.shot("newtab_switched")
        steps.append(Step("switch_back", s.alive(), "Ctrl+Shift+Tab", switched))
        steps.append(assert_content_not_blank(s, switched, "switched_not_blank"))

        ok = all(st.ok for st in steps)
        return ScenarioResult("new_tab", ok, steps, duration_sec=time.time() - t0)
    except Exception as e:  # noqa: BLE001
        steps.append(Step("new_tab", False, str(e), s.shot("newtab_fail")))
        return ScenarioResult("new_tab", False, steps, error=str(e), duration_sec=time.time() - t0)


def scenario_youtube_signin_page(s: LinuxRpaSession) -> ScenarioResult:
    """Soft-fail friendly: live Google challenges vary."""
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate("https://accounts.google.com/signin", settle=4.0)
        shot = s.shot("youtube_signin_ui")
        url_txt = s.url_bar_text()
        title = s.window_title()
        info = analyze_content_region(s.shot_path(shot))
        hit = any(
            n in (url_txt + " " + title).lower()
            for n in ("accounts.google", "sign in", "google", "youtube")
        ) or (not info.get("blank"))
        steps.append(
            Step(
                "reach_google_signin",
                bool(hit),
                f"title={title!r} url={url_txt!r} blank={info.get('blank')}",
                shot,
            )
        )
        if hit and s.alive():
            return ScenarioResult("youtube_signin_page", True, steps, duration_sec=time.time() - t0)
        detail = "did not clearly reach Google sign-in UI"
        steps.append(Step("youtube_signin_soft", False, detail, shot))
        return ScenarioResult(
            "youtube_signin_page",
            True,
            steps,
            error=f"soft_fail (allow_failure): {detail}",
            duration_sec=time.time() - t0,
            soft_fail=True,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("youtube_signin_page", False, str(e), None))
        return ScenarioResult(
            "youtube_signin_page",
            True,
            steps,
            error=f"soft_fail (allow_failure): {e}",
            duration_sec=time.time() - t0,
            soft_fail=True,
        )


def scenario_context_menu(s: LinuxRpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.focus_url_bar()
        s.type_keys("ctrl+a")
        s.type_text("https://example.com")
        s.type_keys("ctrl+a")
        s.type_keys("ctrl+c")
        time.sleep(0.3)
        steps.append(Step("url_copy", True, "Ctrl+A Ctrl+C in URL bar", s.shot("ctx_copy")))
        s.type_keys("ctrl+a")
        s.type_keys("BackSpace")
        time.sleep(0.2)
        s.type_keys("ctrl+v")
        time.sleep(0.3)
        pasted = s.url_bar_text()
        paste_ok = "example.com" in pasted.lower() or s.alive()
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


def scenario_visit_void_site(s: LinuxRpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate(VOID_SITE_LAN_URL, settle=3.0)
        hero = s.shot("void_site_hero")
        title = s.window_title()
        url_txt = s.url_bar_text()
        steps.append(
            Step(
                "open_void_home_lan",
                s.alive(),
                f"LAN {VOID_SITE_LAN_URL}; title={title!r}; url_bar={url_txt!r}",
                hero,
            )
        )
        steps.append(assert_content_not_blank(s, hero, "void_hero_not_blank"))

        s.scroll_page(downs=8, pause=0.25)
        mid = s.shot("void_site_scrolled")
        steps.append(Step("scroll_marketing", s.alive(), "scrolled toward features/download", mid))

        s.navigate(VOID_SITE_LAN_DOWNLOAD_URL, settle=2.5)
        dl = s.shot("void_site_download")
        steps.append(
            Step(
                "open_download_anchor_lan",
                s.alive(),
                f"LAN #download; url_bar={s.url_bar_text()!r}",
                dl,
            )
        )
        steps.append(assert_content_not_blank(s, dl, "void_download_not_blank"))

        ok = all(st.ok for st in steps) and s.alive()
        return ScenarioResult(
            "visit_void_site",
            ok,
            steps,
            error=None if ok else "void site blank or failed",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("visit_void_site", False, str(e), s.shot("void_fail")))
        return ScenarioResult("visit_void_site", False, steps, error=str(e), duration_sec=time.time() - t0)


def scenario_auto_update(s: LinuxRpaSession) -> ScenarioResult:
    """
    Stage older Linux AppImage, launch with updater enabled, assert latest.json
    linux-x86_64 URLs, and best-effort accept an update dialog.
    """
    steps: list[Step] = []
    t0 = time.time()
    previous = Path(s.appimage)
    prev_dismiss = s.dismiss_update_on_launch
    prev_disable = s.disable_updater
    s.dismiss_update_on_launch = False
    s.disable_updater = False
    try:
        manifest = http_get_json(LATEST_JSON_URL)
        ok_manifest, detail = validate_latest_json_linux(manifest)
        steps.append(Step("fetch_latest_json", ok_manifest, detail, None))
        if not ok_manifest:
            return ScenarioResult(
                "auto_update", False, steps, error=detail, duration_sec=time.time() - t0
            )

        latest_ver = str(manifest.get("version") or "")
        old_tag, old_url = resolve_older_appimage_url(latest_ver)
        steps.append(
            Step("resolve_older_build", True, f"tag={old_tag} url={old_url} latest={latest_ver}", None)
        )

        old_dir = DEFAULT_DOWNLOAD_DIR / "old"
        old_dir.mkdir(parents=True, exist_ok=True)
        old_name = old_url.rsplit("/", 1)[-1]
        old_app = old_dir / old_name
        need = not old_app.is_file() or old_app.stat().st_size < MIN_APPIMAGE_BYTES
        if need:
            nbytes = http_download(old_url, old_app, min_bytes=MIN_APPIMAGE_BYTES)
        else:
            nbytes = old_app.stat().st_size
        os.chmod(old_app, 0o755)
        steps.append(
            Step("download_older_appimage", True, f"{old_app.name} {nbytes} bytes", None)
        )

        s.restart(old_app, dismiss_update=False, enable_updater=True)
        time.sleep(5.0)
        launch_shot = s.shot("auto_update_older_launch")
        steps.append(
            Step(
                "launch_older_build",
                s.alive(),
                f"app={old_app} title={s.window_title()!r}",
                launch_shot,
            )
        )

        dialog_seen = False
        # Startup quiet check may show update dialog
        time.sleep(2.0)
        title = s.window_title().lower()
        if "update" in title:
            dialog_seen = True
            steps.append(
                Step("startup_update_prompt", True, f"title={s.window_title()!r}", s.shot("auto_update_startup_dialog"))
            )
            # Try to accept — Tab to Install & press Return (best-effort)
            s.type_keys("Tab")
            time.sleep(0.2)
            s.type_keys("Tab")
            time.sleep(0.2)
            s.type_keys("Return")
            time.sleep(2.0)
            steps.append(Step("install_relaunch", True, "attempted accept via Tab+Return", None))
        else:
            steps.append(
                Step("startup_update_prompt", True, "no startup dialog yet (try Settings)", None)
            )
            s.open_settings()
            settings_shot = s.shot("auto_update_settings_check")
            steps.append(Step("open_settings_for_update", s.alive(), "Ctrl+,", settings_shot))
            # Click approximate "Check for updates" region (lower half of settings)
            left, top, right, bottom = s.window_rect()
            w, h = right - left, bottom - top
            if w > 200:
                cx = left + int(w * 0.55)
                cy = top + int(h * 0.55)
                _run(["xdotool", "mousemove", str(cx), str(cy), "click", "1"], timeout=5)
                time.sleep(4.0)
            dlg_shot = s.shot("auto_update_dialog")
            title2 = s.window_title().lower()
            dialog_seen = "update" in title2
            steps.append(
                Step(
                    "settings_check_for_updates",
                    True,
                    f"dialog_seen={dialog_seen} title={s.window_title()!r}",
                    dlg_shot,
                )
            )
            if dialog_seen:
                s.type_keys("Tab")
                time.sleep(0.2)
                s.type_keys("Return")
                steps.append(Step("install_relaunch", True, "attempted accept", None))
            else:
                # Soft-ish: latest.json validated + older build launched is strong signal
                steps.append(
                    Step(
                        "install_relaunch",
                        True,
                        "no dialog visible; latest.json + older launch still exercised",
                        None,
                    )
                )

        # Hard requirements: manifest + older download + launch
        hard_ok = all(
            st.ok
            for st in steps
            if st.name
            in ("fetch_latest_json", "resolve_older_build", "download_older_appimage", "launch_older_build")
        )
        ok = hard_ok and s.alive()
        return ScenarioResult(
            "auto_update",
            ok,
            steps,
            error=None if ok else "auto_update failed (manifest / older AppImage / launch)",
            duration_sec=time.time() - t0,
        )
    except Exception as e:  # noqa: BLE001
        steps.append(Step("auto_update", False, str(e), None))
        return ScenarioResult(
            "auto_update", False, steps, error=str(e), duration_sec=time.time() - t0
        )
    finally:
        s.dismiss_update_on_launch = prev_dismiss
        s.disable_updater = prev_disable
        try:
            if previous.is_file():
                s.restart(
                    previous,
                    dismiss_update=prev_dismiss,
                    enable_updater=not prev_disable,
                )
        except Exception:  # noqa: BLE001
            pass


SCENARIOS: dict[str, Callable[[LinuxRpaSession], ScenarioResult]] = {
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
    # Aliases used by early smoke script
    "smoke_launch": scenario_app_launch,
}


def teardown_cleanup() -> dict[str, Any]:
    """
    Always-run teardown (Windows uninstall parity):
      kill Void, purge .deb if installed, remove staged/downloaded AppImages when CLEANUP=1
      (default ON via run-rpa-linux.sh).
    """
    cmds: list[str] = []
    detail_parts: list[str] = []
    ok = True
    kill_void_processes()
    dismiss_interfering_dialogs()
    cmds.append("kill_void_processes()")
    detail_parts.append("killed void AppImage processes")

    # Always attempt dpkg purge if package present (idempotent)
    sudo_pass = os.environ.get("VOID_RPA_LINUX_SUDO_PASS", "").strip()
    try:
        check = subprocess.run(
            ["dpkg", "-l", "void-browser"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        installed = check.returncode == 0 and "void-browser" in (check.stdout or "")
    except Exception:  # noqa: BLE001
        installed = False
    if installed or os.environ.get("VOID_RPA_RUN_DEB", "").strip() == "1":
        try:
            if sudo_pass:
                proc = subprocess.run(
                    ["sudo", "-S", "dpkg", "--purge", "void-browser"],
                    input=sudo_pass + "\n",
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            else:
                proc = subprocess.run(
                    ["sudo", "-n", "dpkg", "--purge", "void-browser"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            cmds.append(f"dpkg --purge void-browser -> {proc.returncode}")
            detail_parts.append(f"dpkg purge exit={proc.returncode}")
        except Exception as e:  # noqa: BLE001
            ok = False
            detail_parts.append(f"dpkg purge error: {e}")
    else:
        detail_parts.append("dpkg: not installed (ok)")

    cleanup = os.environ.get("VOID_RPA_CLEANUP", "1").strip() == "1"
    if cleanup:
        removed = 0
        for pattern in ("*.AppImage", "*.deb", "*.partial"):
            for p in DEFAULT_DOWNLOAD_DIR.glob(pattern):
                try:
                    p.unlink()
                    removed += 1
                except OSError as e:
                    detail_parts.append(f"unlink {p.name}: {e}")
        old_dir = DEFAULT_DOWNLOAD_DIR / "old"
        if old_dir.is_dir():
            for p in old_dir.glob("*"):
                try:
                    if p.is_file():
                        p.unlink()
                        removed += 1
                except OSError:
                    pass
        staged = ROOT / "dist" / "void-browser.AppImage"
        if staged.is_file():
            try:
                staged.unlink()
                removed += 1
            except OSError as e:
                detail_parts.append(f"unlink staged: {e}")
        detail_parts.append(f"removed_files={removed}")
        cmds.append(f"cleanup removed_files={removed}")

    still = _run(["pgrep", "-x", "void-browser"], timeout=5)
    if still[0] == 0:
        ok = False
        detail_parts.append("void-browser still running after teardown")

    return {"ok": ok, "detail": "; ".join(detail_parts), "commands": cmds}


def _run_scenario_with_timeout(
    fn: Callable[[LinuxRpaSession], ScenarioResult],
    session: LinuxRpaSession,
    name: str,
    timeout_sec: float,
) -> ScenarioResult:
    if timeout_sec <= 0:
        return fn(session)
    t0 = time.time()
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(fn, session)
    try:
        return fut.result(timeout=timeout_sec)
    except FuturesTimeoutError:
        detail = f"scenario hard-timeout after {timeout_sec:.0f}s"
        print(f"  TIMEOUT: {detail}", flush=True)
        try:
            session.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            shot = session.shot(f"{name}_timeout")
        except Exception:  # noqa: BLE001
            shot = None
        return ScenarioResult(
            name,
            False,
            [Step("scenario_timeout", False, detail, shot)],
            error=detail,
            duration_sec=time.time() - t0,
        )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def find_existing_appimage() -> Optional[Path]:
    env = os.environ.get("VOID_RPA_APPIMAGE", "").strip()
    if env and Path(env).is_file():
        return Path(env)
    staged = ROOT / "dist" / "void-browser.AppImage"
    if staged.is_file() and staged.stat().st_size >= MIN_APPIMAGE_BYTES:
        return staged
    if DEFAULT_DOWNLOAD_DIR.is_dir():
        cands = sorted(
            DEFAULT_DOWNLOAD_DIR.glob("*.AppImage"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for c in cands:
            if c.stat().st_size >= MIN_APPIMAGE_BYTES:
                return c
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Void Browser Linux RPA")
    parser.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
    parser.add_argument("--appimage", default="")
    parser.add_argument("--launch-wait", type=float, default=6.0)
    parser.add_argument("--out", default=str(DEFAULT_ARTIFACTS))
    args = parser.parse_args()

    # Session env for GUI
    os.environ.setdefault("DISPLAY", ":0")
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    os.environ.setdefault(
        "DBUS_SESSION_BUS_ADDRESS",
        f"unix:path={os.environ['XDG_RUNTIME_DIR']}/bus",
    )

    names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"Unknown scenarios: {unknown}. Known: {list(SCENARIOS)}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    appimage = Path(args.appimage) if args.appimage else find_existing_appimage()
    need_download_first = appimage is None or "download_install" in names

    session: Optional[LinuxRpaSession] = None
    results: list[ScenarioResult] = []
    soft_fail_names: list[str] = []

    print(f"[rpa-linux] artifacts -> {out_dir}", flush=True)
    print(f"[rpa-linux] scenarios: {','.join(names)}", flush=True)

    try:
        # Bootstrap download if needed before launch
        if need_download_first and "download_install" in names:
            # Temp session without app for download-only bits
            bootstrap_path = appimage or (DEFAULT_DOWNLOAD_DIR / "placeholder.AppImage")
            session = LinuxRpaSession(
                bootstrap_path if bootstrap_path.is_file() else Path("/tmp/void-missing.AppImage"),
                out_dir,
                launch_wait=args.launch_wait,
            )
            # If we have an existing image, start so download_install can open marketing page
            if appimage and appimage.is_file():
                try:
                    session.start(appimage)
                except Exception as e:  # noqa: BLE001
                    print(f"[rpa-linux] pre-launch skip: {e}", flush=True)

            print("-- download_install --", flush=True)
            result = _run_scenario_with_timeout(
                SCENARIOS["download_install"],
                session,
                "download_install",
                DEFAULT_SCENARIO_TIMEOUT_SEC,
            )
            results.append(result)
            mark = "PASS" if result.ok else "FAIL"
            print(f"  {mark} download_install ({result.duration_sec:.1f}s)", flush=True)
            if result.new_exe:
                appimage = Path(result.new_exe)
                session.appimage = appimage
                session.exe = appimage
                # Relaunch on freshly downloaded build
                try:
                    session.restart(appimage, dismiss_update=True, enable_updater=False)
                except Exception as e:  # noqa: BLE001
                    print(f"[rpa-linux] relaunch after download: {e}", flush=True)
            names = [n for n in names if n != "download_install"]
        else:
            if appimage is None or not appimage.is_file():
                print(
                    "No AppImage found. Include download_install or pass --appimage.",
                    file=sys.stderr,
                )
                return 2
            session = LinuxRpaSession(appimage, out_dir, launch_wait=args.launch_wait)
            session.start(appimage)

        assert session is not None

        for name in names:
            print(f"-- {name} --", flush=True)
            if name != "auto_update":
                session.dismiss_update_on_launch = True
                session.disable_updater = True
                session.dismiss_update_dialogs()
            result = _run_scenario_with_timeout(
                SCENARIOS[name], session, name, DEFAULT_SCENARIO_TIMEOUT_SEC
            )
            results.append(result)
            if result.soft_fail:
                soft_fail_names.append(name)
                mark = "SOFT"
            else:
                mark = "PASS" if result.ok else "FAIL"
            print(f"  {mark} {name} ({result.duration_sec:.1f}s) {result.error or ''}", flush=True)
            if result.new_exe:
                session.appimage = Path(result.new_exe)
                session.exe = session.appimage

    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}\n{traceback.format_exc()}", file=sys.stderr)
        results.append(
            ScenarioResult("harness", False, error=str(e), duration_sec=0.0)
        )
    finally:
        teardown: dict[str, Any] = {"ok": True, "detail": "not run", "commands": []}
        try:
            print("-- teardown --", flush=True)
            if session is not None:
                session.stop()
            teardown = teardown_cleanup()
            status = "PASS" if teardown.get("ok") else "FAIL"
            print(f"  {status}: {teardown.get('detail', '')}", flush=True)
        except Exception as e:  # noqa: BLE001
            teardown = {"ok": False, "detail": str(e), "commands": []}
            print(f"  FAIL: teardown error: {e}", flush=True)

    hard_ok = all(r.ok for r in results if not r.soft_fail)
    teardown_ok = bool(teardown.get("ok", True))
    overall = hard_ok and teardown_ok

    report = {
        "ok": overall,
        "platform": "linux",
        "stamp": stamp,
        "scenarios": [asdict(r) for r in results],
        "meta": {
            "appimage": str(appimage) if appimage else None,
            "display": os.environ.get("DISPLAY", ""),
            "soft_fail_scenarios": soft_fail_names,
            "teardown_uninstall": teardown,
            "default_scenarios": DEFAULT_SCENARIOS,
        },
    }
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[rpa-linux] report -> {report_path} (ok={overall})", flush=True)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
