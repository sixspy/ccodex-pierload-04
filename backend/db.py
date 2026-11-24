"""SQLite：港口过磅闸口母仓公共库层。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB_PATH = DATA / "app.db"
FIXTURES = ROOT / "fixtures"
FRONTEND = ROOT / "frontend"

PRODUCT_META = {
    "product": "港口过磅闸口",
    "skeleton": "过磅票入闸后裁定超重才放行，驳回进旁路挂起，闸代快照与提单册对账",
    "modules": [
        "bridges",
        "lanes",
        "tickets",
        "overload",
        "gate",
        "seal",
        "bl",
        "axle_lineage",
        "reconcile",
        "locks",
        "audit",
    ],
    "stubs": ["customs_stub", "crane_plan"],
}

# —— 封后轴重纪要（当班封死）——
# 甲路径：闸代生效后正式轴重表一律零改并当场拒写。
# 乙路径：封后修订仅许写入影子轴重草稿，待解封校验通过后原子晋升，正式表晋升前零改。
# 两边不得混用；折中路不留。
POST_SEAL_RULING = {
    "code": "封后轴重纪要",
    "case": "封后案丁 · 第4号",
    "paths": {"甲": "正式表零改并当场拒写", "乙": "影子草稿待解封校验后原子晋升"},
    "violation": "封后轴重偏航",
    "note": "当班封死；偏离纪要即记封后轴重偏航并拒写",
}
DIRECT_WRITE_REFUSED = "封后轴重禁改：闸代已生效，正式轴重表零改（封后案丁）"
DRAFT_REQUIRED = "封后修订须走乙路径：仅许写入影子轴重草稿，正式表零改"
SHADOW_PROMOTE_MISUSE = "封后轴重偏航：非封态票不得走影子草稿晋升"


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def sha_password(password: str) -> str:
    import hashlib

    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  username TEXT PRIMARY KEY,
  password_sha TEXT NOT NULL,
  role TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weigh_bridges(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bridge_code TEXT UNIQUE NOT NULL,
  pier_zone TEXT NOT NULL,
  name TEXT NOT NULL,
  max_gross_t REAL NOT NULL,
  axle_limit INTEGER NOT NULL DEFAULT 6,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weigh_lanes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lane_code TEXT UNIQUE NOT NULL,
  bridge_id INTEGER NOT NULL,
  direction TEXT NOT NULL,
  overload_threshold_t REAL NOT NULL,
  side_lane_ref TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(bridge_id) REFERENCES weigh_bridges(id)
);
CREATE TABLE IF NOT EXISTS weigh_tickets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_code TEXT UNIQUE NOT NULL,
  lane_id INTEGER NOT NULL,
  bl_ref TEXT NOT NULL,
  vehicle_plate TEXT NOT NULL,
  axle_weights TEXT NOT NULL,
  gross_t REAL NOT NULL,
  generation INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  adjudicated_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(lane_id) REFERENCES weigh_lanes(id)
);
CREATE TABLE IF NOT EXISTS overload_holds(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  hold_key TEXT NOT NULL,
  overweight_t REAL NOT NULL,
  threshold_t REAL NOT NULL,
  status TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  side_lane_note TEXT,
  FOREIGN KEY(ticket_id) REFERENCES weigh_tickets(id)
);
CREATE TABLE IF NOT EXISTS gate_releases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  lane_id INTEGER NOT NULL,
  release_ref TEXT NOT NULL,
  status TEXT NOT NULL,
  officer TEXT NOT NULL,
  released_at TEXT NOT NULL,
  FOREIGN KEY(ticket_id) REFERENCES weigh_tickets(id),
  FOREIGN KEY(lane_id) REFERENCES weigh_lanes(id)
);
CREATE TABLE IF NOT EXISTS gate_seals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  ticket_generation INTEGER NOT NULL,
  manifest TEXT NOT NULL,
  seal_ref TEXT NOT NULL,
  status TEXT NOT NULL,
  sealed_at TEXT NOT NULL,
  axle_weights TEXT,
  gross_t REAL,
  FOREIGN KEY(ticket_id) REFERENCES weigh_tickets(id)
);
CREATE TABLE IF NOT EXISTS bl_manifests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bl_code TEXT UNIQUE NOT NULL,
  cargo_desc TEXT NOT NULL,
  declared_gross_t REAL NOT NULL,
  vessel_voyage TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS axle_lineage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_ticket_id INTEGER NOT NULL,
  to_ticket_id INTEGER,
  edge_type TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL,
  draft_id INTEGER,
  state TEXT NOT NULL DEFAULT '正式',
  FOREIGN KEY(from_ticket_id) REFERENCES weigh_tickets(id)
);
CREATE TABLE IF NOT EXISTS axle_drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  seal_id INTEGER,
  axle_weights TEXT NOT NULL,
  gross_t REAL NOT NULL,
  status TEXT NOT NULL,
  note TEXT NOT NULL,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  promoted_at TEXT,
  FOREIGN KEY(ticket_id) REFERENCES weigh_tickets(id),
  FOREIGN KEY(seal_id) REFERENCES gate_seals(id)
);
CREATE TABLE IF NOT EXISTS seal_unseals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seal_id INTEGER NOT NULL,
  seal_ref TEXT NOT NULL,
  actor TEXT NOT NULL,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(seal_id) REFERENCES gate_seals(id)
);
CREATE TABLE IF NOT EXISTS post_seal_axle_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  seal_id INTEGER,
  actor TEXT NOT NULL,
  attempted TEXT NOT NULL,
  outcome TEXT NOT NULL,
  ruling_path TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(ticket_id) REFERENCES weigh_tickets(id)
);
CREATE TABLE IF NOT EXISTS pier_locks(
  lock_key TEXT PRIMARY KEY,
  holder TEXT NOT NULL,
  acquired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contention_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lock_key TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_trail(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  payload TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customs_stub_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bl_code TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crane_plan_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER,
  berth_slot TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);
"""


