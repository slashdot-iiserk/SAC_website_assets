#!/usr/bin/env python3
"""Generate grid thumbnails for the SAC website.

Walks processed/<club>/... images and writes a 480px-wide WebP (quality 62)
variant for each image wider than 600px, into processed/thumbs/<club>/...
mirroring the source tree. generate_assets_map.py then exposes each as
`thumb_url` so site grids can serve tiny thumbs and keep full-resolution
files for the lightbox.

Idempotent: skips sources that already have a newer thumbnail.

Usage (from the submodule root):
    python tools/make_thumbs.py [--force] [--max-width 480]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageOps

# Pillow ≥10 moved resampling constants to Image.Resampling; accept both layouts.
try:
    RESAMPLE = Image.Resampling.LANCZOS  # modern Pillow
except AttributeError:  # pragma: no cover - legacy Pillow
    RESAMPLE = 1  # Pillow's historical LANCZOS value

TOOLS_DIR = Path(__file__).resolve().parent
SUBMODULE_ROOT = TOOLS_DIR.parent
PROCESSED = SUBMODULE_ROOT / "processed"
THUMBS = PROCESSED / "thumbs"

# Campus_Archive served the 3D book at ~355px CSS width; 480px covers 1x-1.5x.
MIN_SOURCE_WIDTH = 600
DEFAULT_MAX_WIDTH = 480
QUALITY = 62
SUFFIX = ".webp"


def iter_source_images():
    for club_dir in sorted(PROCESSED.iterdir()):
        if not club_dir.is_dir() or club_dir.name == "thumbs":
            continue
        for img in sorted(club_dir.rglob("*.webp")):
            # Skip non-image sidecars just in case
            yield img


def make_thumb(src: Path, dst: Path, max_width: int) -> tuple[int, int]:
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
        if w <= max_width:
            return w, h
        ratio = max_width / w
        im = im.resize((max_width, round(h * ratio)), RESAMPLE)
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "WEBP", quality=QUALITY, method=4)
        return im.size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate even if fresh")
    ap.add_argument("--max-width", type=int, default=DEFAULT_MAX_WIDTH)
    args = ap.parse_args()

    made = skipped = small = errors = 0
    for src in iter_source_images():
        rel = src.relative_to(PROCESSED)
        if rel.parts[0] == "thumbs":
            continue
        try:
            with Image.open(src) as probe:
                w, h = probe.size
        except Exception as e:
            print(f"  SKIP (unreadable) {rel}: {e}", file=sys.stderr)
            errors += 1
            continue
        if w < MIN_SOURCE_WIDTH:
            small += 1
            continue
        dst = THUMBS / rel
        if (
            not args.force
            and dst.exists()
            and dst.stat().st_mtime >= src.stat().st_mtime
        ):
            skipped += 1
            continue
        try:
            make_thumb(src, dst, args.max_width)
            made += 1
        except Exception as e:
            print(f"  ERR {rel}: {e}", file=sys.stderr)
            errors += 1
        if (made + skipped) % 150 == 0 and made:
            print(f"  …{made} made, {skipped} fresh", flush=True)

    print(
        f"\n[thumbs] made={made} fresh-skip={skipped} too-small={small} errors={errors}"
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
