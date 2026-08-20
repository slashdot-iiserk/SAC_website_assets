#!/usr/bin/env python3
"""Parse HTML profile/document pages to Markdown, extracting text content and images."""

import os
import re
import sys
from pathlib import Path
from html.parser import HTMLParser
from PIL import Image

WEBP_QUALITY = 85
MAX_DIMENSION = 2400

# Tags whose text we skip entirely (only head-level boilerplate)
_SKIP_TAGS = {"style", "script", "noscript", "head"}
# Tags that imply a heading when their class contains these patterns
_HEADING_CLASSES = {
    "section-title": "## ",
    "banner-sac": None,
    "banner-institute": None,
}
# Semantic class patterns that map to markdown formatting
_ROLE_TITLE_CLASSES = {"pos-title", "r-title"}
_ROLE_SUB_CLASSES = {"pos-sub", "r-sub"}
_ROLE_DESC_CLASSES = {"r-desc", "pos-body"}
_INFO_LABEL_CLASSES = {"left-info-label"}
_INFO_VALUE_CLASSES = {"left-info-value", "contact-edit"}
_NAME_CLASSES = {"name-block"}


class HTMLProfileExtractor(HTMLParser):
    """Extracts structured content from HTML profile pages as Markdown."""

    def __init__(self):
        super().__init__()
        self._md_parts: list[str] = []
        self._text_buf: list[str] = []
        self._tag_stack: list[tuple[str, list[tuple[str, str | None]]]] = []
        self._skip_depth = 0
        self._title: str | None = None
        self._in_title = False
        self._in_body = False
        self._img_counter = 0
        self._current_classes: set[str] = set()
        self._heading_prefix: str | None = None
        self._in_info_item = False
        self._current_info_label: str | None = None

    def _emit(self, text: str):
        self._md_parts.append(text)

    def _flush_text(self) -> str:
        buf = "".join(self._text_buf).strip()
        self._text_buf.clear()
        return buf

    def _classes_for_attrs(self, attrs: list[tuple[str, str | None]]) -> set[str]:
        for name, val in attrs:
            if name == "class" and val:
                return {c.strip() for c in val.split()}
        return set()

    def _has_class(self, classes: set[str], targets: set[str]) -> bool:
        return bool(classes & targets)

    def _tag_name(self) -> str:
        return self._tag_stack[-1][0] if self._tag_stack else ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        self._tag_stack.append((tag_lower, attrs))
        classes = self._classes_for_attrs(attrs)
        self._current_classes = classes

        if tag_lower in _SKIP_TAGS:
            self._skip_depth += 1
            return

        if tag_lower == "title":
            self._in_title = True

        if tag_lower == "body":
            self._in_body = True

        # Handle semantic class roles
        if self._has_class(classes, _ROLE_TITLE_CLASSES):
            buf = self._flush_text()
            self._heading_prefix = "### "
        elif self._has_class(classes, _ROLE_SUB_CLASSES):
            buf = self._flush_text()
            self._heading_prefix = None
        elif self._has_class(classes, {"section-title"}):
            self._heading_prefix = "## "
        elif self._has_class(classes, _INFO_LABEL_CLASSES):
            self._in_info_item = True
            self._current_info_label = None
        elif self._has_class(classes, _INFO_VALUE_CLASSES):
            self._in_info_item = True
        elif self._has_class(classes, {"name-block"}):
            self._in_info_item = True

        # Handle images
        if tag_lower == "img" and self._in_body:
            src = dict(attrs).get("src", "")
            if src and not src.startswith("data:") and ".css" not in src:
                self._img_counter += 1
                img_name = f"html_img_{self._img_counter:03d}.webp"
                self._emit(f"\n![Image {self._img_counter}]({img_name})\n\n")

        # Line breaks
        if tag_lower == "br":
            self._text_buf.append("\n")

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()

        if tag_lower in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            if self._tag_stack and self._tag_stack[-1][0] == tag_lower:
                self._tag_stack.pop()
            return

        if tag_lower == "title":
            buf = self._flush_text()
            if buf and not self._title:
                self._title = buf
            self._in_title = False
            if self._tag_stack and self._tag_stack[-1][0] == tag_lower:
                self._tag_stack.pop()
            return

        if self._skip_depth > 0:
            if self._tag_stack and self._tag_stack[-1][0] == tag_lower:
                self._tag_stack.pop()
            return

        # On block-level tag end, flush text and emit
        if tag_lower in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th"}:
            buf = self._flush_text()
            if not buf:
                if self._tag_stack and self._tag_stack[-1][0] == tag_lower:
                    self._tag_stack.pop()
                return

            hn = None
            if tag_lower.startswith("h") and len(tag_lower) == 2 and tag_lower[1].isdigit():
                hn = int(tag_lower[1])

            if hn is not None:
                self._emit(f"\n{'#' * hn} {buf}\n")
            elif self._heading_prefix and self._heading_prefix.startswith("#"):
                self._emit(f"\n{self._heading_prefix}{buf}\n")
                self._heading_prefix = None
            elif self._has_class(self._current_classes, _INFO_LABEL_CLASSES):
                self._current_info_label = buf
                self._in_info_item = True
            elif self._has_class(self._current_classes, _INFO_VALUE_CLASSES) and self._in_info_item:
                label = self._current_info_label or ""
                self._emit(f"**{label}:** {buf}\n")
                self._current_info_label = None
                self._in_info_item = False
            elif self._has_class(self._current_classes, {"pos-title"}):
                self._emit(f"**{buf}**\n")
            elif self._has_class(self._current_classes, {"pos-sub"}):
                self._emit(f"*{buf}*\n")
            elif self._has_class(self._current_classes, {"r-title"}):
                self._emit(f"**{buf}**\n")
            elif self._has_class(self._current_classes, {"r-sub"}):
                self._emit(f"*{buf}*\n")
            elif self._has_class(self._current_classes, {"r-desc", "pos-body"}):
                self._emit(f"{buf}\n")
            elif tag_lower == "li":
                self._emit(f"- {buf}\n")
            else:
                self._emit(f"{buf}\n")

        if tag_lower == "a":
            pass

        if self._tag_stack and self._tag_stack[-1][0] == tag_lower:
            self._tag_stack.pop()

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        if not data or not data.strip():
            return

        # Clean up whitespace
        text = re.sub(r"[ \t]+", " ", data)
        text = re.sub(r"\n{3,}", "\n\n", text)
        self._text_buf.append(text)

    def handle_entityref(self, name: str):
        entity_map = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "nbsp": " "}
        self._text_buf.append(entity_map.get(name, f"&{name};"))

    def handle_charref(self, name: str):
        try:
            cp = int(name)
            self._text_buf.append(chr(cp))
        except (ValueError, OverflowError):
            self._text_buf.append(f"&#{name};")

    def get_markdown(self) -> str:
        remaining = self._flush_text()
        if remaining:
            self._emit(remaining)
        raw = "\n".join(self._md_parts)
        # Collapse multiple blank lines
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def convert_image_to_webp(src: Path) -> Path:
    """Convert image to WebP format, return new path."""
    if src.suffix.lower() == ".webp":
        return src
    dst = src.with_suffix(".webp")
    if dst.exists():
        return dst
    try:
        with Image.open(src) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > MAX_DIMENSION:
                ratio = MAX_DIMENSION / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            img.save(dst, "WEBP", quality=WEBP_QUALITY, method=6)
            src.unlink()
            return dst
    except Exception as e:
        print(f"    WARN: Could not convert {src.name} to WebP: {e}", file=sys.stderr)
        return src


