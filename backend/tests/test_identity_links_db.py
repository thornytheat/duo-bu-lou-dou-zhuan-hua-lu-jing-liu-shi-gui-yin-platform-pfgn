"""关联台账状态机单测：幂等投递、冲突标记、中断/恢复、冲突解决。

用 SQLite 内存库 + SQLAlchemy 建表，不依赖 MySQL；只测 identity_link_service
的台账语义，不触碰漏斗算法。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.config.database import Base
from src.constants.enums import IdentityLinkStatus
from src.models.identity_link import IdentityLink
from src.services import identity_link_service as svc
from src.utils.errors import AppError


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine, tables=[IdentityLink.__table__])
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_first_delivery_creates_active_link(db):
    link, info = svc.deliver(db, "anon:dev1", "U0001")
    assert info["created"] is True
    assert link.status == IdentityLinkStatus.ACTIVE.value
    assert link.delivery_count == 1
    assert db.scalar(select(IdentityLink)) is link


def test_repeated_delivery_is_idempotent_and_increments_count(db):
    first, info1 = svc.deliver(db, "anon:dev1", "U0001", "首次")
    second, info2 = svc.deliver(db, "anon:dev1", "U0001")
    assert info1["created"] is True
    assert info2["created"] is False
    assert first.id == second.id
    assert second.delivery_count == 2
    # 台账始终只有一行，人数不会新增
    assert len(db.scalars(select(IdentityLink)).all()) == 1


def test_second_account_marks_both_links_conflicted(db):
    svc.deliver(db, "anon:shared", "U0101")
    link2, info = svc.deliver(db, "anon:shared", "U0102")
    assert info["conflict"] is True
    rows = db.scalars(select(IdentityLink).where(IdentityLink.anonymous_key == "anon:shared")).all()
    assert {row.status for row in rows} == {IdentityLinkStatus.CONFLICTED.value}
    assert all(row.conflict_detail.get("reason") == "anonymous_key_bound_to_multiple_accounts" for row in rows)
    assert link2.conflict_with == "U0101"


def test_validation_rejects_bad_prefixes(db):
    with pytest.raises(AppError):
        svc.deliver(db, "dev1", "U0001")            # 匿名侧缺前缀
    with pytest.raises(AppError):
        svc.deliver(db, "anon:dev1", "anon:U0001")  # 账号侧带匿名前缀
    with pytest.raises(AppError):
        svc.deliver(db, "anon:same", "anon:same")


def test_suspend_then_resume_roundtrip(db):
    link, _ = svc.deliver(db, "anon:dev2", "U0002")
    suspended = svc.suspend(db, link.id)
    assert suspended.status == IdentityLinkStatus.SUSPENDED.value
    resumed = svc.resume(db, link.id)
    assert resumed.status == IdentityLinkStatus.ACTIVE.value
    # 台账行始终是同一行，历史结果引用不会断
    assert resumed.id == link.id


def test_suspend_conflicted_link_is_rejected(db):
    svc.deliver(db, "anon:c", "U0201")
    svc.deliver(db, "anon:c", "U0202")
    link = db.scalars(select(IdentityLink).where(IdentityLink.account_key == "U0201")).first()
    with pytest.raises(AppError):
        svc.suspend(db, link.id)


def test_resume_blocked_when_another_active_link_exists(db):
    first, _ = svc.deliver(db, "anon:d", "U0301")
    svc.suspend(db, first.id)
    # 另一条指向不同账号的关联生效中
    svc.deliver(db, "anon:d", "U0302")
    with pytest.raises(AppError):
        svc.resume(db, first.id)


def test_resolve_conflict_activate_clears_marks(db):
    svc.deliver(db, "anon:e", "U0401")
    second, _ = svc.deliver(db, "anon:e", "U0402")
    # 先把对方中断，才能激活当前行
    first = db.scalars(select(IdentityLink).where(IdentityLink.account_key == "U0401")).first()
    svc.resolve(db, first.id, "suspend")
    resolved = svc.resolve(db, second.id, "activate")
    assert resolved.status == IdentityLinkStatus.ACTIVE.value
    assert resolved.conflict_with is None
    assert resolved.conflict_detail == {}


def test_resolve_conflict_suspend_keeps_other_side(db):
    svc.deliver(db, "anon:f", "U0501")
    svc.deliver(db, "anon:f", "U0502")
    first = db.scalars(select(IdentityLink).where(IdentityLink.account_key == "U0501")).first()
    second = db.scalars(select(IdentityLink).where(IdentityLink.account_key == "U0502")).first()
    out = svc.resolve(db, first.id, "suspend")
    assert out.status == IdentityLinkStatus.SUSPENDED.value
    # 另一侧仍是冲突态，必须人工逐条解决，系统不替用户选边
    assert second.status == IdentityLinkStatus.CONFLICTED.value


def test_resolve_rejects_non_conflicted_link(db):
    link, _ = svc.deliver(db, "anon:g", "U0601")
    with pytest.raises(AppError):
        svc.resolve(db, link.id, "activate")


def test_resolve_rejects_unknown_action(db):
    svc.deliver(db, "anon:h", "U0701")
    svc.deliver(db, "anon:h", "U0702")
    link = db.scalars(select(IdentityLink).where(IdentityLink.account_key == "U0701")).first()
    with pytest.raises(AppError):
        svc.resolve(db, link.id, "merge")


def test_summary_counts_by_status(db):
    svc.deliver(db, "anon:i", "U0801")
    svc.deliver(db, "anon:i", "U0801")  # 重复投递
    svc.deliver(db, "anon:j", "U0901")
    svc.deliver(db, "anon:j", "U0902")  # 两条冲突
    link = db.scalars(select(IdentityLink).where(IdentityLink.anonymous_key == "anon:i")).first()
    svc.suspend(db, link.id)
    from src.services import identity_service
    out = identity_service.conflict_summary(db)
    assert out["total"] == 3
    assert out["conflicted"] == 2
    assert out["suspended"] == 1
    assert out["active"] == 0
    assert out["repeated_deliveries"] == 1
