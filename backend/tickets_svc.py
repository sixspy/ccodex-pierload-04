"""过磅票：轴重采集与裁定入闸。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from axle_seal_guard import (
    active_seal,
    axle_json,
    is_sealed,
    log_deviation,
    refuse_formal_write,
    ticket_axle_state,
)
from db import DRAFT_REQUIRED, connect, now_iso, write_audit
from locks import release, require_lock
from models import AxleReviseIn, TicketIn
from validators import (
    adjudicate_overload,
    validate_axle_count,
    validate_axle_weights,
    validate_gross_cap,
    validate_lane_open,
    validate_ticket_mutable,
)

router = APIRouter(tags=["tickets"])


def _next_ticket_code(conn, lane_id: int) -> str:
    lane = conn.execute(
        "SELECT lane_code FROM weigh_lanes WHERE id=?", (lane_id,)
    ).fetchone()
    prefix = lane["lane_code"] if lane else f"LN-{lane_id}"
    n = conn.execute(
        "SELECT COUNT(*) c FROM weigh_tickets WHERE lane_id=?", (lane_id,)
    ).fetchone()["c"]
    return f"WT-{prefix}-{n + 1:03d}"


@router.get("/api/tickets")
def list_tickets(authorization: AuthHeader = None, status: str | None = None):
    require_user(authorization)
    with connect() as conn:
        q = """SELECT t.*, l.lane_code, b.bridge_code FROM weigh_tickets t
               JOIN weigh_lanes l ON l.id=t.lane_id
               JOIN weigh_bridges b ON b.id=l.bridge_id"""
        if status:
            rows = conn.execute(
                q + " WHERE t.status=? ORDER BY t.id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(q + " ORDER BY t.id DESC").fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["axle_weights"] = json.loads(d["axle_weights"] or "[]")
            items.append(d)
        return {"items": items}


@router.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT t.*, l.lane_code, l.overload_threshold_t, b.name bridge_name
               FROM weigh_tickets t
               JOIN weigh_lanes l ON l.id=t.lane_id
               JOIN weigh_bridges b ON b.id=l.bridge_id
               WHERE t.id=?""",
            (ticket_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "过磅票不存在")
        data = dict(row)
        data["axle_weights"] = json.loads(data["axle_weights"] or "[]")
        holds = conn.execute(
            "SELECT * FROM overload_holds WHERE ticket_id=? ORDER BY id DESC",
            (ticket_id,),
        ).fetchall()
        seals = conn.execute(
            "SELECT * FROM gate_seals WHERE ticket_id=? ORDER BY id DESC",
            (ticket_id,),
        ).fetchall()
        releases = conn.execute(
            "SELECT * FROM gate_releases WHERE ticket_id=? ORDER BY id DESC",
            (ticket_id,),
        ).fetchall()
        drafts = conn.execute(
            """SELECT * FROM axle_drafts WHERE ticket_id=? ORDER BY id DESC""",
            (ticket_id,),
        ).fetchall()
        deviations = conn.execute(
            """SELECT * FROM post_seal_axle_log WHERE ticket_id=?
               ORDER BY id DESC LIMIT 50""",
            (ticket_id,),
        ).fetchall()
        return {
            "ticket": data,
            "overload_holds": [dict(h) for h in holds],
            "gate_seals": [dict(s) for s in seals],
            "gate_releases": [dict(r) for r in releases],
            "axle_state": ticket_axle_state(conn, data),
            "axle_drafts": [_draft_row(d) for d in drafts],
            "axle_deviations": [dict(d) for d in deviations],
        }


def _draft_row(row) -> dict:
    d = dict(row)
    d["axle_weights"] = json.loads(d["axle_weights"] or "[]")
    return d


