from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time as time_module
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request
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


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DEFAULT_SQLITE_FILENAME = "laser_monitor.db"


def resolve_sqlite_path() -> Path:
    explicit_sqlite_path = os.getenv("LASER_SQLITE_PATH")
    if explicit_sqlite_path:
        return Path(explicit_sqlite_path).expanduser()

    explicit_data_dir = os.getenv("LASER_DATA_DIR")
    if explicit_data_dir:
        return Path(explicit_data_dir).expanduser() / DEFAULT_SQLITE_FILENAME

    legacy_path = BASE_DIR / "data" / DEFAULT_SQLITE_FILENAME
    persistent_path = Path("/data") / DEFAULT_SQLITE_FILENAME

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
DEFAULT_MACHINE_KEY = "laser1"
MANUAL_SOURCE_PREFIX = "manual"
OCR_AVAILABLE = cv2 is not None and np is not None and pytesseract is not None
BACKGROUND_SYNC_ENABLED = os.getenv("BACKGROUND_SYNC_ENABLED", "1") != "0"
BACKGROUND_SYNC_INTERVAL_SECONDS = max(int(os.getenv("BACKGROUND_SYNC_INTERVAL_SECONDS", "3")), 1)
SNAPSHOT_FRESHNESS_SECONDS = max(int(os.getenv("SNAPSHOT_FRESHNESS_SECONDS", "3")), 1)
ABKANT_IDLE_STAGNATION_SECONDS = max(int(os.getenv("ABKANT_IDLE_STAGNATION_SECONDS", "600")), 60)
OPERATOR_CACHE_SECONDS = max(int(os.getenv("OPERATOR_CACHE_SECONDS", "20")), 3)
_background_sync_started = False
RUNTIME_VALUE_UNCHANGED = object()
PROMETHEUS_BASE_URL = (os.getenv("PROMETHEUS_BASE_URL", "http://localhost:9090") or "http://localhost:9090").rstrip("/")
UNKNOWN_OPERATOR_LABEL = "Fara operator la salvare"
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

DEFAULT_MACHINE_HMI_URLS = {
    "laser1": "https://laser.helpan.ro/",
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
            {"label": "Machine ON", "value": "ramane OFF pana exista sursa separata"},
            {"label": "Cutting", "value": "oprit"},
            {"label": "Table change", "value": "oprit"},
            {"label": "Idle", "value": "oprit"},
        ],
        "derivation_rules": [
            {"label": "Machine ON", "value": "ramane NU fara feed sau semnal dedicat pentru Laser2"},
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
            {"label": "Machine ON", "value": "camera accesibila + rpiabkantworking"},
            {"label": "Bending", "value": "program activ + progres piese sub total"},
            {"label": "Bend change", "value": "program terminat; asteptam urmatorul program"},
            {"label": "Idle", "value": "program neschimbat / fara progres bucati"},
        ],
        "derivation_rules": [
            {"label": "Machine ON", "value": "DA cand captura merge si parametrul rpiabkantworking ramane TRUE"},
            {"label": "Bending", "value": "DA cand exista program activ si numarul de piese produse nu a ajuns la total"},
            {"label": "Bend change", "value": "DA cand programul activ si-a terminat toate piesele si asteapta urmatorul program"},
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
}

