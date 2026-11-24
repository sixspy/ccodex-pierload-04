"""封后轴重纪要：闸代生效后正式轴重零改；修订仅走影子草稿乙路径。

两边不得混用：
  甲  正式轴重表一律零改并当场拒写；
  乙  封后修订仅许写入影子轴重草稿，待解封校验通过后原子晋升，正式表晋升前零改。
偏离纪要即记「封后轴重偏航」并拒写，并写入 post_seal_axle_log。
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from db import (
    DIRECT_WRITE_REFUSED,
    POST_SEAL_RULING,
    now_iso,
)

SEALED_STATUSES = ("已封闸",)
FROZEN_STATUSES = ("已封闸", "已放行", "作废")


def active_seal(conn, ticket_id: int):
    """取该票当前生效的封闸快照（最新一条已封闸）。"""
    return conn.execute(
        """SELECT * FROM gate_seals WHERE ticket_id=? AND status='已封闸'
           ORDER BY id DESC LIMIT 1""",
        (ticket_id,),
    ).fetchone()


def is_sealed(ticket: dict, seal=None) -> bool:
    if ticket["status"] in SEALED_STATUSES:
        return True
    return seal is not None


def log_deviation(
    conn,
    ticket_id: int,
    seal_id: int | None,
    actor: str,
    attempted: str,
    outcome: str,
    ruling_path: str,
    detail: str,
) -> None:
    conn.execute(
        """INSERT INTO post_seal_axle_log(ticket_id,seal_id,actor,attempted,outcome,
           ruling_path,detail,created_at) VALUES (?,?,?,?,?,?,?,?)""",
        (
            ticket_id,
            seal_id,
            actor,
            attempted,
            outcome,
            ruling_path,
            detail,
            now_iso(),
        ),
    )


def refuse_formal_write(
    conn, actor: str, ticket_id: int, seal, attempted: str, detail: str
) -> None:
    """甲路径：正式轴重写回当场拒写，记偏航，事务内不留业务写。"""
    log_deviation(
        conn,
        ticket_id,
        seal["id"] if seal else None,
        actor,
        attempted,
        POST_SEAL_RULING["violation"],
        "甲",
        detail,
    )
    conn.commit()
    raise HTTPException(409, DIRECT_WRITE_REFUSED)


def axle_json(axles) -> str:
    return json.dumps(axles, ensure_ascii=False)


def parse_axle_weights(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


def same_axles(a: list, b: list) -> bool:
    """轴重同态：轴序与各轴重量都一致（浮点按 3 位小数量化）。"""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        ax = x.get("axle") if isinstance(x, dict) else None
        ay = y.get("axle") if isinstance(y, dict) else None
        wx = round(float(x.get("w", 0)), 3) if isinstance(x, dict) else None
        wy = round(float(y.get("w", 0)), 3) if isinstance(y, dict) else None
        if ax != ay or wx != wy:
            return False
    return True


def seal_axle_coherent(conn, ticket_id: int, seal=None) -> bool:
    """正式轴重与闸代快照轴重同态核验。"""
    if seal is None:
        seal = active_seal(conn, ticket_id)
    if seal is None:
        return True
    if seal["axle_weights"] is None or seal["gross_t"] is None:
        # 迁移后仍缺快照轴重：视为不可证明，按偏航处理
        return False
    ticket = conn.execute(
        "SELECT axle_weights, gross_t FROM weigh_tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if not ticket:
        return False
    live = parse_axle_weights(ticket["axle_weights"])
    snap = parse_axle_weights(seal["axle_weights"])
    if not same_axles(live, snap):
        return False
    return abs(round(float(ticket["gross_t"]), 3) - round(float(seal["gross_t"]), 3)) <= 0.001


def ticket_axle_state(conn, ticket: dict) -> dict:
    seal = active_seal(conn, ticket["id"])
    sealed = is_sealed(ticket, seal)
    drafts = conn.execute(
        """SELECT * FROM axle_drafts WHERE ticket_id=? ORDER BY id DESC""",
        (ticket["id"],),
    ).fetchall()
    pending = [d for d in drafts if d["status"] == "待解封校验"]
    deviations = conn.execute(
        """SELECT * FROM post_seal_axle_log WHERE ticket_id=? ORDER BY id DESC LIMIT 50""",
        (ticket["id"],),
    ).fetchall()
    return {
        "sealed": sealed,
        "seal_ref": seal["seal_ref"] if seal else None,
        "seal_generation": seal["ticket_generation"] if seal else None,
        "axle_locked": sealed,
        "ruling_path": "甲" if sealed else None,
        "pending_drafts": len(pending),
        "axle_coherent": seal_axle_coherent(conn, ticket["id"], seal) if sealed else True,
        "deviation_count": len(deviations),
    }


def seal_axle_audit(conn) -> dict:
    """五页同态核：封后轴重偏航统计（快照/过磅/血缘/对账/地磅共用）。

    只核每票当前生效（最新一条已封闸）的快照；解封后升代重封的历史快照不重复计偏。
    """
    rows = conn.execute(
        """SELECT * FROM gate_seals s
           WHERE s.status='已封闸'
             AND s.id = (SELECT MAX(x.id) FROM gate_seals x
                         WHERE x.ticket_id=s.ticket_id AND x.status='已封闸')"""
    ).fetchall()
    incoherent = []
    for s in rows:
        if not seal_axle_coherent(conn, s["ticket_id"], s):
            incoherent.append(
                {"seal_id": s["id"], "ticket_id": s["ticket_id"], "seal_ref": s["seal_ref"]}
            )
    shadow_edges = conn.execute(
        """SELECT COUNT(*) c FROM axle_lineage WHERE state!='正式'"""
    ).fetchone()["c"]
    return {
        "sealed_count": len(rows),
        "incoherent": len(incoherent),
        "incoherent_seals": incoherent,
        "shadow_edges": shadow_edges,
        "ruling": POST_SEAL_RULING,
    }
