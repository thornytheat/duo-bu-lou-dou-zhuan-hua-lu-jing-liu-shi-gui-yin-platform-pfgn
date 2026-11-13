"""关联台账写入：幂等投递、冲突检测、中断 / 恢复 / 冲突解决。

与 identity_service 的分工：本模块只负责台账行的状态机；合成口径全部在
identity_service 的纯函数核心里，本模块不碰事件与算法。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import ANONYMOUS_KEY_PREFIX, IdentityLinkStatus
from src.models.identity_link import IdentityLink
from src.utils.errors import conflict, not_found, validation
from src.utils.timeutil import BIZ_TZ


def serialize(link: IdentityLink) -> dict:
    return {
        "id": link.id,
        "anonymous_key": link.anonymous_key,
        "account_key": link.account_key,
        "status": link.status,
        "delivery_count": link.delivery_count,
        "conflict_with": link.conflict_with,
        "conflict_detail": link.conflict_detail or {},
        "note": link.note,
        "first_seen_at": link.first_seen_at.isoformat() if link.first_seen_at else None,
        "last_delivery_at": link.last_delivery_at.isoformat() if link.last_delivery_at else None,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "updated_at": link.updated_at.isoformat() if link.updated_at else None,
    }


def list_links(
    db: Session,
    status: str | None = None,
    anonymous_key: str | None = None,
    account_key: str | None = None,
) -> list[IdentityLink]:
    stmt = select(IdentityLink).order_by(IdentityLink.id.desc())
    if status:
        stmt = stmt.where(IdentityLink.status == status)
    if anonymous_key:
        stmt = stmt.where(IdentityLink.anonymous_key == anonymous_key.strip())
    if account_key:
        stmt = stmt.where(IdentityLink.account_key == account_key.strip())
    return list(db.scalars(stmt).all())


def _validate_pair(anonymous_key: str, account_key: str) -> tuple[str, str]:
    anonymous_key = (anonymous_key or "").strip()
    account_key = (account_key or "").strip()
    if not anonymous_key or not account_key:
        raise validation({"reason": "anonymous_key 与 account_key 都不能为空"})
    if not is_anonymous_safe(anonymous_key):
        raise validation({"reason": f"匿名标识必须以 {ANONYMOUS_KEY_PREFIX} 开头", "anonymous_key": anonymous_key})
    if is_anonymous_safe(account_key):
        raise validation({"reason": "账号标识不能使用匿名前缀", "account_key": account_key})
    if len(anonymous_key) > 64 or len(account_key) > 64:
        raise validation({"reason": "标识长度不能超过 64"})
    if anonymous_key == account_key:
        raise validation({"reason": "匿名标识与账号标识不能相同"})
    return anonymous_key, account_key


def is_anonymous_safe(key: str) -> bool:
    return key.startswith(ANONYMOUS_KEY_PREFIX)


def deliver(
    db: Session,
    anonymous_key: str,
    account_key: str,
    note: str = "",
) -> tuple[IdentityLink, dict]:
    """投递一次关联。返回 (台账行, 投递元信息)。

    幂等：同一 (anonymous_key, account_key) 重复投递只累加 delivery_count，
    不新增行；人数在事件合成阶段计算，重复投递不会让人数 +1。
    冲突：该匿名标识已指向别的账号时，新老链接全部置 conflicted，禁止合并。
    """
    anonymous_key, account_key = _validate_pair(anonymous_key, account_key)
    now = datetime.now(BIZ_TZ).replace(tzinfo=None)

    existing = db.scalar(
        select(IdentityLink).where(
            IdentityLink.anonymous_key == anonymous_key,
            IdentityLink.account_key == account_key,
        )
    )
    if existing is not None:
        existing.delivery_count += 1
        existing.last_delivery_at = now
        if note:
            existing.note = note
        # 已经冲突/中断的关联不因重复投递被静默改写状态
        return existing, {
            "created": False,
            "delivery_count": existing.delivery_count,
            "status": existing.status,
            "conflict": existing.status == IdentityLinkStatus.CONFLICTED.value,
        }

    # 冲突检测：同一匿名标识指向不同账号
    siblings = list(
        db.scalars(
            select(IdentityLink).where(
                IdentityLink.anonymous_key == anonymous_key,
                IdentityLink.account_key != account_key,
            )
        ).all()
    )
    has_conflict = bool(siblings)
    link = IdentityLink(
        anonymous_key=anonymous_key,
        account_key=account_key,
        status=IdentityLinkStatus.CONFLICTED.value if has_conflict else IdentityLinkStatus.ACTIVE.value,
        delivery_count=1,
        note=note or "",
        first_seen_at=now,
        last_delivery_at=now,
    )
    if has_conflict:
        other_accounts = sorted({item.account_key for item in siblings})
        link.conflict_with = other_accounts[0]
        link.conflict_detail = {
            "reason": "anonymous_key_bound_to_multiple_accounts",
            "other_accounts": other_accounts,
            "detected_at": now.isoformat(),
        }
    db.add(link)
    db.flush()

    if has_conflict:
        for sibling in siblings:
            sibling.status = IdentityLinkStatus.CONFLICTED.value
            sibling.conflict_with = account_key
            sibling.conflict_detail = {
                "reason": "anonymous_key_bound_to_multiple_accounts",
                "other_accounts": sorted(
                    {account_key, *(item.account_key for item in siblings if item.id != sibling.id)}
                ),
                "detected_at": now.isoformat(),
            }
        db.flush()

    return link, {
        "created": True,
        "delivery_count": 1,
        "status": link.status,
        "conflict": has_conflict,
        "conflict_accounts": sorted({item.account_key for item in siblings}) if has_conflict else [],
    }


def _get(db: Session, link_id: int) -> IdentityLink:
    link = db.get(IdentityLink, link_id)
    if link is None:
        raise not_found({"link_id": link_id})
    return link


def _has_other_active_link(db: Session, link: IdentityLink) -> IdentityLink | None:
    """同一匿名标识是否还有指向别的账号且仍生效的链接（恢复/解决前的安全闸）。"""
    return db.scalar(
        select(IdentityLink).where(
            IdentityLink.anonymous_key == link.anonymous_key,
            IdentityLink.account_key != link.account_key,
            IdentityLink.status == IdentityLinkStatus.ACTIVE.value,
        )
    )


def suspend(db: Session, link_id: int) -> IdentityLink:
    """中断关联：保留台账与历史结果，后续运行不再合成。"""
    link = _get(db, link_id)
    if link.status == IdentityLinkStatus.SUSPENDED.value:
        return link
    if link.status == IdentityLinkStatus.CONFLICTED.value:
        raise conflict({"reason": "冲突关联请先在台账中解决冲突，不能直接中断"})
    link.status = IdentityLinkStatus.SUSPENDED.value
    db.flush()
    return link


def resume(db: Session, link_id: int) -> IdentityLink:
    """恢复中断的关联：下一次运行重新合成，旧结果在此期间一直可用。"""
    link = _get(db, link_id)
    if link.status == IdentityLinkStatus.ACTIVE.value:
        return link
    if link.status == IdentityLinkStatus.CONFLICTED.value:
        raise conflict({"reason": "冲突关联请先解决冲突，不能直接恢复"})
    blocker = _has_other_active_link(db, link)
    if blocker is not None:
        raise conflict(
            {
                "reason": "恢复会把同一匿名标识合到两个账号，已拦截",
                "blocker_link_id": blocker.id,
                "blocker_account": blocker.account_key,
            }
        )
    link.status = IdentityLinkStatus.ACTIVE.value
    db.flush()
    return link


def resolve(db: Session, link_id: int, action: str) -> IdentityLink:
    """人工解决冲突：activate 重新生效（须无其他生效链接）或 suspend 放弃该绑定。"""
    link = _get(db, link_id)
    if link.status != IdentityLinkStatus.CONFLICTED.value:
        raise conflict({"reason": "只有冲突状态的关联需要解决", "status": link.status})
    if action == "suspend":
        link.status = IdentityLinkStatus.SUSPENDED.value
        db.flush()
        return link
    if action == "activate":
        blocker = _has_other_active_link(db, link)
        if blocker is not None:
            raise conflict(
                {
                    "reason": "该匿名标识仍有另一条生效关联，请先中断对方",
                    "blocker_link_id": blocker.id,
                    "blocker_account": blocker.account_key,
                }
            )
        link.status = IdentityLinkStatus.ACTIVE.value
        link.conflict_with = None
        link.conflict_detail = {}
        db.flush()
        return link
    raise validation({"reason": "action 只支持 activate / suspend"})