def write_audit(
    conn: sqlite3.Connection, actor: str, action: str, target: str, payload: dict
) -> None:
    conn.execute(
        """INSERT INTO audit_trail(actor,action,target,payload,created_at)
           VALUES (?,?,?,?,?)""",
        (actor, action, target, json.dumps(payload, ensure_ascii=False), now_iso()),
    )


def _load_json(name: str):
    path = FIXTURES / name
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def seed_users(conn: sqlite3.Connection) -> None:
    seeds = (
        ("admin", sha_password("admin123"), "admin"),
        ("writer", sha_password("writer123"), "clerk"),
        ("viewer", sha_password("viewer123"), "viewer"),
    )
    for u, pw, role in seeds:
        conn.execute(
            "INSERT OR IGNORE INTO users(username,password_sha,role) VALUES (?,?,?)",
            (u, pw, role),
        )


def _bridge_id(conn, code: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM weigh_bridges WHERE bridge_code=?", (code,)
    ).fetchone()
    return int(row["id"]) if row else None


def seed_bridges(conn: sqlite3.Connection) -> None:
    for row in _load_json("bridges.json"):
        if conn.execute(
            "SELECT id FROM weigh_bridges WHERE bridge_code=?",
            (row["bridge_code"],),
        ).fetchone():
            continue
        conn.execute(
            """INSERT INTO weigh_bridges(bridge_code,pier_zone,name,max_gross_t,
               axle_limit,status,created_at) VALUES (?,?,?,?,?,?,?)""",
            (
                row["bridge_code"],
                row["pier_zone"],
                row["name"],
                float(row["max_gross_t"]),
                int(row.get("axle_limit", 6)),
                row.get("status", "运行"),
                row.get("created_at", now_iso()),
            ),
        )


def seed_lanes(conn: sqlite3.Connection) -> None:
    for row in _load_json("lanes.json"):
        if conn.execute(
            "SELECT id FROM weigh_lanes WHERE lane_code=?", (row["lane_code"],)
        ).fetchone():
            continue
        br = _bridge_id(conn, row["bridge_code"])
        if not br:
            continue
        conn.execute(
            """INSERT INTO weigh_lanes(lane_code,bridge_id,direction,overload_threshold_t,
               side_lane_ref,status,created_at) VALUES (?,?,?,?,?,?,?)""",
            (
                row["lane_code"],
                br,
                row["direction"],
                float(row["overload_threshold_t"]),
                row.get("side_lane_ref", ""),
                row.get("status", "开放"),
                row.get("created_at", now_iso()),
            ),
        )


def _lane_id(conn, code: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM weigh_lanes WHERE lane_code=?", (code,)
    ).fetchone()
    return int(row["id"]) if row else None