@router.post("/api/tickets")
def create_ticket(body: TicketIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"lane:{body.lane_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            lane = conn.execute(
                """SELECT l.*, b.max_gross_t, b.axle_limit FROM weigh_lanes l
                   JOIN weigh_bridges b ON b.id=l.bridge_id WHERE l.id=?""",
                (body.lane_id,),
            ).fetchone()
            if not lane:
                raise HTTPException(404, "车道不存在")
            validate_lane_open(dict(lane))
            axles = [a.model_dump() for a in body.axle_weights]
            validate_axle_count(axles, int(lane["axle_limit"]))
            gross, _peak, _spread = validate_axle_weights(axles)
            validate_gross_cap(gross, float(lane["max_gross_t"]))
            bl = conn.execute(
                "SELECT * FROM bl_manifests WHERE bl_code=?", (body.bl_ref,)
            ).fetchone()
            if not bl:
                raise HTTPException(422, "提单册未登记")
            is_over, overweight = adjudicate_overload(
                gross, float(lane["overload_threshold_t"])
            )
            code = body.ticket_code or _next_ticket_code(conn, body.lane_id)
            if conn.execute(
                "SELECT id FROM weigh_tickets WHERE ticket_code=?", (code,)
            ).fetchone():
                raise HTTPException(409, "票号已存在")
            now = now_iso()
            if is_over:
                status = "旁路挂起"
            else:
                status = "已裁定"
            cur = conn.execute(
                """INSERT INTO weigh_tickets(ticket_code,lane_id,bl_ref,vehicle_plate,
                   axle_weights,gross_t,generation,status,adjudicated_at,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    code,
                    body.lane_id,
                    body.bl_ref,
                    body.vehicle_plate,
                    json.dumps(axles, ensure_ascii=False),
                    gross,
                    1,
                    status,
                    now if status == "已裁定" else None,
                    now,
                ),
            )
            ticket_id = cur.lastrowid
            if is_over:
                hold_key = f"HOLD-{code}"
                conn.execute(
                    """INSERT INTO overload_holds(ticket_id,hold_key,overweight_t,threshold_t,
                       status,opened_at,side_lane_note) VALUES (?,?,?,?,?,?,?)""",
                    (
                        ticket_id,
                        hold_key,
                        overweight,
                        float(lane["overload_threshold_t"]),
                        "开放",
                        now,
                        lane["side_lane_ref"] or "旁路待驳",
                    ),
                )
            write_audit(
                conn,
                user,
                "ticket_weigh",
                code,
                {
                    "ticket_id": ticket_id,
                    "gross_t": gross,
                    "status": status,
                    "overload": is_over,
                },
            )
            conn.commit()
            return {
                "id": ticket_id,
                "ticket_code": code,
                "gross_t": gross,
                "status": status,
                "overload": is_over,
            }
        finally:
            release(conn, lock_key, user)


@router.post("/api/tickets/{ticket_id}/axles")
def revise_axles(ticket_id: int, body: AxleReviseIn, authorization: AuthHeader = None):
    """封后轴重修订：甲路径当场拒写；乙路径只落影子草稿。两边不得混用。"""
    user = require_writer(authorization)
    lock_key = f"ticket:{ticket_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            ticket = conn.execute(
                """SELECT t.*, b.axle_limit FROM weigh_tickets t
                   JOIN weigh_lanes l ON l.id=t.lane_id
                   JOIN weigh_bridges b ON b.id=l.bridge_id
                   WHERE t.id=?""",
                (ticket_id,),
            ).fetchone()
            if not ticket:
                raise HTTPException(404, "过磅票不存在")
            seal = active_seal(conn, ticket_id)
            sealed = is_sealed(dict(ticket), seal)

            axles = [a.model_dump() for a in body.axle_weights]
            gross, _peak, _spread = validate_axle_weights(axles)

            if body.draft:
                if not sealed:
                    log_deviation(
                        conn,
                        ticket_id,
                        seal["id"] if seal else None,
                        user,
                        "影子草稿",
                        "封后轴重偏航",
                        "乙",
                        "非封态票走影子草稿",
                    )
                    conn.commit()
                    raise HTTPException(409, DRAFT_REQUIRED)
                cur = conn.execute(
                    """INSERT INTO axle_drafts(ticket_id,seal_id,axle_weights,gross_t,
                       status,note,actor,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        ticket_id,
                        seal["id"] if seal else None,
                        axle_json(axles),
                        gross,
                        "待解封校验",
                        body.note or "封后修订（影子草稿）",
                        user,
                        now_iso(),
                    ),
                )
                write_audit(
                    conn,
                    user,
                    "axle_draft",
                    ticket["ticket_code"],
                    {"draft_id": cur.lastrowid, "gross_t": gross, "path": "乙"},
                )
                conn.commit()
                return {
                    "draft_id": cur.lastrowid,
                    "status": "待解封校验",
                    "gross_t": gross,
                    "axle_weights": axles,
                    "path": "乙",
                }

            # 甲路径：封态票正式写回当场拒写，正式轴重零改
            validate_axle_count(axles, int(ticket["axle_limit"]))
            if sealed:
                refuse_formal_write(
                    conn,
                    user,
                    ticket_id,
                    seal,
                    "正式轴重写回",
                    f"axes={len(axles)}; gross={gross}",
                )

            # 非封态票：正式的轴重修订（既有行为，须先过票态可变校验）
            validate_ticket_mutable(dict(ticket))
            conn.execute(
                "UPDATE weigh_tickets SET axle_weights=?, gross_t=? WHERE id=?",
                (axle_json(axles), gross, ticket_id),
            )
            write_audit(
                conn,
                user,
                "axle_revise",
                ticket["ticket_code"],
                {"gross_t": gross},
            )
            conn.commit()
            return {
                "ticket_id": ticket_id,
                "gross_t": gross,
                "axle_weights": axles,
                "path": "甲",
            }
        finally:
            release(conn, lock_key, user)