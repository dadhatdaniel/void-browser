#!/usr/bin/env python3
"""
Build a slim GitLab upload tree from the latest artifacts/rpa/<stamp>/.

Why: uploading the full artifacts/rpa/ tree (or accumulated cache stamps) has
triggered GitLab coordinator 500 / "invalid argument" FATAL on this instance.
We keep full PNGs on the Unraid share (VOID_RPA_MIRROR_DIR) and only upload:
  - report.json
  - JPEG-compressed screenshots (quality ~55, max edge 1280)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def latest_stamp(rpa_root: Path) -> Path | None:
    dirs = [p for p in rpa_root.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return sorted(dirs, key=lambda p: p.name, reverse=True)[0]


def compress_png(src: Path, dest: Path, max_edge: int = 1280, quality: int = 55) -> int:
    from PIL import Image

    img = Image.open(src).convert("RGB")
    w, h = img.size
    scale = min(1.0, float(max_edge) / float(max(w, h)))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="JPEG", quality=quality, optimize=True)
    return dest.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src",
        default="artifacts/rpa",
        help="Root containing timestamp dirs (default artifacts/rpa)",
    )
    ap.add_argument(
        "--dest",
        default="artifacts/rpa-upload",
        help="Flat upload dir for GitLab artifacts",
    )
    ap.add_argument("--max-edge", type=int, default=1280)
    ap.add_argument("--quality", type=int, default=55)
    args = ap.parse_args()

    src_root = Path(args.src)
    dest = Path(args.dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    stamp = latest_stamp(src_root)
    if stamp is None:
        # Also accept files directly under src_root (pulled flat).
        report = src_root / "report.json"
        if not report.is_file():
            print(f"No stamp dir or report.json under {src_root}", file=sys.stderr)
            return 1
        stamp = src_root

    report_src = stamp / "report.json"
    if not report_src.is_file():
        print(f"Missing {report_src}", file=sys.stderr)
        return 1
    shutil.copy2(report_src, dest / "report.json")
    for extra in ("network.har", "network-summary.json", "network.jsonl"):
        srcf = stamp / extra
        if srcf.is_file():
            shutil.copy2(srcf, dest / extra)
            print(f"  copied {extra} ({srcf.stat().st_size} bytes)")
    (dest / "SOURCE_STAMP.txt").write_text(stamp.name + "\n", encoding="utf-8")

    total = (dest / "report.json").stat().st_size
    pngs = sorted(stamp.glob("*.png"))
    for png in pngs:
        jpg = dest / (png.stem + ".jpg")
        try:
            total += compress_png(png, jpg, max_edge=args.max_edge, quality=args.quality)
            print(f"  {png.name} -> {jpg.name} ({jpg.stat().st_size} bytes)")
        except Exception as e:  # noqa: BLE001
            # Fall back to copying original if Pillow fails.
            shutil.copy2(png, dest / png.name)
            total += (dest / png.name).stat().st_size
            print(f"  {png.name} copy-fallback ({e})")

    meta = {
        "source_stamp": stamp.name,
        "source_path": str(stamp),
        "upload_bytes": total,
        "files": sorted(p.name for p in dest.iterdir()),
    }
    (dest / "upload_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Prepared {dest} ({total} bytes, {len(meta['files'])} files) from {stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
