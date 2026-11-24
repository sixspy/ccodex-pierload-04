"""地磅桥登记与查询。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from axle_seal_guard import seal_axle_coherent
from db import connect, now_iso, write_audit
from models import BridgeIn

router = APIRouter(tags=["bridges"])


@router.get("/api/bridges")
def list_bridges(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM weigh_bridges ORDER BY id"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/bridges/{bridge_id}")
def get_bridge(bridge_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM weigh_bridges WHERE id=?", (bridge_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "地磅不存在")
        lanes = conn.execute(
            "SELECT * FROM weigh_lanes WHERE bridge_id=? ORDER BY lane_code",
            (bridge_id,),
        ).fetchall()
        stats = conn.execute(
            """SELECT COUNT(*) active FROM weigh_tickets t
               JOIN weigh_lanes l ON l.id=t.lane_id
               WHERE l.bridge_id=? AND t.status NOT IN ('作废','已封闸')""",
            (bridge_id,),
        ).fetchone()
        seal_rows = conn.execute(
            """SELECT s.id, s.ticket_id FROM gate_seals s
               JOIN weigh_tickets t ON t.id=s.ticket_id
               JOIN weigh_lanes l ON l.id=t.lane_id
               WHERE l.bridge_id=? AND s.status='已封闸'
                 AND s.id = (SELECT MAX(x.id) FROM gate_seals x
                             WHERE x.ticket_id=s.ticket_id AND x.status='已封闸')""",
            (bridge_id,),
        ).fetchall()
        incoherent = [
            s["id"] for s in seal_rows if not seal_axle_coherent(conn, s["ticket_id"])
        ]
        return {
            "bridge": dict(row),
            "lanes": [dict(x) for x in lanes],
            "active_tickets": stats["active"],
            "seal_axle": {
                "sealed": len(seal_rows),
                "incoherent": len(incoherent),
                "incoherent_seal_ids": incoherent,
            },
        }


@router.post("/api/bridges")
def create_bridge(body: BridgeIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        if conn.execute(
            "SELECT id FROM weigh_bridges WHERE bridge_code=?", (body.bridge_code,)
        ).fetchone():
            raise HTTPException(409, "地磅编码已存在")
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO weigh_bridges(bridge_code,pier_zone,name,max_gross_t,
               axle_limit,status,created_at) VALUES (?,?,?,?,?,?,?)""",
            (
                body.bridge_code,
                body.pier_zone,
                body.name,
                body.max_gross_t,
                body.axle_limit,
                body.status,
                now,
            ),
        )
        write_audit(
            conn,
            user,
            "bridge_create",
            body.bridge_code,
            {"bridge_id": cur.lastrowid},
        )
        conn.commit()
        return {"id": cur.lastrowid, "bridge_code": body.bridge_code}