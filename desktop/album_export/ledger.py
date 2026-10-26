"""SQLite ledger: what was saved, from where, and how each run went."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    sha256        TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    local_path    TEXT NOT NULL,
    size          INTEGER NOT NULL,
    width         INTEGER,
    height        INTEGER,
    captured_utc  INTEGER,
    captured_local TEXT,
    tz            TEXT,
    time_source   TEXT,
    night         INTEGER,
    device_serial TEXT,
    device_model  TEXT,
    remote_mtime  INTEGER,
    run_id        INTEGER,
    committed_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remote_seen (
    device_serial TEXT NOT NULL,
    name          TEXT NOT NULL,
    size          INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    seen_at       TEXT NOT NULL,
    PRIMARY KEY (device_serial, name)
);
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    device_serial TEXT,
    device_model  TEXT,
    app_version   TEXT,
    mode          TEXT,
    status        TEXT,
    note          TEXT,
    stats         TEXT
);
CREATE TABLE IF NOT EXISTS batches (
    run_id        INTEGER NOT NULL,
    idx           INTEGER NOT NULL,
    dates         TEXT,
    selected      INTEGER,
    downloaded    INTEGER,
    select_seconds REAL,
    download_seconds REAL,
    ended_at      TEXT,
    PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS problems (
    run_id        INTEGER,
    name          TEXT,
    kind          TEXT,
    detail        TEXT,
    at            TEXT
);
"""


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---- runs ----------------------------------------------------------
    def start_run(self, serial: str, model: str, app_version: str, mode: str) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (started_at, device_serial, device_model, app_version, mode, status) "
            "VALUES (?, ?, ?, ?, ?, 'running')", (now_text(), serial, model, app_version, mode))
        self.db.commit()
        return int(cur.lastrowid)

    def end_run(self, run_id: int, status: str, note: str = "", stats: Optional[dict] = None) -> None:
        self.db.execute("UPDATE runs SET ended_at=?, status=?, note=?, stats=? WHERE id=?",
                        (now_text(), status, note, json.dumps(stats or {}, ensure_ascii=False), run_id))
        self.db.commit()

    def record_batch(self, run_id: int, idx: int, dates: List[str], selected: int, downloaded: int,
                     select_seconds: float, download_seconds: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO batches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, idx, json.dumps(dates, ensure_ascii=False), selected, downloaded,
             round(select_seconds, 2), round(download_seconds, 2), now_text()))
        self.db.commit()

    def problem(self, run_id: Optional[int], name: str, kind: str, detail: str) -> None:
        self.db.execute("INSERT INTO problems VALUES (?, ?, ?, ?, ?)", (run_id, name, kind, detail, now_text()))
        self.db.commit()

    # ---- files ---------------------------------------------------------
    def has_sha(self, sha: str) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM files WHERE sha256=?", (sha,)).fetchone()

    def seen(self, serial: str) -> Dict[str, Tuple[int, str]]:
        rows = self.db.execute("SELECT name, size, outcome FROM remote_seen WHERE device_serial=?", (serial,))
        return {r["name"]: (r["size"], r["outcome"]) for r in rows}

    def mark_seen(self, serial: str, name: str, size: int, sha: str, outcome: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO remote_seen VALUES (?, ?, ?, ?, ?, ?)",
                        (serial, name, size, sha, outcome, now_text()))

    def add_file(self, **row) -> None:
        row.setdefault("committed_at", now_text())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.db.execute("INSERT INTO files (%s) VALUES (%s)" % (cols, marks), tuple(row.values()))

    def commit(self) -> None:
        self.db.commit()

    def files(self) -> List[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM files ORDER BY captured_utc, name"))

    def last_run(self) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    def run_batches(self, run_id: int) -> List[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM batches WHERE run_id=? ORDER BY idx", (run_id,)))

    def problems(self, run_id: Optional[int] = None) -> List[sqlite3.Row]:
        if run_id is None:
            return list(self.db.execute("SELECT * FROM problems ORDER BY at"))
        return list(self.db.execute("SELECT * FROM problems WHERE run_id=? ORDER BY at", (run_id,)))

    def count_by_outcome(self, serial: str) -> Dict[str, int]:
        rows = self.db.execute(
            "SELECT outcome, COUNT(*) AS n FROM remote_seen WHERE device_serial=? GROUP BY outcome", (serial,))
        return {r["outcome"]: r["n"] for r in rows}

    def iter_names(self) -> Iterable[str]:
        return (r["name"] for r in self.db.execute("SELECT name FROM files"))
