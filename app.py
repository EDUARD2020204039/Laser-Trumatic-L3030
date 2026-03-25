from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time as time_module
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

try:
    import pyodbc
except ImportError:  # pragma: no cover - optional during early local setup
    pyodbc = None

try:  # pragma: no cover - optional OCR stack
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:  # pragma: no cover - optional OCR stack
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:  # pragma: no cover - optional OCR stack
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
load_dotenv(BASE_DIR / ".env")
SQLITE_PATH = Path(os.getenv("LASER_SQLITE_PATH", DATA_DIR / "laser_monitor.db"))

DEFAULT_ODBC_DRIVER = (
    "ODBC Driver 17 for SQL Server" if os.name == "nt" else "ODBC Driver 18 for SQL Server"
)
APP_TITLE = "HABA Production Monitor"
DASHBOARD_TITLE = "Laser TruMatic L3030"
DEFAULT_MACHINE_KEY = "laser1"
MANUAL_SOURCE_PREFIX = "manual"
OCR_AVAILABLE = cv2 is not None and np is not None and pytesseract is not None
BACKGROUND_SYNC_ENABLED = os.getenv("BACKGROUND_SYNC_ENABLED", "1") != "0"
BACKGROUND_SYNC_INTERVAL_SECONDS = max(int(os.getenv("BACKGROUND_SYNC_INTERVAL_SECONDS", "10")), 3)
_background_sync_started = False

if pytesseract is not None:  # pragma: no cover - runtime environment specific
    windows_tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if windows_tesseract.exists():
        pytesseract.pytesseract.tesseract_cmd = str(windows_tesseract)

MACHINE_DEFINITIONS = {
    "laser1": {
        "label": "Laser1",
        "description": "Post principal de taiere si monitorizare laser.",
        "accent": "ember",
    },
    "laser2": {
        "label": "Laser2",
        "description": "Al doilea post laser, pregatit pentru flux separat.",
        "accent": "steel",
    },
    "abkant": {
        "label": "Abkant",
        "description": "Zona de indoire si lucru pe utilajul abkant.",
        "accent": "teal",
    },
}

REAL_DATA_FEEDS = {
    "laser1": {
        "script_name": "laserFeed.py",
        "display_name": "laserFeed OCR bridge",
        "endpoint": "https://laser.helpan.ro/",
        "transport": "Redis + MQTT",
        "left_panel": [
            {"label": "OCR program", "value": "da"},
            {"label": "OCR repetitie", "value": "da"},
            {"label": "Camera feed", "value": "UP / DOWN"},
            {"label": "Semnal live", "value": "partial"},
        ],
        "screen_rows": [
            {"label": "Selected program", "value": "OCR nume program din ecran"},
            {"label": "Active program", "value": "LaserState / nume program OCR"},
            {"label": "Program status", "value": "OK / ERR din script"},
            {"label": "Machine ON", "value": "LaserStatus = UP"},
            {"label": "Cutting", "value": "nu este extras direct inca"},
            {"label": "Table change", "value": "nu este extras direct inca"},
            {"label": "Idle", "value": "derivat doar dupa ce avem Cutting"},
        ],
        "derivation_rules": [
            {"label": "Machine ON", "value": "DA cand Redis key LaserStatus este UP"},
            {"label": "Cutting", "value": "Nu exista in script un OCR direct pe zona Running / Laser ON / Wati"},
            {"label": "Table change", "value": "Nu exista in script o zona OCR sau semnal IO dedicat schimbului de masa"},
            {"label": "Idle", "value": "Poate fi calculat doar dupa ce definim clar Cutting si Table change"},
        ],
        "details": [
            "Camera OCR: laserbvision-1:8081",
            "Redis keys observate: LaserStatus, LaserState",
            "MQTT topic observat: Laser/3020/Status",
            "Scriptul urmareste downtime si numele programului activ",
        ],
    },
    "laser2": {
        "script_name": "laserFeed.py",
        "display_name": "laserFeed OCR bridge",
        "endpoint": "https://laser.helpan.ro/",
        "transport": "Redis + MQTT",
        "left_panel": [
            {"label": "OCR program", "value": "da"},
            {"label": "OCR repetitie", "value": "da"},
            {"label": "Camera feed", "value": "UP / DOWN"},
            {"label": "Semnal live", "value": "partial"},
        ],
        "screen_rows": [
            {"label": "Selected program", "value": "OCR nume program din ecran"},
            {"label": "Active program", "value": "LaserState / nume program OCR"},
            {"label": "Program status", "value": "OK / ERR din script"},
            {"label": "Machine ON", "value": "LaserStatus = UP"},
            {"label": "Cutting", "value": "nu este extras direct inca"},
            {"label": "Table change", "value": "nu este extras direct inca"},
            {"label": "Idle", "value": "derivat doar dupa ce avem Cutting"},
        ],
        "derivation_rules": [
            {"label": "Machine ON", "value": "DA cand Redis key LaserStatus este UP"},
            {"label": "Cutting", "value": "Foloseste momentan acelasi feed ca Laser1, deci nu avem OCR separat"},
            {"label": "Table change", "value": "Necesita feed separat sau semnal suplimentar pentru Laser2"},
            {"label": "Idle", "value": "Poate fi calculat doar dupa ce clarificam Cutting si Table change"},
        ],
        "details": [
            "Foloseste acelasi feed incarcat pentru Laser1",
            "Camera OCR: laserbvision-1:8081",
            "Redis keys observate: LaserStatus, LaserState",
            "MQTT topic observat: Laser/3020/Status",
        ],
    },
    "abkant": {
        "script_name": "AbkantFeed.py",
        "display_name": "Abkant OCR bridge",
        "endpoint": "https://abkant.helpan.ro/",
        "transport": "MQTT + PostgreSQL",
        "left_panel": [
            {"label": "OCR program", "value": "da"},
            {"label": "OCR nr buc", "value": "da"},
            {"label": "Camera feed", "value": "UP / DOWN"},
            {"label": "Semnal live", "value": "partial"},
        ],
        "screen_rows": [
            {"label": "Active program", "value": "Abkant/ProgramActiv"},
            {"label": "Program valid", "value": "Abkant/StareProgramIdentificat"},
            {"label": "Nr bucati", "value": "OCR numar_bucati / nr_bucati"},
            {"label": "Machine ON", "value": "camera accesibila + rpiabkantworking"},
            {"label": "Cutting", "value": "nu se aplica; se poate interpreta ca lucru activ"},
            {"label": "Table change", "value": "nu se aplica direct la abkant"},
            {"label": "Idle", "value": "program neschimbat / fara progres bucati"},
        ],
        "derivation_rules": [
            {"label": "Machine ON", "value": "DA cand captura merge si parametrul rpiabkantworking ramane TRUE"},
            {"label": "Cutting", "value": "Pentru abkant nu avem taiere; putem urmari lucru activ prin program + numar bucati"},
            {"label": "Table change", "value": "Nu exista echivalent direct in scriptul de abkant"},
            {"label": "Idle", "value": "Poate fi derivat cand masina este ON dar programul / numarul de bucati nu avanseaza"},
        ],
        "details": [
            "Camera OCR: 100.126.29.52:8081",
            "MQTT topics observate: Abkant/StareProgramIdentificat, Abkant/ProgramActiv",
            "Tabela observata: raportare_abkant",
            "Scriptul urmareste programul activ si numarul de bucati",
        ],
    },
}

