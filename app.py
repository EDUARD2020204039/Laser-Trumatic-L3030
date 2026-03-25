from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

try:
    import pyodbc
except ImportError:  # pragma: no cover - optional during early local setup
    pyodbc = None


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
        "endpoint": "http://100.126.29.52:8081",
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

    endpoint = os.getenv("LASER_REAL_DATA_ENDPOINT", "").strip() or feed["endpoint"]
    name = os.getenv("LASER_REAL_DATA_NAME", "").strip() or feed["display_name"]
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
                "endpoint": feed["endpoint"] or "Fara endpoint clar",
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
        "recent_events": fetch_recent_events(machine_key),
        "signal_definitions": SIGNAL_DEFINITIONS,
        "updated_at": now_local().isoformat(timespec="seconds"),
    }


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


if __name__ == "__main__":
    app.run(
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "3030")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
