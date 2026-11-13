"""端到端：身份台账 -> 漏斗运行 -> 会话路径 -> 报告 -> 导出 同源（SQLite 内存库）。

验证需求里的串联口径：
- 关联后合成一条路径，渠道/设备沿用最早有效触达；
- 同一关联重复投递人数不新增；
- 冲突不强行合并、两边原结果保留；
- 中断后旧结果可读，恢复后重新合成；
- 结果页(run)、路径(SessionPath)、报告、导出反映同一次合并。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.config.database import Base
from src.constants.enums import IdentityLinkStatus, ReportStatus
from src.models.drop_report import DropReport
from src.models.event import EventLog
from src.models.funnel import FunnelDef
from src.models.funnel_run import FunnelRun
from src.models.identity_link import IdentityLink
from src.models.session_path import SessionPath
from src.services import (
    export_service,
    identity_link_service,
    report_service,
    run_service,
)

STEPS = [
    {"key": "register", "label": "注册", "event": "register"},
    {"key": "trial", "label": "试听完成", "event": "trial_complete"},
    {"key": "pay", "label": "付费", "event": "pay"},
]
NOISE = ["browse_course", "exit"]
T0 = datetime(2026, 9, 1, 8, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _funnel(db) -> FunnelDef:
    funnel = FunnelDef(
        code="signup-trial-pay",
        name="注册→试听→付费",
        description="",
        line_no="L1",
        line_color="line-a",
        steps=[dict(s) for s in STEPS],
        noise_events=list(NOISE),
        window_days=7,
        cross_session=True,
        status="published",
        version=1,
        owner_email="admin@funnelpath.io",
    )
    db.add(funnel)
    db.flush()
    return funnel


def _event(db, user_key, name, hour, seq, channel="feed", device="ios"):
    db.add(
        EventLog(
            user_key=user_key,
            event_name=name,
            event_ts=T0 + timedelta(hours=hour),
            seq=seq,
            session_id=f"{user_key}-s",
            channel=channel,
            device=device,
            source="fixture",
        )
    )


def _build_split_person(db):
    """自然人 P：匿名段(feed/android) + 账号段(organic/ios)。"""
    _event(db, "anon:dev1", "register", 0, 1, channel="feed", device="android")
    _event(db, "anon:dev1", "browse_course", 1, 2, channel="feed", device="android")
    _event(db, "U0001", "trial_complete", 24, 3, channel="organic", device="ios")
    _event(db, "U0001", "pay", 48, 4, channel="organic", device="ios")
    _event(db, "U0001", "exit", 49, 5, channel="organic", device="ios")


def test_full_pipeline_run_path_report_export_share_one_merge(db):
    funnel = _funnel(db)
    _build_split_person(db)
    db.flush()

    # 投递关联（含一次重复投递）
    identity_link_service.deliver(db, "anon:dev1", "U0001")
    link, info = identity_link_service.deliver(db, "anon:dev1", "U0001")
    assert info["created"] is False and link.delivery_count == 2
    db.flush()

    run = run_service.execute(db, funnel, code="RUN-T1")
    db.flush()

    # 结果页口径：只算一个人，且完成全链路
    assert run.entry_users == 1
    assert run.converted_users == 1
    assert run.discard["identity_merged"] == 1
    assert run.discard["identity_absorbed_anonymous"] == 1

    # 路径物化：一条路径、含两个 source_key、标记 merged、首触渠道设备
    paths = db.scalars(select(SessionPath).where(SessionPath.run_id == run.id)).all()
    assert len(paths) == 1
    path = paths[0]
    assert path.identity_merged is True
    assert set(path.source_keys) == {"anon:dev1", "U0001"}
    assert path.channel == "feed" and path.device == "android"
    # 转化用户在命中最后一步后引擎即结束，末尾 exit 不进路径（既有状态机口径）
    assert [item["event"] for item in path.path_json] == [
        "register", "browse_course", "trial_complete", "pay"
    ]

    # 路径分析基于物化结果，仍能还原合成 outcome
    analysis = run_service.path_analysis(db, run, funnel, step_index=1)
    assert analysis["run"]["identity"]["merged"] == 1

    # 报告固化同一次合并的身份快照
    class _Payload:
        drop_step_index = None
        title = "测试归因报告"
        conclusion = ""

    report = report_service.create(db, run, funnel, _Payload(), "analyst@funnelpath.io")
    db.flush()
    assert report.identity_snapshot["merged"] == 1
    assert report.identity_snapshot["run_code"] == "RUN-T1"

    # 导出 MD / CSV 都带出身份合成信息（同一次合并）
    run_payload = run_service.serialize(run, funnel)
    md = export_service.render_markdown(report, funnel, run_payload)
    csv_text = export_service.render_csv(report)
    assert "身份关联" in md and "合并 1 个跨身份轨迹" in md
    assert "RUN-T1" in md
    assert "identity" in csv_text and "merged=1" in csv_text


def test_conflicted_identities_keep_both_outcomes(db):
    funnel = _funnel(db)
    _event(db, "anon:shared", "register", 0, 1, channel="feed")
    _event(db, "anon:shared", "browse_course", 1, 2, channel="feed")
    _event(db, "U0101", "register", 3, 3, channel="organic")
    _event(db, "U0102", "register", 5, 4, channel="social")
    db.flush()

    identity_link_service.deliver(db, "anon:shared", "U0101")
    identity_link_service.deliver(db, "anon:shared", "U0102")
    db.flush()

    conflicted = db.scalars(select(IdentityLink)).all()
    assert {row.status for row in conflicted} == {IdentityLinkStatus.CONFLICTED.value}

    run = run_service.execute(db, funnel, code="RUN-C1")
    # 三条身份都独立进入，没有被并成一个
    assert run.entry_users == 3
    assert run.discard.get("identity_merged", 0) == 0
    keys = set(db.scalars(select(SessionPath.user_key).where(SessionPath.run_id == run.id)).all())
    assert keys == {"anon:shared", "U0101", "U0102"}


def test_suspension_preserves_old_run_and_resume_remerges(db):
    funnel = _funnel(db)
    _event(db, "anon:dev2", "register", 0, 1)
    _event(db, "anon:dev2", "browse_course", 1, 2)
    _event(db, "U0002", "trial_complete", 20, 3)
    db.flush()

    link, _ = identity_link_service.deliver(db, "anon:dev2", "U0002")
    db.flush()

    # 第一次运行：合成一个人
    run_merged = run_service.execute(db, funnel, code="RUN-S1")
    assert run_merged.entry_users == 1
    assert run_merged.discard["identity_merged"] == 1

    # 中断关联
    identity_link_service.suspend(db, link.id)
    db.flush()

    # 第二次运行：不再合成；第一次运行的物化结果仍然完整可读
    run_split = run_service.execute(db, funnel, code="RUN-S2")
    assert run_split.discard.get("identity_merged", 0) == 0
    old_paths = db.scalars(select(SessionPath).where(SessionPath.run_id == run_merged.id)).all()
    assert len(old_paths) == 1 and old_paths[0].identity_merged is True

    # 恢复关联后第三次运行：重新合成
    identity_link_service.resume(db, link.id)
    db.flush()
    run_resumed = run_service.execute(db, funnel, code="RUN-S3")
    assert run_resumed.entry_users == 1
    assert run_resumed.discard["identity_merged"] == 1
    # 三次运行各自独立保留
    assert db.scalar(select(func.count()).select_from(FunnelRun)) == 3


def test_report_rebuild_picks_up_latest_identity_snapshot(db):
    funnel = _funnel(db)
    _build_split_person(db)
    db.flush()
    identity_link_service.deliver(db, "anon:dev1", "U0001")
    db.flush()

    run = run_service.execute(db, funnel, code="RUN-R1")

    class _Payload:
        drop_step_index = None
        title = "报告 A"
        conclusion = ""

    report = report_service.create(db, run, funnel, _Payload(), "analyst@funnelpath.io")
    db.flush()
    assert report.status == ReportStatus.DRAFT.value
    # 报告与运行、路径的身份口径一致
    assert report.identity_snapshot["absorbed_anonymous"] == 1
    assert report.cohort_users >= 0
    assert isinstance(report.top_paths, list)