SIGNAL_DEFINITIONS = {
    "machine_on": {
        "label": "Machine ON",
        "description": "Masina este alimentata si pregatita.",
        "accent": "steel",
    },
    "cutting_active": {
        "label": "Cutting active",
        "description": "Utilajul lucreaza activ in productie.",
        "accent": "ember",
    },
    "table_change": {
        "label": "Table change",
        "description": "Se schimba masa sau se pregateste urmatorul ciclu.",
        "accent": "teal",
    },
}

STATE_DEFINITIONS = {
    "off": {
        "label": "Oprit",
        "description": "Masina nu este alimentata.",
        "tone": "slate",
    },
    "ready": {
        "label": "Pregatit",
        "description": "Masina este pornita, dar nu lucreaza activ.",
        "tone": "steel",
    },
    "cutting": {
        "label": "In productie",
        "description": "Productie activa in curs.",
        "tone": "ember",
    },
    "table_change": {
        "label": "Schimb de masa",
        "description": "Operatorul pregateste urmatorul ciclu.",
        "tone": "teal",
    },
}

LASER_OCR_ZONES = {
    "right_panel": (620, 170, 620, 550),
    "left_panel": (0, 170, 620, 260),
}

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


def now_local() -> datetime:
    return datetime.now().replace(microsecond=0)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError("Value must be boolean-compatible.")


def parse_optional_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return int(stripped)
    return int(value)


def ensure_signal_name(signal_name: str) -> str:
    if signal_name not in SIGNAL_DEFINITIONS:
        raise ValueError(f"Unsupported signal: {signal_name}")
    return signal_name


def ensure_machine_key(machine_key: str | None) -> str:
    candidate = (machine_key or DEFAULT_MACHINE_KEY).strip().lower()
    if candidate not in MACHINE_DEFINITIONS:
        raise ValueError(f"Unsupported machine: {candidate}")
    return candidate


def get_machine_env_value(machine_key: str, suffix: str, legacy_names: tuple[str, ...] = ()) -> str:
    env_names = [f"{machine_key.upper()}_{suffix}", *legacy_names]
    for env_name in env_names:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return ""


def resolve_real_data_endpoint(machine_key: str) -> str:
    legacy_names = ("LASER_REAL_DATA_ENDPOINT",) if machine_key in {"laser1", "laser2"} else ()
    return get_machine_env_value(machine_key, "REAL_DATA_ENDPOINT", legacy_names) or REAL_DATA_FEEDS[machine_key]["endpoint"]


def resolve_real_data_name(machine_key: str) -> str:
    legacy_names = ("LASER_REAL_DATA_NAME",) if machine_key in {"laser1", "laser2"} else ()
    return get_machine_env_value(machine_key, "REAL_DATA_NAME", legacy_names) or REAL_DATA_FEEDS[machine_key]["display_name"]


def clean_ocr_text(value: str) -> str:
    sanitized = re.sub(r"\s+", " ", (value or "").replace("\x0c", " ")).strip()
    return sanitized.replace(" / ", "/").replace(" _ ", "_")


def fetch_mjpeg_frame(url: str, timeout: float = 2.0):
    if not OCR_AVAILABLE:
        return None, "OCR stack lipseste in container sau pe host."
    if not url:
        return None, "Endpointul live nu este configurat."

    buffer_bytes = bytes()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as stream:
            while True:
                buffer_bytes += stream.read(1024)
                start = buffer_bytes.find(b"\xff\xd8")
                end = buffer_bytes.find(b"\xff\xd9")
                if start != -1 and end != -1:
                    jpg = buffer_bytes[start : end + 2]
                    image = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if image is None:
                        return None, "Fluxul MJPEG a raspuns, dar cadrul nu a putut fi decodat."
                    return image, None
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}"

    return None, "Fluxul MJPEG nu a livrat niciun cadru valid."


def read_ocr_zone(image, zone: tuple[int, int, int, int], whitelist: str, psm: int = 7) -> str:
    if not OCR_AVAILABLE or image is None:
        return ""

    x, y, width, height = zone
    crop = image[y : y + height, x : x + width]
    if crop.size == 0:
        return ""

    enlarged = cv2.resize(crop, None, fx=2.4, fy=2.4, interpolation=cv2.INTER_LINEAR)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    _, threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    config = f"--psm {psm} --oem 3 -c tessedit_char_whitelist={whitelist}"
    try:
        return clean_ocr_text(pytesseract.image_to_string(threshold, config=config))
    except Exception:
        return ""


