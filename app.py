from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import threading
import time as time_module
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from flask import Flask, Response, has_request_context, jsonify, render_template, request
from dotenv import load_dotenv

try:
    import pyodbc
except ImportError:  # pragma: no cover - optional during early local setup
    pyodbc = None

try:  # pragma: no cover - optional Postgres fallback for Abkant
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

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

try:  # pragma: no cover - optional RTU support
    from pymodbus.client import ModbusSerialClient
except ImportError:  # pragma: no cover
    ModbusSerialClient = None


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DEFAULT_SQLITE_FILENAME = "laser_monitor.db"


def read_env_float(name: str, fallback: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def get_static_asset_version(filename: str) -> str:
    asset_path = BASE_DIR / "static" / filename
    try:
        return str(int(asset_path.stat().st_mtime))
    except OSError:
        return "dev"


def is_running_in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def resolve_sqlite_path() -> Path:
    explicit_sqlite_path = os.getenv("LASER_SQLITE_PATH")
    if explicit_sqlite_path:
        return Path(explicit_sqlite_path).expanduser()

    explicit_data_dir = os.getenv("LASER_DATA_DIR")
    if explicit_data_dir:
        return Path(explicit_data_dir).expanduser() / DEFAULT_SQLITE_FILENAME

    legacy_path = BASE_DIR / "data" / DEFAULT_SQLITE_FILENAME
    persistent_path = Path("/data") / DEFAULT_SQLITE_FILENAME

    if is_running_in_container():
        return persistent_path

    if legacy_path.exists():
        return legacy_path
    if persistent_path.exists():
        return persistent_path
    if persistent_path.parent.exists():
        return persistent_path
    return legacy_path


SQLITE_PATH = resolve_sqlite_path()
DATA_DIR = SQLITE_PATH.parent


def migrate_legacy_sqlite_if_needed() -> None:
    legacy_path = BASE_DIR / "data" / DEFAULT_SQLITE_FILENAME
    if SQLITE_PATH == legacy_path:
        return
    if SQLITE_PATH.exists() or not legacy_path.exists():
        return

    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy_path, SQLITE_PATH)

DEFAULT_ODBC_DRIVER = (
    "ODBC Driver 17 for SQL Server" if os.name == "nt" else "ODBC Driver 18 for SQL Server"
)
APP_TITLE = "HABA Production Monitor"
DASHBOARD_TITLE = "Laser TruMatic L3030"
DEFAULT_MACHINE_KEY = "laser1modbus"
HIDDEN_MACHINE_KEYS = {"laser1"}
MANUAL_SOURCE_PREFIX = "manual"
OCR_AVAILABLE = cv2 is not None and np is not None and pytesseract is not None
CV_IMAGE_AVAILABLE = cv2 is not None and np is not None
BACKGROUND_SYNC_ENABLED = os.getenv("BACKGROUND_SYNC_ENABLED", "1") != "0"
BACKGROUND_SYNC_INTERVAL_SECONDS = max(int(os.getenv("BACKGROUND_SYNC_INTERVAL_SECONDS", "3")), 1)
REQUEST_LIVE_SYNC_ENABLED = os.getenv("REQUEST_LIVE_SYNC_ENABLED", "0") == "1"
SAVED_RECORDS_PROMETHEUS_ENABLED = os.getenv("SAVED_RECORDS_PROMETHEUS_ENABLED", "0") == "1"
SNAPSHOT_FRESHNESS_SECONDS = max(int(os.getenv("SNAPSHOT_FRESHNESS_SECONDS", "3")), 1)
ABKANT_IDLE_STAGNATION_SECONDS = max(int(os.getenv("ABKANT_IDLE_STAGNATION_SECONDS", "600")), 60)
ABKANT_FEED_STALE_SECONDS = max(int(os.getenv("ABKANT_FEED_STALE_SECONDS", "120")), 15)
MODBUS_TCP_RETRY_ATTEMPTS = max(int(os.getenv("MODBUS_TCP_RETRY_ATTEMPTS", "3")), 1)
try:
    MODBUS_TCP_RETRY_DELAY_SECONDS = float(os.getenv("MODBUS_TCP_RETRY_DELAY_SECONDS", "0.15") or 0.15)
except (TypeError, ValueError):
    MODBUS_TCP_RETRY_DELAY_SECONDS = 0.15
MODBUS_TCP_RETRY_DELAY_SECONDS = min(max(MODBUS_TCP_RETRY_DELAY_SECONDS, 0.0), 2.0)
MODBUS_SNAPSHOT_GRACE_SECONDS = max(int(os.getenv("MODBUS_SNAPSHOT_GRACE_SECONDS", "180")), 0)
OPERATOR_CACHE_SECONDS = max(int(os.getenv("OPERATOR_CACHE_SECONDS", "20")), 3)
PROMETHEUS_MAX_PARALLEL_QUERIES = max(int(os.getenv("PROMETHEUS_MAX_PARALLEL_QUERIES", "6")), 1)
try:
    PROMETHEUS_QUERY_TIMEOUT_SECONDS = float(os.getenv("PROMETHEUS_QUERY_TIMEOUT_SECONDS", "2.5") or 2.5)
except (TypeError, ValueError):
    PROMETHEUS_QUERY_TIMEOUT_SECONDS = 2.5
PROMETHEUS_QUERY_TIMEOUT_SECONDS = min(max(PROMETHEUS_QUERY_TIMEOUT_SECONDS, 0.5), 30.0)
TABLE_SHEET_ROI_X1_RATIO = min(max(read_env_float("TABLE_SHEET_ROI_X1_RATIO", 0.24), 0.0), 0.95)
TABLE_SHEET_ROI_X2_RATIO = min(max(read_env_float("TABLE_SHEET_ROI_X2_RATIO", 0.64), 0.05), 1.0)
TABLE_SHEET_ROI_Y1_RATIO = min(max(read_env_float("TABLE_SHEET_ROI_Y1_RATIO", 0.22), 0.0), 0.95)
TABLE_SHEET_ROI_Y2_RATIO = min(max(read_env_float("TABLE_SHEET_ROI_Y2_RATIO", 0.92), 0.05), 1.0)
TABLE_SHEET_EDGE_DENSITY_THRESHOLD = min(max(read_env_float("TABLE_SHEET_EDGE_DENSITY_THRESHOLD", 0.13), 0.02), 0.45)
TABLE_SHEET_BRIGHTNESS_THRESHOLD = min(max(read_env_float("TABLE_SHEET_BRIGHTNESS_THRESHOLD", 90.0), 30.0), 220.0)
TABLE_SHEET_LAPLACIAN_VAR_THRESHOLD = min(max(read_env_float("TABLE_SHEET_LAPLACIAN_VAR_THRESHOLD", 1800.0), 200.0), 10000.0)
_background_sync_started = False
RUNTIME_VALUE_UNCHANGED = object()
PROMETHEUS_BASE_URL = (os.getenv("PROMETHEUS_BASE_URL", "http://localhost:9090") or "http://localhost:9090").rstrip("/")
ESP32_DHT_URL = (os.getenv("ESP32_DHT_URL", "") or "").strip()
try:
    ESP32_DHT_TIMEOUT_SECONDS = float(os.getenv("ESP32_DHT_TIMEOUT_SECONDS", "2.5") or 2.5)
except (TypeError, ValueError):
    ESP32_DHT_TIMEOUT_SECONDS = 2.5
