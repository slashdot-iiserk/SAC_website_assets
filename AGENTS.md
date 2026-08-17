# SAC Website Assets — Repository Overview for AI Agents

## Purpose

This repository holds all **processed** assets for the SAC (Student Activity Council) IISER Kolkata website, plus the tooling that produces them. The website consumes `processed/assets_map.jsonl` (the canonical 32-field catalogue) to render club cards, images, documents, events, and galleries.

---

## Directory Structure

```
SAC_website_assets/
├── processed/                       # PROCESSED assets (website-ready, ONLY data used by the site)
│   ├── <Club_Slug>/                 # One dir per club (slug = site contract)
│   │   ├── <Category>/…             # *.webp images, *.md documents, *.mp4 videos, *.m4a/*.wav audio
│   │   └── <Doc>_images/            # Images extracted from DOCX/PDF (WebP)
│   └── assets_map.jsonl             # Master index (1387 entries as of 2026-08)
│
├── tools/                           # Python processing tools (uv venv: .venv)
│   ├── rebuild_assets.py            # v2 pipeline: stage → rename → WebP → docs → videos → map
│   ├── process_assets.py            # v1 pipeline (legacy)
│   ├── image_converter.py           # Images → WebP (q85, max 2400px)
│   ├── docx_parser.py               # DOCX → Markdown + extract images
│   ├── pdf_parser.py                # PDF → Markdown + extract images
│   ├── html_parser.py               # HTML → Markdown
│   ├── xlsx support (in rebuild)    # XLSX → Markdown tables (openpyxl)
│   ├── generate_assets_map.py       # Map generator (32-field schema)
│   └── file_renamer.py              # Legacy sanitizer
│
└── (old raw assets/ dir was removed 2026-08 — raw source lives in the website repo's
     "SAC Website details" folder and in local backups; the submodule is processed-only)
```

---

## Processing Rules (v2 — 2026-08)

1. **Source of truth**: `SAC Website details/` in the main website repo.
   Slug map routes: `SAC Cultural/<Club>` → `<Club_Slug>`, `SAC Sports/<Sport>` → `SAC_Sports_<Sport>`,
   `SAC Hostel/*` → `SAC_Hostel`, `SAC Academics/*` → `SAC_Academics`/`Singularity_Astro_Club`/
   `Placement_Cell`, `SAC Food and Hygine` → `SAC_Food_and_Hygiene`.
2. **Images**: every image (jpg/jpeg/png/heic/webp) → WebP (quality 85, max 2400px).
   HEIC handled via ImageMagick. ARW (Sony raw) and Jupyter `.ipynb`/worksheet files are excluded.
3. **Smart renaming**: generic names (`WhatsApp Image…`, `IMG_…`, `PXL_…`, `VID_…`, `Board 1.x`)
   are renamed with the event-folder context, e.g. `Field_Trip_01.webp`, `Fresher_s_2025_Board_1_3.jpg`.
   `Copy of X` exact duplicates are dropped.
4. **Docs**: DOCX/PDF/HTML/XLSX → Markdown (tables preserved). Raw sources removed after parse.
5. **Videos**: compressed to H.264 mp4 (720p, CRF 30, AAC 128k) so every clip stays under
   GitHub's 100 MB hard limit (one 64 MB file remains, others < 20 MB).
6. **Audio**: kept as-is (`m4a`/`wav`).
7. **Map roles**: `office-bearer`, `logo`, `event` (folder-name driven: freshers/iism/interbatch/
   auction/farewell/tug/practice/workshops…), `iicm-achievement`, `equipment`, `portfolio`,
   `outer-fest`, `club-document`, `extracted-image`, `video`, `audio`, `other`.
   Videos are NEVER `is_event`/`is_iicm` (events page renders `<img>` only).
8. **Clubs with no new data** (Slashdot as of 2026-08) are carried over from the previous build.

---

## Asset Counts (2026-08 rebuild)

| File type | Count | Notes |
| --------- | ----- | ----- |
| image (webp) | 1050 | incl. extracted images |
| markdown | 186 | club docs, OB bios, event writeups |
| video (mp4) | 101 | compressed H.264 |
| audio | 6 | Nrutya performance clips |
| **total** | **1387** | ~467 MB (raw source was ~3.8 GB) |

Clubs: 31 slugs, matching the website's `getClubPageUrl` map exactly.

---

## Rebuild Command

```bash
cd tools
PYTHONPATH= .venv/bin/python rebuild_assets.py \
  "/path/to/SAC Website details" -o ../processed
```

Then regenerate the map:
```bash
PYTHONPATH= .venv/bin/python -c "import sys; sys.path.insert(0,'.'); from generate_assets_map import generate_assets_map; from pathlib import Path; generate_assets_map(Path('../processed'))"
```

After the rebuild: `git add -A && git commit && git push origin main`.

---

## Remote

- **URL:** git@github.com:slashdot-iiserk/SAC_website_assets.git
- **Branch:** main (deploy requires this submodule to be checked out)
