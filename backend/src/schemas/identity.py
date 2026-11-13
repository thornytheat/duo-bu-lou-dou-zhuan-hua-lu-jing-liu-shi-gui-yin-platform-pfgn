from __future__ import annotations

from pydantic import BaseModel, Field


class IdentityLinkIn(BaseModel):
    anonymous_key: str = Field(min_length=1, max_length=64)
    account_key: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=240)


class IdentityResolveIn(BaseModel):
    # activate = 冲突解决后重新生效；suspend = 放弃该绑定
    action: str = Field(pattern="^(activate|suspend)$")