ESP32_DHT_TIMEOUT_SECONDS = min(max(ESP32_DHT_TIMEOUT_SECONDS, 0.5), 15.0)
UNKNOWN_OPERATOR_LABEL = "Fara operator la salvare"
SAVED_CYCLE_INSERT_PLACEHOLDERS = ", ".join(["?"] * 23)
_operator_snapshot_cache: dict[int, dict] = {}

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
    "laser1modbus": {
        "label": "LASER1MODBUS",
        "description": "Laser1 cu timpi cititi din Modbus si program extras din feed.",
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

LOCKED_MACHINE_MODBUS_CONFIGS: dict[str, dict[str, object]] = {
    "laser1modbus": {
        "machine_key": "laser1modbus",
        "transport": "tcp",
        "host": "192.168.2.242",
        "port": 502,
        "serial_port": "",
        "serial_baudrate": 9600,
        "serial_parity": "N",
        "serial_stopbits": 1,
        "unit_id": 1,
        "bit_source": "discrete_input",
        "start_address": 0,
        "poll_timeout_seconds": 1.5,
        "in1_signal": "idle_abort",
        "in2_signal": "table_change",
        "in3_signal": "cutting_active",
        "in4_signal": "machine_on",
    }
}

DEFAULT_MACHINE_HMI_URLS = {
    "laser1": "http://192.168.2.242:8081/",
    "laser1modbus": "http://192.168.2.242:8081/",
    "laser2": "",
    "abkant": "https://abkant.helpan.ro/",
}

DEFAULT_MACHINE_CAMERA_FEEDS = {
    "laser1": {
        "url": "http://192.168.2.140/ISAPI/Streaming/channels/101/picture",
        "mode": "image",
        "username": "admin",
        "password": "HELPAN2011$",
        "auth": "digest",
    },
    "laser1modbus": {
        "url": "http://192.168.2.140/ISAPI/Streaming/channels/101/picture",
        "mode": "image",
        "username": "admin",
        "password": "HELPAN2011$",
        "auth": "digest",
        "extra_feeds": [
            {
                "key": "camera_2",
                "url": "http://192.168.2.10/ISAPI/Streaming/channels/101/picture",
                "mode": "image",
                "username": "admin",
                "password": "HELPAN321$",
                "auth": "digest",
            }
        ],
    },
    "laser2": {
        "url": "",
        "mode": "image",
        "username": "",
        "password": "",
        "auth": "basic",
    },
    "abkant": {
        "url": "",
        "mode": "image",
        "username": "",
        "password": "",
        "auth": "basic",
    },
}

REAL_DATA_FEEDS = {
    "laser1": {
        "script_name": "laserFeed.py",
        "display_name": "laserFeed OCR bridge",
        "endpoint": "http://laserbvision-1:8081",
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
            {"label": "Feed activ", "value": "LaserStatus = UP"},
            {"label": "Cutting", "value": "nu este extras direct inca"},
            {"label": "Table change", "value": "nu este extras direct inca"},
            {"label": "Idle", "value": "derivat doar dupa ce avem Cutting"},
        ],
        "derivation_rules": [
            {"label": "Feed activ", "value": "DA cand Redis key LaserStatus este UP"},
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
    "laser1modbus": {
        "script_name": "app.py",
        "display_name": "Laser1 Modbus bridge",
        "endpoint": "http://laserbvision-1:8081",
        "transport": "Modbus TCP/RTU + OCR din feed",
        "left_panel": [
            {"label": "OCR program", "value": "da"},
            {"label": "OCR material", "value": "da"},
            {"label": "Modbus bits", "value": "IN1 .. IN4"},
            {"label": "Semnal live", "value": "complet din Modbus"},
        ],
        "screen_rows": [
            {"label": "Selected program", "value": "OCR din feedul Laser1"},
            {"label": "Active program", "value": "OCR din feedul Laser1"},
            {"label": "Machine ON", "value": "bit Modbus mapat pe IN1..IN4"},
            {"label": "Cutting", "value": "bit Modbus mapat pe IN1..IN4"},
            {"label": "Table change", "value": "bit Modbus mapat pe IN1..IN4"},
            {"label": "Idle / Aborted", "value": "bit Modbus optional mapat pe IN1..IN4"},
            {"label": "Randament", "value": "Cutting / Machine ON"},
        ],
        "derivation_rules": [
            {"label": "Machine ON", "value": "Se activeaza cind bitul mapat este 1 sau daca orice alt semnal productiv este 1"},
            {"label": "Cutting", "value": "Se activeaza direct din bitul Modbus mapat"},
            {"label": "Table change", "value": "Porneste pe 1 si ciclul se inchide cind bitul revine pe 0"},
            {"label": "Program", "value": "Se citeste doar din feed pe intervalul semnalelor Modbus"},
        ],
        "details": [
            "Configurezi transportul, endpointul si maparea IN1..IN4 direct din dashboard",
            "Aplicatia poate citi Modbus TCP sau Modbus RTU direct din container",
            "Programul si materialul ramin citite din feedul Laser1",
        ],
    },
    "laser2": {
        "script_name": "laserFeed.py",
        "display_name": "laserFeed OCR bridge",
        "endpoint": "",
        "transport": "Redis + MQTT",
        "left_panel": [
            {"label": "OCR program", "value": "in asteptare"},
            {"label": "OCR repetitie", "value": "in asteptare"},
            {"label": "Camera feed", "value": "neconfigurat"},
            {"label": "Semnal live", "value": "oprit"},
        ],
        "screen_rows": [
            {"label": "Selected program", "value": "necitit"},
            {"label": "Active program", "value": "necitit"},
            {"label": "Program status", "value": "fara feed dedicat"},
            {"label": "Feed activ", "value": "ramane NU pina exista sursa separata"},
            {"label": "Cutting", "value": "oprit"},
            {"label": "Table change", "value": "oprit"},
            {"label": "Idle", "value": "oprit"},
        ],
        "derivation_rules": [
            {"label": "Feed activ", "value": "ramane NU fara feed sau semnal dedicat pentru Laser2"},
            {"label": "Cutting", "value": "necesita OCR separat sau PLC dedicat"},
            {"label": "Table change", "value": "necesita feed separat sau semnal suplimentar"},
            {"label": "Idle", "value": "va fi calculat doar dupa instrumentarea dedicata"},
        ],
        "details": [
            "Nu mai mosteneste automat feedul de la Laser1",
            "Configureaza LASER2_REAL_DATA_ENDPOINT sau LASER2_CAMERA_FEED_URL pentru activare",
            "Pana atunci dashboardul il trateaza ca utilaj neinstrumentat",
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
            {"label": "Feed activ", "value": "camera accesibila + rpiabkantworking"},
            {"label": "Bending", "value": "program activ + progres piese sub total"},
            {"label": "Setup change", "value": "upper/lower se schimba pina porneste urmatoarea indoire"},
            {"label": "Idle", "value": "program neschimbat / fara progres bucati"},
        ],
        "derivation_rules": [
            {"label": "Feed activ", "value": "DA cand captura merge si parametrul rpiabkantworking ramane TRUE"},
            {"label": "Bending", "value": "DA cand exista program activ si numarul de piese produse nu a ajuns la total"},
            {"label": "Setup change", "value": "DA de la prima schimbare detectata la Upper/Lower pina cind indoirea porneste cu noul setup"},
            {"label": "Idle", "value": "Poate fi derivat cand feedul este activ dar programul / numarul de bucati nu avanseaza"},
        ],
        "details": [
            "Camera OCR: 100.126.29.52:8081",
            "MQTT topics observate: Abkant/StareProgramIdentificat, Abkant/ProgramActiv",
            "Tabela observata: raportare_abkant",
            "Scriptul urmareste programul activ, numarul de bucati si setup-ul Upper/Lower",
        ],
    },
}

SIGNAL_DEFINITIONS = {
    "machine_on": {
        "label": "Machine ON",
        "description": "Masina este alimentata si pregatita.",
        "accent": "steel",
        "button_on_label": "Opreste masina",
        "button_off_label": "Porneste masina",
        "metric_label": "Machine ON",
        "report_label": "Masina pornita",
    },
    "cutting_active": {
        "label": "Cutting active",
        "description": "Utilajul lucreaza activ in productie.",
        "accent": "ember",
        "button_on_label": "Opreste productia",
        "button_off_label": "Porneste productia",
        "metric_label": "Cutting",
        "report_label": "Taiere",
    },
    "table_change": {
        "label": "Table change",
        "description": "Se schimba masa sau se pregateste urmatorul ciclu.",
        "accent": "teal",
        "button_on_label": "Opreste schimbul",
        "button_off_label": "Porneste schimbul",
        "metric_label": "Table change",
        "report_label": "Schimb masa",
    },
    "idle_abort": {
        "label": "Idle / Aborted",
        "description": "Semnal dedicat pentru idle sau abort din utilaj.",
        "accent": "slate",
        "metric_label": "Idle / Aborted",
        "report_label": "Idle / Aborted",
    },
}

MACHINE_SIGNAL_OVERRIDES = {
    "laser1": {
        "machine_on": {
            "label": "Feed activ",
            "description": "Bridge-ul OCR si semnalul LaserStatus raspund pentru Laser1.",
            "button_on_label": "Marcheaza feed inactiv",
            "button_off_label": "Marcheaza feed activ",
            "metric_label": "Feed activ",
            "report_label": "Feed activ",
        },
    },
    "laser1modbus": {
        "machine_on": {
            "label": "Machine ON",
            "description": "Bitul Modbus mapat pe Machine ON este activ.",
            "metric_label": "Machine ON",
            "report_label": "Machine ON",
        },
        "cutting_active": {
            "label": "Cutting",
            "description": "Bitul Modbus mapat pe Cutting este activ.",
            "metric_label": "Cutting",
            "report_label": "Cutting",
        },
        "table_change": {
            "label": "Table change",
            "description": "Bitul Modbus mapat pe Table change este activ.",
            "metric_label": "Table change",
            "report_label": "Table change",
        },
        "idle_abort": {
            "label": "Idle / Aborted",
            "description": "Bitul Modbus mapat pe Idle sau Aborted este activ.",
            "metric_label": "Idle / Aborted",
            "report_label": "Idle / Aborted",
        },
    },
    "laser2": {
        "machine_on": {
            "label": "Feed activ",
            "description": "Dashboardul vede doar disponibilitatea feedului configurat pentru Laser2.",
            "button_on_label": "Marcheaza feed inactiv",
            "button_off_label": "Marcheaza feed activ",
            "metric_label": "Feed activ",
            "report_label": "Feed activ",
        },
    },
    "abkant": {
        "machine_on": {
            "label": "Feed activ",
            "description": "Bridge-ul OCR si parametrul rpiabkantworking raspund pentru Abkant.",
            "button_on_label": "Marcheaza feed inactiv",
            "button_off_label": "Marcheaza feed activ",
            "metric_label": "Feed activ",
            "report_label": "Feed activ",
        },
        "cutting_active": {
            "label": "Bending",
            "description": "Abkantul indoaie activ piesele programului curent.",
            "button_on_label": "Opreste indoirea",
            "button_off_label": "Porneste indoirea",
            "metric_label": "Bending",
            "report_label": "Indoire",
        },
        "table_change": {
            "label": "Setup change",
            "description": "Abkantul schimba sculele Upper/Lower si pregateste urmatorul setup.",
            "button_on_label": "Opreste schimbarea",
            "button_off_label": "Porneste schimbarea",
            "metric_label": "Setup change",
            "report_label": "Setup change",
        },
    }
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
    "idle": {
        "label": "Idle",
        "description": "Masina este pornita, dar este in idle sau abort.",
        "tone": "slate",
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

MACHINE_STATE_OVERRIDES = {
    "laser1": {
        "off": {
            "label": "Feed indisponibil",
            "description": "Nu mai vine snapshot valid din feed-ul Laser1.",
        },
        "ready": {
            "label": "Pregatit",
            "description": "Feedul Laser1 este activ, dar nu avem taiere detectata acum.",
        },
    },
    "laser2": {
        "off": {
            "label": "Feed indisponibil",
            "description": "Laser2 nu are inca feed dedicat sau snapshot valid.",
        },
        "ready": {
            "label": "Pregatit",
            "description": "Feedul Laser2 este activ, dar nu avem taiere detectata acum.",
        },
    },
    "laser1modbus": {
        "off": {
            "label": "Modbus indisponibil",
            "description": "Containerul nu poate citi inca bitii Modbus configurati.",
        },
        "ready": {
            "label": "Pregatit",
            "description": "Machine ON este activ, dar nici taierea, nici schimbul de masa nu ruleaza acum.",
        },
        "idle": {
            "label": "Idle / Aborted",
            "description": "Bitul dedicat de idle sau abort este activ in Modbus.",
        },
    },
    "abkant": {
        "off": {
            "label": "Feed indisponibil",
            "description": "Nu mai vine snapshot valid din feed-ul Abkant.",
        },
        "ready": {
            "label": "Pregatit",
            "description": "Feedul Abkant este activ, dar programul nu indoaie acum.",
        },
        "cutting": {
            "label": "In indoire",
            "description": "Programul curent este in curs de indoire.",
        },
        "table_change": {
            "label": "Setup change",
            "description": "Abkantul schimba sculele Upper/Lower si pregateste urmatoarea indoire.",
        },
    }
}

LASER_OCR_ZONES = {
    "top_banner": (0, 40, 1280, 110),
    "right_panel": (620, 170, 620, 550),
    "left_panel": (0, 170, 620, 260),
}

MODBUS_MACHINE_KEYS = {"laser1modbus"}
MODBUS_TRANSPORT_CHOICES = ("tcp", "rtu")
MODBUS_SIGNAL_TARGET_CHOICES = (
    "unused",
    "machine_on",
    "cutting_active",
    "table_change",
    "idle_abort",
)
MODBUS_SERIAL_PARITY_CHOICES = ("N", "E", "O")
MODBUS_SERIAL_STOPBITS_CHOICES = (1, 2)
PROGRAM_STATS_MACHINE_KEYS = {"laser1", "laser1modbus"}

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


def parse_optional_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return float(stripped)
    return float(value)


def ensure_signal_name(signal_name: str) -> str:
    if signal_name not in SIGNAL_DEFINITIONS:
        raise ValueError(f"Unsupported signal: {signal_name}")
    return signal_name


def ensure_machine_key(machine_key: str | None) -> str:
    candidate = (machine_key or DEFAULT_MACHINE_KEY).strip().lower()
    if candidate not in MACHINE_DEFINITIONS:
        raise ValueError(f"Unsupported machine: {candidate}")
    return candidate


def resolve_signal_definition(machine_key: str, signal_name: str) -> dict:
    meta = dict(SIGNAL_DEFINITIONS[signal_name])
    meta.update(MACHINE_SIGNAL_OVERRIDES.get(machine_key, {}).get(signal_name, {}))
    return meta


def resolve_state_definition(machine_key: str, state_key: str) -> dict:
    meta = dict(STATE_DEFINITIONS[state_key])
    meta.update(MACHINE_STATE_OVERRIDES.get(machine_key, {}).get(state_key, {}))
    return meta


def get_machine_env_value(machine_key: str, suffix: str, legacy_names: tuple[str, ...] = ()) -> str:
    env_names = [f"{machine_key.upper()}_{suffix}", *legacy_names]
    for env_name in env_names:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return ""


def normalize_modbus_transport(value: str | None, fallback: str = "tcp") -> str:
    normalized = (value or "").strip().lower()
    if normalized in MODBUS_TRANSPORT_CHOICES:
        return normalized
    return fallback


def normalize_modbus_serial_parity(value: str | None) -> str:
    normalized = (value or "N").strip().upper()
    if normalized in MODBUS_SERIAL_PARITY_CHOICES:
        return normalized
    return "N"


def machine_uses_modbus(machine_key: str) -> bool:
    return ensure_machine_key(machine_key) in MODBUS_MACHINE_KEYS


def machine_supports_idle_signal(machine_key: str) -> bool:
    if not machine_uses_modbus(machine_key):
        return False
    config = get_machine_modbus_config(machine_key)
    return "idle_abort" in set(config.get("signal_map", {}).values())


def resolve_real_data_endpoint_candidates(machine_key: str) -> list[str]:
    machine_key = ensure_machine_key(machine_key)
    primary_legacy_names = ("LASER_REAL_DATA_ENDPOINT",) if machine_key in {"laser1", "laser2"} else ()
    fallback_legacy_names = ("LASER_REAL_DATA_ENDPOINT_FALLBACK",) if machine_key in {"laser1", "laser2"} else ()
    primary_endpoint = (
        get_machine_env_value(machine_key, "REAL_DATA_ENDPOINT", primary_legacy_names)
        or REAL_DATA_FEEDS[machine_key]["endpoint"]
    )
    fallback_endpoint = get_machine_env_value(machine_key, "REAL_DATA_ENDPOINT_FALLBACK", fallback_legacy_names)

    candidates: list[str] = []
    for endpoint in (primary_endpoint, fallback_endpoint):
        normalized_endpoint = (endpoint or "").strip()
        if normalized_endpoint and normalized_endpoint not in candidates:
            candidates.append(normalized_endpoint)
    return candidates


def resolve_real_data_endpoint(machine_key: str) -> str:
    candidates = resolve_real_data_endpoint_candidates(machine_key)
    return candidates[0] if candidates else ""


def resolve_real_data_name(machine_key: str) -> str:
    legacy_names = ("LASER_REAL_DATA_NAME",) if machine_key in {"laser1", "laser2"} else ()
    return get_machine_env_value(machine_key, "REAL_DATA_NAME", legacy_names) or REAL_DATA_FEEDS[machine_key]["display_name"]


def machine_has_dedicated_live_source(machine_key: str) -> bool:
    machine_key = ensure_machine_key(machine_key)
    if machine_key == "laser1modbus":
        try:
            return bool(get_machine_modbus_config(machine_key).get("enabled"))
        except Exception:
            return False
    if machine_key != "laser2":
        return True

    laser1_endpoint = get_machine_env_value("laser1", "REAL_DATA_ENDPOINT", ("LASER_REAL_DATA_ENDPOINT",))
    laser1_camera = get_machine_env_value("laser1", "CAMERA_FEED_URL")
    laser1_hmi = get_machine_env_value("laser1", "HMI_FEED_URL")
    laser2_endpoint = get_machine_env_value(machine_key, "REAL_DATA_ENDPOINT")
    laser2_camera = get_machine_env_value(machine_key, "CAMERA_FEED_URL")
    laser2_hmi = get_machine_env_value(machine_key, "HMI_FEED_URL")

    return bool(
        (laser2_endpoint and laser2_endpoint != laser1_endpoint)
        or (laser2_camera and laser2_camera != laser1_camera and laser2_camera != laser1_endpoint)
        or (laser2_hmi and laser2_hmi != laser1_hmi)
    )


def resolve_machine_camera_feed_url(machine_key: str) -> str:
    return (
        get_machine_env_value(machine_key, "CAMERA_FEED_URL")
        or DEFAULT_MACHINE_CAMERA_FEEDS.get(machine_key, {}).get("url", "")
        or resolve_real_data_endpoint(machine_key)
    )


def should_proxy_camera_feed(machine_key: str, camera_url: str, username: str, password: str) -> bool:
    return bool(camera_url and username and password)


def resolve_machine_camera_feed_mode(machine_key: str) -> str:
    mode = (
        get_machine_env_value(machine_key, "CAMERA_FEED_MODE")
        or DEFAULT_MACHINE_CAMERA_FEEDS.get(machine_key, {}).get("mode", "image")
        or "image"
    ).strip().lower()
    if mode not in {"image", "page"}:
        return "image"
    return mode


def resolve_machine_camera_feed_credentials(machine_key: str) -> tuple[str, str, str]:
    defaults = DEFAULT_MACHINE_CAMERA_FEEDS.get(machine_key, {})
    username = get_machine_env_value(machine_key, "CAMERA_FEED_USERNAME") or defaults.get("username", "")
    password = get_machine_env_value(machine_key, "CAMERA_FEED_PASSWORD") or defaults.get("password", "")
    auth_type = (get_machine_env_value(machine_key, "CAMERA_FEED_AUTH") or defaults.get("auth", "basic")).strip().lower()
    if auth_type not in {"basic", "digest"}:
        auth_type = "basic"
    return username, password, auth_type


def resolve_machine_camera_feed_configs(machine_key: str) -> list[dict]:
    machine_key = ensure_machine_key(machine_key)
    primary_url = resolve_machine_camera_feed_url(machine_key)
    primary_mode = resolve_machine_camera_feed_mode(machine_key)
    primary_username, primary_password, primary_auth = resolve_machine_camera_feed_credentials(machine_key)

    feeds = [
        {
            "key": "camera",
            "url": primary_url,
            "mode": primary_mode,
            "username": primary_username,
            "password": primary_password,
            "auth": primary_auth,
        }
    ]

    defaults = DEFAULT_MACHINE_CAMERA_FEEDS.get(machine_key, {})
    extra_defaults = defaults.get("extra_feeds", [])
    for index, default_feed in enumerate(extra_defaults, start=2):
        key = (default_feed.get("key") or f"camera_{index}").strip()
        if not key:
            key = f"camera_{index}"
        prefix = key.upper()
        feed_url = get_machine_env_value(machine_key, f"{prefix}_URL") or (default_feed.get("url", "").strip())
        feed_mode = (
            get_machine_env_value(machine_key, f"{prefix}_MODE")
            or (default_feed.get("mode", "image") or "image")
        ).strip().lower()
        if feed_mode not in {"image", "page"}:
            feed_mode = "image"
        feed_username = get_machine_env_value(machine_key, f"{prefix}_USERNAME") or (default_feed.get("username", "").strip())
        feed_password = get_machine_env_value(machine_key, f"{prefix}_PASSWORD") or (default_feed.get("password", "").strip())
        feed_auth = (get_machine_env_value(machine_key, f"{prefix}_AUTH") or default_feed.get("auth", "basic")).strip().lower()
        if feed_auth not in {"basic", "digest"}:
            feed_auth = "basic"
        feeds.append(
            {
                "key": key,
                "url": feed_url,
                "mode": feed_mode,
                "username": feed_username,
                "password": feed_password,
                "auth": feed_auth,
            }
        )
    return feeds


def resolve_machine_camera_feed_config(machine_key: str, feed_key: str = "camera") -> dict | None:
    normalized_key = (feed_key or "camera").strip().lower().replace("-", "_")
    for config in resolve_machine_camera_feed_configs(machine_key):
        if config.get("key", "").strip().lower() == normalized_key:
            return config
    return None


def resolve_machine_hmi_feed_url(machine_key: str) -> str:
    return get_machine_env_value(machine_key, "HMI_FEED_URL") or DEFAULT_MACHINE_HMI_URLS.get(machine_key, "")


def build_machine_feeds(machine_key: str) -> list[dict]:
    machine_key = ensure_machine_key(machine_key)
    if machine_key == "laser2" and not machine_has_dedicated_live_source(machine_key):
        return []

    hmi_url = resolve_machine_hmi_feed_url(machine_key)
    camera_configs = resolve_machine_camera_feed_configs(machine_key)
    feeds = []
    for config in camera_configs:
        camera_key = config.get("key", "camera")
        camera_url = config.get("url", "")
        camera_mode = config.get("mode", "image")
        camera_username = config.get("username", "")
        camera_password = config.get("password", "")
        camera_refresh_ms = None
        if machine_key in {"laser1", "laser1modbus"} and camera_mode == "image" and (
            camera_url.strip().lower().endswith("/picture")
            or (camera_username and camera_password)
        ):
            camera_refresh_ms = 1500

        rendered_url = camera_url
        if camera_mode == "image" and should_proxy_camera_feed(machine_key, camera_url, camera_username, camera_password):
            if camera_key == "camera":
                rendered_url = f"/api/camera-feed/{machine_key}"
            else:
                rendered_url = f"/api/camera-feed/{machine_key}/{camera_key}"

        feeds.append(
            {
                "key": camera_key,
                "mode": camera_mode,
                "url": rendered_url,
                "open_url": camera_url,
                "display_url": urllib.parse.urlsplit(camera_url).netloc or camera_url,
                "refresh_ms": camera_refresh_ms,
            }
        )

    if machine_key == "abkant":
        return feeds

    feeds.append(
        {
            "key": "hmi",
            "mode": "page",
            "url": hmi_url,
            "open_url": hmi_url,
            "display_url": urllib.parse.urlsplit(hmi_url).netloc or hmi_url,
            "refresh_ms": None,
        }
    )
    return feeds


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


def fetch_camera_feed_content(machine_key: str, feed_key: str = "camera", timeout: float = 8.0) -> tuple[bytes | None, str, str | None]:
    feed_config = resolve_machine_camera_feed_config(machine_key, feed_key)
    if not feed_config:
        return None, "Camera feed inexistent pentru utilajul selectat.", None

    camera_url = (feed_config.get("url") or "").strip()
    if not camera_url:
        return None, "Camera feed URL nu este configurat.", None

    request_obj = urllib.request.Request(camera_url, headers={"User-Agent": "HABA-Production-Monitor/1.0"})
    camera_username = (feed_config.get("username") or "").strip()
    camera_password = (feed_config.get("password") or "").strip()
    auth_type = (feed_config.get("auth") or "basic").strip().lower()

    try:
        if camera_username and camera_password:
            password_manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            password_manager.add_password(None, camera_url, camera_username, camera_password)

            parsed_url = urllib.parse.urlsplit(camera_url)
            if parsed_url.scheme and parsed_url.netloc:
                base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"
                password_manager.add_password(None, base_url, camera_username, camera_password)

            auth_handler = (
                urllib.request.HTTPDigestAuthHandler(password_manager)
                if auth_type == "digest"
                else urllib.request.HTTPBasicAuthHandler(password_manager)
            )
            opener = urllib.request.build_opener(auth_handler)
            with opener.open(request_obj, timeout=timeout) as response:
                return response.read(), "", response.headers.get("Content-Type")

        with urllib.request.urlopen(request_obj, timeout=timeout) as response:
            return response.read(), "", response.headers.get("Content-Type")
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}", None


def fetch_camera_feed_frame(machine_key: str, feed_key: str = "camera", timeout: float = 3.0):
    if not CV_IMAGE_AVAILABLE:
        return None, "Lipseste stack-ul OpenCV/Numpy necesar pentru analiza imaginii."

    content, error_message, _ = fetch_camera_feed_content(machine_key, feed_key=feed_key, timeout=timeout)
    if content is None:
        return None, error_message or "Nu am putut citi camera."

    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None, "Fluxul camerei a raspuns, dar cadrul nu a putut fi decodat."
    return image, None


def detect_sheet_on_change_table(machine_key: str) -> dict:
    detection = {
        "available": False,
        "present": None,
        "confidence": 0.0,
        "message": "",
        "feed_key": "camera_2",
        "edge_density": None,
        "mean_brightness": None,
        "laplacian_var": None,
    }

    image, error_message = fetch_camera_feed_frame(machine_key, feed_key="camera_2", timeout=2.5)
    if image is None:
        detection["message"] = error_message or "Nu am putut citi camera de masa."
        return detection

    height, width = image.shape[:2]
    x1 = int(width * TABLE_SHEET_ROI_X1_RATIO)
    x2 = int(width * TABLE_SHEET_ROI_X2_RATIO)
    y1 = int(height * TABLE_SHEET_ROI_Y1_RATIO)
    y2 = int(height * TABLE_SHEET_ROI_Y2_RATIO)
    x1 = max(0, min(x1, width - 1))
    x2 = max(x1 + 1, min(x2, width))
    y1 = max(0, min(y1, height - 1))
    y2 = max(y1 + 1, min(y2, height))

    roi = image[y1:y2, x1:x2]
    if roi.size == 0:
        detection["message"] = "ROI invalida pentru detectia tablei."
        return detection

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, threshold1=60, threshold2=140)
    edge_density = float(np.count_nonzero(edges)) / float(edges.size)
    mean_brightness = float(np.mean(gray))
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    is_present = (
        edge_density <= TABLE_SHEET_EDGE_DENSITY_THRESHOLD
        and mean_brightness >= TABLE_SHEET_BRIGHTNESS_THRESHOLD
        and laplacian_var <= TABLE_SHEET_LAPLACIAN_VAR_THRESHOLD
    )
    edge_score = (TABLE_SHEET_EDGE_DENSITY_THRESHOLD - edge_density) / max(TABLE_SHEET_EDGE_DENSITY_THRESHOLD, 1e-6)
    brightness_score = (mean_brightness - TABLE_SHEET_BRIGHTNESS_THRESHOLD) / max(255.0 - TABLE_SHEET_BRIGHTNESS_THRESHOLD, 1.0)
    texture_score = (TABLE_SHEET_LAPLACIAN_VAR_THRESHOLD - laplacian_var) / max(TABLE_SHEET_LAPLACIAN_VAR_THRESHOLD, 1.0)
    combined = 0.5 + (0.3 * edge_score) + (0.2 * brightness_score) + (0.35 * texture_score)
    confidence = max(0.0, min(1.0, combined))

    detection.update(
        {
            "available": True,
            "present": bool(is_present),
            "confidence": round(confidence, 2),
            "edge_density": round(edge_density, 4),
            "mean_brightness": round(mean_brightness, 1),
            "laplacian_var": round(laplacian_var, 1),
            "message": (
                "Tabla detectata pe masa de schimb."
                if is_present
                else "Nu detectez tabla pe masa de schimb."
            ),
        }
    )
    return detection


def fetch_esp32_environment_snapshot() -> dict:
    timestamp = now_local().isoformat(timespec="seconds")
    if not ESP32_DHT_URL:
        return {
            "success": True,
            "connected": False,
            "status": "not_configured",
            "temperature_c": None,
            "humidity_percent": None,
            "message": "Seteaza ESP32_DHT_URL in .env pentru a citi senzorul DHT11.",
            "source_url": "",
            "updated_at": timestamp,
        }

    request_obj = urllib.request.Request(
        ESP32_DHT_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "HABA-Production-Monitor/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=ESP32_DHT_TIMEOUT_SECONDS) as response:
            payload_raw = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {
            "success": True,
            "connected": False,
            "status": "offline",
            "temperature_c": None,
            "humidity_percent": None,
            "message": f"Nu ma pot conecta la ESP32: {exc.__class__.__name__}: {exc}",
            "source_url": ESP32_DHT_URL,
            "updated_at": timestamp,
        }

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return {
            "success": True,
            "connected": False,
            "status": "invalid_payload",
            "temperature_c": None,
            "humidity_percent": None,
            "message": "ESP32 a raspuns, dar payload-ul nu este JSON valid.",
            "source_url": ESP32_DHT_URL,
            "updated_at": timestamp,
        }

    if not isinstance(payload, dict):
        return {
            "success": True,
            "connected": False,
            "status": "invalid_payload",
            "temperature_c": None,
            "humidity_percent": None,
            "message": "ESP32 a raspuns, dar payload-ul nu este un obiect JSON.",
            "source_url": ESP32_DHT_URL,
            "updated_at": timestamp,
        }

    def coerce_float(value):
        try:
            return parse_optional_float(value)
        except (TypeError, ValueError):
            return None

    temperature_c = coerce_float(payload.get("temperature_c"))
    if temperature_c is None:
        temperature_c = coerce_float(payload.get("temperature"))

    humidity_percent = coerce_float(payload.get("humidity_percent"))
    if humidity_percent is None:
        humidity_percent = coerce_float(payload.get("humidity"))

    status = str(payload.get("status", "")).strip().lower()
    has_measurement = temperature_c is not None or humidity_percent is not None
    is_connected = status in {"ok", "online", "ready", "success"} or has_measurement
    message = str(payload.get("message") or "").strip()
    if not message:
        message = (
            "Conexiune activa cu ESP32."
            if is_connected
            else "ESP32 a raspuns, dar nu a trimis inca valori valide de la DHT11."
        )

    return {
        "success": True,
        "connected": is_connected,
        "status": status or ("ok" if is_connected else "unknown"),
        "temperature_c": round(temperature_c, 1) if temperature_c is not None else None,
        "humidity_percent": round(humidity_percent, 1) if humidity_percent is not None else None,
        "message": message,
        "source_url": ESP32_DHT_URL,
        "updated_at": timestamp,
    }


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


def read_ocr_block_variants(image, zone: tuple[int, int, int, int]) -> list[str]:
    if not OCR_AVAILABLE or image is None:
        return []

    x, y, width, height = zone
    crop = image[y : y + height, x : x + width]
    if crop.size == 0:
        return []

    enlarged = cv2.resize(crop, None, fx=2.8, fy=2.8, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    threshold = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    adaptive = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )

    variants = []
    configs = (
        ("--psm 6 --oem 3 -c preserve_interword_spaces=1", threshold),
        ("--psm 11 --oem 3 -c preserve_interword_spaces=1", threshold),
        ("--psm 6 --oem 3 -c preserve_interword_spaces=1", adaptive),
        ("--psm 11 --oem 3 -c preserve_interword_spaces=1", adaptive),
    )
    for config, prepared_image in configs:
        try:
            text = clean_ocr_text(pytesseract.image_to_string(prepared_image, config=config))
        except Exception:
            text = ""
        if text and text not in variants:
            variants.append(text)
    return variants


def normalize_program_token(token: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_ ]", "", token or "").upper().replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if re.match(r"^P\d{5,}_", cleaned):
        cleaned = f"S{cleaned}"
    underscore_count = cleaned.count("_")
    if underscore_count == 2:
        parts = cleaned.split("_")
        if len(parts) == 3 and len(parts[2]) == 3:
            cleaned = f"{parts[0]}_{parts[1]}_{parts[2][:2]}_{parts[2][2:]}"
    return cleaned


def build_flexible_label_pattern(label: str) -> str:
    parts = [re.escape(part) for part in label.split()]
    return r"\W*".join(parts)


def extract_section_text(text: str, start_label: str, end_label: str | None = None) -> str:
    if not text:
        return ""

    pattern = build_flexible_label_pattern(start_label)
    if end_label:
        pattern += rf"(.*?){build_flexible_label_pattern(end_label)}"
    else:
        pattern += r"(.*)"

    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return ""

    return clean_ocr_text(match.group(1))


def extract_program_value(section: str) -> str:
    if not section:
        return ""

    patterns = (
        r"(?:SP)?\d{4,}[A-Z0-9_ ]{2,}",
        r"[A-Z]{2,}(?:_[A-Z0-9 ]+)+",
    )
    for pattern in patterns:
        matches = re.findall(pattern, section, re.IGNORECASE)
        for match in matches:
            token = normalize_program_token(match)
            if token and not token.startswith("N_") and "DIR" not in token:
                if "SP" in section.upper() and token[0].isdigit():
                    token = f"SP{token}"
                return token
    return ""


def extract_section_token(text: str, start_label: str, end_label: str | None = None) -> str:
    section = extract_section_text(text, start_label, end_label)
    if not section:
        return ""
    return extract_program_value(section)


PROGRAM_STATUS_ALIASES = {
    "Running": ("RUNNING", "RUNING", "RUNNING", "RURNING", "RUNN1NG"),
    "Ready": ("READY", "RDY"),
    "Stopped": ("STOPPED", "STOPPEDD", "STOP", "ST0PPED"),
    "Hold": ("HOLD", "PAUSE", "PAUSED"),
    "Error": ("ERROR", "ERR"),
}


def canonicalize_program_status(token: str) -> str:
    normalized = re.sub(r"[^A-Z]", "", (token or "").upper())
    if not normalized:
        return ""

    for label, aliases in PROGRAM_STATUS_ALIASES.items():
        if normalized in aliases:
            return label

    best_label = ""
    best_score = 0.0
    for label, aliases in PROGRAM_STATUS_ALIASES.items():
        for alias in aliases:
            score = SequenceMatcher(None, normalized, alias).ratio()
            if score > best_score:
                best_score = score
                best_label = label

    if best_score >= 0.72 and len(normalized) >= 3:
        return best_label
    return ""


def extract_program_status(*texts: str) -> str:
    candidate_tokens: list[str] = []
    for text in texts:
        if not text:
            continue

        match = re.search(r"Program\s*status\s*([A-Za-z]+)", text, re.IGNORECASE)
        if match:
            candidate_tokens.append(match.group(1))

        section = extract_section_text(text, "Program status")
        if section:
            candidate_tokens.extend(re.findall(r"[A-Za-z]+", section))

        candidate_tokens.extend(re.findall(r"[A-Za-z]+", text))

    for token in candidate_tokens:
        status = canonicalize_program_status(token)
        if status:
            return status

    return ""


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


def detect_laser_warning(image) -> str:
    if not OCR_AVAILABLE or image is None:
        return ""

    x, y, width, height = LASER_OCR_ZONES["top_banner"]
    crop = image[y : y + height, x : x + width]
    if crop.size == 0:
        return ""

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yellow_mask = cv2.inRange(hsv, np.array([10, 70, 120]), np.array([40, 255, 255]))
    yellow_ratio = float(cv2.countNonZero(yellow_mask)) / float(yellow_mask.size)
    if yellow_ratio < 0.08:
        return ""

    warning_text = read_ocr_block(image, LASER_OCR_ZONES["top_banner"], psm=6)
    if not warning_text:
        return ""

    normalized_warning = clean_ocr_text(warning_text)
    if len(normalized_warning) < 6:
        return ""
    return normalized_warning


def get_abkant_pg_connection_settings() -> dict[str, str | int]:
    return {
        "host": os.getenv("ABKANT_PG_HOST", "192.168.2.130"),
        "database": os.getenv("ABKANT_PG_DATABASE", "oee_helpan"),
        "user": os.getenv("ABKANT_PG_USER", "postgres"),
        "password": os.getenv("ABKANT_PG_PASSWORD", "postgres"),
        "timeout": int(os.getenv("ABKANT_PG_TIMEOUT", "5")),
    }


def get_abkant_pg_connection():
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is not installed in this environment.")

    settings = get_abkant_pg_connection_settings()
    return psycopg2.connect(
        host=settings["host"],
        database=settings["database"],
        user=settings["user"],
        password=settings["password"],
        connect_timeout=settings["timeout"],
    )


def normalize_abkant_tool_value(value: str | None) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().upper())
    normalized = re.sub(r"[^A-Z0-9/_\\\- ]", "", normalized)
    if normalized in {"", "NECITIT", "N/A", "NA"}:
        return ""
    return normalized


def build_abkant_tool_signature_from_values(upper_tool: str | None, lower_tool: str | None) -> str:
    normalized_upper = normalize_abkant_tool_value(upper_tool)
    normalized_lower = normalize_abkant_tool_value(lower_tool)
    if not normalized_upper and not normalized_lower:
        return ""
    return f"{normalized_upper} || {normalized_lower}"


def resolve_abkant_tool_signature(snapshot: dict | None) -> str:
    if not snapshot:
        return ""
    signature = normalize_context_token(snapshot.get("setup_signature"))
    if signature:
        return signature
    return build_abkant_tool_signature_from_values(
        snapshot.get("upper_tool"),
        snapshot.get("lower_tool"),
    )


def abkant_tool_signature_changed(previous_snapshot: dict | None, current_snapshot: dict | None) -> bool:
    previous_signature = resolve_abkant_tool_signature(previous_snapshot)
    current_signature = resolve_abkant_tool_signature(current_snapshot)
    return bool(previous_signature and current_signature and previous_signature != current_signature)


def fetch_abkant_latest_report_row(cursor):
    try:
        cursor.execute(
            """
            SELECT datacolectare, programidentificat, numar_bucati, faraschimbare, nr_bucati, upper_tool, lower_tool
            FROM raportare_abkant
            ORDER BY id DESC
            LIMIT 1
            """
        )
        return cursor.fetchone()
    except Exception:
        cursor.execute(
            """
            SELECT datacolectare, programidentificat, numar_bucati, faraschimbare, nr_bucati
            FROM raportare_abkant
            ORDER BY id DESC
            LIMIT 1
            """
        )
        return cursor.fetchone()


def fetch_abkant_postgres_snapshot() -> dict | None:
    try:
        with get_abkant_pg_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT status
                FROM parameters
                WHERE lower(parametru) = 'rpiabkantworking'
                LIMIT 1
                """
            )
            parameter_row = cursor.fetchone()

            latest_row = fetch_abkant_latest_report_row(cursor)

            cursor.execute(
                """
                SELECT datacolectare
                FROM raportare_abkant
                WHERE faraschimbare = TRUE
                ORDER BY id DESC
                LIMIT 1
                """
            )
            last_changed_row = cursor.fetchone()
            cursor.close()
    except Exception as exc:
        return {
            "available": False,
            "connected": False,
            "source": "postgres-fallback",
            "message": f"Fallback Abkant PostgreSQL indisponibil: {exc}",
        }

    active_program = (latest_row[1] or "").strip() if latest_row else ""
    pieces_text = (latest_row[2] or "").strip() if latest_row and latest_row[2] is not None else ""
    produced_pieces_raw = latest_row[4] if latest_row and latest_row[4] is not None else None
    upper_tool = normalize_abkant_tool_value(latest_row[5] if latest_row and len(latest_row) > 5 else None)
    lower_tool = normalize_abkant_tool_value(latest_row[6] if latest_row and len(latest_row) > 6 else None)
    setup_signature = build_abkant_tool_signature_from_values(upper_tool, lower_tool)
    collected_at_dt = latest_row[0] if latest_row and latest_row[0] else None
    collected_at = collected_at_dt.isoformat(sep=" ", timespec="seconds") if collected_at_dt else None
    snapshot_age_seconds = (
        max(
            int(
                (
                    now_local() - collected_at_dt.replace(tzinfo=None)
                    if getattr(collected_at_dt, "tzinfo", None)
                    else now_local() - collected_at_dt
                ).total_seconds()
            ),
            0,
        )
        if collected_at_dt
        else 0
    )
    has_recent_snapshot = bool(collected_at_dt and snapshot_age_seconds <= ABKANT_FEED_STALE_SECONDS)
    parameter_machine_on = bool(parameter_row[0]) if parameter_row is not None else False
    inferred_feed_activity = bool(has_recent_snapshot and (active_program or setup_signature or pieces_text))
    machine_on = bool(parameter_machine_on or inferred_feed_activity)
    last_changed_at = last_changed_row[0] if last_changed_row and last_changed_row[0] else (latest_row[0] if latest_row and latest_row[0] else None)
    stagnation_seconds = (
        max(int((now_local() - last_changed_at.replace(tzinfo=None) if getattr(last_changed_at, "tzinfo", None) else now_local() - last_changed_at).total_seconds()), 0)
        if last_changed_at
        else 0
    )

    produced_pieces = None
    if produced_pieces_raw is not None:
        try:
            produced_pieces = parse_optional_int(produced_pieces_raw)
        except (TypeError, ValueError):
            produced_pieces = None

    pieces_done_from_text = None
    total_pieces = None
    if "/" in pieces_text:
        first_part, second_part = pieces_text.split("/", 1)
        try:
            pieces_done_from_text = parse_optional_int(first_part)
        except (TypeError, ValueError):
            pieces_done_from_text = None
        try:
            total_pieces = parse_optional_int(second_part)
        except (TypeError, ValueError):
            total_pieces = None

    if produced_pieces is None:
        produced_pieces = pieces_done_from_text or 0

    pieces_label = pieces_text or (
        f"{produced_pieces}/{total_pieces}" if total_pieces is not None else str(produced_pieces)
    )
    pieces_done = pieces_done_from_text if pieces_done_from_text is not None else produced_pieces
    has_piece_counters = pieces_done is not None and total_pieces is not None
    legacy_setup_change = bool(
        machine_on
        and active_program
        and has_piece_counters
        and pieces_done == 0
        and total_pieces == 0
    )
    setup_change = legacy_setup_change
    idle = bool(
        machine_on
        and active_program
        and not setup_change
        and stagnation_seconds >= ABKANT_IDLE_STAGNATION_SECONDS
    )
    bending_active = bool(
        machine_on
        and active_program
        and (
            (has_piece_counters and not setup_change and not idle)
            or not has_piece_counters
        )
    )

    if not has_recent_snapshot:
        program_status = "Feed indisponibil"
    elif setup_change:
        program_status = "Setup change"
    elif idle:
        program_status = "Idle"
    elif bending_active:
        program_status = "Bending active"
    elif machine_on:
        program_status = "Pregatit"
    else:
        program_status = "Pregatit"

    return {
        "available": True,
        "connected": True,
        "source": "postgres-fallback",
        "endpoint": get_abkant_pg_connection_settings()["host"],
        "captured_at": now_local().isoformat(timespec="seconds"),
        "machine_mode": "abkant",
        "selected_program": active_program or "Necitit",
        "active_program": active_program or "Necitit",
        "material": pieces_label or "n/a",
        "program_status": program_status,
        "pieces_label": pieces_label or "n/a",
        "produced_pieces": produced_pieces,
        "total_pieces": total_pieces,
        "snapshot_age_seconds": snapshot_age_seconds,
        "stagnation_seconds": stagnation_seconds,
        "upper_tool": upper_tool or "n/a",
        "lower_tool": lower_tool or "n/a",
        "setup_signature": setup_signature or "",
        "derived_signals": {
            "machine_on": machine_on,
            "cutting_active": bending_active,
            "table_change": setup_change,
            "idle": idle,
        },
        "message": (
            f"Abkant citit din PostgreSQL. Ultima colectare: {collected_at or 'necunoscuta'}. "
            f"Program: {active_program or 'necunoscut'}. Piese: {pieces_label or 'n/a'}. "
            f"Upper: {upper_tool or 'n/a'}. Lower: {lower_tool or 'n/a'}. "
            f"Fara schimbare de {format_seconds(stagnation_seconds)}. "
            f"Varsta snapshot: {format_seconds(snapshot_age_seconds)}."
        ),
    }


def analyze_laser_live_snapshot(machine_key: str) -> dict | None:
    if machine_key == "laser2" and not machine_has_dedicated_live_source(machine_key):
        return {
            "available": True,
            "connected": False,
            "source": "not-configured",
            "endpoint": "",
            "selected_program": "n/a",
            "active_program": "n/a",
            "material": "n/a",
            "program_status": "Fara feed dedicat",
            "derived_signals": {
                "machine_on": False,
                "cutting_active": False,
                "table_change": False,
                "idle": False,
            },
            "message": "Laser2 nu are inca feed sau semnal dedicat, deci dashboardul il trateaza doar ca feed indisponibil pana il configuram separat.",
        }

    ocr_snapshot = get_laser_ocr_snapshot(machine_key)
    if not ocr_snapshot.get("available"):
        return ocr_snapshot

    selected_program = ocr_snapshot.get("selected_program") or "Necitit"
    active_program = ocr_snapshot.get("active_program") or "Necitit"
    material = ocr_snapshot.get("material") or "Necitit"
    program_status = ocr_snapshot.get("program_status") or "Necitit"
    warning_message = ocr_snapshot.get("warning_message")
    normalized_status = str(program_status).upper()
    normalized_active_program = str(active_program).upper()
    machine_on = bool(selected_program or active_program or program_status or material)
    table_change = "SHEET_LOAD" in normalized_active_program or "LOAD_SHEET" in normalized_active_program
    cutting_active = machine_on and normalized_status == "RUNNING" and not table_change
    idle = machine_on and not cutting_active and not table_change

    return {
        **ocr_snapshot,
        "source": "live-ocr",
        "derived_signals": {
            "machine_on": machine_on,
            "cutting_active": cutting_active,
            "table_change": table_change,
            "idle": idle,
        },
        "message": (
            "Stare derivata din OCR pe panourile din stanga si dreapta ale ecranului laser."
            + (f" Banner galben detectat: {warning_message}." if warning_message else "")
        ),
    }


def analyze_abkant_live_snapshot(machine_key: str) -> dict | None:
    postgres_snapshot = fetch_abkant_postgres_snapshot()
    if postgres_snapshot and postgres_snapshot.get("available"):
        return postgres_snapshot

    endpoint = resolve_real_data_endpoint(machine_key)
    reachable = bool(endpoint)
    postgres_reason = (postgres_snapshot or {}).get("message")
    base_message = (
        "Abkant foloseste momentan feedul din script, dar nu avem inca un semnal live separat pentru utilaj; aici tratam doar disponibilitatea feedului."
        if reachable
        else "Feedul abkant nu este accesibil din dashboard."
    )
    if postgres_reason:
        base_message = f"{base_message} Diagnostic PostgreSQL: {postgres_reason}"
    return {
        "available": reachable,
        "connected": reachable,
        "source": "feed-script",
        "endpoint": endpoint,
        "captured_at": now_local().isoformat(timespec="seconds"),
        "machine_mode": "abkant",
        "selected_program": "n/a",
        "active_program": "Abkant/ProgramActiv",
        "material": "n/a",
        "program_status": "Program identificat din script",
        "pieces_label": "n/a",
        "produced_pieces": 0,
        "total_pieces": None,
        "upper_tool": "n/a",
        "lower_tool": "n/a",
        "setup_signature": "",
        "derived_signals": {
            "machine_on": False,
            "cutting_active": False,
            "table_change": False,
            "idle": False,
        },
        "message": base_message,
    }


def get_live_machine_snapshot(machine_key: str) -> dict | None:
    if machine_key == "laser1modbus":
        return analyze_laser_modbus_live_snapshot(machine_key)
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
            "machine_key": "laser1modbus",
            "label": MACHINE_DEFINITIONS["laser1modbus"]["label"],
            "workcenter_id": parse_optional_int(os.getenv("PONTAJ_LASER1MODBUS_WORKCENTER_ID", laser_default)),
            "sort_order": 2,
        },
        {
            "machine_key": "laser2",
            "label": MACHINE_DEFINITIONS["laser2"]["label"],
            "workcenter_id": parse_optional_int(os.getenv("PONTAJ_LASER2_WORKCENTER_ID", laser_default)),
            "sort_order": 3,
        },
        {
            "machine_key": "abkant",
            "label": MACHINE_DEFINITIONS["abkant"]["label"],
            "workcenter_id": parse_optional_int(os.getenv("PONTAJ_ABKANT_WORKCENTER_ID", "2")),
            "sort_order": 4,
        },
    ]


def get_default_machine_modbus_configs() -> list[dict]:
    serial_port = get_machine_env_value("laser1modbus", "MODBUS_SERIAL_PORT")
    host = get_machine_env_value("laser1modbus", "MODBUS_HOST")
    config = {
        "machine_key": "laser1modbus",
        "transport": normalize_modbus_transport(
            get_machine_env_value("laser1modbus", "MODBUS_TRANSPORT"),
            fallback="rtu" if serial_port and not host else "tcp",
        ),
        "host": host,
        "port": parse_optional_int(get_machine_env_value("laser1modbus", "MODBUS_PORT")) or 502,
        "serial_port": serial_port,
        "serial_baudrate": parse_optional_int(get_machine_env_value("laser1modbus", "MODBUS_SERIAL_BAUDRATE")) or 9600,
        "serial_parity": normalize_modbus_serial_parity(
            get_machine_env_value("laser1modbus", "MODBUS_SERIAL_PARITY") or "N"
        ),
        "serial_stopbits": parse_optional_int(get_machine_env_value("laser1modbus", "MODBUS_SERIAL_STOPBITS")) or 1,
        "unit_id": parse_optional_int(get_machine_env_value("laser1modbus", "MODBUS_UNIT_ID")) or 1,
        "bit_source": (get_machine_env_value("laser1modbus", "MODBUS_BIT_SOURCE") or "discrete_input").strip().lower(),
        "start_address": parse_optional_int(get_machine_env_value("laser1modbus", "MODBUS_START_ADDRESS")) or 0,
        "poll_timeout_seconds": parse_optional_float(get_machine_env_value("laser1modbus", "MODBUS_TIMEOUT_SECONDS")) or 1.5,
        "in1_signal": (get_machine_env_value("laser1modbus", "MODBUS_IN1_SIGNAL") or "machine_on").strip().lower(),
        "in2_signal": (get_machine_env_value("laser1modbus", "MODBUS_IN2_SIGNAL") or "table_change").strip().lower(),
        "in3_signal": (get_machine_env_value("laser1modbus", "MODBUS_IN3_SIGNAL") or "cutting_active").strip().lower(),
        "in4_signal": (get_machine_env_value("laser1modbus", "MODBUS_IN4_SIGNAL") or "idle_abort").strip().lower(),
    }
    locked_config = LOCKED_MACHINE_MODBUS_CONFIGS.get("laser1modbus")
    if locked_config:
        config.update(locked_config)
    return [config]


def get_modbus_signal_target_options() -> list[dict[str, str]]:
    return [
        {"value": "unused", "label": "Neutilizat"},
        {"value": "machine_on", "label": "Machine ON"},
        {"value": "cutting_active", "label": "Cutting"},
        {"value": "table_change", "label": "Table change"},
        {"value": "idle_abort", "label": "Idle / Aborted"},
    ]


def get_modbus_transport_options() -> list[dict[str, str]]:
    return [
        {"value": "tcp", "label": "Modbus TCP"},
        {"value": "rtu", "label": "Modbus RTU / RS485"},
    ]


def get_modbus_serial_parity_options() -> list[dict[str, str]]:
    return [
        {"value": "N", "label": "None"},
        {"value": "E", "label": "Even"},
        {"value": "O", "label": "Odd"},
    ]


def get_modbus_serial_stopbits_options() -> list[dict[str, str]]:
    return [
        {"value": "1", "label": "1"},
        {"value": "2", "label": "2"},
    ]


def build_modbus_endpoint(config: dict | sqlite3.Row) -> str:
    transport = normalize_modbus_transport(config["transport"] if "transport" in config.keys() else None)
    unit_id = int(config["unit_id"] or 1)
    if transport == "rtu":
        serial_port = (config["serial_port"] or "").strip()
        if not serial_port:
            return ""
        baudrate = int(config["serial_baudrate"] or 9600)
        parity = normalize_modbus_serial_parity(config["serial_parity"] or "N")
        stopbits = int(config["serial_stopbits"] or 1)
        return f"{serial_port} / {baudrate} 8{parity}{stopbits} / unit {unit_id}"

    host = (config["host"] or "").strip()
    if not host:
        return ""
    return f"{host}:{int(config['port'] or 502)}"


def build_modbus_signal_map(config: dict | sqlite3.Row | None) -> dict[str, str]:
    if not config:
        return {}
    return {
        "in1": (config["in1_signal"] or "unused").strip().lower(),
        "in2": (config["in2_signal"] or "unused").strip().lower(),
        "in3": (config["in3_signal"] or "unused").strip().lower(),
        "in4": (config["in4_signal"] or "unused").strip().lower(),
    }


def serialize_machine_modbus_config(row: sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None

    row_data = row
    machine_key = row_data["machine_key"] if "machine_key" in row_data.keys() else ""
    locked_config = LOCKED_MACHINE_MODBUS_CONFIGS.get(machine_key)
    if locked_config:
        normalized = {key: row_data[key] for key in row_data.keys()}
        normalized.update({key: value for key, value in locked_config.items() if key != "machine_key"})
        row_data = normalized

    signal_map = build_modbus_signal_map(row_data)
    transport = normalize_modbus_transport(row_data["transport"] if "transport" in row_data.keys() else None)
    serial_parity = normalize_modbus_serial_parity(row_data["serial_parity"] if "serial_parity" in row_data.keys() else "N")
    serial_stopbits = int(row_data["serial_stopbits"] or 1) if "serial_stopbits" in row_data.keys() else 1
    enabled = bool((row_data["serial_port"] or "").strip()) if transport == "rtu" else bool((row_data["host"] or "").strip())
    return {
        "machine_key": row_data["machine_key"],
        "transport": transport,
        "transport_options": get_modbus_transport_options(),
        "host": (row_data["host"] or "").strip(),
        "port": int(row_data["port"] or 502),
        "serial_port": (row_data["serial_port"] or "").strip() if "serial_port" in row_data.keys() else "",
        "serial_baudrate": int(row_data["serial_baudrate"] or 9600) if "serial_baudrate" in row_data.keys() else 9600,
        "serial_parity": serial_parity,
        "serial_parity_options": get_modbus_serial_parity_options(),
        "serial_stopbits": serial_stopbits,
        "serial_stopbits_options": get_modbus_serial_stopbits_options(),
        "unit_id": int(row_data["unit_id"] or 1),
        "bit_source": (row_data["bit_source"] or "discrete_input").strip().lower(),
        "start_address": int(row_data["start_address"] or 0),
        "poll_timeout_seconds": float(row_data["poll_timeout_seconds"] or 1.5),
        "signal_map": signal_map,
        "inputs": [
            {"key": "in1", "label": "IN1", "signal": signal_map["in1"]},
            {"key": "in2", "label": "IN2", "signal": signal_map["in2"]},
            {"key": "in3", "label": "IN3", "signal": signal_map["in3"]},
            {"key": "in4", "label": "IN4", "signal": signal_map["in4"]},
        ],
        "signal_options": get_modbus_signal_target_options(),
        "enabled": enabled,
        "endpoint": build_modbus_endpoint(row_data),
    }


def get_machine_modbus_config(machine_key: str) -> dict:
    machine_key = ensure_machine_key(machine_key)
    if not machine_uses_modbus(machine_key):
        raise ValueError(f"Machine does not use Modbus: {machine_key}")

    with get_sqlite_connection() as connection:
        row = connection.execute(
            """
            SELECT machine_key, transport, host, port, serial_port, serial_baudrate, serial_parity, serial_stopbits,
                   unit_id, bit_source, start_address, poll_timeout_seconds,
                   in1_signal, in2_signal, in3_signal, in4_signal, updated_at
            FROM machine_modbus_configs
            WHERE machine_key = ?
            """,
            (machine_key,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Machine Modbus config not found: {machine_key}")
    return serialize_machine_modbus_config(row)


def merge_machine_modbus_config_payload(machine_key: str, data: dict | None) -> dict:
    payload = dict(data or {})

    try:
        existing = get_machine_modbus_config(machine_key)
    except ValueError:
        return payload

    existing_signal_map = existing.get("signal_map") or {}
    merged = {
        "transport": existing.get("transport"),
        "host": existing.get("host"),
        "port": existing.get("port"),
        "serial_port": existing.get("serial_port"),
        "serial_baudrate": existing.get("serial_baudrate"),
        "serial_parity": existing.get("serial_parity"),
        "serial_stopbits": existing.get("serial_stopbits"),
        "unit_id": existing.get("unit_id"),
        "bit_source": existing.get("bit_source"),
        "start_address": existing.get("start_address"),
        "poll_timeout_seconds": existing.get("poll_timeout_seconds"),
        "in1_signal": existing_signal_map.get("in1", "unused"),
        "in2_signal": existing_signal_map.get("in2", "unused"),
        "in3_signal": existing_signal_map.get("in3", "unused"),
        "in4_signal": existing_signal_map.get("in4", "unused"),
    }

    signal_map_payload = payload.get("signal_map")
    if isinstance(signal_map_payload, dict):
        for input_key in ("in1", "in2", "in3", "in4"):
            mapped_value = signal_map_payload.get(input_key)
            if mapped_value is None:
                continue
            mapped_text = str(mapped_value).strip()
            if mapped_text:
                merged[f"{input_key}_signal"] = mapped_text

    for item in payload.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        input_key = (item.get("key") or "").strip().lower()
        signal_value = (item.get("signal") or "").strip()
        if input_key in {"in1", "in2", "in3", "in4"} and signal_value:
            merged[f"{input_key}_signal"] = signal_value

    for key, value in payload.items():
        if key in {"signal_map", "inputs"}:
            continue
        if value is None:
            continue
        if key in {"host", "serial_port"} and isinstance(value, str) and not value.strip():
            continue
        merged[key] = value

    return merged


def validate_machine_modbus_config(machine_key: str, data: dict) -> dict:
    if not machine_uses_modbus(machine_key):
        raise ValueError(f"Machine does not use Modbus: {machine_key}")

    transport = normalize_modbus_transport(data.get("transport"))
    host = (data.get("host") or "").strip()
    port = parse_optional_int(data.get("port"))
    serial_port = (data.get("serial_port") or "").strip()
    serial_baudrate = parse_optional_int(data.get("serial_baudrate"))
    serial_parity = normalize_modbus_serial_parity(data.get("serial_parity") or "N")
    serial_stopbits = parse_optional_int(data.get("serial_stopbits"))
    unit_id = parse_optional_int(data.get("unit_id"))
    start_address = parse_optional_int(data.get("start_address"))
    poll_timeout_seconds = parse_optional_float(data.get("poll_timeout_seconds"))
    bit_source = (data.get("bit_source") or "discrete_input").strip().lower()
    if bit_source not in {"discrete_input", "coil"}:
        raise ValueError("Tipul de bit Modbus trebuie sa fie discrete_input sau coil.")

    if transport == "tcp":
        if port is None or port < 1 or port > 65535:
            raise ValueError("Portul Modbus trebuie sa fie intre 1 si 65535.")
    else:
        if serial_baudrate is None or serial_baudrate < 300 or serial_baudrate > 1000000:
            raise ValueError("Baud rate-ul Modbus RTU trebuie sa fie intre 300 si 1000000.")
        if serial_stopbits not in MODBUS_SERIAL_STOPBITS_CHOICES:
            raise ValueError("Stop bits pentru Modbus RTU trebuie sa fie 1 sau 2.")
    if unit_id is None or unit_id < 0 or unit_id > 255:
        raise ValueError("Unit ID-ul Modbus trebuie sa fie intre 0 si 255.")
    if start_address is None or start_address < 0:
        raise ValueError("Adresa de start Modbus trebuie sa fie 0 sau mai mare.")
    if poll_timeout_seconds is None or poll_timeout_seconds <= 0 or poll_timeout_seconds > 30:
        raise ValueError("Timeout-ul Modbus trebuie sa fie intre 0 si 30 secunde.")

    signal_map = {
        "in1_signal": (data.get("in1_signal") or "unused").strip().lower(),
        "in2_signal": (data.get("in2_signal") or "unused").strip().lower(),
        "in3_signal": (data.get("in3_signal") or "unused").strip().lower(),
        "in4_signal": (data.get("in4_signal") or "unused").strip().lower(),
    }
    seen_targets: set[str] = set()
    for field_name, target in signal_map.items():
        if target not in MODBUS_SIGNAL_TARGET_CHOICES:
            raise ValueError(f"Maparea {field_name} nu este suportata: {target}")
        if target != "unused":
            if target in seen_targets:
                raise ValueError("Fiecare semnal Modbus poate fi asignat o singura data.")
            seen_targets.add(target)

    return {
        "machine_key": machine_key,
        "transport": transport,
        "host": host,
        "port": port or 502,
        "serial_port": serial_port,
        "serial_baudrate": serial_baudrate or 9600,
        "serial_parity": serial_parity,
        "serial_stopbits": serial_stopbits or 1,
        "unit_id": unit_id,
        "bit_source": bit_source,
        "start_address": start_address,
        "poll_timeout_seconds": poll_timeout_seconds,
        **signal_map,
    }


def update_machine_modbus_config(machine_key: str, data: dict) -> dict:
    merged_payload = merge_machine_modbus_config_payload(machine_key, data)
    locked_config = LOCKED_MACHINE_MODBUS_CONFIGS.get(machine_key)
    if locked_config:
        merged_payload.update(locked_config)
    config = validate_machine_modbus_config(machine_key, merged_payload)
    updated_at = now_local().isoformat(timespec="seconds")
    with get_sqlite_connection() as connection:
        connection.execute(
            """
            INSERT INTO machine_modbus_configs (
                machine_key, transport, host, port, serial_port, serial_baudrate, serial_parity, serial_stopbits,
                unit_id, bit_source, start_address, poll_timeout_seconds,
                in1_signal, in2_signal, in3_signal, in4_signal, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(machine_key) DO UPDATE SET
                transport = excluded.transport,
                host = excluded.host,
                port = excluded.port,
                serial_port = excluded.serial_port,
                serial_baudrate = excluded.serial_baudrate,
                serial_parity = excluded.serial_parity,
                serial_stopbits = excluded.serial_stopbits,
                unit_id = excluded.unit_id,
                bit_source = excluded.bit_source,
                start_address = excluded.start_address,
                poll_timeout_seconds = excluded.poll_timeout_seconds,
                in1_signal = excluded.in1_signal,
                in2_signal = excluded.in2_signal,
                in3_signal = excluded.in3_signal,
                in4_signal = excluded.in4_signal,
                updated_at = excluded.updated_at
            """,
            (
                config["machine_key"],
                config["transport"],
                config["host"],
                config["port"],
                config["serial_port"],
                config["serial_baudrate"],
                config["serial_parity"],
                config["serial_stopbits"],
                config["unit_id"],
                config["bit_source"],
                config["start_address"],
                config["poll_timeout_seconds"],
                config["in1_signal"],
                config["in2_signal"],
                config["in3_signal"],
                config["in4_signal"],
                updated_at,
            ),
        )
        connection.commit()
    return get_machine_modbus_config(machine_key)


def serialize_machine_profile(row: sqlite3.Row, selected_machine_key: str | None = None) -> dict:
    definition = MACHINE_DEFINITIONS[row["machine_key"]]
    modbus_config = None
    if row["machine_key"] in MODBUS_MACHINE_KEYS:
        with get_sqlite_connection() as connection:
            modbus_row = connection.execute(
                """
                SELECT machine_key, transport, host, port, serial_port, serial_baudrate, serial_parity, serial_stopbits,
                       unit_id, bit_source, start_address, poll_timeout_seconds,
                       in1_signal, in2_signal, in3_signal, in4_signal, updated_at
                FROM machine_modbus_configs
                WHERE machine_key = ?
                """,
                (row["machine_key"],),
            ).fetchone()
        modbus_config = serialize_machine_modbus_config(modbus_row)
    return {
        "key": row["machine_key"],
        "label": row["label"] or definition["label"],
        "description": definition["description"],
        "accent": definition["accent"],
        "workcenter_id": row["workcenter_id"],
        "updated_at": row["updated_at"],
        "is_selected": row["machine_key"] == selected_machine_key,
        "modbus_config": modbus_config,
    }


def init_db() -> None:
    migrate_legacy_sqlite_if_needed()
    print(f"SQLite storage path: {SQLITE_PATH}")
    if is_running_in_container() and str(SQLITE_PATH).startswith("/data/"):
        try:
            if not Path("/data").is_mount():
                print(
                    "WARNING: /data nu este montat ca volum persistent. "
                    "Datele (randamente, setari MODBUS, istoric) se pot pierde la update/redeploy."
                )
        except OSError:
            pass
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
                upper_tool TEXT,
                lower_tool TEXT,
                setup_signature TEXT,
                setup_changed INTEGER NOT NULL DEFAULT 0,
                cutting_started_at TEXT,
                table_change_started_at TEXT NOT NULL,
                table_change_ended_at TEXT,
                table_change_duration_seconds INTEGER,
                cycle_duration_seconds INTEGER,
                machine_on_duration_seconds INTEGER,
                idle_duration_seconds INTEGER,
                efficiency_percent REAL,
                source TEXT NOT NULL DEFAULT 'live-ocr',
                snapshot_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS machine_runtime (
                machine_key TEXT PRIMARY KEY,
                last_snapshot_json TEXT,
                pending_cycle_json TEXT,
                stats_anchor_started_at TEXT,
                stats_anchor_context_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS machine_modbus_configs (
                machine_key TEXT PRIMARY KEY,
                transport TEXT NOT NULL DEFAULT 'tcp',
                host TEXT NOT NULL DEFAULT '',
                port INTEGER NOT NULL DEFAULT 502,
                serial_port TEXT NOT NULL DEFAULT '',
                serial_baudrate INTEGER NOT NULL DEFAULT 9600,
                serial_parity TEXT NOT NULL DEFAULT 'N',
                serial_stopbits INTEGER NOT NULL DEFAULT 1,
                unit_id INTEGER NOT NULL DEFAULT 1,
                bit_source TEXT NOT NULL DEFAULT 'discrete_input',
                start_address INTEGER NOT NULL DEFAULT 0,
                poll_timeout_seconds REAL NOT NULL DEFAULT 1.5,
                in1_signal TEXT NOT NULL DEFAULT 'machine_on',
                in2_signal TEXT NOT NULL DEFAULT 'table_change',
                in3_signal TEXT NOT NULL DEFAULT 'cutting_active',
                in4_signal TEXT NOT NULL DEFAULT 'idle_abort',
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
        if "upper_tool" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN upper_tool TEXT")
        if "lower_tool" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN lower_tool TEXT")
        if "setup_signature" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN setup_signature TEXT")
        if "setup_changed" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN setup_changed INTEGER NOT NULL DEFAULT 0")
        if "machine_on_duration_seconds" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN machine_on_duration_seconds INTEGER")
        if "idle_duration_seconds" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN idle_duration_seconds INTEGER")
        if "efficiency_percent" not in saved_cycle_columns:
            connection.execute("ALTER TABLE saved_cycles ADD COLUMN efficiency_percent REAL")

        machine_runtime_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(machine_runtime)").fetchall()
        }
        if "stats_anchor_started_at" not in machine_runtime_columns:
            connection.execute("ALTER TABLE machine_runtime ADD COLUMN stats_anchor_started_at TEXT")
        if "stats_anchor_context_json" not in machine_runtime_columns:
            connection.execute("ALTER TABLE machine_runtime ADD COLUMN stats_anchor_context_json TEXT")

        machine_modbus_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(machine_modbus_configs)").fetchall()
        }
        if "transport" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN transport TEXT NOT NULL DEFAULT 'tcp'")
        if "serial_port" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN serial_port TEXT NOT NULL DEFAULT ''")
        if "serial_baudrate" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN serial_baudrate INTEGER NOT NULL DEFAULT 9600")
        if "serial_parity" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN serial_parity TEXT NOT NULL DEFAULT 'N'")
        if "serial_stopbits" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN serial_stopbits INTEGER NOT NULL DEFAULT 1")
        if "poll_timeout_seconds" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN poll_timeout_seconds REAL NOT NULL DEFAULT 1.5")
        if "in1_signal" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN in1_signal TEXT NOT NULL DEFAULT 'machine_on'")
        if "in2_signal" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN in2_signal TEXT NOT NULL DEFAULT 'table_change'")
        if "in3_signal" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN in3_signal TEXT NOT NULL DEFAULT 'cutting_active'")
        if "in4_signal" not in machine_modbus_columns:
            connection.execute("ALTER TABLE machine_modbus_configs ADD COLUMN in4_signal TEXT NOT NULL DEFAULT 'idle_abort'")

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
                    machine_key, last_snapshot_json, pending_cycle_json, stats_anchor_started_at, stats_anchor_context_json, updated_at
                )
                VALUES (?, NULL, NULL, NULL, NULL, ?)
                """,
                (
                    profile["machine_key"],
                    updated_at,
                ),
            )

        for config in get_default_machine_modbus_configs():
            connection.execute(
                """
                INSERT OR IGNORE INTO machine_modbus_configs (
                    machine_key, transport, host, port, serial_port, serial_baudrate, serial_parity, serial_stopbits,
                    unit_id, bit_source, start_address, poll_timeout_seconds,
                    in1_signal, in2_signal, in3_signal, in4_signal, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config["machine_key"],
                    config["transport"],
                    config["host"],
                    config["port"],
                    config["serial_port"],
                    config["serial_baudrate"],
                    config["serial_parity"],
                    config["serial_stopbits"],
                    config["unit_id"],
                    config["bit_source"],
                    config["start_address"],
                    config["poll_timeout_seconds"],
                    config["in1_signal"],
                    config["in2_signal"],
                    config["in3_signal"],
                    config["in4_signal"],
                    updated_at,
                ),
            )
            connection.execute(
                """
                UPDATE machine_modbus_configs
                SET transport = COALESCE(NULLIF(transport, ''), ?),
                    host = COALESCE(NULLIF(host, ''), ?),
                    port = COALESCE(port, ?),
                    serial_port = COALESCE(NULLIF(serial_port, ''), ?),
                    serial_baudrate = COALESCE(serial_baudrate, ?),
                    serial_parity = COALESCE(NULLIF(serial_parity, ''), ?),
                    serial_stopbits = COALESCE(serial_stopbits, ?),
                    unit_id = COALESCE(unit_id, ?),
                    bit_source = COALESCE(NULLIF(bit_source, ''), ?),
                    start_address = COALESCE(start_address, ?),
                    poll_timeout_seconds = COALESCE(poll_timeout_seconds, ?),
                    in1_signal = COALESCE(NULLIF(in1_signal, ''), ?),
                    in2_signal = COALESCE(NULLIF(in2_signal, ''), ?),
                    in3_signal = COALESCE(NULLIF(in3_signal, ''), ?),
                    in4_signal = COALESCE(NULLIF(in4_signal, ''), ?)
                WHERE machine_key = ?
                """,
                (
                    config["transport"],
                    config["host"],
                    config["port"],
                    config["serial_port"],
                    config["serial_baudrate"],
                    config["serial_parity"],
                    config["serial_stopbits"],
                    config["unit_id"],
                    config["bit_source"],
                    config["start_address"],
                    config["poll_timeout_seconds"],
                    config["in1_signal"],
                    config["in2_signal"],
                    config["in3_signal"],
                    config["in4_signal"],
                    config["machine_key"],
                ),
            )

        for machine_key, config in LOCKED_MACHINE_MODBUS_CONFIGS.items():
            connection.execute(
                """
                UPDATE machine_modbus_configs
                SET transport = ?,
                    host = ?,
                    port = ?,
                    serial_port = ?,
                    serial_baudrate = ?,
                    serial_parity = ?,
                    serial_stopbits = ?,
                    unit_id = ?,
                    bit_source = ?,
                    start_address = ?,
                    poll_timeout_seconds = ?,
                    in1_signal = ?,
                    in2_signal = ?,
                    in3_signal = ?,
                    in4_signal = ?,
                    updated_at = ?
                WHERE machine_key = ?
                """,
                (
                    config["transport"],
                    config["host"],
                    config["port"],
                    config["serial_port"],
                    config["serial_baudrate"],
                    config["serial_parity"],
                    config["serial_stopbits"],
                    config["unit_id"],
                    config["bit_source"],
                    config["start_address"],
                    config["poll_timeout_seconds"],
                    config["in1_signal"],
                    config["in2_signal"],
                    config["in3_signal"],
                    config["in4_signal"],
                    updated_at,
                    machine_key,
                ),
            )

        connection.commit()
    backfill_saved_cycle_metrics()


def get_machine_profiles() -> list[dict]:
    with get_sqlite_connection() as connection:
        rows = connection.execute(
            """
            SELECT machine_key, label, workcenter_id, sort_order, updated_at
            FROM machine_profiles
            ORDER BY sort_order ASC, machine_key ASC
            """
        ).fetchall()
    return [
        serialize_machine_profile(row)
        for row in rows
        if row["machine_key"] not in HIDDEN_MACHINE_KEYS
    ]


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
    dedicated_live_source = machine_has_dedicated_live_source(machine_profile["key"])

    endpoint = resolve_real_data_endpoint(machine_profile["key"])
    name = resolve_real_data_name(machine_profile["key"])
    details = list(feed["details"])
    resolved_endpoint = endpoint or "Fara endpoint dedicat"
    message = ""
    if machine_uses_modbus(machine_profile["key"]):
        modbus_config = get_machine_modbus_config(machine_profile["key"])
        transport_label = "Modbus RTU" if modbus_config["transport"] == "rtu" else "Modbus TCP"
        details.extend(
            [
                f"{transport_label}: {modbus_config['endpoint'] or 'neconfigurat'}",
                f"Tip biti: {modbus_config['bit_source']}",
                f"Adresa start: {modbus_config['start_address']}",
                "Mapare: "
                + ", ".join(
                    f"{item['label']} -> {item['signal']}"
                    for item in modbus_config["inputs"]
                ),
            ]
        )
        resolved_endpoint = modbus_config["endpoint"] or resolved_endpoint
        message = (
            f"{machine_profile['label']} citeste timpii din Modbus, iar programul din feedul Laser1."
            if dedicated_live_source
            else "Sursa Modbus nu este configurata complet. Seteaza hostul/portul TCP sau portul serial RTU si maparea intrarilor."
        )
    status = "configured" if script_exists and dedicated_live_source else "pending"
    return {
        "name": name,
        "endpoint": resolved_endpoint,
        "status": status,
        "transport": feed["transport"],
        "script_name": script_name,
        "details": details,
        "message": message
        or (
            f"Sursa reala pentru {machine_profile['label']} a fost identificata din fisierul {script_name}."
            if script_exists and dedicated_live_source
            else (
                f"{machine_profile['label']} nu are inca feed live dedicat, asa ca dashboardul nu il mai considera activ automat."
                if machine_profile["key"] == "laser2"
                else "Sursa reala nu este inca pregatita complet. Butoanele manuale raman pentru test."
            )
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


def _read_modbus_tcp_bits_once(
    host: str,
    port: int,
    unit_id: int,
    start_address: int,
    count: int,
    bit_source: str = "discrete_input",
    timeout_seconds: float = 1.5,
) -> list[bool]:
    function_code = 2 if bit_source == "discrete_input" else 1
    transaction_id = int(time_module.time() * 1000) & 0xFFFF
    protocol_id = 0
    pdu = bytes(
        [
            function_code,
            (start_address >> 8) & 0xFF,
            start_address & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
        ]
    )
    frame = (
        transaction_id.to_bytes(2, "big")
        + protocol_id.to_bytes(2, "big")
        + (len(pdu) + 1).to_bytes(2, "big")
        + bytes([unit_id & 0xFF])
        + pdu
    )

    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.settimeout(timeout_seconds)
        sock.sendall(frame)
        response_header = sock.recv(7)
        if len(response_header) < 7:
            raise RuntimeError("Raspuns Modbus incomplet la nivel MBAP.")
        response_length = int.from_bytes(response_header[4:6], "big")
        payload = bytes()
        expected_payload_length = max(response_length - 1, 0)
        while len(payload) < expected_payload_length:
            chunk = sock.recv(expected_payload_length - len(payload))
            if not chunk:
                break
            payload += chunk

    if len(payload) < 2:
        raise RuntimeError("Raspuns Modbus incomplet la nivel PDU.")

    response_function = payload[0]
    if response_function == (function_code | 0x80):
        exception_code = payload[1] if len(payload) > 1 else -1
        raise RuntimeError(f"Modbus a raspuns cu exceptia {exception_code}.")
    if response_function != function_code:
        raise RuntimeError(f"Codul de functie Modbus returnat este invalid: {response_function}.")

    byte_count = payload[1]
    data_bytes = payload[2 : 2 + byte_count]
    if len(data_bytes) < byte_count:
        raise RuntimeError("Raspuns Modbus incomplet pentru bitii ceruti.")

    bits: list[bool] = []
    for bit_index in range(count):
        data_byte = data_bytes[bit_index // 8]
        bits.append(bool((data_byte >> (bit_index % 8)) & 0x01))
    return bits


def read_modbus_tcp_bits(
    host: str,
    port: int,
    unit_id: int,
    start_address: int,
    count: int,
    bit_source: str = "discrete_input",
    timeout_seconds: float = 1.5,
) -> list[bool]:
    last_exception: Exception | None = None
    for attempt in range(1, MODBUS_TCP_RETRY_ATTEMPTS + 1):
        try:
            return _read_modbus_tcp_bits_once(
                host=host,
                port=port,
                unit_id=unit_id,
                start_address=start_address,
                count=count,
                bit_source=bit_source,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            last_exception = exc
            if attempt < MODBUS_TCP_RETRY_ATTEMPTS and MODBUS_TCP_RETRY_DELAY_SECONDS > 0:
                time_module.sleep(MODBUS_TCP_RETRY_DELAY_SECONDS)

    if last_exception is None:
        raise RuntimeError("Citirea Modbus TCP a esuat fara detalii.")
    raise last_exception


def read_modbus_rtu_bits(
    serial_port: str,
    baudrate: int,
    unit_id: int,
    start_address: int,
    count: int,
    bit_source: str = "discrete_input",
    timeout_seconds: float = 1.5,
    parity: str = "N",
    stopbits: int = 1,
) -> list[bool]:
    if ModbusSerialClient is None:
        raise RuntimeError("Lipseste dependinta pymodbus necesara pentru Modbus RTU.")

    client = ModbusSerialClient(
        port=serial_port,
        baudrate=baudrate,
        parity=normalize_modbus_serial_parity(parity),
        stopbits=stopbits,
        timeout=timeout_seconds,
    )
    try:
        if not client.connect():
            raise RuntimeError(f"Nu pot deschide portul serial {serial_port}.")

        if bit_source == "discrete_input":
            response = client.read_discrete_inputs(start_address, count=count, device_id=unit_id)
        else:
            response = client.read_coils(start_address, count=count, device_id=unit_id)

        if response.isError():
            raise RuntimeError(f"Modbus RTU a raspuns cu eroare: {response}")

        bits = [bool(bit) for bit in (response.bits or [])[:count]]
        if len(bits) < count:
            bits.extend([False] * (count - len(bits)))
        return bits
    finally:
        client.close()


def build_modbus_input_signal_map(config: dict) -> dict[str, str]:
    return {
        "in1": config.get("signal_map", {}).get("in1", "unused"),
        "in2": config.get("signal_map", {}).get("in2", "unused"),
        "in3": config.get("signal_map", {}).get("in3", "unused"),
        "in4": config.get("signal_map", {}).get("in4", "unused"),
    }


def build_modbus_signal_state(config: dict, inputs: list[bool]) -> tuple[dict[str, bool], list[dict[str, object]]]:
    input_map = build_modbus_input_signal_map(config)
    derived_signals = {
        "machine_on": False,
        "cutting_active": False,
        "table_change": False,
        "idle_abort": False,
    }
    raw_inputs: list[dict[str, object]] = []
    for index, bit_value in enumerate(inputs[:4], start=1):
        input_key = f"in{index}"
        target_signal = input_map.get(input_key, "unused")
        raw_inputs.append(
            {
                "key": input_key,
                "label": f"IN{index}",
                "signal": target_signal,
                "active": bool(bit_value),
            }
        )
        if target_signal in derived_signals:
            derived_signals[target_signal] = bool(bit_value)

    # Unele controlere pot tine simultan mai multe stari.
    # Pentru LASER1MODBUS tratam "cutting" ca prioritate, ca sa evitam stari conflictuale.
    if derived_signals["cutting_active"]:
        derived_signals["idle_abort"] = False
        derived_signals["table_change"] = False

    if derived_signals["cutting_active"] or derived_signals["table_change"] or derived_signals["idle_abort"]:
        derived_signals["machine_on"] = True

    return derived_signals, raw_inputs


def get_laser_ocr_snapshot(machine_key: str) -> dict:
    endpoints = resolve_real_data_endpoint_candidates(machine_key)
    endpoint = endpoints[0] if endpoints else ""
    image = None
    error_message = "Endpointul live nu este configurat."
    endpoint_errors: list[str] = []

    for candidate_endpoint in endpoints:
        image, candidate_error = fetch_mjpeg_frame(candidate_endpoint)
        if image is not None:
            endpoint = candidate_endpoint
            error_message = None
            break
        endpoint_errors.append(f"{candidate_endpoint}: {candidate_error}")
        error_message = candidate_error or error_message

    if image is None:
        joined_errors = " | ".join(endpoint_errors) if endpoint_errors else error_message
        return {
            "available": False,
            "connected": False,
            "endpoint": endpoint,
            "message": f"Nu pot citi captura live. Endpointuri incercate: {joined_errors}",
        }

    right_panel_variants = read_ocr_block_variants(image, LASER_OCR_ZONES["right_panel"])
    if right_panel_variants:
        right_panel_text = right_panel_variants[0]
    else:
        right_panel_text = read_ocr_block(image, LASER_OCR_ZONES["right_panel"], psm=6)
        right_panel_variants = [right_panel_text] if right_panel_text else []
    left_panel_text = read_ocr_block(image, LASER_OCR_ZONES["left_panel"], psm=11)

    selected_program = extract_section_token(right_panel_text, "Selected program", "Active program")
    active_program = extract_section_token(right_panel_text, "Active program", "NC blocks")
    material = extract_material(left_panel_text)
    program_status = extract_program_status(*right_panel_variants)
    warning_message = detect_laser_warning(image)

    return {
        "available": True,
        "connected": True,
        "endpoint": endpoint,
        "captured_at": now_local().isoformat(timespec="seconds"),
        "selected_program": selected_program or "Necitit",
        "active_program": active_program or "Necitit",
        "material": material or "Necitit",
        "program_status": program_status or "Necitit",
        "warning_message": warning_message,
    }


def build_modbus_grace_snapshot(machine_key: str, error_message: str) -> dict | None:
    if MODBUS_SNAPSHOT_GRACE_SECONDS <= 0:
        return None

    runtime = get_machine_runtime(machine_key)
    previous_snapshot = runtime.get("last_snapshot")
    if not isinstance(previous_snapshot, dict):
        return None
    if not previous_snapshot.get("available"):
        return None
    if previous_snapshot.get("machine_mode") != "laser1modbus":
        return None
    if not str(previous_snapshot.get("source") or "").startswith("modbus"):
        return None

    captured_at_raw = previous_snapshot.get("captured_at")
    if not captured_at_raw:
        return None
    try:
        captured_at = parse_timestamp(captured_at_raw)
    except Exception:
        return None

    age_seconds = max(int((now_local() - captured_at).total_seconds()), 0)
    if age_seconds > MODBUS_SNAPSHOT_GRACE_SECONDS:
        return None

    snapshot = dict(previous_snapshot)
    snapshot["available"] = True
    snapshot["connected"] = True
    snapshot["captured_at"] = now_local().isoformat(timespec="seconds")
    snapshot["source"] = "modbus+ocr-grace"
    snapshot["grace_mode"] = True
    snapshot["grace_snapshot_age_seconds"] = age_seconds
    snapshot["message"] = (
        f"{error_message} Pastrez temporar ultimul snapshot valid de acum {format_seconds(age_seconds)} "
        f"ca sa evit reseturile false la intreruperi scurte."
    )
    return snapshot


def analyze_laser_modbus_live_snapshot(machine_key: str) -> dict | None:
    config = get_machine_modbus_config(machine_key)
    if not config.get("enabled"):
        transport = config.get("transport", "tcp")
        transport_hint = (
            "Configureaza portul serial, baud rate-ul si maparea IN1..IN4 pentru Modbus RTU."
            if transport == "rtu"
            else "Configureaza hostul, portul si maparea IN1..IN4 pentru Modbus TCP."
        )
        return {
            "available": False,
            "connected": False,
            "source": "modbus",
            "endpoint": "",
            "message": transport_hint,
        }

    try:
        if config.get("transport") == "rtu":
            inputs = read_modbus_rtu_bits(
                serial_port=config["serial_port"],
                baudrate=config["serial_baudrate"],
                unit_id=config["unit_id"],
                start_address=config["start_address"],
                count=4,
                bit_source=config["bit_source"],
                timeout_seconds=config["poll_timeout_seconds"],
                parity=config["serial_parity"],
                stopbits=config["serial_stopbits"],
            )
        else:
            inputs = read_modbus_tcp_bits(
                host=config["host"],
                port=config["port"],
                unit_id=config["unit_id"],
                start_address=config["start_address"],
                count=4,
                bit_source=config["bit_source"],
                timeout_seconds=config["poll_timeout_seconds"],
            )
    except Exception as exc:
        transport_label = "Modbus RTU" if config.get("transport") == "rtu" else "Modbus TCP"
        error_message = f"Nu pot citi {transport_label} de la {config['endpoint']}. Motiv: {exc}"
        grace_snapshot = build_modbus_grace_snapshot(machine_key, error_message)
        if grace_snapshot:
            return grace_snapshot
        return {
            "available": False,
            "connected": False,
            "source": "modbus",
            "endpoint": config["endpoint"],
            "message": error_message,
        }

    derived_signals, raw_inputs = build_modbus_signal_state(config, inputs)
    idle = bool(
        not derived_signals["cutting_active"]
        and (
            derived_signals["idle_abort"]
            or (derived_signals["machine_on"] and not derived_signals["table_change"])
        )
    )
    table_sheet_detection = detect_sheet_on_change_table(machine_key)

    def resolve_previous_context_for_fallback() -> dict[str, str]:
        fallback_context = {"program": "", "material": ""}
        try:
            runtime = get_machine_runtime(machine_key)
        except Exception:
            return fallback_context

        previous_snapshot = runtime.get("last_snapshot") or {}
        if isinstance(previous_snapshot, dict):
            previous_program = normalize_context_token(resolve_snapshot_program(previous_snapshot))
            previous_material = normalize_context_token(previous_snapshot.get("material"))
            if previous_program or previous_material:
                return {
                    "program": previous_program,
                    "material": previous_material,
                }

        pending_cycle = runtime.get("pending_cycle") or {}
        if isinstance(pending_cycle, dict):
            return {
                "program": (
                    normalize_context_token(pending_cycle.get("selected_program"))
                    or normalize_context_token(pending_cycle.get("active_program"))
                ),
                "material": normalize_context_token(pending_cycle.get("material")),
            }
        return fallback_context

    ocr_snapshot = get_laser_ocr_snapshot(machine_key)
    if not ocr_snapshot.get("available"):
        fallback_context = (
            resolve_previous_context_for_fallback()
            if derived_signals["machine_on"]
            else {"program": "", "material": ""}
        )
        fallback_program = fallback_context["program"]
        fallback_material = fallback_context["material"]
        fallback_program_status = (
            "Idle / program anterior (fallback feed)"
            if fallback_program and idle
            else "Program anterior (fallback feed)"
            if fallback_program
            else "Feed indisponibil / Modbus activ"
        )
        return {
            "available": True,
            "connected": True,
            "source": "modbus+ocr",
            "captured_at": now_local().isoformat(timespec="seconds"),
            "selected_program": fallback_program or "Necitit",
            "active_program": fallback_program or "Necitit",
            "material": fallback_material or "Necitit",
            "program_status": fallback_program_status,
            "modbus_endpoint": config["endpoint"],
            "endpoint": config["endpoint"],
            "modbus_inputs": raw_inputs,
            "derived_signals": {
                "machine_on": derived_signals["machine_on"],
                "cutting_active": derived_signals["cutting_active"],
                "table_change": derived_signals["table_change"],
                "idle_abort": derived_signals["idle_abort"],
                "idle": idle,
            },
            "table_sheet_on_change_table": table_sheet_detection.get("present"),
            "table_sheet_detection": table_sheet_detection,
            "message": (
                f"Bitii Modbus sunt activi si continua sa fie cititi din {config['endpoint']}, "
                f"dar feedul pentru program nu raspunde acum: {ocr_snapshot.get('message')}"
                + (
                    f" Afisez ultimul program valid ({fallback_program}) deoarece feedul nu raspunde."
                    if fallback_program
                    else ""
                )
                + (
                    f" Tabla pe masa de schimb: {'DA' if table_sheet_detection.get('present') else 'NU'}."
                    if table_sheet_detection.get("available")
                    else f" Detectie tabla indisponibila: {table_sheet_detection.get('message')}."
                )
            ),
        }

    selected_program = normalize_context_token(ocr_snapshot.get("selected_program"))
    active_program = normalize_context_token(ocr_snapshot.get("active_program"))
    material = normalize_context_token(ocr_snapshot.get("material"))
    if selected_program and not active_program:
        active_program = selected_program
    elif active_program and not selected_program:
        selected_program = active_program

    fallback_program = ""
    fallback_material = ""
    fallback_context = {"program": "", "material": ""}
    if derived_signals["machine_on"] and (not (selected_program or active_program) or not material):
        fallback_context = resolve_previous_context_for_fallback()
    if not (selected_program or active_program):
        fallback_program = fallback_context["program"]
        if fallback_program:
            selected_program = fallback_program
            active_program = fallback_program
    if not material:
        fallback_material = fallback_context["material"]
        if fallback_material:
            material = fallback_material

    message = (
        f"Programul este citit din feed, iar timpii vin din Modbus {config['endpoint']}. "
        f"Bitii activi: {', '.join(input_item['label'] for input_item in raw_inputs if input_item['active']) or 'niciunul'}."
    )
    if ocr_snapshot.get("warning_message"):
        message = f"{message} Banner galben detectat: {ocr_snapshot['warning_message']}."
    if fallback_program:
        message = f"{message} Feedul nu afiseaza program, afisez ultimul program valid: {fallback_program}."
    if fallback_material:
        message = f"{message} Feedul nu afiseaza material, pastrez ultimul material valid: {fallback_material}."
    if table_sheet_detection.get("available"):
        message = f"{message} Tabla pe masa de schimb: {'DA' if table_sheet_detection.get('present') else 'NU'}."
    else:
        message = f"{message} Detectie tabla indisponibila: {table_sheet_detection.get('message')}."

    return {
        **ocr_snapshot,
        "available": True,
        "connected": True,
        "source": "modbus+ocr",
        "machine_mode": "laser1modbus",
        "selected_program": selected_program or "Necitit",
        "active_program": active_program or "Necitit",
        "material": material or "Necitit",
        "program_status": (
            ("Idle / program anterior (fallback feed)" if idle else "Program anterior (fallback feed)")
            if fallback_program and str(ocr_snapshot.get("program_status") or "").strip().lower() in {"", "necitit"}
            else ocr_snapshot.get("program_status") or "Necitit"
        ),
        "endpoint": config["endpoint"],
        "modbus_endpoint": config["endpoint"],
        "modbus_inputs": raw_inputs,
        "derived_signals": {
            "machine_on": derived_signals["machine_on"],
            "cutting_active": derived_signals["cutting_active"],
            "table_change": derived_signals["table_change"],
            "idle_abort": derived_signals["idle_abort"],
            "idle": idle,
        },
        "table_sheet_on_change_table": table_sheet_detection.get("present"),
        "table_sheet_detection": table_sheet_detection,
        "message": message,
    }


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

    cached_snapshot = _operator_snapshot_cache.get(workcenter_id)
    if cached_snapshot and (time_module.time() - cached_snapshot["cached_at"]) < OPERATOR_CACHE_SECONDS:
        return json.loads(json.dumps(cached_snapshot["payload"], ensure_ascii=False))

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
            active_rows = cursor.fetchall()

            cursor.execute(
                """
                SELECT TOP 25
                    A.ID,
                    A.Nume,
                    A.Prenume,
                    MAX(CAST(P.Data AS datetime) + CAST(P.OraCheckIn AS datetime)) AS UltimulPontaj
                FROM PontajWorkCenter P
                INNER JOIN Angajati A ON P.ID = A.ID
                WHERE P.WorkCenterID = ?
                GROUP BY A.ID, A.Nume, A.Prenume
                ORDER BY UltimulPontaj DESC
                """,
                (workcenter_id,),
            )
            related_rows = cursor.fetchall()
            cursor.close()
    except Exception as exc:  # pragma: no cover - depends on networked SQL Server
        payload["status"] = "error"
        payload["message"] = str(exc)
        return payload

    active_operators = []
    for row in active_rows:
        first_name = (row[1] or "").strip()
        last_name = (row[2] or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        check_in = None
        if row[3] is not None and row[4] is not None:
            check_in = f"{row[3]} {row[4]}"
        active_operators.append(
            {
                "employee_id": str(row[0]),
                "full_name": full_name or f"Angajat {row[0]}",
                "check_in": check_in,
                "is_active": True,
                "last_seen": check_in,
            }
        )

    related_operators = []
    for row in related_rows:
        first_name = (row[1] or "").strip()
        last_name = (row[2] or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        last_seen = row[3].isoformat(sep=" ", timespec="seconds") if row[3] is not None else None
        related_operators.append(
            {
                "employee_id": str(row[0]),
                "full_name": full_name or f"Angajat {row[0]}",
                "check_in": None,
                "is_active": False,
                "last_seen": last_seen,
            }
        )

    merged_operators: list[dict] = []
    seen_employee_ids: set[str] = set()
    for operator in active_operators + related_operators:
        employee_id = operator["employee_id"]
        if employee_id in seen_employee_ids:
            continue
        seen_employee_ids.add(employee_id)
        merged_operators.append(operator)

    payload["status"] = "connected"
    payload["message"] = "Pontaj online."
    payload["operators"] = merged_operators
    payload["primary_operator"] = active_operators[0] if active_operators else None
    if not active_operators and merged_operators:
        payload["message"] = "Nu exista operator activ pe acest workcenter. Afisez operatorii asociati istoric."
    elif not merged_operators:
        payload["message"] = "Nu exista operatori cunoscuti pentru acest workcenter."
    _operator_snapshot_cache[workcenter_id] = {
        "cached_at": time_module.time(),
        "payload": payload,
    }
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
            "signal_label": resolve_signal_definition(row["machine_key"], row["signal_name"])["label"],
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


def calculate_runtime_efficiency_percent(
    machine_on_seconds: int,
    cutting_seconds: int,
    table_change_seconds: int,
) -> float:
    machine_on = max(int(machine_on_seconds or 0), 0)
    if machine_on <= 0:
        return 0.0

    productive_seconds = max(int(cutting_seconds or 0), 0) + max(int(table_change_seconds or 0), 0)
    productive_seconds = min(productive_seconds, machine_on)
    return round((productive_seconds / machine_on) * 100, 1)


def calculate_saved_cycle_metrics(
    machine_key: str,
    cutting_started_at: str | None,
    table_change_started_at: str | None,
    table_change_ended_at: str | None,
    fallback_cutting_seconds: int | None = None,
    fallback_table_change_seconds: int | None = None,
) -> dict[str, int | float]:
    cycle_start_raw = cutting_started_at or table_change_started_at
    cycle_end_raw = table_change_ended_at or table_change_started_at or cutting_started_at
    if not cycle_start_raw or not cycle_end_raw:
        cutting_seconds = max(int(fallback_cutting_seconds or 0), 0)
        table_change_seconds = max(int(fallback_table_change_seconds or 0), 0)
        machine_on_seconds = cutting_seconds + table_change_seconds
        idle_seconds = max(machine_on_seconds - cutting_seconds - table_change_seconds, 0)
        efficiency_percent = calculate_runtime_efficiency_percent(
            machine_on_seconds,
            cutting_seconds,
            table_change_seconds,
        )
        return {
            "machine_on_duration_seconds": machine_on_seconds,
            "cutting_duration_seconds": cutting_seconds,
            "table_change_duration_seconds": table_change_seconds,
            "idle_duration_seconds": idle_seconds,
            "efficiency_percent": efficiency_percent,
        }

    start_dt = parse_timestamp(cycle_start_raw)
    end_dt = parse_timestamp(cycle_end_raw)
    if end_dt < start_dt:
        end_dt = start_dt

    machine_on_seconds = calculate_active_seconds(machine_key, "machine_on", start_dt, end_dt)
    cutting_seconds = calculate_active_seconds(machine_key, "cutting_active", start_dt, end_dt)
    table_change_seconds = calculate_active_seconds(machine_key, "table_change", start_dt, end_dt)
    idle_seconds = (
        calculate_active_seconds(machine_key, "idle_abort", start_dt, end_dt)
        if machine_supports_idle_signal(machine_key)
        else max(machine_on_seconds - cutting_seconds - table_change_seconds, 0)
    )
    if not table_change_seconds:
        table_change_seconds = max(int(fallback_table_change_seconds or 0), 0)
    if not cutting_seconds:
        cutting_seconds = max(int(fallback_cutting_seconds or 0), 0)
    if machine_on_seconds < cutting_seconds + table_change_seconds:
        machine_on_seconds = cutting_seconds + table_change_seconds
    if not machine_supports_idle_signal(machine_key):
        idle_seconds = max(machine_on_seconds - cutting_seconds - table_change_seconds, 0)
    efficiency_percent = calculate_runtime_efficiency_percent(
        machine_on_seconds,
        cutting_seconds,
        table_change_seconds,
    )
    return {
        "machine_on_duration_seconds": machine_on_seconds,
        "cutting_duration_seconds": cutting_seconds,
        "table_change_duration_seconds": table_change_seconds,
        "idle_duration_seconds": idle_seconds,
        "efficiency_percent": efficiency_percent,
    }


def backfill_saved_cycle_metrics() -> None:
    with get_sqlite_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                machine_key,
                cutting_started_at,
                table_change_started_at,
                table_change_ended_at,
                cycle_duration_seconds,
                table_change_duration_seconds,
                machine_on_duration_seconds,
                idle_duration_seconds,
                efficiency_percent
            FROM saved_cycles
            WHERE machine_on_duration_seconds IS NULL
               OR idle_duration_seconds IS NULL
               OR efficiency_percent IS NULL
            """
        ).fetchall()
        for row in rows:
            metrics = calculate_saved_cycle_metrics(
                machine_key=row["machine_key"],
                cutting_started_at=row["cutting_started_at"],
                table_change_started_at=row["table_change_started_at"],
                table_change_ended_at=row["table_change_ended_at"],
                fallback_cutting_seconds=row["cycle_duration_seconds"],
                fallback_table_change_seconds=row["table_change_duration_seconds"],
            )
            connection.execute(
                """
                UPDATE saved_cycles
                SET machine_on_duration_seconds = ?,
                    idle_duration_seconds = ?,
                    efficiency_percent = ?
                WHERE id = ?
                """,
                (
                    metrics["machine_on_duration_seconds"],
                    metrics["idle_duration_seconds"],
                    metrics["efficiency_percent"],
                    row["id"],
                ),
            )
        connection.commit()


def format_saved_cycle_row(row: sqlite3.Row) -> dict:
    duration_seconds = row["cycle_duration_seconds"]
    machine_key = row["machine_key"]
    cutting_meta = resolve_signal_definition(machine_key, "cutting_active")
    table_change_meta = resolve_signal_definition(machine_key, "table_change")
    snapshot_payload = {}
    try:
        if row["snapshot_json"]:
            parsed_snapshot = json.loads(row["snapshot_json"])
            if isinstance(parsed_snapshot, dict):
                snapshot_payload = parsed_snapshot
    except (TypeError, ValueError, json.JSONDecodeError):
        snapshot_payload = {}
    close_reason = str(snapshot_payload.get("close_reason") or "").strip().lower()
    next_program = resolve_snapshot_program(snapshot_payload.get("resume_snapshot"))
    if close_reason == "program_change":
        close_reason_label = "Program schimbat"
    else:
        close_reason = "table_change_completed"
        close_reason_label = "Table change finalizat"
        if "program schimbat" in str(row["program_status"] or "").lower():
            close_reason = "program_change"
            close_reason_label = "Program schimbat"
    machine_on_seconds = row["machine_on_duration_seconds"] or 0
    idle_seconds = row["idle_duration_seconds"] or 0
    efficiency_percent = float(row["efficiency_percent"] or 0.0)
    return {
        "id": row["id"],
        "machine_key": machine_key,
        "machine_label": MACHINE_DEFINITIONS.get(machine_key, {}).get("label", machine_key),
        "workcenter_id": row["workcenter_id"],
        "operator_id": row["operator_id"],
        "operator_name": row["operator_name"] or UNKNOWN_OPERATOR_LABEL,
        "selected_program": row["selected_program"] or "Necitit",
        "active_program": row["active_program"] or "Necitit",
        "material": row["material"] or "Necitit",
        "program_status": row["program_status"] or "Necitit",
        "upper_tool": row["upper_tool"] or "n/a",
        "lower_tool": row["lower_tool"] or "n/a",
        "setup_signature": row["setup_signature"] or "",
        "setup_changed": bool(row["setup_changed"]),
        "cutting_started_at": row["cutting_started_at"],
        "table_change_started_at": row["table_change_started_at"],
        "table_change_ended_at": row["table_change_ended_at"],
        "table_change_duration_seconds": row["table_change_duration_seconds"] or 0,
        "table_change_duration_label": format_seconds(row["table_change_duration_seconds"] or 0),
        "cycle_duration_seconds": duration_seconds,
        "cycle_duration_label": format_seconds(duration_seconds or 0),
        "machine_on_duration_seconds": machine_on_seconds,
        "machine_on_duration_label": format_seconds(machine_on_seconds),
        "idle_duration_seconds": idle_seconds,
        "idle_duration_label": format_seconds(idle_seconds),
        "efficiency_percent": efficiency_percent,
        "activity_label": cutting_meta.get("report_label", cutting_meta["label"]),
        "change_label": table_change_meta.get("report_label", table_change_meta["label"]),
        "close_reason": close_reason,
        "close_reason_label": close_reason_label,
        "next_program": next_program or None,
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


def fetch_saved_cycles_all(machine_key: str | None = None, limit: int = 500) -> list[dict]:
    with get_sqlite_connection() as connection:
        if machine_key:
            rows = connection.execute(
                """
                SELECT *
                FROM saved_cycles
                WHERE machine_key = ?
                ORDER BY table_change_started_at DESC, id DESC
                LIMIT ?
                """,
                (machine_key, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM saved_cycles
                ORDER BY table_change_started_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
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


def build_efficiency_report(label: str, records: list[dict], machine_key: str | None = None) -> dict:
    total_cutting_seconds = sum(int(record.get("cycle_duration_seconds") or 0) for record in records)
    total_table_change_seconds = sum(int(record.get("table_change_duration_seconds") or 0) for record in records)
    productive_seconds = total_cutting_seconds + total_table_change_seconds
    total_machine_on_seconds = sum(int(record.get("machine_on_duration_seconds") or 0) for record in records)
    machine_on_seconds = max(total_machine_on_seconds, productive_seconds)
    efficiency_percent = calculate_runtime_efficiency_percent(
        machine_on_seconds,
        total_cutting_seconds,
        total_table_change_seconds,
    )
    resolved_machine_key = machine_key
    if resolved_machine_key is None and records:
        first_machine_key = records[0].get("machine_key")
        if all(record.get("machine_key") == first_machine_key for record in records):
            resolved_machine_key = first_machine_key
    cutting_meta = resolve_signal_definition(resolved_machine_key or DEFAULT_MACHINE_KEY, "cutting_active")
    table_change_meta = resolve_signal_definition(resolved_machine_key or DEFAULT_MACHINE_KEY, "table_change")

    return {
        "label": label,
        "records_count": len(records),
        "efficiency_percent": efficiency_percent,
        "cutting_seconds": total_cutting_seconds,
        "cutting_label": format_seconds(total_cutting_seconds),
        "cutting_display_label": cutting_meta.get("report_label", cutting_meta["label"]),
        "table_change_seconds": total_table_change_seconds,
        "table_change_label": format_seconds(total_table_change_seconds),
        "table_change_display_label": table_change_meta.get("report_label", table_change_meta["label"]),
        "productive_window_seconds": machine_on_seconds,
        "productive_window_label": format_seconds(machine_on_seconds),
    }


def build_saved_cycles_reports(machine_key: str | None = None) -> list[dict]:
    now = now_local()
    today_start = datetime.combine(date.today(), time.min)
    week_start = datetime.combine(date.today(), time.min)
    week_start = week_start.replace(day=week_start.day) - timedelta(days=week_start.weekday())
    month_start = datetime.combine(date.today().replace(day=1), time.min)

    return [
        build_efficiency_report("Zilnic", fetch_saved_cycles_between(today_start, now, machine_key=machine_key), machine_key=machine_key),
        build_efficiency_report("Saptamanal", fetch_saved_cycles_between(week_start, now, machine_key=machine_key), machine_key=machine_key),
        build_efficiency_report("Lunar", fetch_saved_cycles_between(month_start, now, machine_key=machine_key), machine_key=machine_key),
    ]


def build_saved_cycles_reports_by_machine() -> list[dict]:
    reports = []
    for machine_key, machine_meta in MACHINE_DEFINITIONS.items():
        reports.append(
            {
                "machine_key": machine_key,
                "machine_label": machine_meta["label"],
                "periods": build_saved_cycles_reports(machine_key),
            }
        )
    return reports


def prometheus_period_range(period: str) -> str:
    normalized = resolve_saved_period(period)
    return {
        "day": "1d",
        "week": "7d",
        "month": "30d",
        "all": "3650d",
    }[normalized]


def build_prometheus_base_url_candidates() -> list[str]:
    candidates: list[str] = []

    def append_candidate(url: str | None) -> None:
        normalized = (url or "").strip().rstrip("/")
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    append_candidate(PROMETHEUS_BASE_URL)

    if has_request_context():
        request_host = (request.host or "").strip()
        host_name = request_host.split(":", 1)[0].strip()
        if host_name:
            append_candidate(f"http://{host_name}:9090")
            append_candidate(f"https://{host_name}:9090")

    if is_running_in_container():
        append_candidate("http://172.17.0.1:9090")

    append_candidate("http://localhost:9090")
    append_candidate("http://127.0.0.1:9090")
    return candidates


def fetch_prometheus_vector(query: str) -> list[dict]:
    errors_by_endpoint: list[str] = []
    encoded_query = urllib.parse.quote(query, safe="")
    timeout_candidates = [PROMETHEUS_QUERY_TIMEOUT_SECONDS]
    extended_timeout_seconds = min(max(PROMETHEUS_QUERY_TIMEOUT_SECONDS * 2, 5.0), 20.0)
    if abs(extended_timeout_seconds - PROMETHEUS_QUERY_TIMEOUT_SECONDS) > 1e-9:
        timeout_candidates.append(extended_timeout_seconds)

    for base_url in build_prometheus_base_url_candidates():
        request_url = f"{base_url}/api/v1/query?query={encoded_query}"
        endpoint_error: Exception | None = None
        for timeout_seconds in timeout_candidates:
            request_obj = urllib.request.Request(
                request_url,
                headers={"User-Agent": "HABA-Production-Monitor/1.0"},
            )
            try:
                with urllib.request.urlopen(request_obj, timeout=timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("status") != "success":
                    raise RuntimeError(payload.get("error") or "Prometheus query failed.")
                result = payload.get("data", {}).get("result") or []
                return result if isinstance(result, list) else []
            except Exception as exc:
                endpoint_error = exc
        errors_by_endpoint.append(f"{base_url} -> {endpoint_error}")

    raise RuntimeError(
        "Prometheus query failed for all configured endpoints: " + " | ".join(errors_by_endpoint)
    )


def fetch_prometheus_query_map(query_map: dict[str, str]) -> dict[str, list[dict]]:
    if not query_map:
        return {}

    if len(query_map) == 1:
        only_key, only_query = next(iter(query_map.items()))
        return {only_key: fetch_prometheus_vector(only_query)}

    results: dict[str, list[dict]] = {}
    max_workers = min(PROMETHEUS_MAX_PARALLEL_QUERIES, len(query_map))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="prom-query") as executor:
        future_map = {
            executor.submit(fetch_prometheus_vector, query): query_key
            for query_key, query in query_map.items()
        }
        for future in as_completed(future_map):
            results[future_map[future]] = future.result()

    return {query_key: results.get(query_key, []) for query_key in query_map}


def escape_prometheus_label_matcher(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def build_prometheus_saved_cycle_label_filter(
    operator_id: str | None = None,
    machine_key: str | None = None,
) -> str:
    matchers: list[str] = []
    if machine_key:
        matchers.append(f'machine_key="{escape_prometheus_label_matcher(machine_key)}"')
    if operator_id:
        if operator_id.startswith("name:"):
            matchers.append(f'operator_name="{escape_prometheus_label_matcher(operator_id[5:])}"')
        else:
            matchers.append(f'operator_id="{escape_prometheus_label_matcher(operator_id)}"')
    if not matchers:
        return ""
    return "{" + ",".join(matchers) + "}"


def build_prometheus_cycle_series_key(labels: dict) -> str | None:
    cycle_id = str(labels.get("cycle_id") or "").strip()
    if not cycle_id:
        return None

    parts = [
        cycle_id,
        str(labels.get("machine_key") or "").strip(),
        str(labels.get("completed_at") or "").strip(),
        str(labels.get("table_change_started_at") or "").strip(),
        str(labels.get("operator_id") or "").strip(),
        str(labels.get("operator_name") or "").strip(),
        str(labels.get("selected_program") or "").strip(),
    ]
    return "|".join(parts)


def build_prometheus_operator_summaries() -> list[dict]:
    periods = ("day", "week", "month")
    operator_map: dict[str, dict] = {}

    for series in fetch_prometheus_vector(
        'count by (operator_id, operator_name, machine_label) (max_over_time(haba_saved_cycle_completed[3650d]))'
    ):
        labels = series.get("metric") or {}
        operator_name = labels.get("operator_name") or UNKNOWN_OPERATOR_LABEL
        employee_id = labels.get("operator_id") or ""
        operator_id = employee_id or f"name:{operator_name}"
        operator_entry = operator_map.setdefault(
            operator_id,
            {
                "operator_id": operator_id,
                "employee_id": employee_id,
                "operator_name": operator_name,
                "machines": set(),
                "day": {},
                "week": {},
                "month": {},
            },
        )
        machine_label = labels.get("machine_label") or ""
        if machine_label:
            operator_entry["machines"].add(machine_label)

    period_query_map: dict[str, str] = {}
    for period in periods:
        period_range = prometheus_period_range(period)
        query_map = {
            "records_count": f'count by (operator_id, operator_name) (max_over_time(haba_saved_cycle_completed[{period_range}]))',
            "setup_count": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_setup_changed[{period_range}]))',
            "machine_on_seconds": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_machine_on_seconds[{period_range}]))',
            "cutting_seconds": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_cutting_seconds[{period_range}]))',
            "idle_seconds": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_idle_seconds[{period_range}]))',
            "table_change_seconds": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_table_change_seconds[{period_range}]))',
        }
        for field_name, query in query_map.items():
            period_query_map[f"{period}:{field_name}"] = query

    for query_key, result_series in fetch_prometheus_query_map(period_query_map).items():
        period, field_name = query_key.split(":", 1)
        for series in result_series:
            labels = series.get("metric") or {}
            operator_name = labels.get("operator_name") or UNKNOWN_OPERATOR_LABEL
            employee_id = labels.get("operator_id") or ""
            operator_id = employee_id or f"name:{operator_name}"
            operator_entry = operator_map.setdefault(
                operator_id,
                {
                    "operator_id": operator_id,
                    "employee_id": employee_id,
                    "operator_name": operator_name,
                    "machines": set(),
                    "day": {},
                    "week": {},
                    "month": {},
                },
            )
            value_raw = (series.get("value") or [None, "0"])[1]
            numeric_value = float(value_raw or 0)
            target_period = operator_entry[period]
            if field_name.endswith("_seconds"):
                target_period[field_name] = int(round(numeric_value))
                target_period[field_name.replace("_seconds", "_label")] = format_seconds(int(round(numeric_value)))
            elif field_name in {"records_count", "setup_count"}:
                target_period[field_name] = int(round(numeric_value))

    output = []
    for operator_entry in operator_map.values():
        for period in periods:
            target_period = operator_entry[period]
            target_period.setdefault("records_count", 0)
            target_period.setdefault("setup_count", 0)
            for field_name in ("machine_on", "cutting", "idle", "table_change"):
                seconds_key = f"{field_name}_seconds"
                label_key = f"{field_name}_label"
                target_period.setdefault(seconds_key, 0)
                target_period.setdefault(label_key, format_seconds(0))
            target_period["efficiency_percent"] = calculate_operator_efficiency_percent(
                int(target_period.get("cutting_seconds", 0) or 0),
                int(target_period.get("table_change_seconds", 0) or 0),
                int(target_period.get("machine_on_seconds", 0) or 0),
            )
        operator_entry["machines"] = sorted(operator_entry.get("machines") or [])
        output.append(operator_entry)

    output.sort(
        key=lambda item: (
            -int(item["day"].get("records_count", 0)),
            -float(item["week"].get("efficiency_percent", 0.0)),
            item["operator_name"],
        )
    )
    return output