def extract_and_convert_images(html_path: Path, output_dir: Path) -> list[Path]:
    """
    Find referenced images in the same directory as the HTML, convert to WebP,
    and return paths. Also check for an _files/ subdirectory (saves-page format).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    converted: list[Path] = []

    # Check for sibling _files/ directory (common for saved web pages)
    files_dir = html_path.parent / f"{html_path.stem}_files"
    if not files_dir.exists():
        files_dir = html_path.parent

    img_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".avif"}
    seen_bases: set[str] = set()

    for src in files_dir.iterdir():
        if src.suffix.lower() in img_extensions and src.is_file():
            stem = src.stem.lower()
            if stem in seen_bases:
                continue
            seen_bases.add(stem)
            webp_path = convert_image_to_webp(src)
            converted.append(webp_path)

    return converted


def html_to_markdown(html_path: Path, output_dir: Path) -> Path:
    """Convert an HTML file to Markdown, converting referenced images to WebP."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract and convert images
    images = extract_and_convert_images(html_path, output_dir)

    # Parse HTML content
    try:
        raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  ERROR reading {html_path.name}: {e}", file=sys.stderr)
        raise

    parser = HTMLProfileExtractor()
    parser.feed(raw_html)
    body_md = parser.get_markdown()
    title = parser._title or html_path.stem.replace("_", " ").replace("-", " ").title()

    # Build final markdown
    md_lines: list[str] = []
    md_lines.append(f"# {title}\n")

    if body_md.strip():
        # Remove the title if it's duplicated in the body
        title_escaped = re.escape(title)
        body_md = re.sub(rf"^(?:#\s+)?{title_escaped}\s*\n*", "", body_md, flags=re.MULTILINE)
        md_lines.append(body_md.strip())

    # Append image references
    for img_path in images:
        if img_path.exists():
            image_ref = img_path.relative_to(output_dir).as_posix()
            md_lines.append(f"\n![{img_path.stem}]({image_ref})\n")

    md_path = output_dir / f"{html_path.stem}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"  OK: {html_path.name}")
    return md_path


def process_directory(src_dir: Path, dst_dir: Path) -> dict:
    """Process all HTML files in directory."""
    stats = {"processed": 0, "skipped": 0, "errors": 0}

    for root, _, files in os.walk(src_dir):
        for fname in files:
            src = Path(root) / fname
            if src.suffix.lower() not in (".html", ".htm"):
                continue

            rel = src.relative_to(src_dir)
            out = dst_dir / rel.parent
            out.mkdir(parents=True, exist_ok=True)

            try:
                html_to_markdown(src, out)
                stats["processed"] += 1
            except Exception as e:
                stats["errors"] += 1
                print(f"  ERROR: {rel}: {e}", file=sys.stderr)

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Convert HTML to Markdown")
    parser.add_argument("source", help="Source directory")
    parser.add_argument("-o", "--output", help="Output directory (default: source)")
    args = parser.parse_args()

    src = Path(args.source).resolve()
    dst = Path(args.output).resolve() if args.output else src

    if not src.exists():
        print(f"Source not found: {src}", file=sys.stderr)
        sys.exit(1)

    print(f"Processing HTML: {src} -> {dst}")
    stats = process_directory(src, dst)
    print(
        f"\nDone: {stats['processed']} processed, {stats['skipped']} skipped, {stats['errors']} errors"
    )


if __name__ == "__main__":
    main()
