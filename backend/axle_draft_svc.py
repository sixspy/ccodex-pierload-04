"""影子轴重草稿：乙路径的解封校验后原子晋升。

晋升须全绿（事务内一次提交，任一不满足即拒）：
  1. 草稿处于「待解封校验」；
  2. 起票对应封闸快照已解封（status='已解封'）；
  3. 无未解封的活动封闸；
  4. 该草稿的影子血缘边随晋升一并转正式；
  5. 正式轴重、毛重、闸代一并迁移，草稿转「已晋升」。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from axle_seal_guard import active_seal, parse_axle_weights
from db import connect, now_iso, write_audit
from locks import release, require_lock
from validators import format_manifest

router = APIRouter(tags=["axle_drafts"])


@router.get("/api/axle-drafts")
def list_drafts(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.*, t.ticket_code FROM axle_drafts d
               JOIN weigh_tickets t ON t.id=d.ticket_id ORDER BY d.id DESC"""
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["axle_weights"] = json.loads(d["axle_weights"] or "[]")
            items.append(d)
        return {"items": items}


@router.post("/api/axle-drafts/{draft_id}/promote")
def promote_draft(draft_id: int, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        draft = conn.execute(
            "SELECT * FROM axle_drafts WHERE id=?", (draft_id,)
        ).fetchone()
        if not draft:
            raise HTTPException(404, "影子草稿不存在")
        ticket_id = draft["ticket_id"]
        lock_key = f"ticket:{ticket_id}"
        require_lock(conn, lock_key, user)
        try:
            if draft["status"] != "待解封校验":
                raise HTTPException(409, "草稿非待解封校验态")
            if active_seal(conn, ticket_id):
                raise HTTPException(409, "封闸未解封，影子草稿不得晋升")

            ticket = conn.execute(
                "SELECT * FROM weigh_tickets WHERE id=?", (ticket_id,)
            ).fetchone()
            if not ticket:
                raise HTTPException(404, "过磅票不存在")

            axles = parse_axle_weights(draft["axle_weights"])
            gross = round(float(draft["gross_t"]), 3)
            now = now_iso()
            next_gen = int(ticket["generation"]) + 1

            # 原子晋升：正式轴重 + 闸代 + 新代快照 + 票态 + 血缘状态 + 草稿状态，一次提交
            conn.execute(
                """UPDATE weigh_tickets SET axle_weights=?, gross_t=?,
                   generation=?, status='已封闸' WHERE id=?""",
                (json.dumps(axles, ensure_ascii=False), gross, next_gen, ticket_id),
            )
            seal_ref = f"GS-{ticket['ticket_code']}-G{next_gen}"
            cur = conn.execute(
                """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,
                   status,sealed_at,axle_weights,gross_t)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    ticket_id,
                    next_gen,
                    format_manifest(dict(ticket), gross, next_gen),
                    seal_ref,
                    "已封闸",
                    now,
                    json.dumps(axles, ensure_ascii=False),
                    gross,
                ),
            )
            conn.execute(
                "UPDATE axle_lineage SET state='正式' WHERE draft_id=?",
                (draft_id,),
            )
            conn.execute(
                "UPDATE axle_drafts SET status='已晋升', promoted_at=? WHERE id=?",
                (now, draft_id),
            )
            write_audit(
                conn,
                user,
                "axle_promote",
                ticket["ticket_code"],
                {
                    "draft_id": draft_id,
                    "gross_t": gross,
                    "generation": next_gen,
                    "seal_id": cur.lastrowid,
                    "path": "乙",
                },
            )
            conn.commit()
            return {
                "draft_id": draft_id,
                "ticket_id": ticket_id,
                "status": "已晋升",
                "gross_t": gross,
                "generation": next_gen,
                "seal_ref": seal_ref,
            }
        finally:
            release(conn, lock_key, user)
