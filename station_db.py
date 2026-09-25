"""
Station database — ONE table per station, SAME column pattern everywhere.

Every station (st012, st013, ... st0NN) gets an identical table. A row is one
event at that station: a production cycle, a fault, or a maintenance stop.
The station-specific meaning of the two process signals is written into the
row itself (primary_name / primary_unit / limits), so any table can be read,
charted, or queried by an LLM agent without joining anything else.

Each row also carries the HUMAN side of the cycle — who did the work, how
long their hands-on part took, retries, andon pulls — because people are part
of every station.

Models write their findings back into the same table:
  train_model.py         machine / process / data   -> anomaly_*
  train_people_model.py  the operator's work         -> human_*
  find_causes.py         why it happened            -> incident_id
Two summary tables are rebuilt by the models:
  operator_shifts  one row per operator per shift   (train_people_model.py)
  incidents        one row per problem + its cause  (find_causes.py)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "factory.db"
LABELS_PATH = ROOT / "data" / "injected_labels.csv"          # ground truth, only for evaluation
PEOPLE_LABELS_PATH = ROOT / "data" / "injected_people_labels.csv"
CAUSES_PATH = ROOT / "data" / "injected_causes.csv"            # true root causes (simulator)
MODELS_DIR = ROOT / "models"
CHARTS_DIR = ROOT / "charts"

# ---------------------------------------------------------------------------
# The single table pattern. {table} is replaced by the station id (e.g. st012).
# ---------------------------------------------------------------------------
STATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    event_id         INTEGER PRIMARY KEY,
    ts               TEXT    NOT NULL,   -- ISO-8601 time the event started
    shift            TEXT,               -- A 06-14 | B 14-22 | C 22-06
    event_type       TEXT    NOT NULL,   -- CYCLE | FAULT | MAINTENANCE
    vin              TEXT,               -- vehicle id (CYCLE rows)
    model            TEXT,               -- vehicle variant
    work_instruction TEXT,               -- standard-work version in force, e.g. WI-012 v4
    part_batch       TEXT,               -- lot of the key part used (bolts / coolant)
    cycle_time_s     REAL,               -- time the vehicle spent in the station
    machine_time_s   REAL,               -- automatic part of the cycle (cycle = machine + operator time)

    -- human cycle: the operator's part of this vehicle's work
    operator_id      TEXT,               -- who was logged in at the station (pseudonymous)
    operator_skill   INTEGER,            -- skill matrix 1 trainee .. 4 trainer
    operator_time_s  REAL,               -- hands-on manual time (cycle = operator + machine time)
    wait_time_s      REAL,               -- operator idle before this vehicle arrived
    retries          INTEGER,            -- re-hits / re-tests needed in this cycle
    andon_pulled     INTEGER,            -- 1 = operator asked for help (andon)
    andon_response_s REAL,               -- seconds until the team lead arrived

    primary_name     TEXT,               -- main process signal, e.g. torque
    primary_value    REAL,
    primary_unit     TEXT,
    primary_lsl      REAL,               -- lower spec limit
    primary_usl      REAL,               -- upper spec limit

    secondary_name   TEXT,               -- second process signal, e.g. angle
    secondary_value  REAL,
    secondary_unit   TEXT,
    secondary_lsl    REAL,
    secondary_usl    REAL,

    temperature_c    REAL,
    result           TEXT,               -- OK | NOK (CYCLE rows)
    fault_code       TEXT,
    downtime_s       REAL,               -- FAULT / MAINTENANCE duration
    comment          TEXT,

    -- written by train_model.py
    anomaly_score    REAL,               -- 0..1, higher = more unusual
    anomaly_flag     INTEGER,            -- 1 = ambiguous / noisy, needs a look
    anomaly_type     TEXT,               -- label_conflict | traceability | sensor | unlogged_event | pattern
    anomaly_reason   TEXT,               -- plain-language why

    -- written by train_people_model.py
    human_score      REAL,               -- 0..1, how unusual the operator's cycle was
    human_flag       INTEGER,            -- 1 = worth a supportive look
    human_type       TEXT,               -- rushed | struggle | retries | support | login | working_time
    human_reason     TEXT,

    -- written by find_causes.py
    incident_id      INTEGER             -- links the cycle to a row in the incidents table
);
CREATE INDEX IF NOT EXISTS idx_{table}_ts  ON {table}(ts);
CREATE INDEX IF NOT EXISTS idx_{table}_vin ON {table}(vin);
"""

