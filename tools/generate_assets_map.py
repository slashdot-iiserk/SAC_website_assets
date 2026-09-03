#!/usr/bin/env python3
"""Generate the canonical assets_map.jsonl for the SAC website.

This is the SINGLE source of truth for the website's asset catalogue. It
supersedes any previous markdown or JSONL index in the repo.

Schema (one JSON object per line, file ordering = walk order):
  id                      int     — sequential 1-indexed identifier
  path                    str     — path relative to the processed/ root
  absolute_path           str     — path relative to the main repo root
  public_url              str     — URL the asset will be served at when the
                                     site is deployed (base: /SAC_Website/)
  club                    str     — sanitized directory name of the club
  club_name               str     — human-readable club name
  category                str     — subdirectory under the club
  category_label          str     — human-readable category description
  filename                str     — basename of the file
  title                   str     — cleaned human title
  extension               str     — file extension (lower-case, no dot)
  file_type               str     — image | markdown | pdf | document | spreadsheet
  mime_type               str     — best-effort MIME type
  size_bytes              int     — file size in bytes
  width                   int?    — pixel width (images only)
  height                  int?    — pixel height (images only)
  orientation             str?    — landscape | portrait | square (images only)
  aspect_ratio            float?  — width / height (images only)
  role                    str     — semantic role: office-bearer | logo | event
                                     | iicm-achievement | equipment | portfolio
                                     | outer-fest | club-document | extracted-image
                                     | other
  tenure                  str?    — YY-YY or YYYY-YY tenure marker
  year                    int?    — primary year (e.g. 2025)
  person                  str?    — person's name (for OB portraits)
  ob_role                 str?    — office-bearer role (e.g. "Secretary")
  is_ob_portrait          bool
  is_logo                 bool
  is_event                bool
  is_iicm                 bool
  is_extracted_from_doc   bool
  is_markdown_content     bool
  description             str     — one-line human description
  tags                    list    — list of lowercase tags
  updated_at              str     — ISO timestamp of when the entry was generated

Usage
-----
Standalone CLI (run from anywhere; defaults assume the standard submodule
layout, i.e. the script lives at <submodule>/tools/generate_assets_map.py
and the processed tree is at <submodule>/processed/):

    python generate_assets_map.py
    python generate_assets_map.py /path/to/processed
    python generate_assets_map.py /path/to/processed -o /tmp/map.jsonl
    python generate_assets_map.py --site-base /my-site/

Importable:

    from generate_assets_map import generate_assets_map
    generate_assets_map(Path("processed"), Path("processed/assets_map.jsonl"))
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import mimetypes
import os
import re
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

# ----------------------------------------------------------------------------
# Constants — paths, club metadata, role dictionaries
# ----------------------------------------------------------------------------
TOOLS_DIR = Path(__file__).resolve().parent
SUBMODULE_ROOT = TOOLS_DIR.parent
DEFAULT_PROCESSED_DIR = SUBMODULE_ROOT / "processed"
DEFAULT_OUTPUT = DEFAULT_PROCESSED_DIR / "assets_map.jsonl"
DEFAULT_SITE_BASE = "/SAC_Website"  # matches Vite base in vite.config.js

CLUB_NAMES = {
    "AARSHI_-_Drama_Club": "AARSHI - Drama Club",
    "Arts_Club_of_IISER_Kolkata": "Arts Club of IISER Kolkata",
    "Campus_Radio_IISER_KOLKATA": "Campus Radio IISER KOLKATA (IKCR)",
    "IKQC_-_Quiz_Club_of_IISER_Kolkata": "IKQC - Quiz Club of IISER Kolkata",
    "Literary_Club_of_IISER_Kolkata": "Literary Club of IISER Kolkata",
    "Movie_Club_of_IISER_K": "Movie Club of IISER K",
    "Music_Club_of_IISER_K": "Music Club of IISER K",
    "Nature_Club_Of_IISER_Kolkata": "Nature Club of IISER Kolkata",
    "Nrutya_-_The_Dance_Club_of_IISER_Kolkata": "Nrutya - Dance Club of IISER Kolkata",
    "PIXEL-Photography_Club": "PIXEL - Photography Club",
    # New SAC committees (added 2026-06)
    "Placement_Cell": "SAC Placement Cell",
    "SAC_Academics": "SAC Academics",
    "SAC_Hostel": "SAC Hostel Committee",
    # SAC Sports clubs (added 2026-07)
    "SAC_Sports": "SAC Sports",
    "SAC_Sports_Badminton": "Badminton",
    "SAC_Sports_Basketball": "Basketball",
    "SAC_Sports_Chess": "Chess Club",
    "SAC_Sports_GYM": "GYM Club",
    "SAC_Sports_Kabaddi": "Kabaddi Club",
    "SAC_Sports_SYDC": "SYDC - Self Defence Club",
    "SAC_Sports_Athletics": "Athletics Club",
    "SAC_Sports_Carrom": "Carrom Club",
    "SAC_Sports_Cricket": "Cricket Club",
    "SAC_Sports_Football": "Football Club",
    "SAC_Sports_Gaming": "Gaming Club",
    "SAC_Sports_Kho_Kho": "Kho-Kho Club",
    "SAC_Sports_Lawn_Tennis": "Lawn Tennis Club",
    "SAC_Sports_Rubik": "Rubik's Cube Club",
    "SAC_Sports_Table_Tennis": "Table Tennis Club",
    "SAC_Sports_Volleyball": "Volleyball Club",
    "Singularity_Astro_Club": "Singularity - The Astronomy Club",
    "Slashdot_Programming_Club": "Slashdot - Programming Club",
    "SPICMACAY": "SPICMACAY",
    "Slashdot_Programming_Club": "Slashdot — Coding & Design Club",
    "SAC_Food_and_Hygiene": "SAC Food and Hygiene",
    # Campus-wide media archive (buildings/places — not a club)
    "Campus_Archive": "Campus Archive",
}

CLUB_TAGS = {
    "AARSHI_-_Drama_Club": ["drama", "theatre", "acting", "stage"],
    "Arts_Club_of_IISER_Kolkata": ["arts", "visual", "creative"],
    "Campus_Radio_IISER_KOLKATA": ["radio", "media", "broadcast", "podcast"],
    "IKQC_-_Quiz_Club_of_IISER_Kolkata": ["quiz", "knowledge", "trivia"],
    "Literary_Club_of_IISER_Kolkata": ["literary", "debate", "writing"],
    "Movie_Club_of_IISER_K": ["movies", "film", "cinema", "screenings"],
    "Music_Club_of_IISER_K": ["music", "singing", "instruments"],
    "Nature_Club_Of_IISER_Kolkata": ["nature", "environment", "ecology"],
    "Nrutya_-_The_Dance_Club_of_IISER_Kolkata": [
        "dance",
        "performance",
        "choreography",
    ],
    "PIXEL-Photography_Club": ["photography", "camera", "visual"],
    # New SAC committees (added 2026-06)
    "Placement_Cell": ["academics", "placement", "career"],
    "SAC_Academics": ["academics", "academic-committee"],
    "SAC_Hostel": ["hostel", "wing-representative", "sub-committee", "residential"],
    # SAC Sports clubs (added 2026-07)
    "SAC_Sports": [
        "sports",
        "gym",
        "badminton",
        "basketball",
        "chess",
        "kabaddi",
        "self-defence",
    ],
    "SAC_Sports_Badminton": ["sports", "badminton"],
    "SAC_Sports_Basketball": ["sports", "basketball"],
    "SAC_Sports_Chess": ["sports", "chess", "carrom"],
    "SAC_Sports_GYM": ["sports", "gym", "fitness"],
    "SAC_Sports_Kabaddi": ["sports", "kabaddi"],
    "SAC_Sports_SYDC": ["sports", "self-defence", "sydc"],
    "SAC_Sports_Athletics": ["sports", "athletics", "running"],
    "SAC_Sports_Carrom": ["sports", "carrom", "board-games"],
    "SAC_Sports_Cricket": ["sports", "cricket"],
    "SAC_Sports_Football": ["sports", "football", "soccer"],
    "SAC_Sports_Gaming": ["sports", "gaming", "esports"],
    "SAC_Sports_Kho_Kho": ["sports", "kho-kho", "traditional"],
    "SAC_Sports_Lawn_Tennis": ["sports", "tennis", "lawn-tennis"],
    "SAC_Sports_Rubik": ["sports", "rubik", "puzzles"],
    "SAC_Sports_Table_Tennis": ["sports", "table-tennis", "ping-pong"],
    "SAC_Sports_Volleyball": ["sports", "volleyball"],
    "Singularity_Astro_Club": ["academics", "astronomy", "science"],
    "Slashdot_Programming_Club": ["academics", "programming", "coding"],
    "SPICMACAY": ["cultural", "classical", "music", "heritage"],
    "Slashdot_Programming_Club": [
        "academics",
        "programming",
        "coding",
        "design",
        "slashdot",
    ],
    "SAC_Food_and_Hygiene": ["hygiene", "food", "mess", "medical", "smc"],
}

CATEGORY_LABEL = {
    "OBs": "Office-bearer portrait",
    "OB": "Office-bearer portrait",
    "nOBs": "Office-bearer portrait (new term)",
    "office-bearers": "Office-bearer portrait",
    "office_bearers": "Office-bearer portrait",
    "25-26_OBs": "Office-bearer portrait (tenure 2025-26)",
    "26-27_OBs": "Office-bearer portrait (tenure 2026-27)",
    "26-27_Club_OBs": "Office-bearer portrait (tenure 2026-27)",
    "2026-27_OBs": "Office-bearer portrait (tenure 2026-27)",
    "New_OB_26-27_Term": "Office-bearer portrait (new term 2026-27)",
    "OBs_26-27": "Office-bearer portrait (tenure 2026-27)",
    "Logos": "Club logo / brand asset",
    "Logo": "Club logo / brand asset",
    "Campus_Radio_Logo": "Club logo / brand asset",
    "EVENTS_PICS": "Event photograph",
    "Event_Photos": "Event photograph",
    "Event_Pics": "Event photograph",
    "Event_Photographs": "Event photograph",
    "Event_Pictures": "Event photograph",
    "Photos_Of_Movie_Club": "Club photograph (screenings / OBs)",
    "Campus_Radio_Pictures": "Club photograph (events / OBs)",
    "IICM_Achievements": "IICM (Inter-IISER Cultural Meet) achievement photo",
    "IICM_Photographs": "IICM (Inter-IISER Cultural Meet) photograph",
    "IICM_Photos": "IICM (Inter-IISER Cultural Meet) photograph",
    "IICM_Pics": "IICM (Inter-IISER Cultural Meet) photograph",
    "IICM": "IICM (Inter-IISER Cultural Meet) photograph",
    "Equipments": "Equipment / gear photograph",
    "Portfolio_Pixel": "Member portfolio image",
    "Portfolio": "Member portfolio image",
    "Outer_Fest_Achievement": "Outer-fest achievement / winner photo",
    "Overall_Document": "Source document (raw)",
    "Past_OBs": "Past office-bearers data",
    "Campus_Radio_Information": "Campus radio information document",
    "Movie_Club_Information": "Movie club information document",
    "Report_Compiled_Extra": "Compiled report (raw)",
    # SAC Academics (added 2026-06)
    "General_Secretaries": "General Secretaries (academics portfolio)",
    "Singularity_The_Astronomy_club": "Singularity - The Astronomy Club",
    "Club_details": "Club details document",
    "Office_Bearers": "Office-bearers",
    "Events_and_activities": "Events and activities",
    "Members": "Club members",
    "Photos_and_media": "Photos and media",
    "Slashdot_programming_and_designing_through_code": "Slashdot - programming and designing through code",
    # SAC Hostel (added 2026-06)
    "Genral_secretaries": "General Secretaries (hostel portfolio)",
    "SUB-Committee": "Hostel sub-committee",
    "Communication_and_Grievances": "Sub-committee: Communication and Grievances",
    "Health_and_Hygiene": "Sub-committee: Health and Hygiene",
    "Purchase_and_Handling": "Sub-committee: Purchase and Handling",
    "Repair_and_Maintenance": "Sub-committee: Repair and Maintenance",
    "Safety_and_Security": "Sub-committee: Safety and Security",
    "WRs": "Wing Representatives (by hostel block)",
    "ICVS": "Wing Representatives - ICVS hostel",
    "NH_boys": "Wing Representatives - NH boys hostel",
    "NH_girls": "Wing Representatives - NH girls hostel",
    "NSCB_boys": "Wing Representatives - NSCB boys hostel",
    "NSCB_girls": "Wing Representatives - NSCB girls hostel",
    # SAC Sports (added 2026-07)
    "21MS_farewell": "Farewell event (21 MS batch)",
    "FAREWELL": "Farewell event",
    "Tug_of_War_photos": "Tug of War event",
    "self-defence": "Self-defence workshop",
    "Events_and_Activities": "Events and activities",
    "Members": "Club members",
    "Photos_and_Medias": "Photos and media",
    "ACTIVE_MEMBERS_images": "Images extracted from active members list",
    # Nrutya (added 2026-07)
    "Dance_Battle": "IICM Dance Battle",
    "Dance_battle": "IICM Dance Battle",
    "Group_Dance": "IICM Group Dance",
    "Solo_Classical": "IICM Solo Classical Dance",
    "Synchro": "IICM Synchro Dance",
    "Winning_Moment": "IICM Prize Ceremony",
    "Garba_Night": "Garba Night event",
    "Interbatch_Dance_Competition": "Interbatch Dance Competition",
    "Dance-Club-Workshops": "Dance workshop",
    "Semi-Classical- Dance- Workshop": "Semi-Classical Dance Workshop",
    "Hip-Hop": "Hip-Hop Workshop",
    "AnnualProduction": "Annual Production",
    # Music Club (added 2026-07)
    "Jhankaar": "Jhankaar — Classical Music event",
    "Rampage": "Rampage — Battle of Bands",
    "Voice": "The Voice — Singing Competition",
    # Nature Club (added 2026-07)
    "Ecotrail": "Ecotrail — Nature walk",
    "Field_Trip": "Field Trip",
    "Field_trip": "Field trip",
    # IKQC categories (added 2026-07)
    "Cult-Consp": "Cult Consp — Cultural Conspiracy event",
    "dublin_wager": "Dublin Wager — Quiz event",
    "freshers": "Freshers' event",
    "interbatch": "Interbatch event",
    "music_quiz": "Music Quiz event",
    "tri-quizard": "Tri-Wizard — Quiz event",
    # 2026-08 new-data categories (sports events)
    "Fresher_s_2025": "Freshers' event (2025)",
    "IISM_2024": "IISM inter-IISER sports meet (2024)",
    "IISM_2025": "IISM inter-IISER sports meet (2025)",
    "INTERBATCH_2026": "Interbatch tournament (2026)",
    "Interbatch_2025": "Interbatch tournament (2025)",
    "Interbatch_2026": "Interbatch tournament (2026)",
    "Auction_tournament": "Auction tournament",
    "Farewell": "Farewell event",
    "FAREWELL": "Farewell event",
    "Boys__Freshers": "Boys' freshers' match",
    "Girls__freshers": "Girls' freshers' match",
    "Match_streamings": "Match streaming / highlights",
    "Test_match": "Test match",
    "practice_sessions": "Practice session",
    "Practice_Matches": "Practice matches",
    "Tug_of_War_photos": "Tug of War event",
    "21MS_farewell": "Farewell event (21 MS batch)",
    "GYM_photos": "GYM club photos",
    "self-defence": "Self-defence workshop",
    "yoga": "Yoga session",
    "Gallery": "Gallery",
    "IISM_-_NISER": "IISM vs NISER match",
    "Interbatch_Tournament": "Interbatch tournament",
    "Freshers_match": "Freshers' match",
    "21ms_Farewell": "Farewell event (21 MS batch)",
    "IISERK_tennis_pictures": "IISERK tennis photos",
    "IISM_pictures": "IISM tennis photos",
    "Fresher_s_Match": "Freshers' match",
    "AUCTION_TOURNAMENT": "Auction tournament",
    "INTER-BATCH_TOURNAMENT": "Inter-batch tournament",
    "Club_Events": "Club events",
    "Club_Details": "Club details document",
    "Events_and_Activities": "Events and activities",
    "Events_and_activities": "Events and activities",
    "Photos_and_Media": "Photos and media",
    "Photos_and_Medias": "Photos and media",
    "Office_Bearers": "Office-bearers",
    "Members": "Club members",
    "Members_": "Club members",
}

ROLES_KW = {
    "secretary",
    "convener",
    "convenor",
    "treasurer",
    "president",
    "vp",
    "ceo",
    "cfo",
    "coo",
    "pro",
    "eo",
    "eventorganiser",
    "eventorganizer",
    "event organiser",
    "event organizer",
    "event manager",
    "events head",
    # Expanded for the SAC dataset (added 2026-06)
    "event coordinator",
    "social media manager",
    "social media head",
    "socialmedia manager",
    "socialmedia head",
    "social media",
    "media manager",
    "media head",
    "publicity",
    "publicity head",
    "public relations",
    "pr head",
    "marketing",
    "marketing head",
    "tech head",
    "technical head",
    "design head",
    "creative head",
    "content head",
    "editorial",
    "editor",
    "wing representative",
    "wr",
    "sub committee",
    "sub-committee",
    "general secretary",
    "joint secretary",
    "deputy secretary",
    "coordinator",
}

EXT_MIME = {
    "webp": "image/webp",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "heic": "image/heic",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
    "avif": "image/avif",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "html": "text/html",
    "htm": "text/html",
    "zip": "application/zip",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "m4v": "video/x-m4v",
    "webm": "video/webm",
    "m4a": "audio/mp4",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
}

ABSOLUTE_PATH_PREFIX = "public/assets/processed"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def file_type_of(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext in {"webp", "jpg", "jpeg", "png", "gif", "heic", "bmp", "svg", "avif"}:
        return "image"
    if ext in {"md", "markdown", "txt"}:
        return "markdown"
    if ext in {"docx", "doc"}:
        return "document"
    if ext == "pdf":
        return "pdf"
    if ext in {"xlsx", "xls", "csv"}:
        return "spreadsheet"
    if ext in {"html", "htm"}:
        return "html"
    if ext in {"mp4", "mov", "m4v", "webm", "avi", "mkv"}:
        return "video"
    if ext in {"m4a", "wav", "mp3", "ogg", "flac", "aac"}:
        return "audio"
    return ext or "other"


def clean_token(s: str) -> str:
    s = re.sub(
        r"\.(webp|jpg|jpeg|png|pdf|md|docx|xlsx|csv|jsonl)$", "", s, flags=re.IGNORECASE
    )
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_tenure(text: str) -> str | None:
    m = re.search(r"(\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    m = re.search(r"(20\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    # Underscore tenure form used by club folders, e.g. "26_27_OBs", "2025_26"
    # Lookarounds keep this from matching inside longer digit runs.
    m = re.search(r"(?<!\d)(\d{2})_(\d{2})(?!\d)", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 20 <= a <= 40 and 20 <= b <= 40 and b == a + 1:
            return f"{m.group(1)}-{m.group(2)}"
    return None


def tenure_to_year(tenure: str | None) -> int | None:
    """Pick the primary year from a tenure marker like '25-26' or '2025-26'."""
    if not tenure:
        return None
    m = re.match(r"(\d{2,4})-", tenure)
    if not m:
        return None
    y = m.group(1)
    if len(y) == 2:
        return 2000 + int(y)
    return int(y)


def _year_window() -> tuple[int, int]:
    """Sane year range: the institute's founding decade through next year."""
    import datetime as _dt

    return (2010, _dt.date.today().year + 1)