def build_prometheus_saved_records_for_range(
    period_range: str,
    operator_id: str | None = None,
    machine_key: str | None = None,
) -> list[dict]:
    label_filter = build_prometheus_saved_cycle_label_filter(operator_id=operator_id, machine_key=machine_key)
    base_query = f"max_over_time(haba_saved_cycle_completed{label_filter}[{period_range}])"
    base_records: dict[str, dict] = {}

    for series in fetch_prometheus_vector(base_query):
        labels = series.get("metric") or {}
        cycle_id = str(labels.get("cycle_id") or "").strip()
        if not cycle_id:
            continue
        series_key = build_prometheus_cycle_series_key(labels)
        if not series_key:
            continue
        try:
            numeric_cycle_id = int(cycle_id)
        except (TypeError, ValueError):
            continue
        base_records[series_key] = {
            "id": numeric_cycle_id,
            "machine_key": labels.get("machine_key") or "",
            "machine_label": labels.get("machine_label") or labels.get("machine_key") or "",
            "workcenter_id": parse_optional_int(labels.get("workcenter_id")),
            "operator_id": labels.get("operator_id") or "",
            "operator_name": labels.get("operator_name") or UNKNOWN_OPERATOR_LABEL,
            "selected_program": labels.get("selected_program") or "Necitit",
            "active_program": labels.get("active_program") or labels.get("selected_program") or "Necitit",
            "material": labels.get("material") or "Necitit",
            "program_status": labels.get("program_status") or "Salvat in Prometheus",
            "upper_tool": labels.get("upper_tool") or "n/a",
            "lower_tool": labels.get("lower_tool") or "n/a",
            "setup_signature": labels.get("setup_signature") or "",
            "setup_changed": to_bool(labels.get("setup_changed", "false")) if labels.get("setup_changed") is not None else False,
            "cutting_started_at": labels.get("cutting_started_at") or None,
            "table_change_started_at": labels.get("table_change_started_at") or labels.get("completed_at") or None,
            "table_change_ended_at": labels.get("completed_at") or None,
            "close_reason": labels.get("close_reason") or "",
            "close_reason_label": labels.get("close_reason_label") or "",
            "next_program": labels.get("next_program") or None,
            "source": labels.get("source") or "prometheus",
            "created_at": labels.get("completed_at") or "",
        }

    metric_map = {
        "machine_on_duration_seconds": "haba_saved_cycle_machine_on_seconds",
        "cycle_duration_seconds": "haba_saved_cycle_cutting_seconds",
        "idle_duration_seconds": "haba_saved_cycle_idle_seconds",
        "table_change_duration_seconds": "haba_saved_cycle_table_change_seconds",
        "efficiency_percent": "haba_saved_cycle_efficiency_percent",
        "setup_changed": "haba_saved_cycle_setup_changed",
    }
    metric_query_map = {
        field_name: f"max_over_time({metric_name}{label_filter}[{period_range}])"
        for field_name, metric_name in metric_map.items()
    }
    for field_name, result_series in fetch_prometheus_query_map(metric_query_map).items():
        for series in result_series:
            labels = series.get("metric") or {}
            series_key = build_prometheus_cycle_series_key(labels)
            if not series_key or series_key not in base_records:
                continue
            numeric_value = float((series.get("value") or [None, "0"])[1] or 0)
            if field_name.endswith("_seconds"):
                base_records[series_key][field_name] = int(round(numeric_value))
                base_records[series_key][field_name.replace("_seconds", "_label")] = format_seconds(int(round(numeric_value)))
            elif field_name == "setup_changed":
                base_records[series_key][field_name] = bool(round(numeric_value))
            else:
                base_records[series_key][field_name] = round(numeric_value, 1)

    records: list[dict] = []
    for record in base_records.values():
        record_machine_key = record["machine_key"]
        cutting_meta = resolve_signal_definition(record_machine_key or DEFAULT_MACHINE_KEY, "cutting_active")
        table_change_meta = resolve_signal_definition(record_machine_key or DEFAULT_MACHINE_KEY, "table_change")
        record.setdefault("machine_on_duration_seconds", 0)
        record.setdefault("machine_on_duration_label", format_seconds(0))
        record.setdefault("cycle_duration_seconds", 0)
        record.setdefault("cycle_duration_label", format_seconds(0))
        record.setdefault("idle_duration_seconds", 0)
        record.setdefault("idle_duration_label", format_seconds(0))
        record.setdefault("table_change_duration_seconds", 0)
        record.setdefault("table_change_duration_label", format_seconds(0))
        record.setdefault("efficiency_percent", 0.0)
        record.setdefault("setup_changed", False)
        if not record.get("close_reason"):
            if "program schimbat" in str(record.get("program_status") or "").lower():
                record["close_reason"] = "program_change"
            else:
                record["close_reason"] = "table_change_completed"
        if not record.get("close_reason_label"):
            record["close_reason_label"] = (
                "Program schimbat"
                if record.get("close_reason") == "program_change"
                else "Table change finalizat"
            )
        record["activity_label"] = cutting_meta.get("report_label", cutting_meta["label"])
        record["change_label"] = table_change_meta.get("report_label", table_change_meta["label"])
        records.append(record)

    records.sort(key=lambda item: item.get("table_change_ended_at") or item.get("created_at") or "", reverse=True)
    return records