MACHINE_SIGNAL_OVERRIDES = {
    "abkant": {
        "cutting_active": {
            "label": "Bending",
            "description": "Abkantul indoaie activ piesele programului curent.",
            "button_on_label": "Opreste indoirea",
            "button_off_label": "Porneste indoirea",
            "metric_label": "Bending",
            "report_label": "Indoire",
        },
        "table_change": {
            "label": "Bend change",
            "description": "Programul curent este terminat si se asteapta urmatorul program.",
            "button_on_label": "Opreste schimbarea",
            "button_off_label": "Porneste schimbarea",
            "metric_label": "Bend change",
            "report_label": "Bend change",
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
    "abkant": {
        "ready": {
            "label": "Pregatit",
            "description": "Abkantul este pornit, dar nu indoaie activ.",
        },
        "cutting": {
            "label": "In indoire",
            "description": "Programul curent este in curs de indoire.",
        },
        "table_change": {
            "label": "Bend change",
            "description": "Programul curent este terminat si se pregateste urmatoarea indoire.",
        },
    }
}

LASER_OCR_ZONES = {
    "top_banner": (0, 40, 1280, 110),
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


def resolve_real_data_endpoint(machine_key: str) -> str:
    legacy_names = ("LASER_REAL_DATA_ENDPOINT",) if machine_key in {"laser1", "laser2"} else ()
    return get_machine_env_value(machine_key, "REAL_DATA_ENDPOINT", legacy_names) or REAL_DATA_FEEDS[machine_key]["endpoint"]


def resolve_real_data_name(machine_key: str) -> str:
    legacy_names = ("LASER_REAL_DATA_NAME",) if machine_key in {"laser1", "laser2"} else ()
    return get_machine_env_value(machine_key, "REAL_DATA_NAME", legacy_names) or REAL_DATA_FEEDS[machine_key]["display_name"]


def machine_has_dedicated_live_source(machine_key: str) -> bool:
    machine_key = ensure_machine_key(machine_key)
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


def resolve_machine_hmi_feed_url(machine_key: str) -> str:
    return get_machine_env_value(machine_key, "HMI_FEED_URL") or DEFAULT_MACHINE_HMI_URLS.get(machine_key, "")


def build_machine_feeds(machine_key: str) -> list[dict]:
    machine_key = ensure_machine_key(machine_key)
    if machine_key == "laser2" and not machine_has_dedicated_live_source(machine_key):
        return []

    camera_url = resolve_machine_camera_feed_url(machine_key)
    camera_mode = resolve_machine_camera_feed_mode(machine_key)
    camera_username, camera_password, _ = resolve_machine_camera_feed_credentials(machine_key)
    hmi_url = resolve_machine_hmi_feed_url(machine_key)
    camera_refresh_ms = None
    if machine_key == "laser1" and camera_mode == "image" and (
        camera_url.strip().lower().endswith("/picture")
        or (camera_username and camera_password)
    ):
        camera_refresh_ms = 1500
    if camera_mode == "image" and should_proxy_camera_feed(machine_key, camera_url, camera_username, camera_password):
        camera_url = f"/api/camera-feed/{machine_key}"

    if machine_key == "abkant":
        return [
            {
                "key": "camera",
                "mode": camera_mode,
                "url": camera_url,
                "open_url": camera_url,
                "display_url": urllib.parse.urlsplit(camera_url).netloc or camera_url,
                "refresh_ms": None,
            }
        ]

    feeds = [
        {
            "key": "camera",
            "mode": camera_mode,
            "url": camera_url,
            "open_url": resolve_machine_camera_feed_url(machine_key),
            "display_url": urllib.parse.urlsplit(resolve_machine_camera_feed_url(machine_key)).netloc or resolve_machine_camera_feed_url(machine_key),
            "refresh_ms": camera_refresh_ms,
        },
        {
            "key": "hmi",
            "mode": "page",
            "url": hmi_url,
            "open_url": hmi_url,
            "display_url": urllib.parse.urlsplit(hmi_url).netloc or hmi_url,
            "refresh_ms": None,
        },
    ]
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


def fetch_camera_feed_content(machine_key: str, timeout: float = 8.0) -> tuple[bytes | None, str, str | None]:
    camera_url = resolve_machine_camera_feed_url(machine_key)
    if not camera_url:
        return None, "Camera feed URL nu este configurat.", None

    request_obj = urllib.request.Request(camera_url, headers={"User-Agent": "HABA-Production-Monitor/1.0"})
    camera_username, camera_password, auth_type = resolve_machine_camera_feed_credentials(machine_key)

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

            cursor.execute(
                """
                SELECT datacolectare, programidentificat, numar_bucati, faraschimbare, nr_bucati
                FROM raportare_abkant
                ORDER BY id DESC
                LIMIT 1
                """
            )
            latest_row = cursor.fetchone()

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

    machine_on = bool(parameter_row[0]) if parameter_row is not None else False
    active_program = (latest_row[1] or "").strip() if latest_row else ""
    pieces_text = (latest_row[2] or "").strip() if latest_row and latest_row[2] is not None else ""
    produced_pieces_raw = latest_row[4] if latest_row and latest_row[4] is not None else None
    collected_at = latest_row[0].isoformat(sep=" ", timespec="seconds") if latest_row and latest_row[0] else None
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
    bend_change = bool(
        machine_on
        and active_program
        and has_piece_counters
        and pieces_done == 0
        and total_pieces == 0
    )
    idle = bool(
        machine_on
        and active_program
        and not bend_change
        and stagnation_seconds >= ABKANT_IDLE_STAGNATION_SECONDS
    )
    bending_active = bool(
        machine_on
        and active_program
        and (
            (has_piece_counters and not bend_change and not idle)
            or not has_piece_counters
        )
    )

    if bend_change:
        program_status = "Bend change"
    elif idle:
        program_status = "Idle"
    elif bending_active:
        program_status = "Bending active"
    elif machine_on:
        program_status = "Pregatit"
    else:
        program_status = "Oprit"

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
        "stagnation_seconds": stagnation_seconds,
        "derived_signals": {
            "machine_on": machine_on,
            "cutting_active": bending_active,
            "table_change": bend_change,
            "idle": idle,
        },
        "message": (
            f"Abkant citit din PostgreSQL. Ultima colectare: {collected_at or 'necunoscuta'}. "
            f"Program: {active_program or 'necunoscut'}. Piese: {pieces_label or 'n/a'}. "
            f"Fara schimbare de {format_seconds(stagnation_seconds)}."
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
            "message": "Laser2 nu are inca feed sau semnal dedicat, deci ramane OFF pana il configuram separat.",
        }

    endpoint = resolve_real_data_endpoint(machine_key)
    image, error_message = fetch_mjpeg_frame(endpoint)
    if image is None:
        return {
            "available": False,
            "connected": False,
            "endpoint": endpoint,
            "message": f"Nu pot citi captura live de la {endpoint}. Motiv: {error_message}",
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
        "captured_at": now_local().isoformat(timespec="seconds"),
        "selected_program": selected_program or "Necitit",
        "active_program": active_program or "Necitit",
        "material": material or "Necitit",
        "program_status": program_status or "Necitit",
        "warning_message": warning_message,
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
        "derived_signals": {
            "machine_on": False,
            "cutting_active": False,
            "table_change": False,
            "idle": False,
        },
        "message": (
            "Abkant foloseste momentan feedul din script, dar nu avem inca semnal live sigur pentru Machine ON."
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
    migrate_legacy_sqlite_if_needed()
    print(f"SQLite storage path: {SQLITE_PATH}")
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
    dedicated_live_source = machine_has_dedicated_live_source(machine_profile["key"])

    endpoint = resolve_real_data_endpoint(machine_profile["key"])
    name = resolve_real_data_name(machine_profile["key"])
    status = "configured" if script_exists and dedicated_live_source else "pending"
    return {
        "name": name,
        "endpoint": endpoint or "Fara endpoint dedicat",
        "status": status,
        "transport": feed["transport"],
        "script_name": script_name,
        "details": feed["details"],
        "message": (
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
        efficiency_percent = round((cutting_seconds / machine_on_seconds) * 100, 1) if machine_on_seconds else 0.0
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
    if not table_change_seconds:
        table_change_seconds = max(int(fallback_table_change_seconds or 0), 0)
    if not cutting_seconds:
        cutting_seconds = max(int(fallback_cutting_seconds or 0), 0)
    if machine_on_seconds < cutting_seconds + table_change_seconds:
        machine_on_seconds = cutting_seconds + table_change_seconds
    idle_seconds = max(machine_on_seconds - cutting_seconds - table_change_seconds, 0)
    efficiency_percent = round((cutting_seconds / machine_on_seconds) * 100, 1) if machine_on_seconds else 0.0
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
    efficiency_percent = (
        round((total_cutting_seconds / productive_seconds) * 100, 1)
        if productive_seconds > 0
        else 0.0
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


def fetch_prometheus_vector(query: str) -> list[dict]:
    request_url = f"{PROMETHEUS_BASE_URL}/api/v1/query?query={urllib.parse.quote(query, safe='')}"
    request_obj = urllib.request.Request(request_url, headers={"User-Agent": "HABA-Production-Monitor/1.0"})
    with urllib.request.urlopen(request_obj, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("error") or "Prometheus query failed.")
    result = payload.get("data", {}).get("result") or []
    return result if isinstance(result, list) else []


def escape_prometheus_label_matcher(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


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

    for period in periods:
        period_range = prometheus_period_range(period)
        query_map = {
            "efficiency_percent": f'avg by (operator_id, operator_name) (max_over_time(haba_saved_cycle_efficiency_percent[{period_range}]))',
            "records_count": f'count by (operator_id, operator_name) (max_over_time(haba_saved_cycle_completed[{period_range}]))',
            "machine_on_seconds": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_machine_on_seconds[{period_range}]))',
            "cutting_seconds": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_cutting_seconds[{period_range}]))',
            "idle_seconds": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_idle_seconds[{period_range}]))',
            "table_change_seconds": f'sum by (operator_id, operator_name) (max_over_time(haba_saved_cycle_table_change_seconds[{period_range}]))',
        }

        for field_name, query in query_map.items():
            for series in fetch_prometheus_vector(query):
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
                elif field_name == "records_count":
                    target_period[field_name] = int(round(numeric_value))
                else:
                    target_period[field_name] = round(numeric_value, 1)

    output = []
    for operator_entry in operator_map.values():
        for period in periods:
            target_period = operator_entry[period]
            target_period.setdefault("records_count", 0)
            target_period.setdefault("efficiency_percent", 0.0)
            for field_name in ("machine_on", "cutting", "idle", "table_change"):
                seconds_key = f"{field_name}_seconds"
                label_key = f"{field_name}_label"
                target_period.setdefault(seconds_key, 0)
                target_period.setdefault(label_key, format_seconds(0))
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


def build_prometheus_saved_records(period: str, operator_id: str | None = None) -> list[dict]:
    period_range = prometheus_period_range(period)
    label_filter = ""
    if operator_id:
        if operator_id.startswith("name:"):
            label_filter = f'{{operator_name="{escape_prometheus_label_matcher(operator_id[5:])}"}}'
        else:
            label_filter = f'{{operator_id="{escape_prometheus_label_matcher(operator_id)}"}}'

    base_query = f"max_over_time(haba_saved_cycle_completed{label_filter}[{period_range}])"
    base_records: dict[str, dict] = {}
    for series in fetch_prometheus_vector(base_query):
        labels = series.get("metric") or {}
        cycle_id = labels.get("cycle_id")
        if not cycle_id:
            continue
        base_records[cycle_id] = {
            "id": int(cycle_id),
            "machine_key": labels.get("machine_key") or "",
            "machine_label": labels.get("machine_label") or labels.get("machine_key") or "",
            "workcenter_id": parse_optional_int(labels.get("workcenter_id")),
            "operator_id": labels.get("operator_id") or "",
            "operator_name": labels.get("operator_name") or UNKNOWN_OPERATOR_LABEL,
            "selected_program": labels.get("selected_program") or "Necitit",
            "active_program": labels.get("active_program") or labels.get("selected_program") or "Necitit",
            "material": labels.get("material") or "Necitit",
            "program_status": labels.get("program_status") or "Salvat in Prometheus",
            "cutting_started_at": labels.get("cutting_started_at") or None,
            "table_change_started_at": labels.get("table_change_started_at") or labels.get("completed_at") or None,
            "table_change_ended_at": labels.get("completed_at") or None,
            "source": labels.get("source") or "prometheus",
            "created_at": labels.get("completed_at") or "",
        }

    metric_map = {
        "machine_on_duration_seconds": "haba_saved_cycle_machine_on_seconds",
        "cycle_duration_seconds": "haba_saved_cycle_cutting_seconds",
        "idle_duration_seconds": "haba_saved_cycle_idle_seconds",
        "table_change_duration_seconds": "haba_saved_cycle_table_change_seconds",
        "efficiency_percent": "haba_saved_cycle_efficiency_percent",
    }
    for field_name, metric_name in metric_map.items():
        query = f"max_over_time({metric_name}{label_filter}[{period_range}])"
        for series in fetch_prometheus_vector(query):
            labels = series.get("metric") or {}
            cycle_id = labels.get("cycle_id")
            if not cycle_id or cycle_id not in base_records:
                continue
            numeric_value = float((series.get("value") or [None, "0"])[1] or 0)
            if field_name.endswith("_seconds"):
                base_records[cycle_id][field_name] = int(round(numeric_value))
                base_records[cycle_id][field_name.replace("_seconds", "_label")] = format_seconds(int(round(numeric_value)))
            else:
                base_records[cycle_id][field_name] = round(numeric_value, 1)

    records = []
    for record in base_records.values():
        machine_key = record["machine_key"]
        cutting_meta = resolve_signal_definition(machine_key or DEFAULT_MACHINE_KEY, "cutting_active")
        table_change_meta = resolve_signal_definition(machine_key or DEFAULT_MACHINE_KEY, "table_change")
        record.setdefault("machine_on_duration_seconds", 0)
        record.setdefault("machine_on_duration_label", format_seconds(0))
        record.setdefault("cycle_duration_seconds", 0)
        record.setdefault("cycle_duration_label", format_seconds(0))
        record.setdefault("idle_duration_seconds", 0)
        record.setdefault("idle_duration_label", format_seconds(0))
        record.setdefault("table_change_duration_seconds", 0)
        record.setdefault("table_change_duration_label", format_seconds(0))
        record.setdefault("efficiency_percent", 0.0)
        record["activity_label"] = cutting_meta.get("report_label", cutting_meta["label"])
        record["change_label"] = table_change_meta.get("report_label", table_change_meta["label"])
        records.append(record)

    records.sort(key=lambda item: item.get("table_change_ended_at") or item.get("created_at") or "", reverse=True)
    return records


def build_empty_operator_period_bucket() -> dict:
    return {
        "records_count": 0,
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
            target = merged.setdefault(
                operator_id,
                build_operator_entry(
                    operator_id,
                    entry.get("employee_id", ""),
                    entry.get("operator_name", UNKNOWN_OPERATOR_LABEL),
                ),
            )
            if entry.get("employee_id") and not target.get("employee_id"):
                target["employee_id"] = entry["employee_id"]
            target["machines"].update(entry.get("machines") or [])
    return list(merged.values())


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
        efficiencies = [float(record.get("efficiency_percent") or 0.0) for record in operator_records]
        machine_on_seconds = sum(int(record.get("machine_on_duration_seconds") or 0) for record in operator_records)
        cutting_seconds = sum(int(record.get("cycle_duration_seconds") or 0) for record in operator_records)
        idle_seconds = sum(int(record.get("idle_duration_seconds") or 0) for record in operator_records)
        table_change_seconds = sum(int(record.get("table_change_duration_seconds") or 0) for record in operator_records)
        period_bucket.update(
            {
                "records_count": len(operator_records),
                "efficiency_percent": round(sum(efficiencies) / len(efficiencies), 1) if efficiencies else 0.0,
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

    return operators[0]["operator_id"]


def resolve_saved_period(period: str | None) -> str:
    candidate = (period or "all").strip().lower()
    if candidate not in {"all", "day", "week", "month"}:
        raise ValueError(f"Unsupported saved period: {candidate}")
    return candidate


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
    try:
        operators = build_prometheus_operator_summaries()
        selected_operator_id = resolve_selected_operator_id(operator_id, operators)
        records = build_prometheus_saved_records(normalized_period, operator_id=selected_operator_id)
        if operators:
            return {
                "view": "saved",
                "selected_machine_key": machine_key,
                "period": normalized_period,
                "operators": operators,
                "selected_operator_id": selected_operator_id,
                "records": records,
                "records_count": len(records),
                "data_source": "prometheus",
                "updated_at": now_local().isoformat(timespec="seconds"),
            }
    except Exception:
        pass

    records = fetch_saved_cycles_for_period(machine_key=machine_key, period=normalized_period)
    operators = build_sqlite_operator_summaries(machine_key=machine_key)
    selected_operator_id = resolve_selected_operator_id(operator_id, operators)
    filtered_records = filter_records_by_operator(records, selected_operator_id)
    return {
        "view": "saved",
        "selected_machine_key": machine_key,
        "period": normalized_period,
        "operators": operators,
        "selected_operator_id": selected_operator_id,
        "records": filtered_records,
        "summary": summarize_saved_cycles(records),
        "reports": build_saved_cycles_reports(machine_key),
        "reports_by_machine": build_saved_cycles_reports_by_machine(),
        "records_count": len(filtered_records),
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
        return {"program": "", "material": ""}

    selected_program = normalize_context_token(snapshot.get("selected_program"))
    active_program = normalize_context_token(snapshot.get("active_program"))
    material = normalize_context_token(snapshot.get("material"))
    return {
        "program": selected_program or active_program,
        "material": material,
    }


def context_requires_stats_reset(previous_snapshot: dict | None, current_snapshot: dict | None) -> bool:
    previous_context = resolve_snapshot_context(previous_snapshot)
    current_context = resolve_snapshot_context(current_snapshot)
    if not (current_context["program"] or current_context["material"]):
        return False
    previous_signals = (previous_snapshot or {}).get("derived_signals") or {}
    current_signals = (current_snapshot or {}).get("derived_signals") or {}
    machine_restarted = not bool(previous_signals.get("machine_on")) and bool(current_signals.get("machine_on"))
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
    cycle_metrics = calculate_saved_cycle_metrics(
        machine_key=machine_key,
        cutting_started_at=cutting_started_at_raw,
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
                    machine_on_duration_seconds,
                    idle_duration_seconds,
                    efficiency_percent,
                    source,
                    snapshot_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    cycle_metrics["machine_on_duration_seconds"],
                    cycle_metrics["idle_duration_seconds"],
                    cycle_metrics["efficiency_percent"],
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


def resolve_snapshot_program(snapshot: dict | None) -> str:
    if not snapshot:
        return ""

    return (
        (snapshot.get("selected_program") or "").strip()
        or (snapshot.get("active_program") or "").strip()
    )


def save_cycle_on_program_change(
    machine_key: str,
    machine_profile: dict,
    previous_snapshot: dict,
    current_snapshot: dict,
    operator_snapshot: dict,
    current_signals: dict[str, dict],
) -> None:
    previous_program = resolve_snapshot_program(previous_snapshot)
    current_program = resolve_snapshot_program(current_snapshot)
    if not previous_program or not current_program or previous_program == current_program:
        return

    if not bool(previous_snapshot.get("derived_signals", {}).get("cutting_active")):
        return

    operator = operator_snapshot.get("primary_operator") or {}
    change_detected_at = now_local()
    cutting_started_at_raw = current_signals["cutting_active"]["changed_at"] if current_signals["cutting_active"]["active"] else None
    cycle_duration_seconds = None
    if cutting_started_at_raw:
        cycle_duration_seconds = max(
            int((change_detected_at - parse_timestamp(cutting_started_at_raw)).total_seconds()),
            0,
        )
    cycle_metrics = calculate_saved_cycle_metrics(
        machine_key=machine_key,
        cutting_started_at=cutting_started_at_raw,
        table_change_started_at=change_detected_at.isoformat(timespec="seconds"),
        table_change_ended_at=change_detected_at.isoformat(timespec="seconds"),
        fallback_cutting_seconds=cycle_duration_seconds,
        fallback_table_change_seconds=0,
    )

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
                    machine_on_duration_seconds,
                    idle_duration_seconds,
                    efficiency_percent,
                    source,
                    snapshot_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    cutting_started_at_raw,
                    change_detected_at.isoformat(timespec="seconds"),
                    change_detected_at.isoformat(timespec="seconds"),
                    0,
                    cycle_duration_seconds,
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
            }
    return current_signals


def derive_machine_state(machine_key: str, current_signals: dict[str, dict]) -> dict:
    if not current_signals["machine_on"]["active"]:
        return {"key": "off", **resolve_state_definition(machine_key, "off")}
    if current_signals["cutting_active"]["active"]:
        return {"key": "cutting", **resolve_state_definition(machine_key, "cutting")}
    if current_signals["table_change"]["active"]:
        return {"key": "table_change", **resolve_state_definition(machine_key, "table_change")}
    return {"key": "ready", **resolve_state_definition(machine_key, "ready")}


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
    runtime = get_machine_runtime(machine_key)
    stats_anchor = runtime.get("stats_anchor") or {}
    stats_anchor_started_at = parse_timestamp(stats_anchor.get("started_at")) if stats_anchor.get("started_at") else None
    stats_window_start = max(start_of_day, stats_anchor_started_at) if stats_anchor_started_at else start_of_day
    elapsed_seconds = max(int((now - stats_window_start).total_seconds()), 1)
    machine_on_seconds = calculate_active_seconds(machine_key, "machine_on", stats_window_start, now)
    cutting_seconds = calculate_active_seconds(machine_key, "cutting_active", stats_window_start, now)
    table_change_seconds = calculate_active_seconds(machine_key, "table_change", stats_window_start, now)
    idle_seconds = max(machine_on_seconds - cutting_seconds - table_change_seconds, 0)
    utilization = round((cutting_seconds / machine_on_seconds) * 100, 1) if machine_on_seconds else 0.0
    availability = round((cutting_seconds / machine_on_seconds) * 100, 1) if machine_on_seconds else 0.0
    cutting_meta = resolve_signal_definition(machine_key, "cutting_active")
    table_change_meta = resolve_signal_definition(machine_key, "table_change")
    availability_prefix = (
        "Disponibilitate indoire/masina_pornita"
        if machine_key == "abkant"
        else "Disponibilitate taiere/masina_pornita"
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
        "randament_percent": utilization,
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
            "cutting_started_at": record["cutting_started_at"] or "",
            "table_change_started_at": record["table_change_started_at"] or "",
            "completed_at": record["table_change_ended_at"] or record["created_at"] or "",
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


def sync_machine_events_from_live_snapshot(machine_key: str) -> dict | None:
    snapshot = get_live_machine_snapshot(machine_key)
    if not snapshot:
        return snapshot

    runtime = get_machine_runtime(machine_key)
    previous_snapshot = runtime.get("last_snapshot")
    stats_anchor = runtime.get("stats_anchor")
    current_context = resolve_snapshot_context(snapshot)
    if snapshot.get("available") and context_requires_stats_reset(previous_snapshot, snapshot):
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

    if previous_snapshot and not runtime.get("pending_cycle"):
        save_cycle_on_program_change(
            machine_key,
            machine_profile,
            previous_snapshot,
            snapshot,
            operator_snapshot,
            current_signals,
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


def snapshot_differs_from_current_signals(
    snapshot: dict | None,
    current_signals: dict[str, dict],
) -> bool:
    if not snapshot or not snapshot.get("available"):
        return False

    derived_signals = snapshot.get("derived_signals") or {}
    for signal_name in ("machine_on", "cutting_active", "table_change"):
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
    machine_profile = get_machine_profile(machine_key)
    if BACKGROUND_SYNC_ENABLED:
        runtime = get_machine_runtime(machine_key)
        live_extraction = runtime.get("last_snapshot")
        current_signals = fetch_current_signals(machine_key)
        if (
            live_extraction is None
            or snapshot_is_stale(live_extraction)
            or snapshot_differs_from_current_signals(live_extraction, current_signals)
        ):
            live_extraction = sync_machine_events_from_live_snapshot(machine_key)
            current_signals = fetch_current_signals(machine_key)
    else:
        live_extraction = sync_machine_events_from_live_snapshot(machine_key)
        current_signals = fetch_current_signals(machine_key)
    operator_snapshot = fetch_current_operator(machine_profile["workcenter_id"])
    current_state = derive_machine_state(machine_key, current_signals)
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


@app.route("/api/camera-feed/<machine_key>")
def camera_feed(machine_key: str):
    try:
        machine_key = ensure_machine_key(machine_key)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    content, error_message, content_type = fetch_camera_feed_content(machine_key)
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