def read_ocr_block(image, zone: tuple[int, int, int, int], psm: int = 6) -> str:
    if not OCR_AVAILABLE or image is None:
        return ""

    x, y, width, height = zone
    crop = image[y : y + height, x : x + width]
    if crop.size == 0:
        return ""

    enlarged = cv2.resize(crop, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    config = f"--psm {psm} --oem 3 -c preserve_interword_spaces=1"
    try:
        return clean_ocr_text(pytesseract.image_to_string(threshold, config=config))
    except Exception:
        return ""


def normalize_program_token(token: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", token or "").upper()
    if re.match(r"^P\d{5,}_", cleaned):
        cleaned = f"S{cleaned}"
    underscore_count = cleaned.count("_")
    if underscore_count == 2:
        parts = cleaned.split("_")
        if len(parts) == 3 and len(parts[2]) == 3:
            cleaned = f"{parts[0]}_{parts[1]}_{parts[2][:2]}_{parts[2][2:]}"
    return cleaned


def extract_section_token(text: str, start_label: str, end_label: str | None = None) -> str:
    if not text:
        return ""

    pattern = re.escape(start_label)
    if end_label:
        pattern += rf"(.*?){re.escape(end_label)}"
    else:
        pattern += r"(.*)"

    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return ""

    section = match.group(1)
    token_match = re.search(r"[A-Z]{0,3}\d{5,}_[A-Z0-9]{2}_[A-Z0-9]{2}_?[A-Z0-9]", section, re.IGNORECASE)
    if not token_match:
        return ""

    return normalize_program_token(token_match.group(0))


def extract_program_status(text: str) -> str:
    if not text:
        return ""

    match = re.search(r"Program\s*status\s*([A-Za-z]+)", text, re.IGNORECASE)
    if not match:
        return ""

    return clean_ocr_text(match.group(1)).title()


def extract_material(text: str) -> str:
    if not text:
        return ""

    patterns = (
        r"\b\d\.\d{4}-\d+\b",
        r"\b[A-Za-z]{1,3}\d{2,}-\d{2}\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return clean_ocr_text(match.group(0))
    return ""


def analyze_laser_live_snapshot(machine_key: str) -> dict | None:
    endpoint = resolve_real_data_endpoint(machine_key)
    image, error_message = fetch_mjpeg_frame(endpoint)
    if image is None:
        return {
            "available": False,
            "connected": False,
            "endpoint": endpoint,
            "message": f"Nu pot citi captura live de la {endpoint}. Motiv: {error_message}",
        }

    right_panel_text = read_ocr_block(image, LASER_OCR_ZONES["right_panel"], psm=6)
    left_panel_text = read_ocr_block(image, LASER_OCR_ZONES["left_panel"], psm=11)

    selected_program = extract_section_token(right_panel_text, "Selected program", "Active program")
    active_program = extract_section_token(right_panel_text, "Active program", "NC blocks")
    material = extract_material(left_panel_text)
    program_status = extract_program_status(right_panel_text)

    normalized_status = program_status.upper()
    normalized_active_program = active_program.upper()
    machine_on = bool(selected_program or active_program or program_status or material)
    table_change = "SHEET_LOAD" in normalized_active_program or "LOAD_SHEET" in normalized_active_program
    cutting_active = machine_on and normalized_status == "RUNNING" and not table_change
    idle = machine_on and not cutting_active and not table_change

    return {
        "available": True,
        "connected": True,
        "source": "live-ocr",
        "endpoint": endpoint,
        "selected_program": selected_program or "Necitit",
        "active_program": active_program or "Necitit",
        "material": material or "Necitit",
        "program_status": program_status or "Necitit",
        "derived_signals": {
            "machine_on": machine_on,
            "cutting_active": cutting_active,
            "table_change": table_change,
            "idle": idle,
        },
        "message": "Stare derivata din OCR pe panourile din stanga si dreapta ale ecranului laser.",
    }


def analyze_abkant_live_snapshot(machine_key: str) -> dict | None:
    endpoint = resolve_real_data_endpoint(machine_key)
    reachable = bool(endpoint)
    return {
        "available": reachable,
        "connected": reachable,
        "source": "feed-script",
        "endpoint": endpoint,
        "selected_program": "n/a",
        "active_program": "Abkant/ProgramActiv",
        "material": "n/a",
        "program_status": "Program identificat din script",
        "derived_signals": {
            "machine_on": reachable,
            "cutting_active": False,
            "table_change": False,
            "idle": False,
        },
        "message": (
            "Abkant foloseste momentan feedul din script, nu OCR live direct in dashboard."
            if reachable
            else "Feedul abkant nu este accesibil din dashboard."
        ),
    }


def get_live_machine_snapshot(machine_key: str) -> dict | None:
    if machine_key in {"laser1", "laser2"}:
        return analyze_laser_live_snapshot(machine_key)
    if machine_key == "abkant":
        return analyze_abkant_live_snapshot(machine_key)
    return None


def get_sqlite_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def get_pontaj_connection_settings() -> dict[str, str | int]:
    return {
        "server": os.getenv("PONTAJ_SQL_SERVER", "192.168.2.6"),
        "database": os.getenv("PONTAJ_SQL_DATABASE", "Metal"),
        "username": os.getenv("PONTAJ_SQL_USERNAME", "bogdan"),
        "password": os.getenv("PONTAJ_SQL_PASSWORD", "HELPAN123$"),
        "driver": os.getenv("PONTAJ_SQL_DRIVER", DEFAULT_ODBC_DRIVER),
        "timeout": int(os.getenv("PONTAJ_SQL_TIMEOUT", "5")),
    }


def get_default_machine_profiles() -> list[dict]:
    legacy_default = parse_optional_int(os.getenv("PONTAJ_WORKCENTER_ID", "1"))
    laser_default = parse_optional_int(os.getenv("PONTAJ_LASER1_WORKCENTER_ID", legacy_default))
    return [
        {
            "machine_key": "laser1",
            "label": MACHINE_DEFINITIONS["laser1"]["label"],
            "workcenter_id": laser_default,
            "sort_order": 1,
        },
        {
            "machine_key": "laser2",
            "label": MACHINE_DEFINITIONS["laser2"]["label"],
            "workcenter_id": parse_optional_int(os.getenv("PONTAJ_LASER2_WORKCENTER_ID", laser_default)),
            "sort_order": 2,
        },
        {
            "machine_key": "abkant",
            "label": MACHINE_DEFINITIONS["abkant"]["label"],
            "workcenter_id": parse_optional_int(os.getenv("PONTAJ_ABKANT_WORKCENTER_ID", "2")),
            "sort_order": 3,
        },
    ]


def serialize_machine_profile(row: sqlite3.Row, selected_machine_key: str | None = None) -> dict:
    definition = MACHINE_DEFINITIONS[row["machine_key"]]
    return {
        "key": row["machine_key"],
        "label": row["label"] or definition["label"],
        "description": definition["description"],
        "accent": definition["accent"],
        "workcenter_id": row["workcenter_id"],
        "updated_at": row["updated_at"],
        "is_selected": row["machine_key"] == selected_machine_key,
    }


def init_db() -> None:
    with get_sqlite_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_key TEXT NOT NULL DEFAULT 'laser1',
                signal_name TEXT NOT NULL,
                value INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                note TEXT,
                operator_id TEXT,
                operator_name TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS machine_profiles (
                machine_key TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                workcenter_id INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS saved_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_key TEXT NOT NULL,
                workcenter_id INTEGER,
                operator_id TEXT,
                operator_name TEXT,
                selected_program TEXT,
                active_program TEXT,
                material TEXT,
                program_status TEXT,
                cutting_started_at TEXT,
                table_change_started_at TEXT NOT NULL,
                table_change_ended_at TEXT,
                table_change_duration_seconds INTEGER,
                cycle_duration_seconds INTEGER,
                source TEXT NOT NULL DEFAULT 'live-ocr',
                snapshot_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS machine_runtime (
                machine_key TEXT PRIMARY KEY,
                last_snapshot_json TEXT,
                pending_cycle_json TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )

        signal_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(signal_events)").fetchall()
        }
        if "machine_key" not in signal_columns:
            connection.execute(
                "ALTER TABLE signal_events ADD COLUMN machine_key TEXT NOT NULL DEFAULT 'laser1'"
            )

        saved_cycle_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(saved_cycles)").fetchall()
        }
        if "table_change_ended_at" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN table_change_ended_at TEXT")
        if "table_change_duration_seconds" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN table_change_duration_seconds INTEGER")

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signal_events_machine_signal_time
            ON signal_events (machine_key, signal_name, created_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signal_events_machine_time
            ON signal_events (machine_key, created_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saved_cycles_machine_time
            ON saved_cycles (machine_key, table_change_started_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saved_cycles_operator_time
            ON saved_cycles (operator_id, table_change_started_at DESC, id DESC)
            """
        )

        updated_at = now_local().isoformat(timespec="seconds")
        for profile in get_default_machine_profiles():
            connection.execute(
                """
                INSERT OR IGNORE INTO machine_profiles (
                    machine_key, label, workcenter_id, sort_order, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    profile["machine_key"],
                    profile["label"],
                    profile["workcenter_id"],
                    profile["sort_order"],
                    updated_at,
                ),
            )
            connection.execute(
                """
                UPDATE machine_profiles
                SET label = ?, sort_order = ?, workcenter_id = COALESCE(workcenter_id, ?)
                WHERE machine_key = ?
                """,
                (
                    profile["label"],
                    profile["sort_order"],
                    profile["workcenter_id"],
                    profile["machine_key"],
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO machine_runtime (
                    machine_key, last_snapshot_json, pending_cycle_json, updated_at
                )
                VALUES (?, NULL, NULL, ?)
                """,
                (
                    profile["machine_key"],
                    updated_at,
                ),
            )

        connection.commit()


def get_machine_profiles() -> list[dict]:
    with get_sqlite_connection() as connection:
        rows = connection.execute(
            """
            SELECT machine_key, label, workcenter_id, sort_order, updated_at
            FROM machine_profiles
            ORDER BY sort_order ASC, machine_key ASC
            """
        ).fetchall()
    return [serialize_machine_profile(row) for row in rows]


def get_machine_profile(machine_key: str) -> dict:
    machine_key = ensure_machine_key(machine_key)
    with get_sqlite_connection() as connection:
        row = connection.execute(
            """
            SELECT machine_key, label, workcenter_id, sort_order, updated_at
            FROM machine_profiles
            WHERE machine_key = ?
            """,
            (machine_key,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Machine profile not found: {machine_key}")
    return serialize_machine_profile(row)


def update_machine_workcenter(machine_key: str, workcenter_id: int | None) -> dict:
    machine_key = ensure_machine_key(machine_key)
    if workcenter_id is not None and workcenter_id < 1:
        raise ValueError("WorkCenter ID must be a positive integer.")

    with get_sqlite_connection() as connection:
        connection.execute(
            """
            UPDATE machine_profiles
            SET workcenter_id = ?, updated_at = ?
            WHERE machine_key = ?
            """,
            (workcenter_id, now_local().isoformat(timespec="seconds"), machine_key),
        )
        connection.commit()

    return get_machine_profile(machine_key)


def get_real_data_settings(machine_profile: dict) -> dict[str, str]:
    feed = REAL_DATA_FEEDS[machine_profile["key"]]
    script_name = feed["script_name"]
    script_exists = bool(script_name and (BASE_DIR / script_name).exists())

    endpoint = resolve_real_data_endpoint(machine_profile["key"])
    name = resolve_real_data_name(machine_profile["key"])
    status = "configured" if script_exists else "pending"
    return {
        "name": name,
        "endpoint": endpoint,
        "status": status,
        "transport": feed["transport"],
        "script_name": script_name,
        "details": feed["details"],
        "message": (
            f"Sursa reala pentru {machine_profile['label']} a fost identificata din fisierul {script_name}."
            if script_exists
            else "Sursa reala nu este inca pregatita complet. Butoanele manuale raman pentru test."
        ),
    }


def build_script_catalog() -> list[dict]:
    catalog = []
    for machine_key, machine_meta in MACHINE_DEFINITIONS.items():
        feed = REAL_DATA_FEEDS[machine_key]
        script_name = feed["script_name"]
        catalog.append(
            {
                "key": machine_key,
                "label": machine_meta["label"],
                "description": machine_meta["description"],
                "script_name": script_name or "Nespecificat",
                "script_exists": bool(script_name and (BASE_DIR / script_name).exists()),
                "endpoint": resolve_real_data_endpoint(machine_key) or "Fara endpoint clar",
                "transport": feed["transport"],
                "left_panel": feed["left_panel"],
                "screen_rows": feed["screen_rows"],
                "derivation_rules": feed["derivation_rules"],
                "details": feed["details"],
            }
        )
    return catalog


def get_pontaj_connection():
    if pyodbc is None:
        raise RuntimeError("pyodbc is not installed in this environment.")

    settings = get_pontaj_connection_settings()
    connection_string = (
        f"DRIVER={{{settings['driver']}}};"
        f"SERVER={settings['server']};"
        f"DATABASE={settings['database']};"
        f"UID={settings['username']};"
        f"PWD={settings['password']};"
        "TrustServerCertificate=yes;"
        "Encrypt=no;"
    )
    return pyodbc.connect(connection_string, timeout=settings["timeout"])


def fetch_current_operator(workcenter_id: int | None) -> dict:
    payload = {
        "status": "pending" if workcenter_id is None else "offline",
        "message": (
            "Seteaza un WorkCenter ID pentru a vedea operatorul activ."
            if workcenter_id is None
            else "Pontaj is not configured."
        ),
        "workcenter_id": workcenter_id,
        "operators": [],
        "primary_operator": None,
    }

    if workcenter_id is None:
        return payload

    try:
        with get_pontaj_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT
                    A.ID,
                    A.Nume,
                    A.Prenume,
                    P.Data,
                    P.OraCheckIn
                FROM PontajWorkCenter P
                INNER JOIN Angajati A ON P.ID = A.ID
                WHERE P.WorkCenterID = ? AND P.OraCheckOut IS NULL
                ORDER BY P.Data DESC, P.OraCheckIn DESC
                """,
                (workcenter_id,),
            )
            rows = cursor.fetchall()
            cursor.close()
    except Exception as exc:  # pragma: no cover - depends on networked SQL Server
        payload["status"] = "error"
        payload["message"] = str(exc)
        return payload

    operators = []
    for row in rows:
        first_name = (row[1] or "").strip()
        last_name = (row[2] or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        check_in = None
        if row[3] is not None and row[4] is not None:
            check_in = f"{row[3]} {row[4]}"
        operators.append(
            {
                "employee_id": str(row[0]),
                "full_name": full_name or f"Angajat {row[0]}",
                "check_in": check_in,
            }
        )

    payload["status"] = "connected"
    payload["message"] = "Pontaj online."
    payload["operators"] = operators
    payload["primary_operator"] = operators[0] if operators else None
    if not operators:
        payload["message"] = "Nu exista operator activ pe acest workcenter."
    return payload


def fetch_recent_events(machine_key: str, limit: int = 18) -> list[dict]:
    with get_sqlite_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, machine_key, signal_name, value, source, note, operator_id, operator_name, created_at
            FROM signal_events
            WHERE machine_key = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (machine_key, limit),
        ).fetchall()

    return [
        {
            "id": row["id"],
            "machine_key": row["machine_key"],
            "signal_name": row["signal_name"],
            "signal_label": SIGNAL_DEFINITIONS[row["signal_name"]]["label"],
            "value": bool(row["value"]),
            "source": row["source"],
            "note": row["note"],
            "operator_id": row["operator_id"],
            "operator_name": row["operator_name"],
            "created_at": row["created_at"],
            "is_manual": str(row["source"]).startswith(MANUAL_SOURCE_PREFIX),
        }
        for row in rows
    ]


def format_saved_cycle_row(row: sqlite3.Row) -> dict:
    duration_seconds = row["cycle_duration_seconds"]
    return {
        "id": row["id"],
        "machine_key": row["machine_key"],
        "machine_label": MACHINE_DEFINITIONS.get(row["machine_key"], {}).get("label", row["machine_key"]),
        "workcenter_id": row["workcenter_id"],
        "operator_id": row["operator_id"],
        "operator_name": row["operator_name"] or "Operator necunoscut",
        "selected_program": row["selected_program"] or "Necitit",
        "active_program": row["active_program"] or "Necitit",
        "material": row["material"] or "Necitit",
        "program_status": row["program_status"] or "Necitit",
        "cutting_started_at": row["cutting_started_at"],
        "table_change_started_at": row["table_change_started_at"],
        "table_change_ended_at": row["table_change_ended_at"],
        "table_change_duration_seconds": row["table_change_duration_seconds"] or 0,
        "table_change_duration_label": format_seconds(row["table_change_duration_seconds"] or 0),
        "cycle_duration_seconds": duration_seconds,
        "cycle_duration_label": format_seconds(duration_seconds or 0),
        "source": row["source"],
        "created_at": row["created_at"],
    }


def fetch_saved_cycles(machine_key: str | None = None, day: date | None = None, limit: int = 120) -> list[dict]:
    start_day = datetime.combine(day or date.today(), time.min).isoformat(timespec="seconds")

    with get_sqlite_connection() as connection:
        if machine_key:
            rows = connection.execute(
                """
                SELECT *
                FROM saved_cycles
                WHERE machine_key = ? AND table_change_started_at >= ?
                ORDER BY table_change_started_at DESC, id DESC
                LIMIT ?
                """,
                (machine_key, start_day, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM saved_cycles
                WHERE table_change_started_at >= ?
                ORDER BY table_change_started_at DESC, id DESC
                LIMIT ?
                """,
                (start_day, limit),
            ).fetchall()

    return [format_saved_cycle_row(row) for row in rows]


def fetch_saved_cycles_between(
    start_dt: datetime,
    end_dt: datetime,
    machine_key: str | None = None,
    limit: int = 500,
) -> list[dict]:
    start_iso = start_dt.isoformat(timespec="seconds")
    end_iso = end_dt.isoformat(timespec="seconds")

    with get_sqlite_connection() as connection:
        if machine_key:
            rows = connection.execute(
                """
                SELECT *
                FROM saved_cycles
                WHERE machine_key = ?
                  AND table_change_started_at >= ?
                  AND table_change_started_at <= ?
                ORDER BY table_change_started_at DESC, id DESC
                LIMIT ?
                """,
                (machine_key, start_iso, end_iso, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM saved_cycles
                WHERE table_change_started_at >= ?
                  AND table_change_started_at <= ?
                ORDER BY table_change_started_at DESC, id DESC
                LIMIT ?
                """,
                (start_iso, end_iso, limit),
            ).fetchall()

    return [format_saved_cycle_row(row) for row in rows]


def summarize_saved_cycles(records: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in records:
        operator_key = record["operator_id"] or record["operator_name"]
        summary = grouped.setdefault(
            operator_key,
            {
                "operator_id": record["operator_id"],
                "operator_name": record["operator_name"],
                "records_count": 0,
                "machines": set(),
                "total_cycle_seconds": 0,
            },
        )
        summary["records_count"] += 1
        summary["machines"].add(record["machine_label"])
        summary["total_cycle_seconds"] += int(record["cycle_duration_seconds"] or 0)

    output = []
    for summary in grouped.values():
        output.append(
            {
                "operator_id": summary["operator_id"],
                "operator_name": summary["operator_name"],
                "records_count": summary["records_count"],
                "machines": sorted(summary["machines"]),
                "total_cycle_seconds": summary["total_cycle_seconds"],
                "total_cycle_label": format_seconds(summary["total_cycle_seconds"]),
            }
        )

    output.sort(key=lambda item: (-item["records_count"], -item["total_cycle_seconds"], item["operator_name"]))
    return output


def build_efficiency_report(label: str, records: list[dict]) -> dict:
    total_cutting_seconds = sum(int(record.get("cycle_duration_seconds") or 0) for record in records)
    total_table_change_seconds = sum(int(record.get("table_change_duration_seconds") or 0) for record in records)
    productive_seconds = total_cutting_seconds + total_table_change_seconds
    efficiency_percent = (
        round((total_cutting_seconds / productive_seconds) * 100, 1)
        if productive_seconds > 0
        else 0.0
    )

    return {
        "label": label,
        "records_count": len(records),
        "efficiency_percent": efficiency_percent,
        "cutting_seconds": total_cutting_seconds,
        "cutting_label": format_seconds(total_cutting_seconds),
        "table_change_seconds": total_table_change_seconds,
        "table_change_label": format_seconds(total_table_change_seconds),
        "productive_window_seconds": productive_seconds,
        "productive_window_label": format_seconds(productive_seconds),
    }


def build_saved_cycles_reports(machine_key: str | None = None) -> list[dict]:
    now = now_local()
    today_start = datetime.combine(date.today(), time.min)
    week_start = datetime.combine(date.today(), time.min)
    week_start = week_start.replace(day=week_start.day) - timedelta(days=week_start.weekday())
    month_start = datetime.combine(date.today().replace(day=1), time.min)

    return [
        build_efficiency_report("Zilnic", fetch_saved_cycles_between(today_start, now, machine_key=machine_key)),
        build_efficiency_report("Saptamanal", fetch_saved_cycles_between(week_start, now, machine_key=machine_key)),
        build_efficiency_report("Lunar", fetch_saved_cycles_between(month_start, now, machine_key=machine_key)),
    ]


def build_saved_cycles_payload(machine_key: str | None = None) -> dict:
    records = fetch_saved_cycles(machine_key=machine_key)
    return {
        "view": "saved",
        "selected_machine_key": machine_key,
        "records": records,
        "summary": summarize_saved_cycles(records),
        "reports": build_saved_cycles_reports(machine_key),
        "records_count": len(records),
        "updated_at": now_local().isoformat(timespec="seconds"),
    }


def get_machine_runtime(machine_key: str) -> dict:
    with get_sqlite_connection() as connection:
        row = connection.execute(
            """
            SELECT machine_key, last_snapshot_json, pending_cycle_json, updated_at
            FROM machine_runtime
            WHERE machine_key = ?
            """,
            (machine_key,),
        ).fetchone()

    if row is None:
        return {
            "machine_key": machine_key,
            "last_snapshot": None,
            "pending_cycle": None,
            "updated_at": None,
        }

    return {
        "machine_key": row["machine_key"],
        "last_snapshot": json.loads(row["last_snapshot_json"]) if row["last_snapshot_json"] else None,
        "pending_cycle": json.loads(row["pending_cycle_json"]) if row["pending_cycle_json"] else None,
        "updated_at": row["updated_at"],
    }


def save_machine_runtime(machine_key: str, last_snapshot: dict | None, pending_cycle: dict | None) -> None:
    updated_at = now_local().isoformat(timespec="seconds")
    with get_sqlite_connection() as connection:
        connection.execute(
            """
            INSERT INTO machine_runtime (machine_key, last_snapshot_json, pending_cycle_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(machine_key) DO UPDATE SET
                last_snapshot_json = excluded.last_snapshot_json,
                pending_cycle_json = excluded.pending_cycle_json,
                updated_at = excluded.updated_at
            """,
            (
                machine_key,
                json.dumps(last_snapshot, ensure_ascii=False) if last_snapshot is not None else None,
                json.dumps(pending_cycle, ensure_ascii=False) if pending_cycle is not None else None,
                updated_at,
            ),
        )
        connection.commit()


def cache_live_snapshot(machine_key: str, snapshot: dict) -> None:
    runtime = get_machine_runtime(machine_key)
    save_machine_runtime(machine_key, snapshot, runtime.get("pending_cycle"))


def open_pending_cycle(
    machine_key: str,
    machine_profile: dict,
    snapshot: dict,
    operator_snapshot: dict,
    current_signals: dict[str, dict],
) -> None:
    operator = operator_snapshot.get("primary_operator") or {}
    pending_cycle = {
        "machine_key": machine_key,
        "workcenter_id": machine_profile.get("workcenter_id"),
        "operator_id": operator.get("employee_id"),
        "operator_name": operator.get("full_name"),
        "selected_program": snapshot.get("selected_program"),
        "active_program": snapshot.get("active_program"),
        "material": snapshot.get("material"),
        "program_status": snapshot.get("program_status"),
        "cutting_started_at": current_signals["cutting_active"]["changed_at"] if current_signals["cutting_active"]["active"] else None,
        "table_change_started_at": now_local().isoformat(timespec="seconds"),
        "source": snapshot.get("source", "live-ocr"),
        "snapshot_json": snapshot,
    }
    runtime = get_machine_runtime(machine_key)
    save_machine_runtime(machine_key, runtime.get("last_snapshot"), pending_cycle)


def finalize_pending_cycle(machine_key: str, current_snapshot: dict) -> None:
    runtime = get_machine_runtime(machine_key)
    pending_cycle = runtime.get("pending_cycle")
    if not pending_cycle:
        return

    table_change_started_at = parse_timestamp(pending_cycle["table_change_started_at"])
    table_change_ended_at = now_local()
    table_change_duration_seconds = max(
        int((table_change_ended_at - table_change_started_at).total_seconds()),
        0,
    )

    cutting_started_at_raw = pending_cycle.get("cutting_started_at")
    cycle_duration_seconds = None
    if cutting_started_at_raw:
        cycle_duration_seconds = max(
            int((table_change_started_at - parse_timestamp(cutting_started_at_raw)).total_seconds()),
            0,
        )

    recent_cutoff_iso = datetime.fromtimestamp(table_change_ended_at.timestamp() - 45).isoformat(timespec="seconds")
    with get_sqlite_connection() as connection:
        duplicate = connection.execute(
            """
            SELECT id
            FROM saved_cycles
            WHERE machine_key = ?
              AND selected_program = ?
              AND table_change_started_at >= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                machine_key,
                pending_cycle.get("selected_program"),
                recent_cutoff_iso,
            ),
        ).fetchone()
        if duplicate is None:
            connection.execute(
                """
                INSERT INTO saved_cycles (
                    machine_key,
                    workcenter_id,
                    operator_id,
                    operator_name,
                    selected_program,
                    active_program,
                    material,
                    program_status,
                    cutting_started_at,
                    table_change_started_at,
                    table_change_ended_at,
                    table_change_duration_seconds,
                    cycle_duration_seconds,
                    source,
                    snapshot_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    machine_key,
                    pending_cycle.get("workcenter_id"),
                    pending_cycle.get("operator_id"),
                    pending_cycle.get("operator_name"),
                    pending_cycle.get("selected_program"),
                    pending_cycle.get("active_program"),
                    pending_cycle.get("material"),
                    pending_cycle.get("program_status"),
                    pending_cycle.get("cutting_started_at"),
                    pending_cycle.get("table_change_started_at"),
                    table_change_ended_at.isoformat(timespec="seconds"),
                    table_change_duration_seconds,
                    cycle_duration_seconds,
                    pending_cycle.get("source", "live-ocr"),
                    json.dumps(
                        {
                            "pending_snapshot": pending_cycle.get("snapshot_json"),
                            "resume_snapshot": current_snapshot,
                        },
                        ensure_ascii=False,
                    ),
                    table_change_ended_at.isoformat(timespec="seconds"),
                ),
            )
            connection.commit()

    save_machine_runtime(machine_key, current_snapshot, None)


def fetch_current_signals(machine_key: str) -> dict[str, dict]:
    current_signals: dict[str, dict] = {}
    with get_sqlite_connection() as connection:
        for signal_name, meta in SIGNAL_DEFINITIONS.items():
            row = connection.execute(
                """
                SELECT value, created_at, operator_name
                FROM signal_events
                WHERE machine_key = ? AND signal_name = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (machine_key, signal_name),
            ).fetchone()
            current_signals[signal_name] = {
                "active": bool(row["value"]) if row else False,
                "changed_at": row["created_at"] if row else None,
                "operator_name": row["operator_name"] if row else None,
                "label": meta["label"],
                "description": meta["description"],
                "accent": meta["accent"],
            }
    return current_signals


def derive_machine_state(current_signals: dict[str, dict]) -> dict:
    if not current_signals["machine_on"]["active"]:
        return {"key": "off", **STATE_DEFINITIONS["off"]}
    if current_signals["cutting_active"]["active"]:
        return {"key": "cutting", **STATE_DEFINITIONS["cutting"]}
    if current_signals["table_change"]["active"]:
        return {"key": "table_change", **STATE_DEFINITIONS["table_change"]}
    return {"key": "ready", **STATE_DEFINITIONS["ready"]}


def calculate_active_seconds(
    machine_key: str,
    signal_name: str,
    start_dt: datetime,
    end_dt: datetime,
) -> int:
    with get_sqlite_connection() as connection:
        previous_row = connection.execute(
            """
            SELECT value, created_at
            FROM signal_events
            WHERE machine_key = ? AND signal_name = ? AND created_at < ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (machine_key, signal_name, start_dt.isoformat(timespec="seconds")),
        ).fetchone()

        rows = connection.execute(
            """
            SELECT value, created_at
            FROM signal_events
            WHERE machine_key = ? AND signal_name = ? AND created_at >= ? AND created_at <= ?
            ORDER BY created_at ASC, id ASC
            """,
            (
                machine_key,
                signal_name,
                start_dt.isoformat(timespec="seconds"),
                end_dt.isoformat(timespec="seconds"),
            ),
        ).fetchall()

    active = bool(previous_row["value"]) if previous_row else False
    cursor_time = start_dt
    total_seconds = 0

    for row in rows:
        event_time = parse_timestamp(row["created_at"])
        if active and event_time > cursor_time:
            total_seconds += int((event_time - cursor_time).total_seconds())
        active = bool(row["value"])
        cursor_time = event_time

    if active and end_dt > cursor_time:
        total_seconds += int((end_dt - cursor_time).total_seconds())

    return max(total_seconds, 0)


def format_seconds(total_seconds: int) -> str:
    hours, remainder = divmod(max(total_seconds, 0), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_today_stats(machine_key: str) -> dict:
    now = now_local()
    start_of_day = datetime.combine(date.today(), time.min)
    elapsed_seconds = max(int((now - start_of_day).total_seconds()), 1)
    machine_on_seconds = calculate_active_seconds(machine_key, "machine_on", start_of_day, now)
    cutting_seconds = calculate_active_seconds(machine_key, "cutting_active", start_of_day, now)
    table_change_seconds = calculate_active_seconds(machine_key, "table_change", start_of_day, now)
    idle_seconds = max(machine_on_seconds - cutting_seconds - table_change_seconds, 0)
    utilization = round((cutting_seconds / machine_on_seconds) * 100, 1) if machine_on_seconds else 0.0
    availability = round((machine_on_seconds / elapsed_seconds) * 100, 1) if elapsed_seconds else 0.0

    return {
        "machine_on_seconds": machine_on_seconds,
        "machine_on_label": format_seconds(machine_on_seconds),
        "cutting_seconds": cutting_seconds,
        "cutting_label": format_seconds(cutting_seconds),
        "table_change_seconds": table_change_seconds,
        "table_change_label": format_seconds(table_change_seconds),
        "idle_seconds": idle_seconds,
        "idle_label": format_seconds(idle_seconds),
        "utilization_percent": utilization,
        "randament_percent": utilization,
        "availability_percent": availability,
        "production_window_label": format_seconds(elapsed_seconds),
        "updated_at": now.isoformat(timespec="seconds"),
    }


def insert_event(
    machine_key: str,
    signal_name: str,
    value: bool,
    source: str,
    note: str | None,
    operator_snapshot: dict,
) -> None:
    operator = operator_snapshot.get("primary_operator") or {}
    with get_sqlite_connection() as connection:
        connection.execute(
            """
            INSERT INTO signal_events (
                machine_key, signal_name, value, source, note, operator_id, operator_name, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                machine_key,
                signal_name,
                1 if value else 0,
                source,
                note,
                operator.get("employee_id"),
                operator.get("full_name"),
                now_local().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()


def sync_machine_events_from_live_snapshot(machine_key: str) -> dict | None:
    snapshot = get_live_machine_snapshot(machine_key)
    if not snapshot:
        return snapshot

    cache_live_snapshot(machine_key, snapshot)
    if not snapshot.get("available"):
        return snapshot

    derived_signals = snapshot.get("derived_signals") or {}
    current_signals = fetch_current_signals(machine_key)
    machine_profile = get_machine_profile(machine_key)
    operator_snapshot = fetch_current_operator(machine_profile["workcenter_id"])

    note = (
        f"selected={snapshot.get('selected_program')}; "
        f"active={snapshot.get('active_program')}; "
        f"material={snapshot.get('material')}; "
        f"status={snapshot.get('program_status')}"
    )

    old_table_change = bool(current_signals["table_change"]["active"])
    new_table_change = bool(derived_signals.get("table_change", False))
    if new_table_change and not old_table_change:
        open_pending_cycle(machine_key, machine_profile, snapshot, operator_snapshot, current_signals)
    elif old_table_change and not new_table_change:
        finalize_pending_cycle(machine_key, snapshot)

    for signal_name in ("machine_on", "cutting_active", "table_change"):
        new_value = bool(derived_signals.get(signal_name, False))
        if current_signals[signal_name]["active"] == new_value:
            continue

        insert_event(
            machine_key=machine_key,
            signal_name=signal_name,
            value=new_value,
            source=snapshot.get("source", "real-live"),
            note=note,
            operator_snapshot=operator_snapshot,
        )
        current_signals[signal_name]["active"] = new_value

    return snapshot


def delete_events(machine_key: str, mode: str, limit: int | None = None) -> int:
    with get_sqlite_connection() as connection:
        if mode == "manual_latest":
            rows = connection.execute(
                """
                SELECT id
                FROM signal_events
                WHERE machine_key = ? AND source LIKE ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (machine_key, f"{MANUAL_SOURCE_PREFIX}%", limit or 10),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            connection.execute(f"DELETE FROM signal_events WHERE id IN ({placeholders})", ids)
            connection.commit()
            return len(ids)

        if mode == "manual_all":
            cursor = connection.execute(
                "DELETE FROM signal_events WHERE machine_key = ? AND source LIKE ?",
                (machine_key, f"{MANUAL_SOURCE_PREFIX}%",),
            )
            connection.commit()
            return cursor.rowcount if cursor.rowcount != -1 else 0

    raise ValueError(f"Unsupported delete mode: {mode}")


def build_event_sequence(signal_name: str, target_value: bool, current_signals: dict[str, dict]) -> list[dict]:
    events: list[dict] = []

    if signal_name == "machine_on" and not target_value:
        if current_signals["cutting_active"]["active"]:
            events.append(
                {
                    "signal_name": "cutting_active",
                    "value": False,
                    "note": "Auto-stop: masina a fost oprita.",
                }
            )
        if current_signals["table_change"]["active"]:
            events.append(
                {
                    "signal_name": "table_change",
                    "value": False,
                    "note": "Auto-stop: masina a fost oprita.",
                }
            )
        events.append({"signal_name": "machine_on", "value": False, "note": None})
        return events

    if signal_name == "cutting_active" and target_value:
        if not current_signals["machine_on"]["active"]:
            events.append(
                {
                    "signal_name": "machine_on",
                    "value": True,
                    "note": "Auto-start: taierea a pornit masina.",
                }
            )
        if current_signals["table_change"]["active"]:
            events.append(
                {
                    "signal_name": "table_change",
                    "value": False,
                    "note": "Auto-stop: taierea a oprit schimbul de masa.",
                }
            )
        events.append({"signal_name": "cutting_active", "value": True, "note": None})
        return events

    if signal_name == "table_change" and target_value:
        if not current_signals["machine_on"]["active"]:
            events.append(
                {
                    "signal_name": "machine_on",
                    "value": True,
                    "note": "Auto-start: schimbul de masa a pornit masina.",
                }
            )
        if current_signals["cutting_active"]["active"]:
            events.append(
                {
                    "signal_name": "cutting_active",
                    "value": False,
                    "note": "Auto-stop: schimbul de masa a oprit taierea.",
                }
            )
        events.append({"signal_name": "table_change", "value": True, "note": None})
        return events

    events.append({"signal_name": signal_name, "value": target_value, "note": None})
    return events


def build_dashboard_payload(machine_key: str = DEFAULT_MACHINE_KEY) -> dict:
    machine_key = ensure_machine_key(machine_key)
    machine_profile = get_machine_profile(machine_key)
    if BACKGROUND_SYNC_ENABLED:
        runtime = get_machine_runtime(machine_key)
        live_extraction = runtime.get("last_snapshot")
    else:
        live_extraction = sync_machine_events_from_live_snapshot(machine_key)
    current_signals = fetch_current_signals(machine_key)
    operator_snapshot = fetch_current_operator(machine_profile["workcenter_id"])
    current_state = derive_machine_state(current_signals)
    stats_today = build_today_stats(machine_key)
    machines = [
        {
            **profile,
            "is_selected": profile["key"] == machine_key,
        }
        for profile in get_machine_profiles()
    ]

    return {
        "app_title": APP_TITLE,
        "dashboard_title": DASHBOARD_TITLE,
        "selected_machine_key": machine_key,
        "machine": machine_profile,
        "machines": machines,
        "workcenter_id": machine_profile["workcenter_id"],
        "current_state": current_state,
        "current_signals": current_signals,
        "stats_today": stats_today,
        "operator_snapshot": operator_snapshot,
        "real_data_source": get_real_data_settings(machine_profile),
        "live_extraction": live_extraction,
        "recent_events": fetch_recent_events(machine_key),
        "signal_definitions": SIGNAL_DEFINITIONS,
        "updated_at": now_local().isoformat(timespec="seconds"),
    }


def background_sync_loop() -> None:
    while True:
        for machine_key in MACHINE_DEFINITIONS:
            try:
                sync_machine_events_from_live_snapshot(machine_key)
            except Exception:
                pass
        time_module.sleep(BACKGROUND_SYNC_INTERVAL_SECONDS)


def ensure_background_sync_started() -> None:
    global _background_sync_started
    if _background_sync_started or not BACKGROUND_SYNC_ENABLED:
        return

    if os.getenv("FLASK_DEBUG", "0") == "1" and os.getenv("WERKZEUG_RUN_MAIN") != "true":
        return

    worker_count = int(os.getenv("GUNICORN_WORKERS", "1"))
    if worker_count > 1:
        return

    thread = threading.Thread(target=background_sync_loop, name="laser-background-sync", daemon=True)
    thread.start()
    _background_sync_started = True


@app.route("/")
def index():
    return render_template(
        "index.html",
        app_title=APP_TITLE,
        dashboard_title=DASHBOARD_TITLE,
        machines=get_machine_profiles(),
        default_machine_key=DEFAULT_MACHINE_KEY,
        script_catalog=build_script_catalog(),
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": now_local().isoformat(timespec="seconds")})


@app.route("/api/machines")
def machines_index():
    return jsonify({"machines": get_machine_profiles()})


@app.route("/api/machines/<machine_key>", methods=["PATCH"])
def update_machine_profile(machine_key: str):
    data = request.get_json(silent=True) or {}

    try:
        workcenter_id = parse_optional_int(data.get("workcenter_id"))
        machine = update_machine_workcenter(machine_key, workcenter_id)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify(
        {
            "success": True,
            "message": "WorkCenter actualizat.",
            "machine": machine,
            "dashboard": build_dashboard_payload(machine["key"]),
        }
    )


@app.route("/api/operator")
def operator_status():
    raw_workcenter_id = request.args.get("workcenter_id")
    machine_key = request.args.get("machine")

    try:
        if machine_key:
            profile = get_machine_profile(machine_key)
            workcenter_id = profile["workcenter_id"]
        else:
            workcenter_id = parse_optional_int(raw_workcenter_id)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify(fetch_current_operator(workcenter_id))


@app.route("/api/dashboard")
def dashboard():
    machine_key = request.args.get("machine", DEFAULT_MACHINE_KEY)
    try:
        payload = build_dashboard_payload(machine_key)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify(payload)


@app.route("/api/saved-records")
def saved_records():
    machine_key = request.args.get("machine")
    if machine_key:
        try:
            machine_key = ensure_machine_key(machine_key)
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify(build_saved_cycles_payload(machine_key))


@app.route("/api/events", methods=["POST"])
def create_event():
    data = request.get_json(silent=True) or {}

    try:
        machine_key = ensure_machine_key(data.get("machine_key", data.get("machine")))
        signal_name = ensure_signal_name(data.get("signal_name", data.get("signal", "")))
        target_value = to_bool(data.get("value"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    note = (data.get("note") or "").strip() or None
    source = (data.get("source") or "manual").strip() or "manual"

    current_signals = fetch_current_signals(machine_key)
    event_sequence = build_event_sequence(signal_name, target_value, current_signals)
    operator_snapshot = fetch_current_operator(get_machine_profile(machine_key)["workcenter_id"])

    for index, event in enumerate(event_sequence):
        event_note = note if index == len(event_sequence) - 1 else event["note"]
        insert_event(
            machine_key=machine_key,
            signal_name=event["signal_name"],
            value=event["value"],
            source=source,
            note=event_note,
            operator_snapshot=operator_snapshot,
        )

    return jsonify(
        {
            "success": True,
            "message": "Signal event saved.",
            "dashboard": build_dashboard_payload(machine_key),
        }
    )


@app.route("/api/events", methods=["DELETE"])
def delete_event_history():
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "manual_latest").strip()

    try:
        machine_key = ensure_machine_key(data.get("machine_key", data.get("machine")))
        limit = data.get("limit")
        deleted_count = delete_events(
            machine_key=machine_key,
            mode=mode,
            limit=int(limit) if limit is not None else None,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify(
        {
            "success": True,
            "message": f"Deleted {deleted_count} event(s).",
            "deleted_count": deleted_count,
            "dashboard": build_dashboard_payload(machine_key),
        }
    )


init_db()
ensure_background_sync_started()


if __name__ == "__main__":
    app.run(
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "3030")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