def build_prometheus_saved_records(period: str, operator_id: str | None = None) -> list[dict]:
    period_range = prometheus_period_range(period)
    return build_prometheus_saved_records_for_range(period_range, operator_id=operator_id)


def build_empty_operator_period_bucket() -> dict:
    return {
        "records_count": 0,
        "setup_count": 0,
        "efficiency_percent": 0.0,
        "machine_on_seconds": 0,
        "machine_on_label": format_seconds(0),
        "cutting_seconds": 0,
        "cutting_label": format_seconds(0),
        "idle_seconds": 0,
        "idle_label": format_seconds(0),
        "table_change_seconds": 0,
        "table_change_label": format_seconds(0),
    }


def calculate_operator_efficiency_percent(
    cutting_seconds: int,
    table_change_seconds: int,
    machine_on_seconds: int,
) -> float:
    return calculate_runtime_efficiency_percent(machine_on_seconds, cutting_seconds, table_change_seconds)


def build_operator_entry(operator_id: str, employee_id: str, operator_name: str) -> dict:
    return {
        "operator_id": operator_id,
        "employee_id": employee_id,
        "operator_name": operator_name or UNKNOWN_OPERATOR_LABEL,
        "machines": set(),
        "day": build_empty_operator_period_bucket(),
        "week": build_empty_operator_period_bucket(),
        "month": build_empty_operator_period_bucket(),
    }


