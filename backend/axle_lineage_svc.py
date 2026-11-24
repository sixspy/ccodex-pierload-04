"""轴重血缘边。

血缘分「正式」与「影子」两态：封后修订的轴重曲线先进影子草稿，
其血缘边随草稿记 state='影子'，晋升时原子转 '正式'。
正式列表默认不混入影子边；?include_shadow=1 才展示并标注状态。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from models import LineageIn

router = APIRouter(tags=["lineage"])


@router.get("/api/lineage")
def list_lineage(authorization: AuthHeader = None, include_shadow: int = 0):
    require_user(authorization)
    with connect() as conn:
        where = "" if include_shadow else " WHERE e.state='正式'"
        rows = conn.execute(
            f"""SELECT e.*, a.ticket_code from_code, b.ticket_code to_code
                FROM axle_lineage e
                JOIN weigh_tickets a ON a.id=e.from_ticket_id
                LEFT JOIN weigh_tickets b ON b.id=e.to_ticket_id
                {where}
                ORDER BY e.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/lineage/ticket/{ticket_id}")
def lineage_for_ticket(ticket_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        out_edges = conn.execute(
            """SELECT e.*, b.ticket_code to_code FROM axle_lineage e
               LEFT JOIN weigh_tickets b ON b.id=e.to_ticket_id
               WHERE e.from_ticket_id=? AND e.state='正式'""",
            (ticket_id,),
        ).fetchall()
        in_edges = conn.execute(
            """SELECT e.*, a.ticket_code from_code FROM axle_lineage e
               JOIN weigh_tickets a ON a.id=e.from_ticket_id
               WHERE e.to_ticket_id=? AND e.state='正式'""",
            (ticket_id,),
        ).fetchall()
        return {
            "outbound": [dict(x) for x in out_edges],
            "inbound": [dict(x) for x in in_edges],
        }


@router.post("/api/lineage")
def add_lineage(body: LineageIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        fr = conn.execute(
            "SELECT id, ticket_code FROM weigh_tickets WHERE id=?",
            (body.from_ticket_id,),
        ).fetchone()
        if not fr:
            raise HTTPException(404, "起点票不存在")
        if body.to_ticket_id:
            to = conn.execute(
                "SELECT id FROM weigh_tickets WHERE id=?", (body.to_ticket_id,)
            ).fetchone()
            if not to:
                raise HTTPException(404, "终点票不存在")

        state = "正式"
        if body.draft_id is not None:
            draft = conn.execute(
                "SELECT * FROM axle_drafts WHERE id=?", (body.draft_id,)
            ).fetchone()
            if not draft:
                raise HTTPException(404, "影子草稿不存在")
            if draft["ticket_id"] != body.from_ticket_id:
                raise HTTPException(409, "影子边须挂在草稿所属票上")
            state = "影子"

        now = now_iso()
        cur = conn.execute(
            """INSERT INTO axle_lineage(from_ticket_id,to_ticket_id,edge_type,detail,
               created_at,draft_id,state) VALUES (?,?,?,?,?,?,?)""",
            (
                body.from_ticket_id,
                body.to_ticket_id,
                body.edge_type,
                body.detail,
                now,
                body.draft_id,
                state,
            ),
        )
        write_audit(
            conn,
            user,
            "lineage_edge",
            fr["ticket_code"],
            {"edge_id": cur.lastrowid, "edge_type": body.edge_type, "state": state},
        )
        conn.commit()
        return {"id": cur.lastrowid, "state": state}
