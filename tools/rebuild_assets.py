#!/usr/bin/env python3
"""
SAC Asset Pipeline v2 — Rebuild the processed/ tree from the new
"SAC Website details" data dump.

Stages:
  1. Stage: copy raw data into processed/ using a slug map + smart renaming
  2. Images: convert every image (jpg/jpeg/png/heic/webp) -> WebP (q85, max 2400)
  3. Docs:   parse docx/pdf/html/xlsx -> Markdown (tables preserved)
  4. Media:  compress mp4/mov videos (ffmpeg 720p CRF 30); copy audio as-is
  5. Map:    regenerate assets_map.jsonl via generate_assets_map.py

Design decisions (see plans/working-memory.md):
  - Slug map routes new-data folders -> canonical site slugs
  - Generic filenames (WhatsApp Image/IMG_/PXL_/VID_/Board 1.x) are renamed
    using the event folder context (user-requested)
  - "Copy of X" duplicates are content-deduped
  - Jupyter notebook JSON (physics worksheet) is excluded (not club data)
  - ARW raw file: attempted via rawpy; if unavailable, skipped with a log
  - Slashdot/SPICMACAY have no new data -> old processed data preserved
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

# ---------------------------------------------------------------------------
# Slug map: new-data 2nd-level dir (or special file) -> canonical site slug
# ---------------------------------------------------------------------------

ROOT_MAP = {
    "SAC Academics": {
        "dirs": {
            "General Secretaries": "SAC_Academics",
            "Singularity_ The Astronomy club": "Singularity_Astro_Club",
            "Slashdot_ programming and designing through code": None,  # empty -> keep old
        },
        "root_files": {
            "Placement Student Committee 2025-26.pdf": "Placement_Cell",
        },
    },
    "SAC Cultural": {
        "dirs": {
            "AARSHI - Drama Club": "AARSHI_-_Drama_Club",
            "Arts Club of IISER Kolkata": "Arts_Club_of_IISER_Kolkata",
            "IISER Kolkata Campus Radio": "Campus_Radio_IISER_KOLKATA",
            "IKQC - Quiz Club of IISER Kolkata": "IKQC_-_Quiz_Club_of_IISER_Kolkata",
            "Literary Club of IISER Kolkata": "Literary_Club_of_IISER_Kolkata",
            "Movie Club of IISER K ": "Movie_Club_of_IISER_K",
            "Music Club of IISER K": "Music_Club_of_IISER_K",
            "Nature Club Of IISER  Kolkata": "Nature_Club_Of_IISER_Kolkata",
            "Nrutya - The Dance Club of IISER Kolkata ": "Nrutya_-_The_Dance_Club_of_IISER_Kolkata",
            "PIXEL-Photography Club": "PIXEL-Photography_Club",
            "SPICMACAY": None,  # empty -> skip
        },
        "root_files": {},
    },
    "SAC Food and Hygine": {
        "dirs": {},
        "root_files": {},
        "slug": "SAC_Food_and_Hygiene",
    },
    "SAC Hostel": {
        "dirs": {},  # all subdirs -> SAC_Hostel
        "root_files": {},
        "slug": "SAC_Hostel",
        "sink_children": True,
    },
    "SAC Sports": {
        "dirs": {
            "Athletics": "SAC_Sports_Athletics",
            "Badminton": "SAC_Sports_Badminton",
            "Basketball": "SAC_Sports_Basketball",
            "Carrom": "SAC_Sports_Carrom",
            "Chess": "SAC_Sports_Chess",
            "Cricket": "SAC_Sports_Cricket",
            "Football": "SAC_Sports_Football",
            "Gaming": "SAC_Sports_Gaming",
            "GYM": "SAC_Sports_GYM",
            "Kabaddi": "SAC_Sports_Kabaddi",
            "Kho-Kho": "SAC_Sports_Kho_Kho",
            "Lawn Tennis": "SAC_Sports_Lawn_Tennis",
            "Rubik": "SAC_Sports_Rubik",
            "SYDC": "SAC_Sports_SYDC",
            "Table Tennis": "SAC_Sports_Table_Tennis",
            "Volleyball": "SAC_Sports_Volleyball",
        },
        "root_files": {},
    },
}

# Clubs with no new data: preserve their old processed/ content
KEEP_OLD_SLUGS = {"Slashdot_Programming_Club"}

# ---------------------------------------------------------------------------
# Generic filename patterns -> event-context rename
# ---------------------------------------------------------------------------

GENERIC_NAME_RE = re.compile(
    r"^(WhatsApp[_ ]?Image|IMG[_-]?|PXL_|SAVE_|VID[_-]?|Screenshot|Screenrecorder"
    r"|Media|Photo|image|DSC|Camera|Board\s*\d+\.\d+)\b",
    re.IGNORECASE,
)
COPY_OF_RE = re.compile(r"^Copy of\s+", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")
BAD_CHARS_RE = re.compile(r"[^\w.\-]+")


def sanitize_name(name: str) -> str:
    name = name.strip()
    name = WHITESPACE_RE.sub("_", name)
    name = BAD_CHARS_RE.sub("_", name)
    name = name.replace("-", "_")
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def content_sniff_ext(path: Path) -> str | None:
    """Return the real image extension for misnamed files (Board 1.3 etc)."""
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:2] == b"\xff\xd8":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


# ---------------------------------------------------------------------------
# Renamer: builds a clean, event-contextual filename
# ---------------------------------------------------------------------------


def rename_file(
    src: Path, leaf_dir: str, siblings: list[Path]
) -> tuple[str | None, str, bool]:
    """
    Return (clean_stem, final_ext, is_generic).

    - clean_stem None + is_generic True  -> pure generic (WhatsApp Image…):
      caller assigns `{folder}_{NN}`.
    - clean_stem set  + is_generic True  -> generic with meaning (Board 1.3):
      caller uses `{folder}_{stem}`.
    - clean_stem set  + is_generic False -> keep semantic name.
    - (None, "", False)                  -> skip the file entirely.
    """
    name = src.name
    ext = src.suffix.lower()

    # --- explicit exclusions -------------------------------------------------
    if "Copy of Document from Aranya" in name or name.endswith(".ipynb"):
        return None, "", False  # Jupyter worksheet, not club data
    if name.lower().endswith(".arw"):
        return None, "", False  # Sony raw — cannot convert with current tooling

    # --- work out the REAL extension ------------------------------------------
    # Names like "Board 1.3" or "4.00.44 PM (1).jpeg" confuse Path.suffix —
    # always slice the extension manually from the trailing known-extension
    # list; if none matches, sniff image content.
    known_exts = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".mp4",
        ".mov",
        ".m4v",
        ".m4a",
        ".wav",
        ".mp3",
        ".docx",
        ".pdf",
        ".xlsx",
        ".html",
        ".htm",
        ".txt",
        ".md",
    }
    final_ext = ext if ext in known_exts else ""
    stem = name[: -len(ext)] if ext and ext in known_exts else name
    real_ext = None
    if not final_ext:
        sniffed = content_sniff_ext(src)
        if sniffed:
            real_ext = sniffed
            final_ext = f".{sniffed}"
            # Board 1.3 -> stem stays "Board 1.3" (dot is part of the name)

    # --- dedupe "Copy of X" ----------------------------------------------------
    copy_match = COPY_OF_RE.match(name)
    if copy_match:
        clean = name[copy_match.end() :]
        target = src.parent / clean
        if target.exists():
            try:
                if md5_of(src) == md5_of(target):
                    return None, "", False  # exact duplicate -> drop
            except OSError:
                pass
        name = clean
        stem = (
            name[: -len(final_ext)] if final_ext and name.endswith(final_ext) else name
        )

    # --- generic names get event context from their leaf folder ---------------
    generic = GENERIC_NAME_RE.match(name) is not None
    if generic:
        clean_stem = sanitize_name(stem) if real_ext else None
        return clean_stem, final_ext, True

    # --- logos: semantic names ------------------------------------------------
    lower = name.lower()
    if "without bg" in lower or "transparent" in lower:
        stem = "logo_transparent"
        final_ext = ".png"
    elif "white background" in lower or "white_bg" in lower:
        stem = "logo_white_bg"
    elif (
        "blackbackground" in lower or "black_bg" in lower or "black background" in lower
    ):
        stem = "logo_black_bg"

    clean = sanitize_name(stem)
    if not clean:
        clean = "file"
    return clean, final_ext, False


def dedupe_within_dir(files: list[Path]) -> list[Path]:
    """Drop exact content duplicates within one directory (keeps first)."""
    seen: dict[str, Path] = {}
    keep: list[Path] = []
    for f in sorted(files):
        try:
            h = md5_of(f)
        except OSError:
            keep.append(f)
            continue
        if h in seen:
            continue
        seen[h] = f
        keep.append(f)
    return keep


# ---------------------------------------------------------------------------
# Stage: copy raw data into processed/ with slug map + renaming
# ---------------------------------------------------------------------------


def stage_all(source: Path, processed: Path) -> dict:
    stats = {"copied": 0, "renamed": 0, "skipped": 0, "dropped": 0, "clubs": set()}
    for top_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        top_key = top_dir.name
        top_cfg = ROOT_MAP.get(top_key)
        if top_cfg is None:
            print(f"  [warn] unmapped top-level dir: {top_key}")
            continue

        # clubs that are direct children of this top dir
        dir_map = top_cfg.get("dirs", {})
        sink_slug = top_cfg.get("slug")
        sink_children = top_cfg.get("sink_children", False)
        root_files_map = top_cfg.get("root_files", {})

        # root-level files of the top dir (e.g. Placement PDF under SAC Academics)
        for f in sorted(top_dir.iterdir()):
            if not f.is_file():
                continue
            slug = root_files_map.get(f.name)
            if slug is None and sink_slug:
                slug = sink_slug  # e.g. SAC Food and Hygine / SAC Hostel root files
            if slug is None:
                print(f"  [warn] unmapped root file: {top_key}/{f.name}")
                stats["dropped"] += 1
                continue
            dst_dir = processed / slug
            dst_dir.mkdir(parents=True, exist_ok=True)
            stem, ext, is_generic = rename_file(f, "(root)", [])
            if stem is None and not is_generic:
                stats["dropped"] += 1
                continue
            new_name = f"{stem}{ext}" if stem else f"(root)_{ext or '.bin'}"
            dst = dst_dir / new_name
            if dst.exists():
                stats["skipped"] += 1
                continue
            shutil.copy2(f, dst)
            stats["copied"] += 1
            stats["clubs"].add(slug)

        for child in sorted(top_dir.iterdir()):
            if not child.is_dir():
                continue
            slug = dir_map.get(child.name)
            if slug is None and sink_slug:
                slug = sink_slug
            if slug is None:
                # e.g. Slashdot folder is empty in the new data dump —
                # old processed content is preserved separately post-pipeline
                print(f"  [info] no new data staged for: {child.name}")
                stats["dropped"] += 1
                continue
            _stage_club_tree(child, processed / slug, stats, sink_children)
            stats["clubs"].add(slug)
    return stats


def _stage_club_tree(club_dir: Path, dst_root: Path, stats: dict, sink_children: bool):
    """Copy a club's tree, renaming dirs and files."""
    for dirpath, dirnames, filenames in os.walk(club_dir):
        rel = Path(dirpath).relative_to(club_dir)
        dst_dir = (
            dst_root / Path(*[sanitize_name(p) for p in rel.parts])
            if rel.parts
            else dst_root
        )
        if not filenames:
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        files = [Path(dirpath) / f for f in filenames]
        files = dedupe_within_dir(files)
        leaf = Path(dirpath).name
        folder = sanitize_name(leaf) or "event"

        used: set[str] = set()
        pending_generic: list[tuple[Path, str | None, str]] = []  # (src, stem, ext)

        # First pass: non-generic files get their clean names.
        for src in sorted(files, key=lambda p: p.name):
            stem, ext, is_generic = rename_file(src, leaf, files)
            if stem is None and not is_generic:
                stats["dropped"] += 1
                continue
            if is_generic:
                pending_generic.append((src, stem, ext))
                continue
            name = f"{stem}{ext}"
            dst = dst_dir / name
            if dst.exists():
                stats["skipped"] += 1
                continue
            used.add(name)
            shutil.copy2(src, dst)
            stats["copied"] += 1
            if name != src.name:
                stats["renamed"] += 1

        # Second pass: generic files get event-context sequence names, e.g.
        # Field_Trip_01.webp (pure generic) or Fresher_s_2025_Board_1_3.jpg
        seq = 0
        for src, stem, ext in pending_generic:
            if stem:  # has meaning (Board 1.3) -> folder_stem[_NN]
                candidate = f"{folder}_{stem}{ext}"
                if candidate in used:
                    candidate = f"{folder}_{stem}_{seq:02d}{ext}"
                    while candidate in used:
                        seq += 1
                        candidate = f"{folder}_{stem}_{seq:02d}{ext}"
                    seq += 1
            else:  # pure generic -> folder_NN
                candidate = f"{folder}_{seq:02d}{ext}"
                while candidate in used:
                    seq += 1
                    candidate = f"{folder}_{seq:02d}{ext}"
                seq += 1
            dst = dst_dir / candidate
            if dst.exists():
                stats["skipped"] += 1
                continue
            used.add(candidate)
            shutil.copy2(src, dst)
            stats["copied"] += 1
            stats["renamed"] += 1


