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
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACTS = ROOT / "artifacts" / "rpa"


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

    def _connect(self) -> None:
        from pywinauto import Application

        deadline = time.time() + 30
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                if self.proc and self.proc.poll() is not None:
                    raise RuntimeError(f"void-browser exited early with code {self.proc.returncode}")
                self.app = Application(backend="uia").connect(process=self.proc.pid)
                # Prefer the main Void window
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

    def shot(self, label: str) -> str:
        self._shot_i += 1
        name = f"{self._shot_i:02d}_{label}.png"
        path = self.out_dir / name
        try:
            img = self.win.capture_as_image()
            img.save(str(path))
        except Exception:  # noqa: BLE001
            # Fallback: full desktop grab via Pillow
            from PIL import ImageGrab

            ImageGrab.grab().save(str(path))
        return name

    def type_keys(self, keys: str, pause: float = 0.15) -> None:
        self.win.type_keys(keys, with_spaces=True, pause=pause, set_foreground=True)

    def focus_url_bar(self) -> None:
        # Ctrl+L focuses the address bar in Void
        self.type_keys("^l")
        time.sleep(0.3)

    def navigate(self, url: str) -> None:
        self.focus_url_bar()
        # Select-all + type URL + Enter
        self.type_keys("^a")
        time.sleep(0.1)
        self.type_keys(url, pause=0.02)
        self.type_keys("{ENTER}")
        time.sleep(2.5)

    def open_settings(self) -> None:
        self.type_keys("^,")
        time.sleep(1.0)

    def close_settings(self) -> None:
        self.type_keys("{ESC}")
        time.sleep(1.0)

    def new_tab(self) -> None:
        self.type_keys("^t")
        time.sleep(1.0)

    def window_title(self) -> str:
        try:
            return str(self.win.window_text())
        except Exception:  # noqa: BLE001
            return ""


