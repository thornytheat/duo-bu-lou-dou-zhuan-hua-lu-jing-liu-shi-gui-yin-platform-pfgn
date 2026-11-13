"""IdentityLink：匿名标识 → 登录账号的关联台账。

口径：
- 一行 = 一次「匿名标识与账号绑定」关系；同一对键重复投递只累加 delivery_count；
- active 参与身份合成；suspended 保留台账与历史结果但不参与合成，可 resume 恢复；
- 一个匿名标识若指向两个不同账号，相关两行全部置 conflicted，禁止强行合并，
  冲突双方各自的历史结果继续可用，由人工在台账里解决（resolve）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.constants.enums import IdentityLinkStatus


class IdentityLink(Base):
    __tablename__ = "identity_links"
    __table_args__ = (
        UniqueConstraint("anonymous_key", "account_key", name="uq_identity_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anonymous_key: Mapped[str] = mapped_column(String(64), nullable=False)
    account_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=IdentityLinkStatus.ACTIVE.value
    )
    # 同一关联对的重复投递次数：首投为 1，重复投递累加，不新增台账行、不新增人数
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    conflict_with: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conflict_detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    note: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_delivery_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
