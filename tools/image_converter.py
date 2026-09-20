#!/usr/bin/env python3
"""Image to WebP converter with compression optimization."""

import os
import sys
from pathlib import Path
from PIL import Image, ImageOps

SUPPORTED_FORMATS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
}
WEBP_QUALITY = 85
MAX_DIMENSION = 2400


def convert_to_webp(
    src: Path, dst: Path, quality: int = WEBP_QUALITY, max_dim: int = MAX_DIMENSION
) -> bool:
    """Convert a single image to WebP format."""
    try:
        with Image.open(src) as img:
            # Apply EXIF orientation BEFORE stripping metadata — WebP does not
            # carry the orientation tag, so a portrait camera frame would
            # otherwise be stored sideways with no way to correct it later.
            img = ImageOps.exif_transpose(img)
            if img.mode == "RGBA":
                img = img.convert("RGB")
            elif img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            w, h = img.size
            if max(w, h) > max_dim:
                ratio = max_dim / max(w, h)
                new_size = (int(w * ratio), int(h * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            dst.parent.mkdir(parents=True, exist_ok=True)
            img.save(dst, "WEBP", quality=quality, method=6)
            return True
    except Exception as e:
        print(f"  ERROR converting {src.name}: {e}", file=sys.stderr)
        return False


def process_directory(
    src_dir: Path,
    dst_dir: Path,
    quality: int = WEBP_QUALITY,
    max_dim: int = MAX_DIMENSION,
) -> dict:
    """Convert all images in directory to WebP."""
    stats = {"converted": 0, "skipped": 0, "errors": 0}

    for root, _, files in os.walk(src_dir):
        for fname in files:
            src = Path(root) / fname
            ext = src.suffix.lower()

            if ext not in SUPPORTED_FORMATS:
                stats["skipped"] += 1
                continue

            rel = src.relative_to(src_dir)
            dst = dst_dir / rel.with_suffix(".webp")

            if dst.exists():
                stats["skipped"] += 1
                continue

            if convert_to_webp(src, dst, quality, max_dim):
                stats["converted"] += 1
                print(f"  OK: {rel}")
            else:
                stats["errors"] += 1

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Convert images to WebP")
    parser.add_argument("source", help="Source directory")
    parser.add_argument("-o", "--output", help="Output directory (default: source)")
    parser.add_argument(
        "-q", "--quality", type=int, default=WEBP_QUALITY, help="WebP quality (1-100)"
    )
    parser.add_argument(
        "--max-dim", type=int, default=MAX_DIMENSION, help="Max dimension in pixels"
    )
    args = parser.parse_args()

    src = Path(args.source).resolve()
    dst = Path(args.output).resolve() if args.output else src

    if not src.exists():
        print(f"Source not found: {src}", file=sys.stderr)
        sys.exit(1)

    print(f"Converting images: {src} -> {dst}")
    stats = process_directory(src, dst, args.quality, args.max_dim)
    print(
        f"\nDone: {stats['converted']} converted, {stats['skipped']} skipped, {stats['errors']} errors"
    )


if __name__ == "__main__":
    main()