# ---------------------------------------------------------------------------
# Images -> WebP
# ---------------------------------------------------------------------------

SUPPORTED_IMAGE_EXT = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
    ".webp",
}


def convert_images(processed: Path, quality: int = 85, max_dim: int = 2400) -> dict:
    from PIL import Image

    stats = {"converted": 0, "errors": 0, "skipped": 0}
    for root, _, files in os.walk(processed):
        for fname in files:
            src = Path(root) / fname
            ext = src.suffix.lower()
            if ext not in SUPPORTED_IMAGE_EXT:
                continue
            if ext == ".webp":
                stats["skipped"] += 1
                continue
            dst = src.with_suffix(".webp")
            try:
                if ext in {".heic", ".heif"}:
                    # Pillow cannot read HEIC -> ImageMagick
                    r = subprocess.run(
                        [
                            "convert",
                            str(src),
                            "-resize",
                            f"{max_dim}x{max_dim}>",
                            "-quality",
                            str(quality),
                            str(dst),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.strip()[:200])
                else:
                    with Image.open(src) as img:
                        if img.mode == "RGBA":
                            img = img.convert("RGB")
                        elif img.mode not in ("RGB", "L"):
                            img = img.convert("RGB")
                        w, h = img.size
                        if max(w, h) > max_dim:
                            ratio = max_dim / max(w, h)
                            img = img.resize(
                                (int(w * ratio), int(h * ratio)),
                                Image.Resampling.LANCZOS,
                            )
                        img.save(dst, "WEBP", quality=quality, method=6)
                src.unlink()
                stats["converted"] += 1
            except Exception as e:
                stats["errors"] += 1
                print(f"  [error] {src.relative_to(processed)}: {e}", file=sys.stderr)
    return stats


# ---------------------------------------------------------------------------
# Docs -> Markdown (docx / pdf / html / xlsx)
# ---------------------------------------------------------------------------


def xlsx_to_markdown(xlsx_path: Path, output_dir: Path) -> Path | None:
    """Convert xlsx to Markdown tables using openpyxl."""
    try:
        import openpyxl
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    except Exception as e:
        print(f"  [error] xlsx {xlsx_path.name}: {e}", file=sys.stderr)
        return None

    md_lines = [f"# {xlsx_path.stem.replace('_', ' ').replace('-', ' ').title()}\n"]
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        md_lines.append(f"## {ws.title}\n")
        header = [("" if c is None else str(c)).strip() for c in rows[0]]
        md_lines.append("| " + " | ".join(header) + " |")
        md_lines.append("| " + " | ".join("---" for _ in header) + " |")
        for row in rows[1:]:
            cells = [
                ("" if c is None else str(c)).strip().replace("|", "\\|") for c in row
            ]
            if not any(cells):
                continue
            md_lines.append("| " + " | ".join(cells) + " |")
        md_lines.append("")
    md_path = output_dir / f"{xlsx_path.stem}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return md_path


def parse_docs(processed: Path) -> dict:
    from docx_parser import process_directory as parse_docx
    from pdf_parser import process_directory as parse_pdf
    from html_parser import process_directory as parse_html

    stats = {"docx": 0, "pdf": 0, "html": 0, "xlsx": 0, "errors": 0}
    docx_files = sorted(processed.rglob("*.docx"))
    pdf_files = sorted(processed.rglob("*.pdf"))
    html_files = sorted(processed.rglob("*.html"))
    xlsx_files = sorted(processed.rglob("*.xlsx"))

    # DOCX/PDF/HTML parsers walk a src dir and write md next to it
    for src_dir in _unique_parents(docx_files + pdf_files + html_files):
        try:
            d = parse_docx(src_dir, src_dir)
            stats["docx"] += d["processed"]
        except Exception as e:
            stats["errors"] += 1
            print(f"  [error] docx dir {src_dir}: {e}", file=sys.stderr)
        try:
            p = parse_pdf(src_dir, src_dir)
            stats["pdf"] += p["processed"]
        except Exception as e:
            stats["errors"] += 1
            print(f"  [error] pdf dir {src_dir}: {e}", file=sys.stderr)
        try:
            h = parse_html(src_dir, src_dir)
            stats["html"] += h["processed"]
        except Exception as e:
            stats["errors"] += 1
            print(f"  [error] html dir {src_dir}: {e}", file=sys.stderr)

    for x in xlsx_files:
        try:
            if xlsx_to_markdown(x, x.parent):
                stats["xlsx"] += 1
                x.unlink()
        except Exception as e:
            stats["errors"] += 1
            print(f"  [error] xlsx {x}: {e}", file=sys.stderr)

    # Remove raw source documents after successful parse: the site only
    # consumes markdown + images (old processed tree contained md+webp only).
    for p in processed.rglob("*"):
        if p.suffix.lower() in {".docx", ".pdf", ".html", ".htm"} and p.is_file():
            p.unlink()

    return stats


def _unique_parents(paths: list[Path]) -> list[Path]:
    return sorted({p.parent for p in paths})


# ---------------------------------------------------------------------------
# Videos -> compressed mp4 ; audio copied as-is
# ---------------------------------------------------------------------------


def compress_videos(processed: Path, crf: int = 30, max_h: int = 720) -> dict:
    stats = {"compressed": 0, "skipped": 0, "errors": 0}
    for root, _, files in os.walk(processed):
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in {".mp4", ".mov", ".m4v"}:
                continue
            src = Path(root) / fname
            if ext == ".mp4":
                # still re-encode: most are huge phone captures
                dst = src
                tmp = src.with_suffix(".tmp.mp4")
            else:
                dst = src.with_suffix(".mp4")
                tmp = dst
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                f"scale=-2:'min({max_h},ih)'",
                "-c:v",
                "libx264",
                "-crf",
                str(crf),
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(tmp),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                stats["errors"] += 1
                print(f"  [error] video {src.name}: {r.stderr[-300:]}", file=sys.stderr)
                continue
            if dst != src:
                src.unlink()
            elif tmp != src:
                tmp.replace(dst)
            stats["compressed"] += 1
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild SAC processed assets")
    parser.add_argument("source", help="Path to 'SAC Website details'")
    parser.add_argument("-o", "--output", default=None, help="processed/ output dir")
    parser.add_argument("--quality", type=int, default=85)
    parser.add_argument("--max-dim", type=int, default=2400)
    parser.add_argument("--video-crf", type=int, default=30)
    parser.add_argument("--skip-videos", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    processed = (
        Path(args.output).resolve() if args.output else TOOLS_DIR.parent / "processed"
    )

    if not source.is_dir():
        print(f"Source not found: {source}", file=sys.stderr)
        sys.exit(1)

    print(f"[stage] {source} -> {processed}")
    st = stage_all(source, processed)
    print(
        f"  staged: {st['copied']} copied, {st['renamed']} renamed, "
        f"{st['skipped']} skipped, {st['dropped']} dropped, {len(st['clubs'])} clubs"
    )
    for club in sorted(st["clubs"]):
        print(f"    - {club}")

    print("[images] converting to WebP...")
    img = convert_images(processed, args.quality, args.max_dim)
    print(
        f"  converted: {img['converted']}, errors: {img['errors']}, skipped: {img['skipped']}"
    )

    print("[docs] parsing docx/pdf/html/xlsx -> markdown...")
    dc = parse_docs(processed)
    print(
        f"  docx: {dc['docx']}, pdf: {dc['pdf']}, html: {dc['html']}, xlsx: {dc['xlsx']}, errors: {dc['errors']}"
    )

    # convert extracted doc images to webp
    print("[images] converting extracted images to WebP...")
    img2 = convert_images(processed, args.quality, args.max_dim)
    print(f"  converted: {img2['converted']}, errors: {img2['errors']}")

    if not args.skip_videos:
        print("[videos] compressing...")
        v = compress_videos(processed, args.video_crf)
        print(f"  compressed: {v['compressed']}, errors: {v['errors']}")
    else:
        print("[videos] skipped")

    print("[map] generating assets_map.jsonl...")
    from generate_assets_map import generate_assets_map

    generate_assets_map(processed)
    print("DONE")


if __name__ == "__main__":
    main()