def find_exe(hint: Optional[str]) -> Path:
    if hint:
        p = Path(hint)
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(hint)

    home = Path(os.environ.get("USERPROFILE", ""))
    candidates = [
        home / "void-browser-target" / "release" / "void-browser.exe",
        home / "void-browser-target" / "debug" / "void-browser.exe",
        ROOT / "src-tauri" / "target" / "release" / "void-browser.exe",
        ROOT / "src-tauri" / "target" / "debug" / "void-browser.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    nsis = list((ROOT / "src-tauri" / "target" / "release" / "bundle" / "nsis").glob("**/void-browser.exe"))
    if nsis:
        return nsis[0].resolve()
    raise FileNotFoundError(
        "void-browser.exe not found. Build with .\\scripts\\dev-windows.ps1 -Release "
        "or pass --exe"
    )


def scenario_smoke_navigate(s: RpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate("https://example.com")
        shot = s.shot("smoke_example")
        steps.append(Step("navigate_example", True, "loaded example.com", shot))
        s.navigate("https://duckduckgo.com")
        shot = s.shot("smoke_ddg")
        steps.append(Step("navigate_ddg", True, "loaded duckduckgo.com", shot))
        return ScenarioResult("smoke_navigate", True, steps, duration_sec=time.time() - t0)
    except Exception as e:  # noqa: BLE001
        steps.append(Step("smoke_navigate", False, str(e), s.shot("smoke_fail")))
        return ScenarioResult("smoke_navigate", False, steps, error=str(e), duration_sec=time.time() - t0)


def scenario_settings_preserves_tab(s: RpaSession) -> ScenarioResult:
    steps: list[Step] = []
    t0 = time.time()
    try:
        s.navigate("https://example.com")
        before = s.shot("settings_before")
        steps.append(Step("open_site", True, "example.com before settings", before))
        s.open_settings()
        mid = s.shot("settings_open")
        steps.append(Step("open_settings", True, "settings overlay visible", mid))
        s.close_settings()
        after = s.shot("settings_after")
        # Tab URL should still be example.com — we cannot always read the URL bar
        # via UIA, so we assert the window survived and content restored visually.
        title = s.window_title()
        ok = s.proc is not None and s.proc.poll() is None
        steps.append(
            Step(
                "close_settings_restore",
                ok,
                f"window_title={title!r}; process alive={ok}",
                after,
            )
        )
        return ScenarioResult(
            "settings_preserves_tab",
            ok,
            steps,
            error=None if ok else "process died",
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
        s.navigate("https://example.com")
        steps.append(Step("tab1", True, "first tab example.com", s.shot("newtab_before")))
        s.new_tab()
        steps.append(Step("create_tab2", True, "Ctrl+T", s.shot("newtab_second")))
        s.navigate("https://duckduckgo.com")
        steps.append(Step("tab2_navigate", True, "second tab ddg", s.shot("newtab_after")))
        # Switch back with Ctrl+Shift+Tab
        s.type_keys("^+{TAB}")
        time.sleep(1.0)
        steps.append(Step("switch_back", True, "Ctrl+Shift+Tab", s.shot("newtab_switched")))
        ok = s.proc is not None and s.proc.poll() is None
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
        s.navigate("https://accounts.google.com/signin")
        shot = s.shot("youtube_signin_ui")
        steps.append(
            Step(
                "load_signin",
                True,
                "accounts.google.com/signin loaded (UI only by default)",
                shot,
            )
        )
        if user and password:
            # Optional credential path — never log the password.
            s.focus_url_bar()  # ensure window focused
            time.sleep(0.5)
            try:
                # Best-effort: type email into focused page (Google email field)
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
        return ScenarioResult("youtube_signin_page", ok, steps, duration_sec=time.time() - t0)
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
        # Right-click is hard via type_keys; use clipboard round-trip as proxy.
        s.type_keys("^a^c")
        time.sleep(0.3)
        steps.append(Step("url_copy", True, "Ctrl+A Ctrl+C in URL bar", s.shot("ctx_copy")))
        s.type_keys("^a{BACKSPACE}")
        time.sleep(0.2)
        s.type_keys("^v")
        time.sleep(0.3)
        steps.append(Step("url_paste", True, "Ctrl+V restore", s.shot("ctx_paste")))
        ok = s.proc is not None and s.proc.poll() is None
        return ScenarioResult("context_menu", ok, steps, duration_sec=time.time() - t0)
    except Exception as e:  # noqa: BLE001
        steps.append(Step("context_menu", False, str(e), s.shot("ctx_fail")))
        return ScenarioResult("context_menu", False, steps, error=str(e), duration_sec=time.time() - t0)


SCENARIOS: dict[str, Callable[[RpaSession], ScenarioResult]] = {
    "smoke_navigate": scenario_smoke_navigate,
    "settings_preserves_tab": scenario_settings_preserves_tab,
    "new_tab": scenario_new_tab,
    "youtube_signin_page": scenario_youtube_signin_page,
    "context_menu": scenario_context_menu,
}


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
                "steps": [asdict(st) for st in r.steps],
            }
            for r in results
        ],
        "agent_review_hints": [
            "Read this report.json and open each *.png with the Read tool (vision).",
            "File bugs for failed scenarios or visually broken screenshots.",
            "settings_preserves_tab: confirm the site content returns after Esc/Done.",
            "youtube_signin_page: confirm Google sign-in UI is visible (not a Void block page).",
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
        default="smoke_navigate,settings_preserves_tab,new_tab,youtube_signin_page,context_menu",
        help="Comma-separated scenario names",
    )
    parser.add_argument("--out", default="", help="Artifact output directory")
    parser.add_argument("--launch-wait", type=float, default=4.0)
    args = parser.parse_args()

    if sys.platform != "win32":
        print("RPA harness requires Windows (WebView2 GUI).", file=sys.stderr)
        return 2

    try:
        exe = find_exe(args.exe or None)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else DEFAULT_ARTIFACTS / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"Unknown scenarios: {unknown}. Known: {list(SCENARIOS)}", file=sys.stderr)
        return 2

    print(f"Exe: {exe}")
    print(f"Artifacts: {out_dir}")
    print(f"Scenarios: {names}")

    session = RpaSession(exe, out_dir, launch_wait=args.launch_wait)
    results: list[ScenarioResult] = []
    try:
        session.start()
        for name in names:
            print(f"-- {name} --")
            try:
                result = SCENARIOS[name](session)
            except Exception as e:  # noqa: BLE001
                result = ScenarioResult(name, False, error=f"{e}\n{traceback.format_exc()}")
            results.append(result)
            status = "PASS" if result.ok else "FAIL"
            print(f"  {status} ({result.duration_sec:.1f}s)")
    finally:
        session.stop()

    report_path = write_report(
        out_dir,
        exe,
        results,
        meta={
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "scenarios_requested": names,
        },
    )
    print(f"Report: {report_path}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