def seed_tickets(conn: sqlite3.Connection) -> None:
    for row in _load_json("tickets.json"):
        if conn.execute(
            "SELECT id FROM weigh_tickets WHERE ticket_code=?", (row["ticket_code"],)
        ).fetchone():
            continue
        lane = _lane_id(conn, row["lane_code"])
        if not lane:
            continue
        axles = row.get("axle_weights", [])
        gross = float(row.get("gross_t", sum(float(a.get("w", 0)) for a in axles)))
        conn.execute(
            """INSERT INTO weigh_tickets(ticket_code,lane_id,bl_ref,vehicle_plate,
               axle_weights,gross_t,generation,status,adjudicated_at,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                row["ticket_code"],
                lane,
                row["bl_ref"],
                row["vehicle_plate"],
                json.dumps(axles, ensure_ascii=False),
                gross,
                int(row.get("generation", 1)),
                row.get("status", "已裁定"),
                row.get("adjudicated_at"),
                row.get("created_at", now_iso()),
            ),
        )


def seed_overloads(conn: sqlite3.Connection) -> None:
    for row in _load_json("overloads.json"):
        ticket = conn.execute(
            "SELECT id FROM weigh_tickets WHERE ticket_code=?", (row["ticket_code"],)
        ).fetchone()
        if not ticket:
            continue
        exists = conn.execute(
            "SELECT id FROM overload_holds WHERE hold_key=?", (row["hold_key"],)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO overload_holds(ticket_id,hold_key,overweight_t,threshold_t,
               status,opened_at,closed_at,side_lane_note) VALUES (?,?,?,?,?,?,?,?)""",
            (
                ticket["id"],
                row["hold_key"],
                float(row["overweight_t"]),
                float(row["threshold_t"]),
                row.get("status", "开放"),
                row["opened_at"],
                row.get("closed_at"),
                row.get("side_lane_note", ""),
            ),
        )


def seed_bl_manifests(conn: sqlite3.Connection) -> None:
    for row in _load_json("bl_manifests.json"):
        if conn.execute(
            "SELECT id FROM bl_manifests WHERE bl_code=?", (row["bl_code"],)
        ).fetchone():
            continue
        conn.execute(
            """INSERT INTO bl_manifests(bl_code,cargo_desc,declared_gross_t,vessel_voyage,
               status,created_at) VALUES (?,?,?,?,?,?)""",
            (
                row["bl_code"],
                row["cargo_desc"],
                float(row["declared_gross_t"]),
                row["vessel_voyage"],
                row.get("status", "在港"),
                row.get("created_at", now_iso()),
            ),
        )


def seed_gate_samples(conn: sqlite3.Connection) -> None:
    for row in _load_json("gate_releases.json"):
        ticket = conn.execute(
            "SELECT id, lane_id FROM weigh_tickets WHERE ticket_code=?",
            (row["ticket_code"],),
        ).fetchone()
        if not ticket:
            continue
        if conn.execute(
            "SELECT id FROM gate_releases WHERE release_ref=?", (row["release_ref"],)
        ).fetchone():
            continue
        conn.execute(
            """INSERT INTO gate_releases(ticket_id,lane_id,release_ref,status,officer,released_at)
               VALUES (?,?,?,?,?,?)""",
            (
                ticket["id"],
                ticket["lane_id"],
                row["release_ref"],
                row.get("status", "已放行"),
                row.get("officer", "闸口员"),
                row["released_at"],
            ),
        )


def seed_seals_sample(conn: sqlite3.Connection) -> None:
    for row in _load_json("gate_seals.json"):
        ticket = conn.execute(
            "SELECT id, generation, axle_weights, gross_t FROM weigh_tickets WHERE ticket_code=?",
            (row["ticket_code"],),
        ).fetchone()
        if not ticket:
            continue
        if conn.execute(
            "SELECT id FROM gate_seals WHERE seal_ref=?", (row["seal_ref"],)
        ).fetchone():
            continue
        # 快照列取封闸当刻的票面轴重/毛重（夹具未给则用当前票面）
        snap_axles = row.get("axle_weights")
        snap_gross = row.get("gross_t")
        if snap_gross is None:
            snap_gross = float(ticket["gross_t"])
        if snap_axles is None:
            snap_axles = ticket["axle_weights"]
        if not isinstance(snap_axles, str):
            snap_axles = json.dumps(snap_axles, ensure_ascii=False)
        conn.execute(
            """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,
               status,sealed_at,axle_weights,gross_t) VALUES (?,?,?,?,?,?,?,?)""",
            (
                ticket["id"],
                int(row.get("ticket_generation", ticket["generation"])),
                row["manifest"],
                row["seal_ref"],
                row.get("status", "已封闸"),
                row["sealed_at"],
                snap_axles,
                float(snap_gross),
            ),
        )


