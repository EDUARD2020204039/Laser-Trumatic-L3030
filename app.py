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

SIGNAL_DEFINITIONS = {
    "machine_on": {
        "label": "Machine ON",
        "description": "Masina este alimentata si pregatita.",
        "accent": "steel",
    },
    "cutting_active": {
        "label": "Cutting active",
        "description": "Laserul taie efectiv.",
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
        "description": "Masina este pornita, dar nu taie.",
        "tone": "steel",
    },
    "cutting": {
        "label": "Taie",
        "description": "Productie activa in curs.",
        "tone": "ember",
    },
    "table_change": {
        "label": "Schimb masa",
        "description": "Operatorul pregateste urmatorul ciclu.",
        "tone": "teal",
    },
}

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
MANUAL_SOURCE_PREFIX = "manual"


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


def ensure_signal_name(signal_name: str) -> str:
    if signal_name not in SIGNAL_DEFINITIONS:
        raise ValueError(f"Unsupported signal: {signal_name}")
    return signal_name


def get_sqlite_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_sqlite_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_name TEXT NOT NULL,
                value INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                note TEXT,
                operator_id TEXT,
                operator_name TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_signal_events_signal_time
            ON signal_events (signal_name, created_at DESC, id DESC);
            """
        )
        connection.commit()


def get_pontaj_settings() -> dict[str, str | int]:
    return {
        "server": os.getenv("PONTAJ_SQL_SERVER", "192.168.2.6"),
        "database": os.getenv("PONTAJ_SQL_DATABASE", "Metal"),
        "username": os.getenv("PONTAJ_SQL_USERNAME", "bogdan"),
        "password": os.getenv("PONTAJ_SQL_PASSWORD", "HELPAN123$"),
        "driver": os.getenv("PONTAJ_SQL_DRIVER", DEFAULT_ODBC_DRIVER),
        "workcenter_id": int(os.getenv("PONTAJ_WORKCENTER_ID", "1")),
        "timeout": int(os.getenv("PONTAJ_SQL_TIMEOUT", "5")),
    }


def get_real_data_settings() -> dict[str, str]:
    endpoint = os.getenv("LASER_REAL_DATA_ENDPOINT", "").strip()
    name = os.getenv("LASER_REAL_DATA_NAME", "PC laser / bridge")
    return {
        "name": name,
        "endpoint": endpoint,
        "status": "configured" if endpoint else "pending",
        "message": (
            f"Sursa reala este pregatita prin {name}."
            if endpoint
            else "Sursa reala nu este inca configurata. Butoanele manuale raman doar pentru test."
        ),
    }


def get_pontaj_connection():
    if pyodbc is None:
        raise RuntimeError("pyodbc is not installed in this environment.")

    settings = get_pontaj_settings()
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


def fetch_current_operator() -> dict:
    settings = get_pontaj_settings()
    payload = {
        "status": "offline",
        "message": "Pontaj is not configured.",
        "workcenter_id": settings["workcenter_id"],
        "operators": [],
        "primary_operator": None,
    }

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
                (settings["workcenter_id"],),
            )
            rows = cursor.fetchall()
            cursor.close()
    except Exception as exc:  # pragma: no cover - depends on networked SQL Server
        payload["status"] = "error"
        payload["message"] = str(exc)
        return payload

    operators = []
    for row in rows:
        full_name = f"{row[1]} {row[2]}".strip()
        check_in = None
        if row[3] is not None and row[4] is not None:
            check_in = f"{row[3]} {row[4]}"
        operators.append(
            {
                "employee_id": str(row[0]),
                "full_name": full_name,
                "check_in": check_in,
            }
        )

    payload["status"] = "connected"
    payload["message"] = "Pontaj online."
    payload["operators"] = operators
    payload["primary_operator"] = operators[0] if operators else None
    return payload


def fetch_recent_events(limit: int = 18) -> list[dict]:
    with get_sqlite_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, signal_name, value, source, note, operator_id, operator_name, created_at
            FROM signal_events
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {
            "id": row["id"],
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


def fetch_current_signals() -> dict[str, dict]:
    current_signals: dict[str, dict] = {}
    with get_sqlite_connection() as connection:
        for signal_name, meta in SIGNAL_DEFINITIONS.items():
            row = connection.execute(
                """
                SELECT value, created_at, operator_name
                FROM signal_events
                WHERE signal_name = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (signal_name,),
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


