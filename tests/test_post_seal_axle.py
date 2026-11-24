"""封后案丁 · 第4号：封后轴重禁改。

三红：
  甲  封闸生效后正式轴重表须零改并当场拒写；
  乙  封后修订只许进影子轴重草稿，未晋升不得混入正式血缘；
  争用 双会话同票改轴须抢票锁，后到只记争用痕。

附核：
  对账行不得对封后轴重偏航仍亮合格（快照/过磅/血缘/对账/地磅 同态）。

夹具：sealed_ticket 取种子里的已封闸票（闸代 2、已有快照）；
偏航夹具直接落一行「先封后改」的旧数据（新守卫只挡封后实时改，挡不住历史坏行）。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db, now_iso


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user="admin", password="admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _sealed_ticket(c: TestClient, h: dict) -> dict:
    items = c.get("/api/tickets?status=已封闸", headers=h).json()["items"]
    assert items, "夹具缺已封闸票"
    return items[0]


def _ticket_row(ticket_id: int) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM weigh_tickets WHERE id=?", (ticket_id,)
        ).fetchone()
    return dict(row)


RESAMPLE = [{"axle": 1, "w": 13.5}, {"axle": 2, "w": 14.5}, {"axle": 3, "w": 15.0}]


def test_case_d4_jia_sealed_axle_rewrite_rejected():
    """甲：已封票的正式轴重写回必须当场 409，正式表零改，并记偏航。"""
    c = TestClient(app)
    h = _auth(c)
    ticket = _sealed_ticket(c, h)
    before = _ticket_row(ticket["id"])

    res = c.post(
        f"/api/tickets/{ticket['id']}/axles",
        headers=h,
        json={"axle_weights": RESAMPLE, "draft": False, "note": "监控台重采样回写"},
    )
    assert res.status_code == 409, res.text
    assert "封后轴重禁改" in res.json()["detail"]

    after = _ticket_row(ticket["id"])
    assert json.loads(after["axle_weights"]) == json.loads(before["axle_weights"])
    assert after["gross_t"] == before["gross_t"]

    detail = c.get(f"/api/tickets/{ticket['id']}", headers=h).json()
    assert detail["axle_state"]["sealed"] is True
    assert detail["axle_state"]["axle_locked"] is True
    assert detail["axle_state"]["ruling_path"] == "甲"
    assert any(
        d["attempted"] == "正式轴重写回" for d in detail["axle_deviations"]
    ), detail["axle_deviations"]


def test_case_d4_yi_shadow_draft_sealed_off_formal_lineage():
    """乙：封后修订只进影子草稿；未晋升不得出现在正式血缘，晋升须先解封。"""
    c = TestClient(app)
    h = _auth(c)
    ticket = _sealed_ticket(c, h)
    tid = ticket["id"]
    before = _ticket_row(tid)

    # 封后正式写回被拒（乙路径同样不接受正式表直改）
    res = c.post(
        f"/api/tickets/{tid}/axles",
        headers=h,
        json={"axle_weights": RESAMPLE, "draft": False, "note": "重采样"},
    )
    assert res.status_code == 409

    # 影子草稿落库，正式轴重零改
    draft = c.post(
        f"/api/tickets/{tid}/axles",
        headers=h,
        json={"axle_weights": RESAMPLE, "draft": True, "note": "解封后晋升用"},
    )
    assert draft.status_code == 200, draft.text
    draft_id = draft.json()["draft_id"]
    assert draft.json()["status"] == "待解封校验"
    assert _ticket_row(tid)["gross_t"] == before["gross_t"]

    # 影子血缘边：未晋升不得入正式血缘
    edge = c.post(
        "/api/lineage",
        headers=h,
        json={
            "from_ticket_id": tid,
            "edge_type": "重采样核校",
            "detail": "影子草稿轴重曲线",
            "draft_id": draft_id,
        },
    )
    assert edge.status_code == 200, edge.text
    formal = c.get("/api/lineage", headers=h).json()["items"]
    assert all(e.get("draft_id") != draft_id for e in formal), "影子边混入正式血缘"
    shadow = c.get("/api/lineage?include_shadow=1", headers=h).json()["items"]
    assert any(
        e.get("draft_id") == draft_id and e.get("state") == "影子" for e in shadow
    )

    # 未解封不得晋升
    seal_ref = c.get(f"/api/tickets/{tid}", headers=h).json()["gate_seals"][0][
        "seal_ref"
    ]
    assert (
        c.post(f"/api/axle-drafts/{draft_id}/promote", headers=h, json={}).status_code
        == 409
    )
    assert _ticket_row(tid)["gross_t"] == before["gross_t"]

    # 解封校验通过后原子晋升：正式轴重、血缘状态、草稿状态一并迁移
    unseal = c.post(
        f"/api/seals/{seal_ref}/unseal",
        headers=h,
        json={"note": "解封核校：影子草稿复磅一致"},
    )
    assert unseal.status_code == 200, unseal.text
    promoted = c.post(f"/api/axle-drafts/{draft_id}/promote", headers=h, json={})
    assert promoted.status_code == 200, promoted.text

    after = _ticket_row(tid)
    assert json.loads(after["axle_weights"]) == RESAMPLE
    assert round(after["gross_t"], 3) == round(sum(a["w"] for a in RESAMPLE), 3)
    assert after["generation"] == before["generation"] + 1
    assert after["status"] == "已封闸", "晋升后新代快照生效，票态须为已封闸"

    formal = c.get("/api/lineage", headers=h).json()["items"]
    hit = [e for e in formal if e.get("draft_id") == draft_id]
    assert hit and hit[0]["state"] == "正式", "晋升后血缘未转正式"
    drafts = c.get(f"/api/tickets/{tid}", headers=h).json()["axle_drafts"]
    assert any(d["id"] == draft_id and d["status"] == "已晋升" for d in drafts)


def test_case_d4_contention_two_sessions_same_ticket():
    """争用：双会话同票改轴，后到只记争用痕，不写业务。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin", "admin123")
    h_writer = _auth(c, "writer", "writer123")
    ticket = _sealed_ticket(c, h_admin)
    tid = ticket["id"]
    before = _ticket_row(tid)

    key = f"ticket:{tid}"
    assert (
        c.post("/api/locks/acquire", headers=h_admin, json={"lock_key": key}).status_code
        == 200
    )
    try:
        for draft in (False, True):
            res = c.post(
                f"/api/tickets/{tid}/axles",
                headers=h_writer,
                json={
                    "axle_weights": RESAMPLE,
                    "draft": draft,
                    "note": "第二会话改轴",
                },
            )
            assert res.status_code == 409, res.text

        cont = c.get("/api/contention", headers=h_admin).json()["items"]
        hits = [x for x in cont if x["lock_key"] == key and x["actor"] == "writer"]
        assert hits, "后到未留争用痕"
        assert any("admin" in (x["detail"] or "") for x in hits)

        after = _ticket_row(tid)
        assert after["gross_t"] == before["gross_t"]
        detail = c.get(f"/api/tickets/{tid}", headers=h_admin).json()
        assert detail["axle_drafts"] == []
    finally:
        c.post("/api/locks/release", headers=h_admin, json={"lock_key": key})