def extract_year(text: str) -> int | None:
    """Pull the most likely year out of a path / filename.

    Only explicit 4-digit years are trusted (e.g. 'IISM_2025', 'Fresher_s_2025').
    The old bare 2-digit fallback produced garbage from sequence counters
    ('Girls_freshers_30.mp4' → 2030) and has been removed — tenure-style
    fragments ('26_27', '25-26') are handled by extract_tenure instead.
    """
    lo, hi = _year_window()
    candidates: list[int] = []
    for m in re.finditer(r"(?<!\d)(20\d{2})(?!\d)", text):
        y = int(m.group(1))
        if lo <= y <= hi:
            candidates.append(y)
    if not candidates:
        return None
    return max(candidates)


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (None, None)


def split_ob_filename(fname: str) -> tuple[str, str | None, str | None]:
    base = re.sub(r"\.(webp|jpg|jpeg|png)$", "", fname, flags=re.IGNORECASE)
    # Strip stray "jpg" suffix leaking from original filenames (e.g. "2026-27jpg")
    base = re.sub(r"(?<=\d)jpg$", "", base, flags=re.IGNORECASE)
    # Strip trailing tenure markers like "_2025-26" or "_26-27" before role matching.
    # Also handles trailing "_" or "-" separators around the tenure.
    base = re.sub(r"[_\-]*\d{2,4}-\d{2}[_\-]*$", "", base)
    # Drop leading "OB-" or "nOB-" markers
    if base.startswith(("OB-", "nOB-")):
        stripped = base[4:] if base.startswith("nOB-") else base[3:]
        parts: list[str]
        for sep in ("_-_", " - "):
            if sep in stripped:
                parts = stripped.split(sep)
                break
        else:
            parts = [stripped]
        marker = "new" if base.startswith("nOB-") else "current"
        # Strip known club prefix abbreviations from the name portion
        if len(parts) >= 2:
            return clean_token(parts[0]), clean_token(parts[-1]), marker
        # For single-part names with club prefixes (e.g. "IKQC-Adhiraj")
        name = stripped
        for prefix in ("IKQC-", "IKQC_"):
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break
        return clean_token(name), None, marker

    # Try multi-word role match from a known list, looking at the tail of the base.
    # Roles can be 1-3 words joined by underscores (e.g. "Event_Manager",
    # "Social_Media_Manager"). Try longest first.
    # Filter out empty tokens (caused by trailing underscores) and "-" fragments.
    raw_tokens = base.split("_")
    tokens = [t for t in raw_tokens if t and t != "-"]
    # Build a candidate list of role suffixes (1..3 tokens) and check against ROLES_KW
    for n_role_words in range(min(3, len(tokens)), 0, -1):
        role_tokens = tokens[-n_role_words:]
        role_str = " ".join(role_tokens).lower()
        if role_str in ROLES_KW:
            role = clean_token(" ".join(role_tokens))
            name = clean_token(" ".join(tokens[:-n_role_words]))
            return name, role, None

    # Single-word role embedded in filename (e.g. "Sukanya_Chowdhury_Event_Coordinator_2025-26")
    if tokens and tokens[-1].lower() in ROLES_KW:
        role = clean_token(tokens[-1])
        name = clean_token(" ".join(tokens[:-1]))
        return name, role, None

    # CEO/CFO/COO/PRO/EO/VP/President/Convener/etc. at end of token (with tenure suffix)
    m = re.search(
        r"^([A-Za-z][\w\-\. ]*?)[_\-]?"
        r"(CEO|CFO|COO|PRO|Secretary|Convener|Treasurer|President|VP|EO|EventOrganiser|EventOrganizer|Event Organiser|Event Organizer)"
        r"(?:[_\-](\d{2,4}-\d{2}))?$",
        base,
        re.IGNORECASE,
    )
    if m:
        return clean_token(m.group(1)), clean_token(m.group(2)), m.group(3)

    return clean_token(base), None, None


