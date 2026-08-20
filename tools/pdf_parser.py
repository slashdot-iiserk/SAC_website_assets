#!/usr/bin/env python3
"""Parse PDF files to Markdown, extracting text and embedded images."""

import os
import sys
from pathlib import Path
from PIL import Image
import fitz  # PyMuPDF

WEBP_QUALITY = 85


def convert_to_webp(src: Path) -> Path:
    """Convert image to WebP format."""
    if src.suffix.lower() == ".webp":
        return src

    dst = src.with_suffix(".webp")
    if dst.exists():
        return dst

    try:
        with Image.open(src) as img:
            if img.mode == "RGBA":
                img = img.convert("RGB")
            elif img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            w, h = img.size
            if max(w, h) > 2000:
                ratio = 2000 / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

            img.save(dst, "WEBP", quality=WEBP_QUALITY, method=6)
            src.unlink()
            return dst
    except Exception:
        return src


def extract_pdf_images(pdf_path: Path, output_dir: Path) -> list[Path]:
    """Extract images from PDF."""
    images = []
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))

    for page_num in range(len(doc)):
        page = doc[page_num]
        for img_idx, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]
            ext = base_image.get("ext", "png")
            img_name = f"page{page_num + 1}_img{img_idx + 1}.{ext}"
            img_path = output_dir / img_name
            img_path.write_bytes(img_bytes)

            webp_path = convert_to_webp(img_path)
            images.append(webp_path)

    doc.close()
    return images


def pdf_to_markdown(
    pdf_path: Path,
    output_dir: Path,
    output_stem: str | None = None,
    image_dir_name: str | None = None,
) -> Path:
    """Convert PDF to Markdown with extracted text and images."""
    output_stem = output_stem or pdf_path.stem
    doc = fitz.open(str(pdf_path))
    img_dir = output_dir / (image_dir_name or f"{output_stem}_images")
    images = extract_pdf_images(pdf_path, img_dir)

    md_lines = [f"# {pdf_path.stem.replace('_', ' ').replace('-', ' ').title()}\n"]

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        if text.strip():
            md_lines.append(f"## Page {page_num + 1}\n")
            md_lines.append(text.strip() + "\n")

        page_imgs = [
            img for img in images if img.name.startswith(f"page{page_num + 1}")
        ]
        for img_path in page_imgs:
            image_ref = img_path.relative_to(output_dir).as_posix()
            md_lines.append(f"\n![Image]({image_ref})\n")

    doc.close()

    md_path = output_dir / f"{output_stem}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return md_path


def process_directory(src_dir: Path, dst_dir: Path) -> dict:
    """Process all PDF files in directory."""
    stats = {"processed": 0, "skipped": 0, "errors": 0}

    for root, _, files in os.walk(src_dir):
        for fname in files:
            src = Path(root) / fname
            if src.suffix.lower() != ".pdf":
                continue

            rel = src.relative_to(src_dir)
            out = dst_dir / rel.parent
            out.mkdir(parents=True, exist_ok=True)

            try:
                pdf_to_markdown(src, out)
                stats["processed"] += 1
                print(f"  OK: {rel}")
            except Exception as e:
                stats["errors"] += 1
                print(f"  ERROR: {rel}: {e}", file=sys.stderr)

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Convert PDF to Markdown")
    parser.add_argument("source", help="Source directory")
    parser.add_argument("-o", "--output", help="Output directory (default: source)")
    args = parser.parse_args()

    src = Path(args.source).resolve()
    dst = Path(args.output).resolve() if args.output else src

    if not src.exists():
        print(f"Source not found: {src}", file=sys.stderr)
        sys.exit(1)

    print(f"Processing PDF: {src} -> {dst}")
    stats = process_directory(src, dst)
    print(
        f"\nDone: {stats['processed']} processed, {stats['skipped']} skipped, {stats['errors']} errors"
    )


if __name__ == "__main__":
    main()