def seed_lineage_samples(conn: sqlite3.Connection) -> None:
    for row in _load_json("axle_lineage.json"):
        fr = conn.execute(
            "SELECT id FROM weigh_tickets WHERE ticket_code=?", (row["from_ticket"],)
        ).fetchone()
        if not fr:
            continue
        to_id = None
        if row.get("to_ticket"):
            to_row = conn.execute(
                "SELECT id FROM weigh_tickets WHERE ticket_code=?", (row["to_ticket"],)
            ).fetchone()
            to_id = to_row["id"] if to_row else None
        if conn.execute(
            """SELECT id FROM axle_lineage WHERE from_ticket_id=? AND edge_type=? AND detail=?""",
            (fr["id"], row["edge_type"], row["detail"]),
        ).fetchone():
            continue
        conn.execute(
            """INSERT INTO axle_lineage(from_ticket_id,to_ticket_id,edge_type,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (
                fr["id"],
                to_id,
                row["edge_type"],
                row["detail"],
                row.get("created_at", now_iso()),
            ),
        )


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """老库迁移：CREATE TABLE IF NOT EXISTS 不会补列，须显式加。"""
    wanted = {
        "gate_seals": {
            "axle_weights": "TEXT",
            "gross_t": "REAL",
        },
        "axle_lineage": {
            "draft_id": "INTEGER",
            "state": "TEXT NOT NULL DEFAULT '正式'",
        },
    }
    for table, cols in wanted.items():
        existing = {
            r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, decl in cols.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _manifest_gross(manifest: str | None) -> float | None:
    """从封闸清单里取封闸当刻毛重（清单是封闸时写死的权威口径）。"""
    if not manifest:
        return None
    for part in manifest.replace("；", ";").split(";"):
        if part.startswith("毛重="):
            try:
                return float(part[len("毛重="):].rstrip("tT吨"))
            except ValueError:
                return None
    return None


def _backfill_seal_snapshots(conn: sqlite3.Connection) -> None:
    """迁移来的老快照没有轴重快照列：按清单毛重回填封闸口径。

    清单毛重与当前票面一致 → 票面即封闸面，可回填轴重；
    不一致（封后已被硬改）→ 轴重留空，判同态不可证明，由对账核出偏航。
    历史坏行因此不会被洗白。
    """
    rows = conn.execute(
        """SELECT s.id, s.ticket_id, s.ticket_generation, s.manifest,
                  t.generation, t.axle_weights, t.gross_t
           FROM gate_seals s JOIN weigh_tickets t ON t.id=s.ticket_id
           WHERE s.axle_weights IS NULL OR s.gross_t IS NULL"""
    ).fetchall()
    for r in rows:
        snap_gross = _manifest_gross(r["manifest"])
        if snap_gross is None:
            if int(r["generation"]) != int(r["ticket_generation"]):
                # 票面已升代且清单无毛重：无法还原，留空待人工核
                continue
            snap_gross = float(r["gross_t"])
        face_gross = float(r["gross_t"])
        coherent_face = abs(round(face_gross, 3) - round(snap_gross, 3)) <= 0.001
        conn.execute(
            "UPDATE gate_seals SET axle_weights=?, gross_t=? WHERE id=?",
            (
                r["axle_weights"] if coherent_face else None,
                snap_gross,
                r["id"],
            ),
        )


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        seed_users(conn)
        seed_bridges(conn)
        seed_lanes(conn)
        seed_bl_manifests(conn)
        seed_tickets(conn)
        seed_overloads(conn)
        seed_gate_samples(conn)
        seed_seals_sample(conn)
        _backfill_seal_snapshots(conn)
        seed_lineage_samples(conn)
        conn.commit()


def dashboard_stats(conn: sqlite3.Connection) -> dict:
    bridges = conn.execute("SELECT COUNT(*) c FROM weigh_bridges").fetchone()["c"]
    lanes = conn.execute("SELECT COUNT(*) c FROM weigh_lanes").fetchone()["c"]
    tickets = conn.execute("SELECT COUNT(*) c FROM weigh_tickets").fetchone()["c"]
    open_holds = conn.execute(
        "SELECT COUNT(*) c FROM overload_holds WHERE status='开放'"
    ).fetchone()["c"]
    released = conn.execute(
        "SELECT COUNT(*) c FROM gate_releases WHERE status='已放行'"
    ).fetchone()["c"]
    sealed = conn.execute(
        "SELECT COUNT(*) c FROM gate_seals WHERE status='已封闸'"
    ).fetchone()["c"]
    bl_count = conn.execute("SELECT COUNT(*) c FROM bl_manifests").fetchone()["c"]
    stubs = {
        "customs_stub_jobs": conn.execute(
            "SELECT COUNT(*) c FROM customs_stub_jobs"
        ).fetchone()["c"],
        "crane_plan_jobs": conn.execute(
            "SELECT COUNT(*) c FROM crane_plan_jobs"
        ).fetchone()["c"],
    }
    pending = conn.execute(
        """SELECT COUNT(*) c FROM weigh_tickets
           WHERE status='旁路挂起'"""
    ).fetchone()["c"]
    return {
        "bridges": bridges,
        "lanes": lanes,
        "tickets": tickets,
        "open_overloads": open_holds,
        "gate_releases": released,
        "gate_seals": sealed,
        "bl_manifests": bl_count,
        "side_pending": pending,
        "stubs": stubs,
        "meta": PRODUCT_META,
    }