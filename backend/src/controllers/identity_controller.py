"""身份关联控制器：台账投递（幂等）/ 列表 / 中断 / 恢复 / 冲突解决 / 摘要。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.schemas.identity import IdentityLinkIn
from src.services import audit_service, identity_link_service, identity_service


def deliver_link(db: Session, payload: IdentityLinkIn, actor: dict, request: Request) -> dict:
    link, info = identity_link_service.deliver(
        db, payload.anonymous_key, payload.account_key, payload.note
    )
    audit_service.record(
        db,
        actor_email=actor["email"],
        actor_role=actor["role"],
        action="identity.deliver",
        entity="identity_link",
        entity_id=link.id,
        detail={
            "anonymous_key": link.anonymous_key,
            "account_key": link.account_key,
            "created": info["created"],
            "delivery_count": info["delivery_count"],
            "conflict": info["conflict"],
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"link": identity_link_service.serialize(link), "delivery": info}


def list_links(
    db: Session,
    status: str | None = None,
    anonymous_key: str | None = None,
    account_key: str | None = None,
) -> dict:
    rows = identity_link_service.list_links(db, status, anonymous_key, account_key)
    return {
        "items": [identity_link_service.serialize(row) for row in rows],
        "total": len(rows),
        "summary": identity_service.conflict_summary(db),
    }


def suspend_link(db: Session, link_id: int, actor: dict, request: Request) -> dict:
    link = identity_link_service.suspend(db, link_id)
    audit_service.record(
        db,
        actor_email=actor["email"],
        actor_role=actor["role"],
        action="identity.suspend",
        entity="identity_link",
        entity_id=link.id,
        detail={"anonymous_key": link.anonymous_key, "account_key": link.account_key},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"link": identity_link_service.serialize(link)}


def resume_link(db: Session, link_id: int, actor: dict, request: Request) -> dict:
    link = identity_link_service.resume(db, link_id)
    audit_service.record(
        db,
        actor_email=actor["email"],
        actor_role=actor["role"],
        action="identity.resume",
        entity="identity_link",
        entity_id=link.id,
        detail={"anonymous_key": link.anonymous_key, "account_key": link.account_key},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"link": identity_link_service.serialize(link)}


def resolve_link(db: Session, link_id: int, action: str, actor: dict, request: Request) -> dict:
    link = identity_link_service.resolve(db, link_id, action)
    audit_service.record(
        db,
        actor_email=actor["email"],
        actor_role=actor["role"],
        action="identity.resolve",
        entity="identity_link",
        entity_id=link.id,
        detail={
            "action": action,
            "anonymous_key": link.anonymous_key,
            "account_key": link.account_key,
            "status": link.status,
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"link": identity_link_service.serialize(link)}


def summary(db: Session) -> dict:
    return identity_service.conflict_summary(db)