def clone_operator_entry(entry: dict) -> dict:
    cloned = build_operator_entry(
        entry.get("operator_id", ""),
        entry.get("employee_id", ""),
        entry.get("operator_name", UNKNOWN_OPERATOR_LABEL),
    )
    cloned["machines"] = set(entry.get("machines") or [])
    for period_key in ("day", "week", "month"):
        source_bucket = entry.get(period_key) or {}
        target_bucket = cloned[period_key]
        target_bucket.update(source_bucket)
    return cloned


def filter_saved_cycle_records_by_operator(records: list[dict], operator_id: str | None) -> list[dict]:
    if not operator_id:
        return records

    filtered_records: list[dict] = []
    for record in records:
        record_operator_id = str(record.get("operator_id") or "").strip()
        record_operator_name = (record.get("operator_name") or UNKNOWN_OPERATOR_LABEL).strip()
        resolved_operator_id = record_operator_id or f"name:{record_operator_name}"
        if resolved_operator_id == operator_id:
            filtered_records.append(record)
    return filtered_records


def build_workcenter_operator_summaries() -> list[dict]:
    operator_map: dict[str, dict] = {}
    for machine_profile in get_machine_profiles():
        operator_snapshot = fetch_current_operator(machine_profile.get("workcenter_id"))
        for operator in operator_snapshot.get("operators", []):
            employee_id = str(operator.get("employee_id") or "").strip()
            operator_name = (operator.get("full_name") or UNKNOWN_OPERATOR_LABEL).strip()
            operator_id = employee_id or f"name:{operator_name}"
            operator_entry = operator_map.setdefault(
                operator_id,
                build_operator_entry(operator_id, employee_id, operator_name),
            )
            operator_entry["machines"].add(machine_profile["label"])

    output = []
    for operator_entry in operator_map.values():
        operator_entry["machines"] = sorted(operator_entry["machines"])
        output.append(operator_entry)

    output.sort(key=lambda item: item["operator_name"])
    return output