def calculate_active_seconds(signal_name: str, start_dt: datetime, end_dt: datetime) -> int:
    with get_sqlite_connection() as connection:
        previous_row = connection.execute(
            """
            SELECT value, created_at
            FROM signal_events
            WHERE signal_name = ? AND created_at < ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (signal_name, start_dt.isoformat(timespec="seconds")),
        ).fetchone()

        rows = connection.execute(
            """
            SELECT value, created_at
            FROM signal_events
            WHERE signal_name = ? AND created_at >= ? AND created_at <= ?
            ORDER BY created_at ASC, id ASC
            """,
            (
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


def build_today_stats() -> dict:
    now = now_local()
    start_of_day = datetime.combine(date.today(), time.min)
    machine_on_seconds = calculate_active_seconds("machine_on", start_of_day, now)
    cutting_seconds = calculate_active_seconds("cutting_active", start_of_day, now)
    table_change_seconds = calculate_active_seconds("table_change", start_of_day, now)
    idle_seconds = max(machine_on_seconds - cutting_seconds - table_change_seconds, 0)
    utilization = round((cutting_seconds / machine_on_seconds) * 100, 1) if machine_on_seconds else 0.0

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
        "updated_at": now.isoformat(timespec="seconds"),
    }


def insert_event(signal_name: str, value: bool, source: str, note: str | None, operator_snapshot: dict) -> None:
    operator = operator_snapshot.get("primary_operator") or {}
    with get_sqlite_connection() as connection:
        connection.execute(
            """
            INSERT INTO signal_events (
                signal_name, value, source, note, operator_id, operator_name, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
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


def delete_events(mode: str, limit: int | None = None) -> int:
    with get_sqlite_connection() as connection:
        if mode == "manual_latest":
            rows = connection.execute(
                """
                SELECT id
                FROM signal_events
                WHERE source LIKE ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (f"{MANUAL_SOURCE_PREFIX}%", limit or 10),
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
                "DELETE FROM signal_events WHERE source LIKE ?",
                (f"{MANUAL_SOURCE_PREFIX}%",),
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


def build_dashboard_payload() -> dict:
    current_signals = fetch_current_signals()
    operator_snapshot = fetch_current_operator()
    current_state = derive_machine_state(current_signals)
    stats_today = build_today_stats()

    return {
        "app_title": "Laser TruMatic L3030",
        "workcenter_id": get_pontaj_settings()["workcenter_id"],
        "current_state": current_state,
        "current_signals": current_signals,
        "stats_today": stats_today,
        "operator_snapshot": operator_snapshot,
        "real_data_source": get_real_data_settings(),
        "recent_events": fetch_recent_events(),
        "signal_definitions": SIGNAL_DEFINITIONS,
        "updated_at": now_local().isoformat(timespec="seconds"),
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        app_title="Laser TruMatic L3030",
        workcenter_id=get_pontaj_settings()["workcenter_id"],
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": now_local().isoformat(timespec="seconds")})


@app.route("/api/operator")
def operator_status():
    return jsonify(fetch_current_operator())


@app.route("/api/dashboard")
def dashboard():
    return jsonify(build_dashboard_payload())


@app.route("/api/events", methods=["POST"])
def create_event():
    data = request.get_json(silent=True) or {}

    try:
        signal_name = ensure_signal_name(data.get("signal_name", data.get("signal", "")))
        target_value = to_bool(data.get("value"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    note = (data.get("note") or "").strip() or None
    source = (data.get("source") or "manual").strip() or "manual"

    current_signals = fetch_current_signals()
    event_sequence = build_event_sequence(signal_name, target_value, current_signals)
    operator_snapshot = fetch_current_operator()

    for index, event in enumerate(event_sequence):
        event_note = note if index == len(event_sequence) - 1 else event["note"]
        insert_event(
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
            "dashboard": build_dashboard_payload(),
        }
    )


@app.route("/api/events", methods=["DELETE"])
def delete_event_history():
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "manual_latest").strip()
    limit = data.get("limit")

    try:
        deleted_count = delete_events(mode=mode, limit=int(limit) if limit is not None else None)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify(
        {
            "success": True,
            "message": f"Deleted {deleted_count} event(s).",
            "deleted_count": deleted_count,
            "dashboard": build_dashboard_payload(),
        }
    )


init_db()


if __name__ == "__main__":
    app.run(
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "3030")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
