"""SQLite 持久化层。

表结构:
  nodes(id PK, role, label, sort)
  segments(id PK, endpoint_a FK, endpoint_b FK, sort)
  valves(id PK, segment_id FK, cost, sort)
  plans(id PK, created_at, feasible, total_cost, closed_valves JSON,
        result JSON, network_snapshot JSON)
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "/data/pipeline.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id    TEXT PRIMARY KEY,
    role  TEXT NOT NULL,
    label TEXT NOT NULL,
    sort  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    id         TEXT PRIMARY KEY,
    endpoint_a TEXT NOT NULL REFERENCES nodes(id),
    endpoint_b TEXT NOT NULL REFERENCES nodes(id),
    sort       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS valves (
    id         TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL REFERENCES segments(id),
    cost       INTEGER NOT NULL,
    sort       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    feasible         INTEGER NOT NULL,
    total_cost       INTEGER,
    closed_valves    TEXT NOT NULL,
    result           TEXT NOT NULL,
    network_snapshot TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """建表; 空库时写入示例管网。"""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if count == 0:
            from demo import DEMO_NETWORK
            replace_network(conn, DEMO_NETWORK)


def replace_network(conn: sqlite3.Connection, payload: dict) -> None:
    """整体替换管网输入(单事务)。payload 已通过 Pydantic 校验。"""
    conn.execute("DELETE FROM valves")
    conn.execute("DELETE FROM segments")
    conn.execute("DELETE FROM nodes")
    for i, nd in enumerate(payload.get("nodes", [])):
        conn.execute(
            "INSERT INTO nodes(id, role, label, sort) VALUES (?,?,?,?)",
            (nd["id"], nd["role"], nd.get("label") or nd["id"], i),
        )
    for i, sg in enumerate(payload.get("segments", [])):
        conn.execute(
            "INSERT INTO segments(id, endpoint_a, endpoint_b, sort) "
            "VALUES (?,?,?,?)",
            (sg["id"], sg["endpoint_a"], sg["endpoint_b"], i),
        )
    for i, vl in enumerate(payload.get("valves", [])):
        conn.execute(
            "INSERT INTO valves(id, segment_id, cost, sort) VALUES (?,?,?,?)",
            (vl["id"], vl["segment_id"], int(vl["cost"]), i),
        )


def load_network(conn: sqlite3.Connection) -> dict:
    nodes = [
        {"id": r["id"], "role": r["role"], "label": r["label"]}
        for r in conn.execute("SELECT * FROM nodes ORDER BY sort, id")
    ]
    segments = [
        {"id": r["id"], "endpoint_a": r["endpoint_a"],
         "endpoint_b": r["endpoint_b"]}
        for r in conn.execute("SELECT * FROM segments ORDER BY sort, id")
    ]
    valves = [
        {"id": r["id"], "segment_id": r["segment_id"], "cost": r["cost"]}
        for r in conn.execute("SELECT * FROM valves ORDER BY sort, id")
    ]
    return {"nodes": nodes, "segments": segments, "valves": valves}


def save_plan(conn: sqlite3.Connection, result: dict, snapshot: dict) -> int:
    cur = conn.execute(
        "INSERT INTO plans(created_at, feasible, total_cost, closed_valves, "
        "result, network_snapshot) VALUES (?,?,?,?,?,?)",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            1 if result["feasible"] else 0,
            result.get("total_cost"),
            json.dumps(result.get("closed_valves", []), ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            json.dumps(snapshot, ensure_ascii=False),
        ),
    )
    return int(cur.lastrowid)


def list_plans(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, feasible, total_cost, closed_valves, result "
        "FROM plans ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    plans = []
    for r in rows:
        # 求解器类型存在 result JSON 中; 早期记录没有该字段时视为常规规划
        try:
            solver_kind = json.loads(r["result"]).get("solver", "base")
        except (ValueError, TypeError):
            solver_kind = "base"
        plans.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "feasible": bool(r["feasible"]),
            "total_cost": r["total_cost"],
            "closed_valves": json.loads(r["closed_valves"]),
            "solver": solver_kind,
        })
    return plans


def get_plan(conn: sqlite3.Connection, plan_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if r is None:
        return None
    result = json.loads(r["result"])
    result["id"] = r["id"]
    result["created_at"] = r["created_at"]
    result["network_snapshot"] = json.loads(r["network_snapshot"])
    return result