def merge_operator_seed_entries(base_entries: list[dict], seed_entries: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for source_entries in (base_entries, seed_entries):
        for entry in source_entries:
            operator_id = entry["operator_id"]
            target = merged.setdefault(operator_id, clone_operator_entry(entry))
            if entry.get("employee_id") and not target.get("employee_id"):
                target["employee_id"] = entry["employee_id"]
            if entry.get("operator_name") and target.get("operator_name") == UNKNOWN_OPERATOR_LABEL:
                target["operator_name"] = entry["operator_name"]
            target["machines"].update(entry.get("machines") or [])
            for period_key in ("day", "week", "month"):
                target_bucket = target[period_key]
                source_bucket = entry.get(period_key) or {}
                for key, value in source_bucket.items():
                    if key.endswith("_label"):
                        base_key = key.replace("_label", "_seconds")
                        if source_bucket.get(base_key, 0) or not target_bucket.get(key):
                            target_bucket[key] = value
                        continue
                    if key == "efficiency_percent":
                        if source_bucket.get("records_count", 0) or not target_bucket.get("records_count", 0):
                            target_bucket[key] = value
                        continue
                    if source_bucket.get(key, 0) or not target_bucket.get(key, 0):
                        target_bucket[key] = value
    return list(merged.values())


def finalize_operator_entries(operator_entries: list[dict]) -> list[dict]:
    output: list[dict] = []
    for entry in operator_entries:
        normalized_entry = clone_operator_entry(entry)
        normalized_entry["machines"] = sorted(machine for machine in normalized_entry["machines"] if machine)
        output.append(normalized_entry)

    output.sort(
        key=lambda item: (
            -int(item["day"].get("records_count", 0)),
            -float(item["week"].get("efficiency_percent", 0.0)),
            item["operator_name"],
        )
    )
    return output


def apply_records_to_operator_period(operator_map: dict[str, dict], period_key: str, records: list[dict]) -> None:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        operator_name = record.get("operator_name") or UNKNOWN_OPERATOR_LABEL
        employee_id = str(record.get("operator_id") or "").strip()
        operator_id = employee_id or f"name:{operator_name}"
        grouped.setdefault(operator_id, []).append(record)

        operator_entry = operator_map.setdefault(
            operator_id,
            build_operator_entry(operator_id, employee_id, operator_name),
        )
        operator_entry["machines"].add(record.get("machine_label") or record.get("machine_key") or "")

    for operator_id, operator_records in grouped.items():
        operator_entry = operator_map[operator_id]
        period_bucket = operator_entry[period_key]
        machine_on_seconds = sum(int(record.get("machine_on_duration_seconds") or 0) for record in operator_records)
        cutting_seconds = sum(int(record.get("cycle_duration_seconds") or 0) for record in operator_records)
        idle_seconds = sum(int(record.get("idle_duration_seconds") or 0) for record in operator_records)
        table_change_seconds = sum(int(record.get("table_change_duration_seconds") or 0) for record in operator_records)
        period_bucket.update(
            {
                "records_count": len(operator_records),
                "setup_count": sum(1 for record in operator_records if bool(record.get("setup_changed"))),
                "efficiency_percent": calculate_operator_efficiency_percent(
                    cutting_seconds,
                    table_change_seconds,
                    machine_on_seconds,
                ),
                "machine_on_seconds": machine_on_seconds,
                "machine_on_label": format_seconds(machine_on_seconds),
                "cutting_seconds": cutting_seconds,
                "cutting_label": format_seconds(cutting_seconds),
                "idle_seconds": idle_seconds,
                "idle_label": format_seconds(idle_seconds),
                "table_change_seconds": table_change_seconds,
                "table_change_label": format_seconds(table_change_seconds),
            }
        )


def build_sqlite_operator_summaries(machine_key: str | None = None) -> list[dict]:
    now = now_local()
    day_records = fetch_saved_cycles_between(datetime.combine(date.today(), time.min), now, machine_key=machine_key)
    week_start = datetime.combine(date.today(), time.min) - timedelta(days=date.today().weekday())
    week_records = fetch_saved_cycles_between(week_start, now, machine_key=machine_key)
    month_start = datetime.combine(date.today().replace(day=1), time.min)
    month_records = fetch_saved_cycles_between(month_start, now, machine_key=machine_key)

    operator_entries = merge_operator_seed_entries([], build_workcenter_operator_summaries())
    operator_map = {entry["operator_id"]: entry for entry in operator_entries}
    apply_records_to_operator_period(operator_map, "day", day_records)
    apply_records_to_operator_period(operator_map, "week", week_records)
    apply_records_to_operator_period(operator_map, "month", month_records)

    output = []
    for operator_entry in operator_map.values():
        operator_entry["machines"] = sorted(machine for machine in operator_entry["machines"] if machine)
        output.append(operator_entry)

    output.sort(
        key=lambda item: (
            -int(item["day"].get("records_count", 0)),
            -float(item["week"].get("efficiency_percent", 0.0)),
            item["operator_name"],
        )
    )
    return output


def filter_records_by_operator(records: list[dict], operator_id: str | None) -> list[dict]:
    if not operator_id:
        return records

    filtered = []
    for record in records:
        record_operator_id = str(record.get("operator_id") or "").strip()
        record_operator_name = record.get("operator_name") or UNKNOWN_OPERATOR_LABEL
        resolved_operator_id = record_operator_id or f"name:{record_operator_name}"
        if resolved_operator_id == operator_id:
            filtered.append(record)
    return filtered


def resolve_selected_operator_id(
    requested_operator_id: str | None,
    operators: list[dict],
) -> str | None:
    if not operators:
        return None

    if requested_operator_id:
        available_operator_ids = {
            str(entry.get("operator_id") or "").strip()
            for entry in operators
        }
        if requested_operator_id in available_operator_ids:
            return requested_operator_id

    return None


def resolve_saved_period(period: str | None) -> str:
    candidate = (period or "all").strip().lower()
    if candidate not in {"all", "day", "week", "month"}:
        raise ValueError(f"Unsupported saved period: {candidate}")
    return candidate


def resolve_saved_modbus_period(period: str | None) -> str:
    candidate = (period or "day").strip().lower()
    if candidate not in {"day", "week", "month"}:
        raise ValueError(f"Unsupported saved MODBUS period: {candidate}")
    return candidate


def resolve_saved_modbus_window(period: str, now: datetime) -> tuple[datetime, datetime, bool, str]:
    if period == "day":
        start = datetime.combine(date.today(), time.min)
        end = now
        return start, end, False, "Ziua curenta (00:00 - acum)"

    if period == "week":
        current_week_start = datetime.combine(date.today(), time.min) - timedelta(days=date.today().weekday())
        start = current_week_start - timedelta(days=7)
        end = current_week_start
        return start, end, True, "Saptamana completa anterioara (Luni - Duminica)"

    current_month_start = datetime.combine(date.today().replace(day=1), time.min)
    previous_month_last_day = current_month_start - timedelta(days=1)
    start = datetime.combine(previous_month_last_day.replace(day=1).date(), time.min)
    end = current_month_start
    return start, end, True, "Luna completa anterioara"


def resolve_saved_modbus_prometheus_range(period: str) -> str:
    normalized = resolve_saved_modbus_period(period)
    return {
        "day": "2d",
        "week": "21d",
        "month": "70d",
    }[normalized]


def build_saved_modbus_payload(period: str = "day", operator_id: str | None = None) -> dict:
    normalized_period = resolve_saved_modbus_period(period)
    now = now_local()
    window_start, window_end, is_closed_period, window_label = resolve_saved_modbus_window(normalized_period, now)
    records: list[dict] = []
    data_source = "sqlite-fallback"
    if SAVED_RECORDS_PROMETHEUS_ENABLED:
        try:
            period_range = resolve_saved_modbus_prometheus_range(normalized_period)
            prometheus_records = build_prometheus_saved_records_for_range(
                period_range,
                machine_key="laser1modbus",
            )
            filtered_prometheus_records: list[dict] = []
            for record in prometheus_records:
                completed_at_raw = (
                    record.get("table_change_ended_at")
                    or record.get("created_at")
                    or record.get("table_change_started_at")
                )
                if not completed_at_raw:
                    continue
                try:
                    completed_at = parse_timestamp(completed_at_raw)
                except (TypeError, ValueError):
                    continue
                if window_start <= completed_at <= window_end:
                    filtered_prometheus_records.append(record)
            if filtered_prometheus_records:
                records = filtered_prometheus_records
                data_source = "prometheus"
        except Exception:
            pass

    if not records:
        records = fetch_saved_cycles_between(window_start, window_end, machine_key="laser1modbus")
    machine_label = MACHINE_DEFINITIONS.get("laser1modbus", {}).get("label", "LASER1MODBUS")
    machine_profile = get_machine_profile("laser1modbus")
    cached_operator_snapshot = _operator_snapshot_cache.get(machine_profile.get("workcenter_id"))
    operator_snapshot = (
        cached_operator_snapshot.get("payload", {})
        if cached_operator_snapshot
        and (time_module.time() - float(cached_operator_snapshot.get("cached_at", 0))) < OPERATOR_CACHE_SECONDS
        else {}
    )
    operator_map: dict[str, dict] = {}

    for operator in operator_snapshot.get("operators", []):
        employee_id = str(operator.get("employee_id") or "").strip()
        operator_name = (operator.get("full_name") or UNKNOWN_OPERATOR_LABEL).strip() or UNKNOWN_OPERATOR_LABEL
        resolved_operator_id = employee_id or f"name:{operator_name}"
        operator_map[resolved_operator_id] = {
            "operator_id": resolved_operator_id,
            "employee_id": employee_id,
            "operator_name": operator_name,
            "records_count": 0,
            "_efficiency_total": 0.0,
        }

    for record in records:
        employee_id = str(record.get("operator_id") or "").strip()
        operator_name = (record.get("operator_name") or UNKNOWN_OPERATOR_LABEL).strip() or UNKNOWN_OPERATOR_LABEL
        resolved_operator_id = employee_id or f"name:{operator_name}"
        operator_entry = operator_map.setdefault(
            resolved_operator_id,
            {
                "operator_id": resolved_operator_id,
                "employee_id": employee_id,
                "operator_name": operator_name,
                "records_count": 0,
                "_efficiency_total": 0.0,
            },
        )
        operator_entry["records_count"] += 1
        operator_entry["_efficiency_total"] += float(record.get("efficiency_percent") or 0.0)

    operators: list[dict] = []
    for operator_entry in operator_map.values():
        records_count = int(operator_entry.get("records_count") or 0)
        average_efficiency_percent = round(operator_entry["_efficiency_total"] / records_count, 1) if records_count else 0.0
        operators.append(
            {
                "operator_id": operator_entry["operator_id"],
                "employee_id": operator_entry["employee_id"],
                "operator_name": operator_entry["operator_name"],
                "machine_label": machine_label,
                "records_count": records_count,
                "average_efficiency_percent": average_efficiency_percent,
            }
        )

    operators.sort(
        key=lambda item: (
            -int(item.get("records_count") or 0),
            item.get("operator_name") or UNKNOWN_OPERATOR_LABEL,
        )
    )
    selected_operator_id = None
    requested_operator_id = (operator_id or "").strip()
    if requested_operator_id:
        available_operator_ids = {
            str(item.get("operator_id") or "").strip()
            for item in operators
        }
        if requested_operator_id in available_operator_ids:
            selected_operator_id = requested_operator_id
    filtered_records = filter_saved_cycle_records_by_operator(records, selected_operator_id)
    efficiencies = [float(record.get("efficiency_percent") or 0.0) for record in filtered_records]
    average_efficiency_percent = round(sum(efficiencies) / len(efficiencies), 1) if efficiencies else 0.0

    return {
        "view": "saved_modbus",
        "period": normalized_period,
        "machine_key": "laser1modbus",
        "machine_label": machine_label,
        "operators": operators,
        "selected_operator_id": selected_operator_id,
        "records_count": len(filtered_records),
        "records": filtered_records,
        "efficiencies": efficiencies,
        "average_efficiency_percent": average_efficiency_percent,
        "window_started_at": window_start.isoformat(timespec="seconds"),
        "window_ended_at": window_end.isoformat(timespec="seconds"),
        "window_label": window_label,
        "is_closed_period": is_closed_period,
        "updated_at": now.isoformat(timespec="seconds"),
        "data_source": data_source,
    }


def fetch_saved_cycles_for_period(machine_key: str | None, period: str) -> list[dict]:
    period = resolve_saved_period(period)
    now = now_local()

    if period == "all":
        return fetch_saved_cycles_all(machine_key=machine_key)
    if period == "day":
        return fetch_saved_cycles_between(datetime.combine(date.today(), time.min), now, machine_key=machine_key)
    if period == "week":
        week_start = datetime.combine(date.today(), time.min) - timedelta(days=date.today().weekday())
        return fetch_saved_cycles_between(week_start, now, machine_key=machine_key)
    if period == "month":
        month_start = datetime.combine(date.today().replace(day=1), time.min)
        return fetch_saved_cycles_between(month_start, now, machine_key=machine_key)

    return []


def build_saved_cycles_payload(machine_key: str | None = None, period: str = "all") -> dict:
    normalized_period = resolve_saved_period(period)
    operator_id = (request.args.get("operator_id") or "").strip() or None
    if SAVED_RECORDS_PROMETHEUS_ENABLED:
        try:
            records = build_prometheus_saved_records(normalized_period, operator_id=None)
            if machine_key:
                records = [
                    record
                    for record in records
                    if str(record.get("machine_key") or "").strip() == machine_key
                ]

            operator_entries_from_records: dict[str, dict] = {}
            for record in records:
                record_operator_name = (record.get("operator_name") or UNKNOWN_OPERATOR_LABEL).strip() or UNKNOWN_OPERATOR_LABEL
                record_employee_id = str(record.get("operator_id") or "").strip()
                record_operator_id = record_employee_id or f"name:{record_operator_name}"
                entry = operator_entries_from_records.setdefault(
                    record_operator_id,
                    build_operator_entry(record_operator_id, record_employee_id, record_operator_name),
                )
                entry["machines"].add(record.get("machine_label") or record.get("machine_key") or "")

            try:
                operator_entries = build_prometheus_operator_summaries()
            except Exception:
                operator_entries = []

            operators = finalize_operator_entries(
                merge_operator_seed_entries(
                    merge_operator_seed_entries(
                        operator_entries,
                        list(operator_entries_from_records.values()),
                    ),
                    build_workcenter_operator_summaries(),
                )
            )

            selected_operator_id = resolve_selected_operator_id(operator_id, operators)
            filtered_records = filter_saved_cycle_records_by_operator(records, selected_operator_id)
            if operators or filtered_records:
                return {
                    "view": "saved",
                    "selected_machine_key": machine_key,
                    "period": normalized_period,
                    "operators": operators,
                    "selected_operator_id": selected_operator_id,
                    "records": filtered_records,
                    "records_count": len(filtered_records),
                    "data_source": "prometheus",
                    "updated_at": now_local().isoformat(timespec="seconds"),
                }
        except Exception:
            pass

    records = fetch_saved_cycles_for_period(machine_key=machine_key, period=normalized_period)
    operators = build_sqlite_operator_summaries(machine_key=machine_key)
    selected_operator_id = resolve_selected_operator_id(operator_id, operators)
    records = filter_saved_cycle_records_by_operator(records, selected_operator_id)
    return {
        "view": "saved",
        "selected_machine_key": machine_key,
        "period": normalized_period,
        "operators": operators,
        "selected_operator_id": selected_operator_id,
        "records": records,
        "summary": summarize_saved_cycles(records),
        "reports": build_saved_cycles_reports(machine_key),
        "reports_by_machine": build_saved_cycles_reports_by_machine(),
        "records_count": len(records),
        "data_source": "sqlite-fallback",
        "updated_at": now_local().isoformat(timespec="seconds"),
    }


def normalize_context_token(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized.lower() in {"", "necitit", "n/a"}:
        return ""
    return normalized


def resolve_snapshot_context(snapshot: dict | None) -> dict[str, str]:
    if not snapshot:
        return {"program": "", "material": "", "setup_signature": ""}

    selected_program = normalize_context_token(snapshot.get("selected_program"))
    active_program = normalize_context_token(snapshot.get("active_program"))
    material = normalize_context_token(snapshot.get("material"))
    setup_signature = normalize_context_token(resolve_abkant_tool_signature(snapshot))
    return {
        "program": selected_program or active_program,
        "material": material,
        "setup_signature": setup_signature,
    }


def context_requires_stats_reset(
    machine_key: str,
    previous_snapshot: dict | None,
    current_snapshot: dict | None,
) -> bool:
    machine_key = ensure_machine_key(machine_key)
    previous_context = resolve_snapshot_context(previous_snapshot)
    current_context = resolve_snapshot_context(current_snapshot)
    if not (current_context["program"] or current_context["material"] or current_context["setup_signature"]):
        return False
    previous_signals = (previous_snapshot or {}).get("derived_signals") or {}
    current_signals = (current_snapshot or {}).get("derived_signals") or {}
    machine_restarted = not bool(previous_signals.get("machine_on")) and bool(current_signals.get("machine_on"))
    if machine_key == "laser1modbus":
        return previous_context["program"] != current_context["program"]
    if machine_key == "laser1":
        return previous_context["program"] != current_context["program"] or machine_restarted
    return previous_context != current_context or machine_restarted


def get_machine_runtime(machine_key: str) -> dict:
    with get_sqlite_connection() as connection:
        row = connection.execute(
            """
            SELECT
                machine_key,
                last_snapshot_json,
                pending_cycle_json,
                stats_anchor_started_at,
                stats_anchor_context_json,
                updated_at
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
            "stats_anchor": None,
            "updated_at": None,
        }

    return {
        "machine_key": row["machine_key"],
        "last_snapshot": json.loads(row["last_snapshot_json"]) if row["last_snapshot_json"] else None,
        "pending_cycle": json.loads(row["pending_cycle_json"]) if row["pending_cycle_json"] else None,
        "stats_anchor": (
            {
                "started_at": row["stats_anchor_started_at"],
                "context": json.loads(row["stats_anchor_context_json"]) if row["stats_anchor_context_json"] else {},
            }
            if row["stats_anchor_started_at"]
            else None
        ),
        "updated_at": row["updated_at"],
    }


def save_machine_runtime(
    machine_key: str,
    last_snapshot: dict | None,
    pending_cycle: dict | None,
    stats_anchor=RUNTIME_VALUE_UNCHANGED,
) -> None:
    if stats_anchor is RUNTIME_VALUE_UNCHANGED:
        stats_anchor = get_machine_runtime(machine_key).get("stats_anchor")

    updated_at = now_local().isoformat(timespec="seconds")
    stats_anchor_started_at = stats_anchor.get("started_at") if stats_anchor else None
    stats_anchor_context = stats_anchor.get("context") if stats_anchor else None
    with get_sqlite_connection() as connection:
        connection.execute(
            """
            INSERT INTO machine_runtime (
                machine_key,
                last_snapshot_json,
                pending_cycle_json,
                stats_anchor_started_at,
                stats_anchor_context_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(machine_key) DO UPDATE SET
                last_snapshot_json = excluded.last_snapshot_json,
                pending_cycle_json = excluded.pending_cycle_json,
                stats_anchor_started_at = excluded.stats_anchor_started_at,
                stats_anchor_context_json = excluded.stats_anchor_context_json,
                updated_at = excluded.updated_at
            """,
            (
                machine_key,
                json.dumps(last_snapshot, ensure_ascii=False) if last_snapshot is not None else None,
                json.dumps(pending_cycle, ensure_ascii=False) if pending_cycle is not None else None,
                stats_anchor_started_at,
                json.dumps(stats_anchor_context, ensure_ascii=False) if stats_anchor_context is not None else None,
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
        "upper_tool": snapshot.get("upper_tool"),
        "lower_tool": snapshot.get("lower_tool"),
        "setup_signature": resolve_abkant_tool_signature(snapshot),
        "setup_changed": bool(snapshot.get("setup_changed")),
        "cutting_started_at": current_signals["cutting_active"]["changed_at"] if current_signals["cutting_active"]["active"] else None,
        "table_change_started_at": now_local().isoformat(timespec="seconds"),
        "source": snapshot.get("source", "live-ocr"),
        "snapshot_json": snapshot,
    }
    runtime = get_machine_runtime(machine_key)
    stats_anchor = runtime.get("stats_anchor") or {}
    cycle_window_started_at = ""
    anchor_started_at_raw = str(stats_anchor.get("started_at") or "").strip()
    if anchor_started_at_raw:
        try:
            anchor_started_at = parse_timestamp(anchor_started_at_raw)
            table_change_started_at = parse_timestamp(pending_cycle["table_change_started_at"])
            if anchor_started_at <= table_change_started_at:
                cycle_window_started_at = anchor_started_at.isoformat(timespec="seconds")
        except Exception:
            cycle_window_started_at = ""
    if not cycle_window_started_at:
        cycle_window_started_at = (
            current_signals["machine_on"]["changed_at"]
            if current_signals["machine_on"]["active"]
            else None
        ) or pending_cycle.get("cutting_started_at") or pending_cycle["table_change_started_at"]
    pending_cycle["cycle_window_started_at"] = cycle_window_started_at
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
    cycle_window_started_at_raw = str(pending_cycle.get("cycle_window_started_at") or "").strip()
    if cycle_window_started_at_raw:
        try:
            cycle_window_started_at = parse_timestamp(cycle_window_started_at_raw)
            if cycle_window_started_at > table_change_ended_at:
                cycle_window_started_at_raw = ""
        except Exception:
            cycle_window_started_at_raw = ""
    cycle_window_start_raw = (
        cycle_window_started_at_raw
        or cutting_started_at_raw
        or pending_cycle.get("table_change_started_at")
    )
    cycle_duration_seconds = None
    if cutting_started_at_raw:
        cycle_duration_seconds = max(
            int((table_change_started_at - parse_timestamp(cutting_started_at_raw)).total_seconds()),
            0,
        )
    cycle_metrics = calculate_saved_cycle_metrics(
        machine_key=machine_key,
        cutting_started_at=cycle_window_start_raw,
        table_change_started_at=pending_cycle.get("table_change_started_at"),
        table_change_ended_at=table_change_ended_at.isoformat(timespec="seconds"),
        fallback_cutting_seconds=cycle_duration_seconds,
        fallback_table_change_seconds=table_change_duration_seconds,
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
                f"""
                INSERT INTO saved_cycles (
                    machine_key,
                    workcenter_id,
                    operator_id,
                    operator_name,
                    selected_program,
                    active_program,
                    material,
                    program_status,
                    upper_tool,
                    lower_tool,
                    setup_signature,
                    setup_changed,
                    cutting_started_at,
                    table_change_started_at,
                    table_change_ended_at,
                    table_change_duration_seconds,
                    cycle_duration_seconds,
                    machine_on_duration_seconds,
                    idle_duration_seconds,
                    efficiency_percent,
                    source,
                    snapshot_json,
                    created_at
                )
                VALUES ({SAVED_CYCLE_INSERT_PLACEHOLDERS})
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
                    pending_cycle.get("upper_tool"),
                    pending_cycle.get("lower_tool"),
                    pending_cycle.get("setup_signature"),
                    1 if pending_cycle.get("setup_changed") else 0,
                    cycle_window_start_raw,
                    pending_cycle.get("table_change_started_at"),
                    table_change_ended_at.isoformat(timespec="seconds"),
                    table_change_duration_seconds,
                    cycle_metrics["cutting_duration_seconds"],
                    cycle_metrics["machine_on_duration_seconds"],
                    cycle_metrics["idle_duration_seconds"],
                    cycle_metrics["efficiency_percent"],
                    pending_cycle.get("source", "live-ocr"),
                    json.dumps(
                        {
                            "pending_snapshot": pending_cycle.get("snapshot_json"),
                            "resume_snapshot": current_snapshot,
                            "close_reason": "table_change_completed",
                        },
                        ensure_ascii=False,
                    ),
                    table_change_ended_at.isoformat(timespec="seconds"),
                ),
            )
            connection.commit()

    stats_anchor = RUNTIME_VALUE_UNCHANGED
    if machine_key in PROGRAM_STATS_MACHINE_KEYS:
        stats_anchor = {
            "started_at": table_change_ended_at.isoformat(timespec="seconds"),
            "context": resolve_snapshot_context(current_snapshot),
        }
    save_machine_runtime(machine_key, current_snapshot, None, stats_anchor=stats_anchor)


def resolve_snapshot_program(snapshot: dict | None) -> str:
    if not snapshot:
        return ""

    selected_program = normalize_context_token(snapshot.get("selected_program"))
    active_program = normalize_context_token(snapshot.get("active_program"))
    return selected_program or active_program


def save_cycle_on_program_change(
    machine_key: str,
    machine_profile: dict,
    previous_snapshot: dict,
    current_snapshot: dict,
    operator_snapshot: dict,
    current_signals: dict[str, dict],
    previous_stats_anchor: dict | None = None,
) -> None:
    previous_program = resolve_snapshot_program(previous_snapshot)
    current_program = resolve_snapshot_program(current_snapshot)
    if not previous_program or not current_program or previous_program == current_program:
        return

    if machine_key == "abkant" and (
        resolve_abkant_tool_signature(previous_snapshot)
        or resolve_abkant_tool_signature(current_snapshot)
    ):
        return

    operator = operator_snapshot.get("primary_operator") or {}
    change_detected_at = now_local()
    previous_anchor_started_at_raw = ""
    if isinstance(previous_stats_anchor, dict):
        anchor_candidate = str(previous_stats_anchor.get("started_at") or "").strip()
        if anchor_candidate:
            try:
                anchor_started_at = parse_timestamp(anchor_candidate)
                if anchor_started_at <= change_detected_at:
                    previous_anchor_started_at_raw = anchor_started_at.isoformat(timespec="seconds")
            except Exception:
                previous_anchor_started_at_raw = ""

    cutting_started_at_raw = current_signals["cutting_active"]["changed_at"] if current_signals["cutting_active"]["active"] else None
    cycle_window_start_raw = cutting_started_at_raw or previous_anchor_started_at_raw
    cycle_duration_seconds = None
    if cutting_started_at_raw:
        cycle_duration_seconds = max(
            int((change_detected_at - parse_timestamp(cutting_started_at_raw)).total_seconds()),
            0,
        )
    cycle_metrics = calculate_saved_cycle_metrics(
        machine_key=machine_key,
        cutting_started_at=cycle_window_start_raw,
        table_change_started_at=change_detected_at.isoformat(timespec="seconds"),
        table_change_ended_at=change_detected_at.isoformat(timespec="seconds"),
        fallback_cutting_seconds=cycle_duration_seconds,
        fallback_table_change_seconds=0,
    )
    if (
        int(cycle_metrics["machine_on_duration_seconds"] or 0) <= 0
        and int(cycle_metrics["cutting_duration_seconds"] or 0) <= 0
        and int(cycle_metrics["idle_duration_seconds"] or 0) <= 0
        and int(cycle_metrics["table_change_duration_seconds"] or 0) <= 0
    ):
        return

    recent_cutoff_iso = datetime.fromtimestamp(change_detected_at.timestamp() - 45).isoformat(timespec="seconds")
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
                previous_program,
                recent_cutoff_iso,
            ),
        ).fetchone()
        if duplicate is None:
            connection.execute(
                f"""
                INSERT INTO saved_cycles (
                    machine_key,
                    workcenter_id,
                    operator_id,
                    operator_name,
                    selected_program,
                    active_program,
                    material,
                    program_status,
                    upper_tool,
                    lower_tool,
                    setup_signature,
                    setup_changed,
                    cutting_started_at,
                    table_change_started_at,
                    table_change_ended_at,
                    table_change_duration_seconds,
                    cycle_duration_seconds,
                    machine_on_duration_seconds,
                    idle_duration_seconds,
                    efficiency_percent,
                    source,
                    snapshot_json,
                    created_at
                )
                VALUES ({SAVED_CYCLE_INSERT_PLACEHOLDERS})
                """,
                (
                    machine_key,
                    machine_profile.get("workcenter_id"),
                    operator.get("employee_id"),
                    operator.get("full_name"),
                    previous_snapshot.get("selected_program") or previous_program,
                    previous_snapshot.get("active_program") or previous_program,
                    previous_snapshot.get("material"),
                    f"{previous_snapshot.get('program_status') or 'Necitit'} -> program schimbat",
                    previous_snapshot.get("upper_tool"),
                    previous_snapshot.get("lower_tool"),
                    resolve_abkant_tool_signature(previous_snapshot),
                    0,
                    cycle_window_start_raw,
                    change_detected_at.isoformat(timespec="seconds"),
                    change_detected_at.isoformat(timespec="seconds"),
                    0,
                    cycle_metrics["cutting_duration_seconds"],
                    cycle_metrics["machine_on_duration_seconds"],
                    cycle_metrics["idle_duration_seconds"],
                    cycle_metrics["efficiency_percent"],
                    previous_snapshot.get("source", "live-ocr"),
                    json.dumps(
                        {
                            "pending_snapshot": previous_snapshot,
                            "resume_snapshot": current_snapshot,
                            "close_reason": "program_change",
                        },
                        ensure_ascii=False,
                    ),
                    change_detected_at.isoformat(timespec="seconds"),
                ),
            )
            connection.commit()


