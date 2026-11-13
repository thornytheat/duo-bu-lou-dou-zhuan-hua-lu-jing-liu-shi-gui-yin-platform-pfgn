from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.controllers import identity_controller
from src.middlewares.rbac import require_permission
from src.schemas.identity import IdentityLinkIn, IdentityResolveIn

router = APIRouter(prefix="/identity", tags=["identity"])


@router.get("/links")
def list_links(
    status: str | None = Query(default=None),
    anonymous_key: str | None = Query(default=None),
    account_key: str | None = Query(default=None),
    principal: dict = Depends(require_permission("identity:read")),
    db: Session = Depends(get_db),
) -> dict:
    return identity_controller.list_links(db, status, anonymous_key, account_key)


@router.post("/links")
def deliver_link(
    payload: IdentityLinkIn,
    request: Request,
    principal: dict = Depends(require_permission("identity:write")),
    db: Session = Depends(get_db),
) -> dict:
    return identity_controller.deliver_link(db, payload, principal, request)


@router.get("/summary")
def summary(
    principal: dict = Depends(require_permission("identity:read")),
    db: Session = Depends(get_db),
) -> dict:
    return identity_controller.summary(db)


@router.post("/links/{link_id}/suspend")
def suspend_link(
    link_id: int,
    request: Request,
    principal: dict = Depends(require_permission("identity:write")),
    db: Session = Depends(get_db),
) -> dict:
    return identity_controller.suspend_link(db, link_id, principal, request)


@router.post("/links/{link_id}/resume")
def resume_link(
    link_id: int,
    request: Request,
    principal: dict = Depends(require_permission("identity:write")),
    db: Session = Depends(get_db),
) -> dict:
    return identity_controller.resume_link(db, link_id, principal, request)


@router.post("/links/{link_id}/resolve")
def resolve_link(
    link_id: int,
    payload: IdentityResolveIn,
    request: Request,
    principal: dict = Depends(require_permission("identity:resolve")),
    db: Session = Depends(get_db),
) -> dict:
    return identity_controller.resolve_link(db, link_id, payload.action, principal, request)