# ---------------------------------------------------------------------------
# What each station measures. Columns stay identical; only this config differs.
# k = how the secondary signal normally follows the primary (the "pattern").
# ---------------------------------------------------------------------------
STATIONS = {
    "st012": {
        "title": "ST012 - Front subframe bolt-down (nutrunner)",
        "primary":   {"name": "torque", "unit": "Nm",  "nominal": 120.0, "lsl": 110.0, "usl": 130.0, "sigma": 2.2},
        "secondary": {"name": "angle",  "unit": "deg", "nominal": 55.0,  "lsl": 35.0,  "usl": 75.0,  "sigma": 2.5},
        "k": 1.6,                     # +1 Nm overshoot -> about +1.6 deg more rotation
        "cycle_time_s": 47.0,
        "operator_time_s": 30.0,      # standard hands-on work: position subframe bolts, run tool, check
        "retry_cost_s": 2.5,          # one re-hit of a bolt
        "temperature_c": 24.0,
        "upstream": None,
        "wi": "WI-012",
        "batch": ("BL", "bolt batch"),
        # equipment per symptom family: (name, what goes wrong, repair logged when fixed)
        "equipment": {
            "quality": ("Nutrunner NR-012", "socket wearing - re-hits rising", "Socket replaced"),
            "pattern": ("Nutrunner NR-012", "torque calibration drifting", "Nutrunner recalibrated"),
            "speed":   ("Subframe fixture FX-012", "clamp sticking - machine time rising", "Fixture clamp repaired"),
            "data":    ("VIN scanner SC-012", "scanner read rate dropping", "VIN scanner replaced"),
        },
        "fault_codes": {
            "E-NR-101": "Nutrunner overload",
            "E-NR-115": "Socket not engaged",
            "E-SC-204": "VIN scanner read error",
            "E-CV-410": "Conveyor stop",
        },
        # genuine rejects: (signal, side, code) — consistent data, NOT ambiguous
        "nok_modes": [("primary", "low", "NOK-TORQUE-LOW"), ("primary", "high", "NOK-TORQUE-HIGH")],
    },
    "st013": {
        "title": "ST013 - Coolant fill & leak test",
        "primary":   {"name": "leak_rate",   "unit": "sccm", "nominal": 0.60, "lsl": 0.00, "usl": 2.00, "sigma": 0.15},
        "secondary": {"name": "fill_volume", "unit": "L",    "nominal": 9.50, "lsl": 9.20, "usl": 9.80, "sigma": 0.035},
        "k": -0.12,                   # more leak -> slightly less coolant retained
        "cycle_time_s": 44.0,         # faster than ST012, so it never becomes the bottleneck
        "operator_time_s": 18.0,      # connect fill head, start test, disconnect, cap
        "retry_cost_s": 4.0,          # one re-seat of the fill head
        "temperature_c": 24.0,
        "upstream": "st012",          # every VIN here should have passed ST012 first
        "wi": "WI-013",
        "batch": ("CL", "coolant batch"),
        "equipment": {
            "quality": ("Fill head CF-013", "fill head seal wearing - re-tests rising", "Fill head seal replaced"),
            "pattern": ("Leak tester LT-013", "leak tester drifting", "Leak tester recalibrated"),
            "speed":   ("Fill pump CF-013", "fill pump slowing - machine time rising", "Fill pump serviced"),
            "data":    ("VIN scanner SC-013", "scanner read rate dropping", "VIN scanner replaced"),
        },
        "fault_codes": {
            "E-CF-310": "Fill nozzle seal failure",
            "E-CF-322": "Vacuum not reached",
            "E-SC-204": "VIN scanner read error",
            "E-CV-410": "Conveyor stop",
        },
        "nok_modes": [("primary", "high", "NOK-LEAK-HIGH"), ("secondary", "low", "NOK-FILL-LOW")],
    },
}


# ---------------------------------------------------------------------------
# People summary: one row per operator per shift (built by train_people_model.py)
# ---------------------------------------------------------------------------
OPERATOR_SHIFTS_SQL = """
DROP TABLE IF EXISTS operator_shifts;
CREATE TABLE operator_shifts (
    operator_id       TEXT,
    shift_date        TEXT,              -- date the shift started
    shift             TEXT,
    operator_skill    INTEGER,
    stations          TEXT,
    cycles            INTEGER,
    first_ts          TEXT,
    last_ts           TEXT,
    pace_ratio        REAL,              -- median operator time / station standard (1.00 = standard)
    slowdown_pct      REAL,              -- trend over the shift, % slower per 8 h (fatigue)
    retry_rate        REAL,              -- retries per cycle
    nok_rate          REAL,
    technique_offset  REAL,              -- avg shift of secondary vs normal pattern, in sigma (how they work)
    technique_station TEXT,
    rushed_cycles     INTEGER,
    struggle_cycles   INTEGER,
    retry_bursts      INTEGER,
    andon_pulls       INTEGER,
    andon_unanswered  INTEGER,
    login_issues      INTEGER,
    off_shift_cycles  INTEGER,
    peer_score        REAL,              -- largest |robust z| vs all operator-shifts
    flag              INTEGER,           -- 1 = worth a conversation / support
    finding_type      TEXT,              -- fatigue | technique | retries | quality | pace | support | login | working_time | rushing
    finding           TEXT,
    cause_check       TEXT,              -- filled by find_causes.py: does the cause finder agree it is this person?
    PRIMARY KEY (operator_id, shift_date, shift)
);
"""


# ---------------------------------------------------------------------------
# Incidents: one row per detected problem with its most likely cause (find_causes.py)
# ---------------------------------------------------------------------------
INCIDENTS_SQL = """
DROP TABLE IF EXISTS incidents;
CREATE TABLE incidents (
    incident_id   INTEGER PRIMARY KEY,
    case_id       INTEGER,           -- incidents with the same cause + culprit form one case
    family        TEXT,              -- quality | speed | pattern | data (what the symptom looks like)
    shift_date    TEXT,
    shift         TEXT,
    start_ts      TEXT,
    end_ts        TEXT,
    stations      TEXT,
    top_station   TEXT,
    blocks        INTEGER,           -- 2-hour rotation blocks showing the symptom
    block_list    TEXT,              -- those blocks as station@start; station@start; ...
    symptom       TEXT,              -- what was seen, in plain words
    cause         TEXT,              -- machine | people | method | station | unclear
    confidence    REAL,
    p_machine     REAL,
    p_people      REAL,
    p_method      REAL,
    p_station     REAL,
    culprit       TEXT,              -- the operator, machine, instruction version, batch or system
    evidence      TEXT,              -- why, in plain words
    graph         TEXT               -- evidence graph: incident -> candidate culprits with strength
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def create_station_table(con: sqlite3.Connection, table: str) -> None:
    con.executescript(STATION_TABLE_SQL.format(table=table))


def station_tables(con: sqlite3.Connection) -> list[str]:
    """All station tables in the database (anything named stNNN)."""
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'st[0-9][0-9][0-9]' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]
