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
  - Exact duplicates within a destination folder are merged by SHA-256;
    near-duplicates and cross-club copies remain separate
  - JSON-like files without an extension are retained with a .json extension
  - ARW raw files are decoded with rawpy when available
  - Slashdot/SPICMACAY have no new data -> old processed data preserved
"""

from __future__ import annotations

import hashlib
import json
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

KNOWN_SOURCE_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
    ".arw",
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
    ".json",
    ".ipynb",
}

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


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
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
    if head.lstrip().startswith((b"{", b"[")):
        return "json"
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

    # --- work out the REAL extension ------------------------------------------
    # Names like "Board 1.3" or "4.00.44 PM (1).jpeg" confuse Path.suffix —
    # always slice the extension manually from the trailing known-extension
    # list; if none matches, sniff image content.
    known_exts = KNOWN_SOURCE_EXTS
    final_ext = ext if ext in known_exts else ""
    stem = name[: -len(ext)] if ext and ext in known_exts else name
    real_ext = None
    if not final_ext:
        sniffed = content_sniff_ext(src)
        if sniffed:
            real_ext = sniffed
            final_ext = f".{sniffed}"
            # Board 1.3 -> stem stays "Board 1.3" (dot is part of the name)

    # --- normalize "Copy of X" without dropping the source --------------------
    copy_match = COPY_OF_RE.match(name)
    if copy_match:
        clean = name[copy_match.end() :]
        name = clean
        stem = (
            name[: -len(final_ext)]
            if final_ext and name.lower().endswith(final_ext)
            else name
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


def next_available(path: Path) -> Path:
    """Return a deterministic collision-free destination without overwriting."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def stage_source(
    src: Path,
    desired: Path,
    source: Path,
    processed: Path,
    stats: dict,
    seen_by_dir: dict[Path, dict[str, dict]],
) -> Path:
    """Copy one source file, merging only exact duplicates in its folder."""
    source_hash = sha256_of(src)
    scope = seen_by_dir.setdefault(desired.parent, {})
    duplicate = scope.get(source_hash)
    if duplicate:
        stats["merged"] += 1
        stats["records"].append(
            {
                "source_path": str(src.relative_to(source)),
                "source_sha256": source_hash,
                "source_size_bytes": src.stat().st_size,
                "staged_path": duplicate["staged_path"],
                "duplicate_of": duplicate["source_path"],
            }
        )
        return processed / duplicate["staged_path"]

    dst = next_available(desired)
    shutil.copy2(src, dst)
    stats["copied"] += 1
    if dst.name != desired.name:
        stats["renamed"] += 1
    record = {
        "source_path": str(src.relative_to(source)),
        "source_sha256": source_hash,
        "source_size_bytes": src.stat().st_size,
        "staged_path": str(dst.relative_to(processed)),
    }
    scope[source_hash] = record
    stats["records"].append(record)
    return dst


# ---------------------------------------------------------------------------
# Stage: copy raw data into processed/ with slug map + renaming
# ---------------------------------------------------------------------------


def stage_all(source: Path, processed: Path) -> dict:
    stats = {
        "copied": 0,
        "renamed": 0,
        "merged": 0,
        "skipped": 0,
        "dropped": 0,
        "clubs": set(),
        "records": [],
    }
    seen_by_dir: dict[Path, dict[str, dict]] = {}
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
            stage_source(
                f,
                dst_dir / new_name,
                source,
                processed,
                stats,
                seen_by_dir,
            )
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
            _stage_club_tree(
                child,
                processed / slug,
                stats,
                source,
                processed,
                sink_children,
                seen_by_dir,
            )
            stats["clubs"].add(slug)
    return stats