def test_case_d4_reconcile_reason_distinguishes_drift_from_mismatch():
    """对账口径：毛重不符不是「无过磅票」；封后偏航单独命名。"""
    c = TestClient(app)
    h = _auth(c)
    # 种子 BL-2026-0103：有过磅票 58.5t，申报 52.3t → 毛重不符
    recon = c.get("/api/reconcile/BL-2026-0103", headers=h).json()
    assert recon["tickets"], "该提单确有过磅票"
    assert recon["balanced"] is False
    assert recon["reason"] == "毛重与提单不符", recon
    # 平衡且无快照：不能误报「毛重不符」
    recon1 = c.get("/api/reconcile/BL-2026-0101", headers=h).json()
    assert recon1["reason"] == "无闸代快照", recon1


def test_case_d4_migrated_legacy_drift_not_whitewashed(tmp_path, monkeypatch):
    """老库迁移：封后已被硬改的历史行不得被回填洗白，须仍核出偏航。"""
    import sqlite3

    from db import SCHEMA, _backfill_seal_snapshots, _ensure_columns
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript(SCHEMA)
    # 回退成迁移前结构
    for tbl, col in (
        ("gate_seals", "axle_weights"),
        ("gate_seals", "gross_t"),
        ("axle_lineage", "draft_id"),
        ("axle_lineage", "state"),
    ):
        conn.execute(f"ALTER TABLE {tbl} DROP COLUMN {col}")
    conn.execute(
        """INSERT INTO weigh_tickets(ticket_code,lane_id,bl_ref,vehicle_plate,
           axle_weights,gross_t,generation,status,created_at)
           VALUES ('WT-OLD',1,'BL-2026-0101','沪A·OLD',?,12.0,2,'已封闸',
                   '2026-01-01T00:00:00')""",
        (json.dumps([{"axle": 1, "w": 6.0}, {"axle": 2, "w": 6.0}]),),
    )
    tid = conn.execute(
        "SELECT id FROM weigh_tickets WHERE ticket_code='WT-OLD'"
    ).fetchone()[0]
    # 清单写死封闸毛重 9.0t；票面已被硬改成 12.0t
    conn.execute(
        """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,
           status,sealed_at) VALUES (?,2,?,?,?,'2026-01-01T00:00:00')""",
        (
            tid,
            "票号=WT-OLD;提单=BL-2026-0101;车牌=沪A·OLD;毛重=9.0t;闸代=2",
            "GS-OLD-G2",
            "已封闸",
        ),
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    _ensure_columns(conn)
    _backfill_seal_snapshots(conn)
    conn.commit()
    row = conn.execute(
        "SELECT axle_weights, gross_t FROM gate_seals WHERE seal_ref='GS-OLD-G2'"
    ).fetchone()
    conn.close()
    # 清单毛重是权威口径；轴重不得按被改票面回填
    assert row["gross_t"] == 9.0
    assert row["axle_weights"] is None, "历史坏行被回填洗白"


def test_case_d4_promoted_draft_not_falsely_flagged():
    """乙路径晋升后：旧快照已解封，证明与对账不得把合法晋升误判为偏航。"""
    c = TestClient(app)
    h = _auth(c)
    ticket = _sealed_ticket(c, h)
    tid = ticket["id"]
    # 与提单申报 41.0t 等量的重采样，避免掺入「毛重与提单不符」这条口径
    resample = [{"axle": 1, "w": 13.5}, {"axle": 2, "w": 14.0}, {"axle": 3, "w": 13.5}]

    draft = c.post(
        f"/api/tickets/{tid}/axles",
        headers=h,
        json={"axle_weights": resample, "draft": True, "note": "解封后晋升"},
    ).json()
    seal_ref = c.get(f"/api/tickets/{tid}", headers=h).json()["gate_seals"][0][
        "seal_ref"
    ]
    c.post(f"/api/seals/{seal_ref}/unseal", headers=h, json={"note": "解封核校"})
    assert (
        c.post(
            f"/api/axle-drafts/{draft['draft_id']}/promote", headers=h, json={}
        ).status_code
        == 200
    )

    code = ticket["ticket_code"]
    proof = c.get(f"/api/proof/ticket/{code}", headers=h).json()
    # 晋升落新代快照，且新快照与晋升后的票面同态
    assert proof["has_seal"] is True
    assert proof["seal_generation"] == ticket["generation"] + 1
    assert proof["seal_axle_coherent"] is True
    assert proof["coherent"] is True, proof

    recon = c.get(f"/api/reconcile/{ticket['bl_ref']}", headers=h).json()
    assert recon["gate_seal_coherent"] is True, recon
    assert recon["reason"] != "封后轴重偏航", recon

    conf = c.get("/api/axle-conformance", headers=h).json()
    assert conf["incoherent"] == 0, conf


def test_case_d4_reconcile_flags_post_seal_axle_drift():
    """同态：已封票正式轴重与闸代快照错位时，对账不得亮合格。"""
    c = TestClient(app)
    h = _auth(c)

    bl_code = "BL-DRIFT-D22"
    assert (
        c.post(
            "/api/bl",
            headers=h,
            json={
                "bl_code": bl_code,
                "cargo_desc": "封后偏航测样",
                "declared_gross_t": 12.0,
                "vessel_voyage": "测/001",
            },
        ).status_code
        == 200
    )

    with connect() as conn:
        lane = conn.execute(
            "SELECT id FROM weigh_lanes WHERE lane_code='LN-N-01-IN'"
        ).fetchone()
        snap = [{"axle": 1, "w": 4.5}, {"axle": 2, "w": 4.5}]
        live = [{"axle": 1, "w": 6.0}, {"axle": 2, "w": 6.0}]
        cur = conn.execute(
            """INSERT INTO weigh_tickets(ticket_code,lane_id,bl_ref,vehicle_plate,
               axle_weights,gross_t,generation,status,adjudicated_at,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "WT-D22-LEGACY",
                lane["id"],
                bl_code,
                "沪A·D22",
                json.dumps(live, ensure_ascii=False),
                12.0,
                2,
                "已封闸",
                now_iso(),
                now_iso(),
            ),
        )
        ticket_id = cur.lastrowid
        conn.execute(
            """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,
               seal_ref,status,sealed_at)
               VALUES (?,?,?,?,?,?)""",
            (
                ticket_id,
                2,
                "票号=WT-D22-LEGACY;提单=BL-DRIFT-D22;车牌=沪A·D22;毛重=9.0t;闸代=2",
                "GS-WT-D22-LEGACY-G2",
                "已封闸",
                "2026-01-11T09:00:00",
            ),
        )
        # 老库快照列回填（迁移后应有；未迁移时此句会失败，正是红点）
        conn.execute(
            "UPDATE gate_seals SET axle_weights=?, gross_t=? WHERE seal_ref=?",
            (json.dumps(snap, ensure_ascii=False), 9.0, "GS-WT-D22-LEGACY-G2"),
        )
        conn.commit()
        bridge_id = conn.execute(
            "SELECT bridge_id FROM weigh_lanes WHERE id=?", (lane["id"],)
        ).fetchone()["bridge_id"]

    recon = c.get(f"/api/reconcile/{bl_code}", headers=h).json()
    assert recon["gate_seal_coherent"] is False, recon
    assert recon["balanced"] is False, "封后轴重偏航却仍亮合格"
    assert recon["proof_ready"] is False
    assert "封后轴重偏航" in recon["reason"]

    snap_page = c.get("/api/seals/GS-WT-D22-LEGACY-G2", headers=h).json()
    assert snap_page["axle_coherent"] is False
    assert snap_page["axle_weights"] == snap
    assert snap_page["gross_t"] == 9.0

    proof = c.get("/api/proof/ticket/WT-D22-LEGACY", headers=h).json()
    assert proof["seal_axle_coherent"] is False
    assert proof["coherent"] is False

    bridge = c.get(f"/api/bridges/{bridge_id}", headers=h).json()
    assert bridge["seal_axle"]["incoherent"] >= 1

    conf = c.get("/api/axle-conformance", headers=h)
    assert conf.status_code == 200, conf.text
    assert conf.json()["incoherent"] >= 1
    assert conf.json()["ruling"]["code"] == "封后轴重纪要"