def fetch_current_signals(machine_key: str) -> dict[str, dict]:
    current_signals: dict[str, dict] = {}
    with get_sqlite_connection() as connection:
        for signal_name in SIGNAL_DEFINITIONS:
            meta = resolve_signal_definition(machine_key, signal_name)
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
                "button_on_label": meta.get("button_on_label"),
                "button_off_label": meta.get("button_off_label"),
                "metric_label": meta.get("metric_label", meta["label"]),
                "report_label": meta.get("report_label", meta["label"]),
                "visible": signal_name != "idle_abort" or machine_uses_modbus(machine_key),
            }
    return current_signals


def derive_machine_state(machine_key: str, current_signals: dict[str, dict]) -> dict:
    if not current_signals["machine_on"]["active"]:
        return {"key": "off", **resolve_state_definition(machine_key, "off")}
    if current_signals["cutting_active"]["active"]:
        return {"key": "cutting", **resolve_state_definition(machine_key, "cutting")}
    if current_signals["table_change"]["active"]:
        return {"key": "table_change", **resolve_state_definition(machine_key, "table_change")}
    if current_signals.get("idle_abort", {}).get("active"):
        return {"key": "idle", **resolve_state_definition(machine_key, "idle")}
    return {"key": "ready", **resolve_state_definition(machine_key, "ready")}