def _stage_club_tree(
    club_dir: Path,
    dst_root: Path,
    stats: dict,
    source: Path,
    processed: Path,
    sink_children: bool,
    seen_by_dir: dict[Path, dict[str, dict]],
):
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
            dst = stage_source(src, dst_dir / name, source, processed, stats, seen_by_dir)
            used.add(dst.name)

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
            dst = stage_source(src, dst_dir / candidate, source, processed, stats, seen_by_dir)
            used.add(dst.name)


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
    ".arw",
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
                if ext == ".arw":
                    try:
                        import rawpy
                    except ImportError as exc:
                        raise RuntimeError(
                            "ARW conversion requires optional dependency rawpy"
                        ) from exc
                    with rawpy.imread(str(src)) as raw:
                        rgb = raw.postprocess(
                            use_camera_wb=True,
                            no_auto_bright=True,
                            output_bps=8,
                        )
                    image = Image.fromarray(rgb)
                    if max(image.size) > max_dim:
                        ratio = max_dim / max(image.size)
                        image = image.resize(
                            (int(image.width * ratio), int(image.height * ratio)),
                            Image.Resampling.LANCZOS,
                        )
                    image.save(dst, "WEBP", quality=quality, method=6)
                elif ext in {".heic", ".heif"}:
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
    from docx_parser import docx_to_markdown
    from html_parser import html_to_markdown
    from pdf_parser import pdf_to_markdown

    stats = {"docx": 0, "pdf": 0, "html": 0, "xlsx": 0, "errors": 0}
    all_files = [p for p in processed.rglob("*") if p.is_file()]
    docx_files = sorted(p for p in all_files if p.suffix.lower() == ".docx")
    pdf_files = sorted(p for p in all_files if p.suffix.lower() == ".pdf")
    html_files = sorted(p for p in all_files if p.suffix.lower() in {".html", ".htm"})
    xlsx_files = sorted(p for p in all_files if p.suffix.lower() == ".xlsx")

    # A DOCX and PDF with the same stem often coexist (for example, a club
    # report supplied in both formats). Give their Markdown and extracted
    # image directories distinct names so the second parser cannot overwrite
    # the first parser's output.
    document_groups: dict[tuple[Path, str], set[str]] = {}
    for path in docx_files + pdf_files + html_files:
        document_groups.setdefault((path.parent, path.stem), set()).add(
            path.suffix.lower()
        )

    successful_docs: list[Path] = []

    for src in docx_files:
        suffixes = document_groups[(src.parent, src.stem)]
        output_stem = f"{src.stem}_docx" if len(suffixes) > 1 else src.stem
        try:
            docx_to_markdown(
                src,
                src.parent,
                output_stem=output_stem,
                image_dir_name=f"{output_stem}_images",
            )
            stats["docx"] += 1
            successful_docs.append(src)
        except Exception as e:
            stats["errors"] += 1
            print(f"  [error] docx {src}: {e}", file=sys.stderr)

    for src in pdf_files:
        suffixes = document_groups[(src.parent, src.stem)]
        output_stem = f"{src.stem}_pdf" if len(suffixes) > 1 else src.stem
        try:
            pdf_to_markdown(
                src,
                src.parent,
                output_stem=output_stem,
                image_dir_name=f"{output_stem}_images",
            )
            stats["pdf"] += 1
            successful_docs.append(src)
        except Exception as e:
            stats["errors"] += 1
            print(f"  [error] pdf {src}: {e}", file=sys.stderr)

    for src in html_files:
        try:
            html_to_markdown(src, src.parent)
            stats["html"] += 1
            successful_docs.append(src)
        except Exception as e:
            stats["errors"] += 1
            print(f"  [error] html {src}: {e}", file=sys.stderr)

    for x in xlsx_files:
        try:
            if xlsx_to_markdown(x, x.parent):
                stats["xlsx"] += 1
                successful_docs.append(x)
        except Exception as e:
            stats["errors"] += 1
            print(f"  [error] xlsx {x}: {e}", file=sys.stderr)

    # Remove raw source documents only after their individual parser succeeded.
    # Failed inputs remain visible for a follow-up run instead of disappearing.
    for p in successful_docs:
        if p.exists():
            p.unlink()

    return stats


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
# Rebuild hygiene and source accounting
# ---------------------------------------------------------------------------


def reset_processed(processed: Path) -> None:
    """Remove generated clubs while retaining explicitly carried-over clubs."""
    processed.mkdir(parents=True, exist_ok=True)
    for child in processed.iterdir():
        if child.is_dir() and child.name in KEEP_OLD_SLUGS:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def source_output_paths(record: dict, processed: Path) -> list[str]:
    """Resolve a staged source record to its final website-ready outputs."""
    staged = processed / record["staged_path"]
    suffix = staged.suffix.lower()

    if suffix in SUPPORTED_IMAGE_EXT:
        candidate = staged.with_suffix(".webp")
        return [str(candidate.relative_to(processed))] if candidate.exists() else []
    if suffix in {".mov", ".m4v"}:
        candidate = staged.with_suffix(".mp4")
        return [str(candidate.relative_to(processed))] if candidate.exists() else []
    if suffix == ".mp4":
        return [str(staged.relative_to(processed))] if staged.exists() else []
    if suffix in {".docx", ".pdf", ".html", ".htm", ".xlsx"}:
        stem = staged.stem
        candidates = [stem]
        if suffix == ".docx":
            candidates.insert(0, f"{stem}_docx")
        elif suffix == ".pdf":
            candidates.insert(0, f"{stem}_pdf")
        outputs: list[Path] = []
        for output_stem in candidates:
            markdown = staged.with_name(f"{output_stem}.md")
            image_dir = staged.with_name(f"{output_stem}_images")
            if markdown.exists():
                outputs.append(markdown)
            if image_dir.is_dir():
                outputs.extend(sorted(p for p in image_dir.rglob("*") if p.is_file()))
            if outputs:
                break
        return [str(path.relative_to(processed)) for path in outputs]
    return [str(staged.relative_to(processed))] if staged.exists() else []


def write_source_manifest(source: Path, processed: Path, records: list[dict]) -> Path:
    """Write a reproducible source-to-output accounting ledger."""
    manifest = processed / "source_manifest.jsonl"
    rows = []
    for record in sorted(records, key=lambda r: r["source_path"]):
        outputs = source_output_paths(record, processed)
        rows.append(
            {
                "source_path": record["source_path"],
                "source_sha256": record["source_sha256"],
                "source_size_bytes": record["source_size_bytes"],
                "staged_path": record["staged_path"],
                "output_paths": outputs,
                "status": "merged" if record.get("duplicate_of") else ("processed" if outputs else "error"),
                **({"duplicate_of": record["duplicate_of"]} if record.get("duplicate_of") else {}),
            }
        )
    with manifest.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    errors = [row for row in rows if row["status"] not in {"processed", "merged"}]
    if errors:
        print(
            f"[manifest] {len(errors)} source files have no processed output",
            file=sys.stderr,
        )
        for row in errors:
            print(f"  [manifest-error] {row['source_path']}", file=sys.stderr)
    else:
        print(f"[manifest] {len(rows)} source files accounted for")
    return manifest


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

    reset_processed(processed)
    print(f"[stage] {source} -> {processed}")
    st = stage_all(source, processed)
    print(
        f"  staged: {st['copied']} copied, {st['renamed']} renamed, "
        f"{st['merged']} exact duplicates merged, {st['skipped']} skipped, "
        f"{st['dropped']} dropped, {len(st['clubs'])} clubs"
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

    write_source_manifest(source, processed, st["records"])

    print("[map] generating assets_map.jsonl...")
    from generate_assets_map import generate_assets_map

    generate_assets_map(processed)
    print("DONE")


if __name__ == "__main__":
    main()
