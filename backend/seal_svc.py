"""闸代快照：升代并写清单。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from axle_seal_guard import seal_axle_coherent
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import SealIn
from validators import format_manifest, validate_ticket_sealable

router = APIRouter(tags=["seal"])


class UnsealIn(BaseModel):
    note: str = ""


@router.get("/api/seals")
def list_seals(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*, t.ticket_code, t.vehicle_plate FROM gate_seals s
               JOIN weigh_tickets t ON t.id=s.ticket_id ORDER BY s.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/seals")
def create_seal(body: SealIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"ticket:{body.ticket_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            ticket = conn.execute(
                "SELECT * FROM weigh_tickets WHERE id=?", (body.ticket_id,)
            ).fetchone()
            if not ticket:
                raise HTTPException(404, "过磅票不存在")
            validate_ticket_sealable(dict(ticket))
            if ticket["status"] == "旁路挂起":
                raise HTTPException(409, "旁路挂起不得封闸")
            open_hold = conn.execute(
                """SELECT id FROM overload_holds WHERE ticket_id=? AND status='开放'""",
                (body.ticket_id,),
            ).fetchone()
            if open_hold:
                raise HTTPException(409, "超重开放不得封闸")
            next_gen = int(ticket["generation"]) + 1
            manifest = body.manifest or format_manifest(
                dict(ticket), float(ticket["gross_t"]), next_gen
            )
            now = now_iso()
            seal_ref = f"GS-{ticket['ticket_code']}-G{next_gen}"
            cur = conn.execute(
                """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,
                   status,sealed_at,axle_weights,gross_t) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    body.ticket_id,
                    next_gen,
                    manifest,
                    seal_ref,
                    "已封闸",
                    now,
                    ticket["axle_weights"],
                    float(ticket["gross_t"]),
                ),
            )
            conn.execute(
                """UPDATE weigh_tickets SET generation=?, status='已封闸' WHERE id=?""",
                (next_gen, body.ticket_id,),
            )
            write_audit(
                conn,
                user,
                "gate_seal",
                seal_ref,
                {
                    "seal_id": cur.lastrowid,
                    "generation": next_gen,
                    "manifest": manifest,
                },
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "seal_ref": seal_ref,
                "generation": next_gen,
                "manifest": manifest,
                "status": "已封闸",
            }
        finally:
            release(conn, lock_key, user)


@router.get("/api/seals/{seal_ref}")
def get_seal(seal_ref: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT s.*, t.ticket_code,
                      t.axle_weights AS live_axle_weights,
                      t.gross_t AS live_gross_t
               FROM gate_seals s JOIN weigh_tickets t ON t.id=s.ticket_id
               WHERE s.seal_ref=?""",
            (seal_ref,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "快照不存在")
        data = dict(row)
        # 快照页轴重取封闸当刻快照；票面轴重另列，供同态对照
        data["axle_weights"] = json.loads(data["axle_weights"] or "[]")
        data["live_axle_weights"] = json.loads(data.pop("live_axle_weights") or "[]")
        data["snapshot_gross_t"] = data["gross_t"]
        data["live_gross_t"] = data.pop("live_gross_t")
        data["axle_coherent"] = seal_axle_coherent(conn, data["ticket_id"])
        return data


@router.post("/api/seals/{seal_ref}/unseal")
def unseal(seal_ref: str, body: UnsealIn, authorization: AuthHeader = None):
    """乙路径前置：解封校验。解封后票态回到已裁定，影子草稿方可晋升。"""
    user = require_writer(authorization)
    with connect() as conn:
        seal = conn.execute(
            "SELECT * FROM gate_seals WHERE seal_ref=?", (seal_ref,)
        ).fetchone()
        if not seal:
            raise HTTPException(404, "快照不存在")
        if seal["status"] != "已封闸":
            raise HTTPException(409, "快照非封闸态")
        lock_key = f"ticket:{seal['ticket_id']}"
        require_lock(conn, lock_key, user)
        try:
            now = now_iso()
            conn.execute(
                "UPDATE gate_seals SET status='已解封' WHERE id=?", (seal["id"],)
            )
            conn.execute(
                """INSERT INTO seal_unseals(seal_id,seal_ref,actor,note,created_at)
                   VALUES (?,?,?,?,?)""",
                (seal["id"], seal_ref, user, body.note or "解封校验", now),
            )
            conn.execute(
                "UPDATE weigh_tickets SET status='已裁定' WHERE id=? AND status='已封闸'",
                (seal["ticket_id"],),
            )
            write_audit(
                conn,
                user,
                "seal_unseal",
                seal_ref,
                {"seal_id": seal["id"], "note": body.note},
            )
            conn.commit()
            return {"seal_ref": seal_ref, "status": "已解封", "unsealed_at": now}
        finally:
            release(conn, lock_key, user)