def derive_modbus_machine_state_from_snapshot(snapshot: dict | None, fallback_state: dict) -> dict:
    if not snapshot:
        return fallback_state

    if not snapshot.get("available") or not snapshot.get("connected"):
        return {"key": "off", **resolve_state_definition("laser1modbus", "off")}

    derived = snapshot.get("derived_signals") or {}
    signal_view = {
        "machine_on": {"active": bool(derived.get("machine_on", False))},
        "cutting_active": {"active": bool(derived.get("cutting_active", False))},
        "table_change": {"active": bool(derived.get("table_change", False))},
        "idle_abort": {"active": bool(derived.get("idle_abort", False))},
    }
    state_from_snapshot = derive_machine_state("laser1modbus", signal_view)
    if state_from_snapshot["key"] != "off":
        return state_from_snapshot

    # Daca Modbus e conectat, dar bitul Machine ON e 0, evitam mesajul fals "Modbus indisponibil".
    return {"key": "off", **resolve_state_definition("", "off")}


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


def resolve_stats_window_start(machine_key: str, now: datetime) -> datetime:
    start_of_day = datetime.combine(date.today(), time.min)
    if machine_key not in PROGRAM_STATS_MACHINE_KEYS:
        return start_of_day

    runtime = get_machine_runtime(machine_key)
    stats_anchor = runtime.get("stats_anchor") or {}
    started_at_raw = stats_anchor.get("started_at")
    if not started_at_raw:
        return start_of_day

    try:
        started_at = parse_timestamp(started_at_raw)
    except Exception:
        return start_of_day

    if started_at > now:
        return start_of_day
    return started_at


def format_seconds(total_seconds: int) -> str:
    hours, remainder = divmod(max(total_seconds, 0), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_today_stats(machine_key: str) -> dict:
    now = now_local()
    stats_window_start = resolve_stats_window_start(machine_key, now)
    elapsed_seconds = max(int((now - stats_window_start).total_seconds()), 1)
    machine_on_seconds = calculate_active_seconds(machine_key, "machine_on", stats_window_start, now)
    cutting_seconds = calculate_active_seconds(machine_key, "cutting_active", stats_window_start, now)
    table_change_seconds = calculate_active_seconds(machine_key, "table_change", stats_window_start, now)
    idle_seconds = (
        calculate_active_seconds(machine_key, "idle_abort", stats_window_start, now)
        if machine_supports_idle_signal(machine_key)
        else max(machine_on_seconds - cutting_seconds - table_change_seconds, 0)
    )
    utilization = round((cutting_seconds / machine_on_seconds) * 100, 1) if machine_on_seconds else 0.0
    randament = calculate_runtime_efficiency_percent(
        machine_on_seconds,
        cutting_seconds,
        table_change_seconds,
    )
    availability = round((cutting_seconds / machine_on_seconds) * 100, 1) if machine_on_seconds else 0.0
    cutting_meta = resolve_signal_definition(machine_key, "cutting_active")
    table_change_meta = resolve_signal_definition(machine_key, "table_change")
    availability_prefix = (
        "Disponibilitate indoire/feed_activ"
        if machine_key == "abkant"
        else "Disponibilitate taiere/feed_activ"
    )

    return {
        "machine_on_seconds": machine_on_seconds,
        "machine_on_label": format_seconds(machine_on_seconds),
        "cutting_seconds": cutting_seconds,
        "cutting_label": format_seconds(cutting_seconds),
        "cutting_metric_label": cutting_meta.get("metric_label", cutting_meta["label"]),
        "table_change_seconds": table_change_seconds,
        "table_change_label": format_seconds(table_change_seconds),
        "table_change_metric_label": table_change_meta.get("metric_label", table_change_meta["label"]),
        "idle_seconds": idle_seconds,
        "idle_label": format_seconds(idle_seconds),
        "utilization_percent": utilization,
        "randament_percent": randament,
        "availability_percent": availability,
        "availability_label": f"{availability_prefix} {availability}%",
        "production_window_label": format_seconds(elapsed_seconds),
        "production_window_started_at": stats_window_start.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
    }


def count_saved_cycles(machine_key: str) -> int:
    with get_sqlite_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM saved_cycles WHERE machine_key = ?",
            (machine_key,),
        ).fetchone()
    return int(row["total"]) if row else 0


def escape_prometheus_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def append_prometheus_metric(
    lines: list[str],
    name: str,
    value: int | float,
    labels: dict[str, str] | None = None,
) -> None:
    if labels:
        rendered_labels = ",".join(
            f'{key}="{escape_prometheus_label(label_value)}"'
            for key, label_value in sorted(labels.items())
        )
        lines.append(f"{name}{{{rendered_labels}}} {value}")
        return
    lines.append(f"{name} {value}")


def build_prometheus_metrics() -> str:
    lines = [
        "# HELP haba_machine_signal Current machine signals from the dashboard state store.",
        "# TYPE haba_machine_signal gauge",
        "# HELP haba_machine_state Current derived machine state.",
        "# TYPE haba_machine_state gauge",
        "# HELP haba_machine_seconds_today Daily accumulated seconds by metric.",
        "# TYPE haba_machine_seconds_today gauge",
        "# HELP haba_machine_percent_today Daily percentage metrics.",
        "# TYPE haba_machine_percent_today gauge",
        "# HELP haba_machine_saved_cycles_total Total saved cycles per machine.",
        "# TYPE haba_machine_saved_cycles_total gauge",
        "# HELP haba_machine_workcenter_id Configured workcenter id per machine.",
        "# TYPE haba_machine_workcenter_id gauge",
        "# HELP haba_saved_cycle_completed Completed saved cycles exposed as one time series per cycle.",
        "# TYPE haba_saved_cycle_completed gauge",
        "# HELP haba_saved_cycle_machine_on_seconds Machine ON seconds for a completed cycle.",
        "# TYPE haba_saved_cycle_machine_on_seconds gauge",
        "# HELP haba_saved_cycle_cutting_seconds Cutting or bending seconds for a completed cycle.",
        "# TYPE haba_saved_cycle_cutting_seconds gauge",
        "# HELP haba_saved_cycle_idle_seconds Idle seconds for a completed cycle.",
        "# TYPE haba_saved_cycle_idle_seconds gauge",
        "# HELP haba_saved_cycle_table_change_seconds Table change seconds for a completed cycle.",
        "# TYPE haba_saved_cycle_table_change_seconds gauge",
        "# HELP haba_saved_cycle_efficiency_percent Efficiency percent for a completed cycle.",
        "# TYPE haba_saved_cycle_efficiency_percent gauge",
        "# HELP haba_saved_cycle_setup_changed Setup change flag for a completed cycle.",
        "# TYPE haba_saved_cycle_setup_changed gauge",
    ]

    for machine_profile in get_machine_profiles():
        machine_key = machine_profile["key"]
        current_signals = fetch_current_signals(machine_key)
        current_state = derive_machine_state(machine_key, current_signals)
        stats = build_today_stats(machine_key)
        base_labels = {
            "machine_key": machine_key,
            "machine_label": machine_profile["label"],
        }

        for signal_name, signal in current_signals.items():
            append_prometheus_metric(
                lines,
                "haba_machine_signal",
                1 if bool(signal["active"]) else 0,
                {**base_labels, "signal_name": signal_name},
            )

        append_prometheus_metric(
            lines,
            "haba_machine_state",
            1,
            {**base_labels, "state_key": current_state["key"]},
        )

        for metric_name in ("machine_on", "cutting", "table_change", "idle"):
            append_prometheus_metric(
                lines,
                "haba_machine_seconds_today",
                int(stats.get(f"{metric_name}_seconds", 0) or 0),
                {**base_labels, "metric_name": metric_name},
            )

        for metric_name, metric_value in (
            ("randament", stats.get("randament_percent", 0)),
            ("availability", stats.get("availability_percent", 0)),
        ):
            append_prometheus_metric(
                lines,
                "haba_machine_percent_today",
                float(metric_value or 0),
                {**base_labels, "metric_name": metric_name},
            )

        append_prometheus_metric(
            lines,
            "haba_machine_saved_cycles_total",
            count_saved_cycles(machine_key),
            base_labels,
        )

        workcenter_id = machine_profile.get("workcenter_id")
        if workcenter_id is not None:
            append_prometheus_metric(
                lines,
                "haba_machine_workcenter_id",
                int(workcenter_id),
                base_labels,
            )

    for record in fetch_saved_cycles_all(limit=5000):
        cycle_labels = {
            "cycle_id": str(record["id"]),
            "machine_key": record["machine_key"],
            "machine_label": record["machine_label"],
            "workcenter_id": str(record["workcenter_id"] or ""),
            "operator_id": str(record["operator_id"] or ""),
            "operator_name": record["operator_name"],
            "selected_program": record["selected_program"],
            "active_program": record["active_program"],
            "material": record["material"],
            "program_status": record["program_status"],
            "upper_tool": record.get("upper_tool") or "n/a",
            "lower_tool": record.get("lower_tool") or "n/a",
            "setup_signature": record.get("setup_signature") or "",
            "setup_changed": "true" if bool(record.get("setup_changed")) else "false",
            "cutting_started_at": record["cutting_started_at"] or "",
            "table_change_started_at": record["table_change_started_at"] or "",
            "completed_at": record["table_change_ended_at"] or record["created_at"] or "",
            "close_reason": record.get("close_reason") or "",
            "close_reason_label": record.get("close_reason_label") or "",
            "next_program": record.get("next_program") or "",
            "source": record["source"] or "",
        }
        append_prometheus_metric(lines, "haba_saved_cycle_completed", 1, cycle_labels)
        append_prometheus_metric(
            lines,
            "haba_saved_cycle_machine_on_seconds",
            int(record["machine_on_duration_seconds"] or 0),
            cycle_labels,
        )
        append_prometheus_metric(
            lines,
            "haba_saved_cycle_cutting_seconds",
            int(record["cycle_duration_seconds"] or 0),
            cycle_labels,
        )
        append_prometheus_metric(
            lines,
            "haba_saved_cycle_idle_seconds",
            int(record["idle_duration_seconds"] or 0),
            cycle_labels,
        )
        append_prometheus_metric(
            lines,
            "haba_saved_cycle_table_change_seconds",
            int(record["table_change_duration_seconds"] or 0),
            cycle_labels,
        )
        append_prometheus_metric(
            lines,
            "haba_saved_cycle_efficiency_percent",
            float(record["efficiency_percent"] or 0),
            cycle_labels,
        )
        append_prometheus_metric(
            lines,
            "haba_saved_cycle_setup_changed",
            1 if bool(record.get("setup_changed")) else 0,
            cycle_labels,
        )

    lines.append("")
    return "\n".join(lines)


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


def enrich_abkant_snapshot_with_setup_state(
    snapshot: dict,
    previous_snapshot: dict | None,
    current_signals: dict[str, dict],
) -> dict:
    if not snapshot.get("available"):
        return snapshot

    derived_signals = dict(snapshot.get("derived_signals") or {})
    machine_on = bool(derived_signals.get("machine_on"))
    cutting_active = bool(derived_signals.get("cutting_active"))
    legacy_table_change = bool(derived_signals.get("table_change", False))
    previous_signature = resolve_abkant_tool_signature(previous_snapshot)
    current_signature = resolve_abkant_tool_signature(snapshot)
    old_table_change = bool(current_signals.get("table_change", {}).get("active"))
    tool_changed = bool(machine_on and previous_signature and current_signature and previous_signature != current_signature)

    if not machine_on:
        setup_change = False
    elif not current_signature:
        setup_change = legacy_table_change
    elif old_table_change:
        setup_change = not cutting_active
    else:
        setup_change = tool_changed and not cutting_active

    snapshot["upper_tool"] = normalize_abkant_tool_value(snapshot.get("upper_tool")) or "n/a"
    snapshot["lower_tool"] = normalize_abkant_tool_value(snapshot.get("lower_tool")) or "n/a"
    snapshot["setup_signature"] = current_signature
    snapshot["setup_changed"] = tool_changed
    derived_signals["table_change"] = setup_change
    snapshot["derived_signals"] = derived_signals

    if setup_change:
        snapshot["program_status"] = "Setup change"

    return snapshot


def sync_machine_events_from_live_snapshot(machine_key: str) -> dict | None:
    snapshot = get_live_machine_snapshot(machine_key)
    if not snapshot:
        return snapshot

    if not snapshot.get("available"):
        if machine_uses_modbus(machine_key):
            snapshot = {
                **snapshot,
                "derived_signals": {signal_name: False for signal_name in SIGNAL_DEFINITIONS},
            }
        else:
            return snapshot

    runtime = get_machine_runtime(machine_key)
    previous_snapshot = runtime.get("last_snapshot")
    stats_anchor = runtime.get("stats_anchor")
    current_signals = fetch_current_signals(machine_key)
    if machine_key == "abkant":
        snapshot = enrich_abkant_snapshot_with_setup_state(snapshot, previous_snapshot, current_signals)
    current_context = resolve_snapshot_context(snapshot)
    if snapshot.get("available") and context_requires_stats_reset(machine_key, previous_snapshot, snapshot):
        stats_anchor = {
            "started_at": now_local().isoformat(timespec="seconds"),
            "context": current_context,
        }
    elif snapshot.get("available") and not stats_anchor and (current_context["program"] or current_context["material"]):
        stats_anchor = {
            "started_at": now_local().isoformat(timespec="seconds"),
            "context": current_context,
        }

    save_machine_runtime(
        machine_key,
        snapshot,
        runtime.get("pending_cycle"),
        stats_anchor=stats_anchor,
    )

    derived_signals = snapshot.get("derived_signals") or {}
    machine_profile = get_machine_profile(machine_key)
    operator_snapshot = fetch_current_operator(machine_profile["workcenter_id"])

    if snapshot.get("available"):
        note = (
            f"selected={snapshot.get('selected_program')}; "
            f"active={snapshot.get('active_program')}; "
            f"material={snapshot.get('material')}; "
            f"upper={snapshot.get('upper_tool', 'n/a')}; "
            f"lower={snapshot.get('lower_tool', 'n/a')}; "
            f"status={snapshot.get('program_status')}"
        )
    else:
        note = f"Auto-stop: snapshot indisponibil. Motiv: {snapshot.get('message') or 'necunoscut'}"

    if snapshot.get("available") and previous_snapshot and not runtime.get("pending_cycle"):
        save_cycle_on_program_change(
            machine_key,
            machine_profile,
            previous_snapshot,
            snapshot,
            operator_snapshot,
            current_signals,
            runtime.get("stats_anchor"),
        )

    old_table_change = bool(current_signals["table_change"]["active"])
    new_table_change = bool(derived_signals.get("table_change", False))
    if new_table_change and not old_table_change:
        open_pending_cycle(machine_key, machine_profile, snapshot, operator_snapshot, current_signals)
    elif old_table_change and not new_table_change:
        finalize_pending_cycle(machine_key, snapshot)

    for signal_name in SIGNAL_DEFINITIONS:
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
        if current_signals.get("idle_abort", {}).get("active"):
            events.append(
                {
                    "signal_name": "idle_abort",
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
        if current_signals.get("idle_abort", {}).get("active"):
            events.append(
                {
                    "signal_name": "idle_abort",
                    "value": False,
                    "note": "Auto-stop: taierea a iesit din idle/abort.",
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
        if current_signals.get("idle_abort", {}).get("active"):
            events.append(
                {
                    "signal_name": "idle_abort",
                    "value": False,
                    "note": "Auto-stop: schimbul de masa a iesit din idle/abort.",
                }
            )
        events.append({"signal_name": "table_change", "value": True, "note": None})
        return events

    if signal_name == "idle_abort" and target_value:
        if not current_signals["machine_on"]["active"]:
            events.append(
                {
                    "signal_name": "machine_on",
                    "value": True,
                    "note": "Auto-start: idle/abort a pornit masina.",
                }
            )
        if current_signals["cutting_active"]["active"]:
            events.append(
                {
                    "signal_name": "cutting_active",
                    "value": False,
                    "note": "Auto-stop: idle/abort a oprit taierea.",
                }
            )
        if current_signals["table_change"]["active"]:
            events.append(
                {
                    "signal_name": "table_change",
                    "value": False,
                    "note": "Auto-stop: idle/abort a oprit schimbul de masa.",
                }
            )
        events.append({"signal_name": "idle_abort", "value": True, "note": None})
        return events

    events.append({"signal_name": signal_name, "value": target_value, "note": None})
    return events


def snapshot_differs_from_current_signals(
    snapshot: dict | None,
    current_signals: dict[str, dict],
) -> bool:
    if not snapshot or not snapshot.get("available"):
        return False

    derived_signals = snapshot.get("derived_signals") or {}
    for signal_name in SIGNAL_DEFINITIONS:
        if bool(derived_signals.get(signal_name, False)) != bool(current_signals.get(signal_name, {}).get("active")):
            return True
    return False


def snapshot_is_stale(snapshot: dict | None, max_age_seconds: int = SNAPSHOT_FRESHNESS_SECONDS) -> bool:
    if not snapshot:
        return True

    captured_at_raw = snapshot.get("captured_at")
    if not captured_at_raw:
        return True

    try:
        captured_at = parse_timestamp(captured_at_raw)
    except Exception:
        return True

    return (now_local() - captured_at).total_seconds() >= max_age_seconds


def build_dashboard_payload(machine_key: str = DEFAULT_MACHINE_KEY) -> dict:
    machine_key = ensure_machine_key(machine_key)
    if machine_key in HIDDEN_MACHINE_KEYS:
        machine_key = DEFAULT_MACHINE_KEY
    machine_profile = get_machine_profile(machine_key)
    if BACKGROUND_SYNC_ENABLED:
        runtime = get_machine_runtime(machine_key)
        live_extraction = runtime.get("last_snapshot")
        current_signals = fetch_current_signals(machine_key)
        needs_live_refresh = (
            live_extraction is None
            or snapshot_is_stale(live_extraction)
            or snapshot_differs_from_current_signals(live_extraction, current_signals)
        )
        # Pentru LASER1MODBUS fortam fallback-ul in request cand snapshotul lipseste/stale,
        # ca sa nu depindem exclusiv de thread-ul de background (care poate lipsi in unele deployment-uri).
        should_sync_in_request = REQUEST_LIVE_SYNC_ENABLED or machine_uses_modbus(machine_key)
        if needs_live_refresh and should_sync_in_request:
            live_extraction = sync_machine_events_from_live_snapshot(machine_key)
            current_signals = fetch_current_signals(machine_key)
    else:
        live_extraction = sync_machine_events_from_live_snapshot(machine_key)
        current_signals = fetch_current_signals(machine_key)
    operator_snapshot = fetch_current_operator(machine_profile["workcenter_id"])
    current_state = derive_machine_state(machine_key, current_signals)
    if machine_key == "laser1modbus":
        current_state = derive_modbus_machine_state_from_snapshot(live_extraction, current_state)
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
        "modbus_config": machine_profile.get("modbus_config"),
        "workcenter_id": machine_profile["workcenter_id"],
        "current_state": current_state,
        "current_signals": current_signals,
        "stats_today": stats_today,
        "operator_snapshot": operator_snapshot,
        "real_data_source": get_real_data_settings(machine_profile),
        "machine_feeds": build_machine_feeds(machine_key),
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
        css_asset_version=get_static_asset_version("app.css"),
        js_asset_version=get_static_asset_version("app.js"),
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
        machine_key = ensure_machine_key(machine_key)
        machine = get_machine_profile(machine_key)
        message_parts: list[str] = []

        if "workcenter_id" in data:
            workcenter_id = parse_optional_int(data.get("workcenter_id"))
            machine = update_machine_workcenter(machine_key, workcenter_id)
            message_parts.append("WorkCenter actualizat")

        if "modbus_config" in data:
            update_machine_modbus_config(machine_key, data.get("modbus_config") or {})
            machine = get_machine_profile(machine_key)
            message_parts.append("Configuratia Modbus a fost salvata")

        if not message_parts:
            raise ValueError("Nu am primit nimic de actualizat.")
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify(
        {
            "success": True,
            "message": ". ".join(message_parts) + ".",
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


@app.route("/api/environment")
def environment_snapshot():
    return jsonify(fetch_esp32_environment_snapshot())


@app.route("/api/saved-records")
def saved_records():
    machine_key = request.args.get("machine")
    period = request.args.get("period", "all")
    if machine_key:
        try:
            machine_key = ensure_machine_key(machine_key)
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

    try:
        return jsonify(build_saved_cycles_payload(machine_key, period=period))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@app.route("/api/saved-records-modbus")
def saved_records_modbus():
    period = request.args.get("period", "day")
    operator_id = (request.args.get("operator_id") or "").strip() or None
    try:
        return jsonify(build_saved_modbus_payload(period=period, operator_id=operator_id))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@app.route("/api/camera-feed/<machine_key>", defaults={"feed_key": "camera"})
@app.route("/api/camera-feed/<machine_key>/<feed_key>")
def camera_feed(machine_key: str, feed_key: str):
    try:
        machine_key = ensure_machine_key(machine_key)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    content, error_message, content_type = fetch_camera_feed_content(machine_key, feed_key=feed_key)
    if content is None:
        return jsonify({"success": False, "message": error_message or "Nu am putut citi camera."}), 502

    response = Response(content, mimetype=(content_type or "image/jpeg"))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.route("/metrics")
def prometheus_metrics():
    return Response(build_prometheus_metrics(), mimetype="text/plain; version=0.0.4; charset=utf-8")


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