def first_markdown_heading(path: Path) -> tuple[str | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None, None
    title = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip()
            break
    if not title:
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("## "):
                title = s[3:].strip()
                break

    def is_header(s: str) -> bool:
        if not s:
            return True
        if s.endswith(":"):
            return True
        letters = [c for c in s if c.isalpha()]
        return bool(letters) and all(c.isupper() for c in letters)

    para: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            if buf and not is_header(" ".join(buf)):
                para = " ".join(buf).strip()
                break
            buf = []
            continue
        if s.startswith(("#", "!", "[", "|", "```", "-", "*", ">")):
            continue
        buf.append(s)
    if para is None and buf and not is_header(" ".join(buf)):
        para = " ".join(buf).strip()
    if para and len(para) > 280:
        para = para[:277].rstrip() + "..."
    return title, para


# ----------------------------------------------------------------------------
# Classification & description
# ----------------------------------------------------------------------------
# Event-named folder categories that do not contain the literal word "event"
# (2026-08 new data: sports tournaments, freshers, farewells, workshops…).
# Images under these categories are flagged is_event so the events page
# surfaces them.
EVENT_CATEGORY_KEYWORDS = (
    "fresher",
    "iism",
    "interbatch",
    "inter_batch",
    "auction",
    "farewell",
    "tug",
    "match",
    "tournament",
    "practice",
    "workshop",
    "garba",
    "jhankaar",
    "rampage",
    "ecotrail",
    "field_trip",
    "field_trip",
    "yoga",
    "gallery",
    "screenings",
    "interbatch",
)


def _is_event_category(category: str) -> bool:
    cat = category.lower().replace(" ", "_")
    return any(kw in cat for kw in EVENT_CATEGORY_KEYWORDS)


def classify_and_describe(
    rel_parts: list[str],
    fname: str,
    ftype: str,
    abs_path: Path,
) -> dict:
    # Use the LAST directory segment as the category. This gives the most
    # specific location in both the flat club layout (e.g.
    # AARSHI/25-26_OBs/file.webp -> "25-26_OBs") and the deeper Hostel layout
    # (e.g. SAC_Hostel/WRs/ICVS/D_block_1st_floor/file.webp -> "D_block_1st_floor").
    category = rel_parts[-2] if len(rel_parts) > 2 else "(root)"
    full = "/".join(rel_parts[:-1]) + "/" + fname if len(rel_parts) > 1 else fname
    # When the path is deeper, also surface the parent grouping (hostel or
    # sub-committee) for richer descriptions / tenure / year extraction.
    parent_full = " ".join(rel_parts[:-1]) if len(rel_parts) > 1 else ""
    full = (parent_full + " " + fname).strip()

    # Avoid false tenure/year matches from equipment filenames like
    # "Nikon_200-500mm" or "16-50mm_lens". Only extract from category
    # (parent directory) for equipment.
    cat_lower = category.lower()
    fname_lower = fname.lower()
    is_equipment = "equipment" in cat_lower or "cameras" in cat_lower
    tenure_source = parent_full if is_equipment else full
    year_source = parent_full if is_equipment else full
    tenure = extract_tenure(tenure_source)
    year = tenure_to_year(tenure)
    if year is None:
        year = extract_year(year_source)

    flags = {
        "is_ob_portrait": False,
        "is_logo": False,
        "is_event": False,
        "is_iicm": False,
        "is_extracted_from_doc": False,
        "is_markdown_content": False,
    }
    role = "other"
    person: str | None = None
    ob_role: str | None = None
    title = clean_token(fname)
    description = ""

    # Detect OB-related category up front so both markdown bio documents
    # and standalone portrait images can be classified consistently.
    is_ob_category = (
        "OBs" in category
        or "office" in cat_lower
        or "office_bearers" in cat_lower
        or "office-bearers" in cat_lower
        or cat_lower.endswith("_obs")
        or re.search(r"\d{2}-\d{2}_obs?$", category, re.IGNORECASE) is not None
        or "_ob_" in cat_lower
        or cat_lower.endswith("_ob_term")
    )

    if ftype == "markdown" and not is_ob_category:
        # Generic club document
        flags["is_markdown_content"] = True
        role = "club-document"
        md_title, _para = first_markdown_heading(abs_path)
        if md_title:
            title = md_title
            description = (
                f'Club information document (parsed from DOCX/PDF) — "{md_title}".'
            )
        else:
            description = "Club information document (parsed from DOCX/PDF)."
    elif ftype == "markdown" and is_ob_category:
        # Office-bearer bio document (e.g. Singularity OBs).
        # These are markdown TEXT docs, not portrait images, so
        # is_ob_portrait stays False — the website can still surface
        # them via the "office-bearer" role but won't try to render
        # text as a photo.
        flags["is_markdown_content"] = True
        role = "office-bearer"
        person, ob_role, marker = split_ob_filename(fname)
        title = person or clean_token(fname)
        bits = []
        if person:
            bits.append(person)
        if ob_role:
            bits.append(ob_role)
        suffix = ", ".join(bits) if bits else clean_token(fname)
        tenure_str = f" ({tenure})" if tenure else ""
        new_str = " — new term" if marker == "new" else ""
        description = f"Office-bearer profile {suffix}{tenure_str}{new_str}."
    elif ftype in {"document", "pdf", "spreadsheet"}:
        role = "source-document"
        description = f"Source document ({ftype}) — {clean_token(fname)}."
    elif ftype == "video":
        # Videos are preserved in the map for completeness but are NOT
        # event/iicm flagged: pages/events.js renders those as <img> tags,
        # which would break on video files.
        role = "video"
        description = f"Video clip — {clean_token(fname)}."
    elif ftype == "audio":
        role = "audio"
        description = f"Audio clip — {clean_token(fname)}."
    else:
        if cat_lower.endswith("_images") or "_images" in cat_lower:
            flags["is_extracted_from_doc"] = True
            role = "extracted-image"
            # If the extracted image looks like a logo (e.g. "Logo_light.webp"),
            # also flag it as a logo so the website can use it as the club's
            # brand mark. Without this, clubs whose only logo is embedded in
            # a document never get a proper logo on the site.
            if re.search(r"logo", fname_lower):
                flags["is_logo"] = True
                role = "logo"
                description = f"Club logo / brand asset — {clean_token(fname)}."
            else:
                description = "Image extracted from a source document (DOCX/PDF), converted to WebP."
        else:
            is_ob_filename = (
                fname.startswith(("OB-", "nOB-"))
                or re.search(
                    r"(CEO|CFO|COO|PRO|Secretary|Convener|Treasurer|President|VP|EO)_?\d{2,4}-\d{2}",
                    fname,
                    re.IGNORECASE,
                )
                is not None
            )
            if is_ob_category or is_ob_filename:
                flags["is_ob_portrait"] = True
                role = "office-bearer"
                person, ob_role, marker = split_ob_filename(fname)
                bits = []
                if person:
                    bits.append(person)
                if ob_role:
                    bits.append(ob_role)
                suffix = ", ".join(bits) if bits else clean_token(fname)
                tenure_str = f" ({tenure})" if tenure else ""
                new_str = " — new term" if marker == "new" else ""
                description = (
                    f"Portrait of office-bearer {suffix}{tenure_str}{new_str}."
                )
            elif "logo" in cat_lower or "logo" in fname_lower:
                flags["is_logo"] = True
                role = "logo"
                description = f"Club logo / brand asset — {clean_token(fname)}."
            elif "iicm" in cat_lower or "iicm" in fname_lower:
                flags["is_iicm"] = True
                role = "iicm-achievement"
                m = re.search(
                    r"IICM[\s_]*(\d{2,4})", fname + " " + category, re.IGNORECASE
                )
                yyyy = m.group(1) if m else None
                if yyyy and len(yyyy) == 2:
                    yyyy = "20" + yyyy
                if yyyy:
                    description = f"IICM {yyyy} achievement / competition photograph — {clean_token(fname)}."
                else:
                    description = f"IICM (Inter-IISER Cultural Meet) photograph — {clean_token(fname)}."
            elif "event" in cat_lower or _is_event_category(category):
                flags["is_event"] = True
                role = "event"
                description = f"Event photograph — {clean_token(fname)}."
            elif "equipment" in cat_lower or "cameras" in cat_lower:
                role = "equipment"
                description = f"Equipment / gear photograph — {clean_token(fname)}."
            elif "portfolio" in cat_lower:
                role = "portfolio"
                description = f"Member portfolio photograph — {clean_token(fname)}."
            elif "outer" in cat_lower:
                role = "outer-fest"
                description = f"Outer-fest achievement / winner photograph — {clean_token(fname)}."
            else:
                description = f"Club photograph — {clean_token(fname)}."

    if ftype == "image" and flags["is_ob_portrait"] and person:
        title = person

    # Videos living in event-named folders ARE event media — the events page
    # renders <video> for them. (Audio stays unflagged: practice clips etc.)
    if ftype == "video" and not flags["is_event"]:
        if "event" in cat_lower or _is_event_category(category):
            flags["is_event"] = True

    return {
        **flags,
        "role": role,
        "tenure": tenure,
        "year": year,
        "person": person,
        "ob_role": ob_role,
        "category_label": _humanize_category(category),
        "title": title,
        "description": description,
    }


def _humanize_category(category: str) -> str:
    """Generate a clean human-readable category label.

    Falls back to underscore→space replacement, with special handling for
    `_images` subdirs (used by the DOCX/PDF parser to hold extracted images):
    instead of "Foo images" we render "Images extracted from Foo".
    """
    if category in CATEGORY_LABEL:
        return CATEGORY_LABEL[category]
    if category.endswith("_images"):
        base = category[: -len("_images")].replace("_", " ").strip()
        if base:
            return f"Images extracted from {base}"
        return "Images extracted from source document"
    return category.replace("_", " ")


def build_tags(club: str, info: dict) -> list[str]:
    tags: list[str] = list(CLUB_TAGS.get(club, []))
    role = info["role"]
    if role == "office-bearer":
        tags.append("ob")
    elif role == "iicm-achievement":
        tags.extend(["iicm", "achievement", "competition"])
    elif role == "event":
        tags.append("event")
    elif role == "logo":
        tags.append("logo")
    elif role == "equipment":
        tags.append("equipment")
    elif role == "portfolio":
        tags.append("portfolio")
    elif role == "outer-fest":
        tags.append("outer-fest")
    elif role == "club-document":
        tags.append("content")
    elif role == "extracted-image":
        tags.append("extracted")
    if info.get("tenure"):
        tags.append(f"tenure-{info['tenure']}")
    if info.get("year"):
        tags.append(f"year-{info['year']}")
    return tags


# ----------------------------------------------------------------------------
# Main entry — used by both the CLI and by process_assets.py
# ----------------------------------------------------------------------------
def generate_assets_map(
    source_dir: Path,
    output_path: Path | None = None,
    site_base: str = DEFAULT_SITE_BASE,
) -> Path:
    """Walk `source_dir` and write the canonical assets_map.jsonl to
    `output_path` (defaulting to `<source_dir>/assets_map.jsonl`).

    Returns the absolute path of the written file.
    """
    source_dir = Path(source_dir).resolve()
    if output_path is None:
        output_path = source_dir / "assets_map.jsonl"
    output_path = Path(output_path).resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    now_iso = _dt.datetime.now().isoformat(timespec="seconds")
    entries: list[dict] = []
    eid = 0

    for root, _, fnames in os.walk(source_dir):
        # thumbs/ holds grid variants of images already indexed — never emit
        # them as standalone entries (they are referenced via thumb_url).
        if (
            Path(root) == source_dir / "thumbs"
            or "thumbs" in Path(root).relative_to(source_dir).parts
        ):
            continue
        for fname in sorted(fnames):
            if fname == "assets_map.jsonl":
                continue
            fpath = Path(root) / fname
            rel = fpath.relative_to(source_dir)
            parts = list(rel.parts)
            if len(parts) < 2:
                continue
            eid += 1
            club = parts[0]
            ext = fpath.suffix.lower()
            ext_clean = ext.lstrip(".")
            ftype = file_type_of(ext)
            mime = EXT_MIME.get(ext_clean) or (
                mimetypes.guess_type(fname)[0] or "application/octet-stream"
            )
            size = fpath.stat().st_size
            width, height = (None, None)
            if ftype == "image":
                width, height = image_dimensions(fpath)

            info = classify_and_describe(parts, fname, ftype, fpath)
            tags = build_tags(club, info)

            orientation = None
            aspect = None
            if width and height:
                if width == height:
                    orientation = "square"
                elif width > height:
                    orientation = "landscape"
                else:
                    orientation = "portrait"
                aspect = round(width / height, 3)

            entry = {
                "id": eid,
                "path": str(rel),
                "absolute_path": f"{ABSOLUTE_PATH_PREFIX}/{rel}",
                "public_url": f"{ABSOLUTE_PATH_PREFIX}/{rel}",
                "thumb_url": (
                    f"{ABSOLUTE_PATH_PREFIX}/thumbs/{rel}"
                    if ftype == "image" and (source_dir / "thumbs" / rel).exists()
                    else None
                ),
                "club": club,
                "club_name": CLUB_NAMES.get(club, club),
                "category": parts[-2] if len(parts) > 2 else "(root)",
                "category_label": info["category_label"],
                "filename": fname,
                "title": info["title"],
                "extension": ext_clean,
                "file_type": ftype,
                "mime_type": mime,
                "size_bytes": size,
                "width": width,
                "height": height,
                "orientation": orientation,
                "aspect_ratio": aspect,
                "role": info["role"],
                "tenure": info["tenure"],
                "year": info["year"],
                "person": info["person"],
                "ob_role": info["ob_role"],
                "is_ob_portrait": info["is_ob_portrait"],
                "is_logo": info["is_logo"],
                "is_event": info["is_event"],
                "is_iicm": info["is_iicm"],
                "is_extracted_from_doc": info["is_extracted_from_doc"],
                "is_markdown_content": info["is_markdown_content"],
                "description": info["description"],
                "tags": tags,
                "updated_at": now_iso,
            }
            entries.append(entry)

    entries.sort(key=lambda r: (r["club"], r["category"], r["filename"]))
    for i, e in enumerate(entries, start=1):
        e["id"] = i

    by_type = Counter(e["file_type"] for e in entries)
    by_role = Counter(e["role"] for e in entries)
    by_club = Counter(e["club"] for e in entries)
    total_size = sum(e["size_bytes"] for e in entries)
    print(
        f"[assets_map] {len(entries)} entries · {total_size / 1024 / 1024:.1f} MB",
        file=sys.stderr,
    )
    print(f"[assets_map] by file_type: {dict(by_type)}", file=sys.stderr)
    print(f"[assets_map] by role: {dict(by_role)}", file=sys.stderr)
    print(f"[assets_map] by club:", file=sys.stderr)
    for club, n in sorted(by_club.items(), key=lambda x: -x[1]):
        print(f"             {n:4d}  {club}", file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"[assets_map] wrote {output_path}", file=sys.stderr)
    return output_path


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the canonical assets_map.jsonl for the SAC website. "
            "Defaults assume this script lives at <submodule>/tools/ and the "
            "processed tree is at <submodule>/processed/."
        ),
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help=f"Source directory to walk (default: {DEFAULT_PROCESSED_DIR})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output JSONL path (default: <source>/assets_map.jsonl)",
    )
    parser.add_argument(
        "--site-base",
        default=DEFAULT_SITE_BASE,
        help=(
            "URL prefix the deployed site is served under, used for the "
            f"public_url field (default: {DEFAULT_SITE_BASE})"
        ),
    )
    args = parser.parse_args(argv)

    source = Path(args.source).resolve() if args.source else DEFAULT_PROCESSED_DIR
    output = Path(args.output).resolve() if args.output else None
    try:
        generate_assets_map(source, output, site_base=args.site_base)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
