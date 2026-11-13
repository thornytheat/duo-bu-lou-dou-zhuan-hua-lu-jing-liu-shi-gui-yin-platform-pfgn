"""身份关联与合成：把「先匿名浏览、后登录」的两段事件合成一个人的轨迹。

设计分两层：
1. 纯函数核心 `build_components` / `resolve_rows`：只吃普通数据（链接三元组 +
   带 6 个字段的事件行），不依赖数据库，单测可直接覆盖；
2. DB 装配 `load_links` / `identity_graph` / `resolve_event_rows`：从关联台账
   读取有效链接，包一层 IdentityRow 改写渠道/设备与归属键。

口径（与需求逐条对应）：
- 关联后按行为事件时间 (event_ts, seq) 合成一条路径，事件顺序交给漏斗引擎排序；
- 渠道/设备沿用「组内最早有效触达」，即组内 event_ts 最早那条事件的渠道与设备；
- 同一关联对重复投递只累加 delivery_count，不新增台账行；合成在事件组上做，
  因此人数不会因重复投递而新增；
- 一个匿名标识已指向两个账号：相关链接全部置 conflicted 且不参与合成，
  两边各自保留原结果，台账里标记冲突，不强行合并；
- 关联中断（suspended）不参与合成，旧 run 的物化结果继续可读；恢复（resume）
  后下一次运行重新合成。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import ANONYMOUS_KEY_PREFIX, IdentityLinkStatus
from src.models.identity_link import IdentityLink


def is_anonymous_key(user_key: str) -> bool:
    return user_key.startswith(ANONYMOUS_KEY_PREFIX)


# ---------------------------------------------------------------------- 纯函数核心


@dataclass(frozen=True)
class IdentityRow:
    """事件行的身份合成包装：字段与 EventLog/测试 Event 同名。

    归属键改写为合成后的 canonical key；渠道/设备改写为组内最早有效触达；
    其余字段透传，漏斗引擎完全不感知合成过程。
    """

    user_key: str
    event_name: str
    event_ts: datetime
    seq: int
    channel: str
    device: str
    source_key: str
    session_id: str = ""


def build_components(links: list[tuple[str, str, str]]) -> dict[str, str]:
    """并查集：返回 原始 key -> 合成后 canonical key。

    links 元素 = (anonymous_key, account_key, status)；只有 active 的链接参与。
    canonical 取组件内的账号键（登录后的身份）；纯匿名组件取字典序最小键。
    conflicted / suspended 的链接直接忽略——前者禁止合并，后者临时断开。
    """
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        root = key
        while parent[root] != root:
            root = parent[root]
        while parent[key] != root:
            parent[key], key = root, parent[key]
        return root

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for anonymous_key, account_key, status in links:
        if status != IdentityLinkStatus.ACTIVE.value:
            parent.setdefault(anonymous_key, anonymous_key)
            parent.setdefault(account_key, account_key)
            continue
        union(anonymous_key, account_key)

    components: dict[str, set[str]] = {}
    for key in list(parent):
        components.setdefault(find(key), set()).add(key)

    mapping: dict[str, str] = {}
    for members in components.values():
        accounts = sorted(key for key in members if not is_anonymous_key(key))
        canonical = accounts[0] if accounts else min(members)
        for key in members:
            mapping[key] = canonical
    return mapping


def resolve_rows(
    rows: list,
    links: list[tuple[str, str, str]],
) -> tuple[list[IdentityRow], dict]:
    """把原始事件行按关联图合成。

    返回 (合成后的事件行, 身份统计)。合成行：
    - user_key 改写为组件 canonical key；
    - channel/device 改写为组件内最早事件（最早有效触达）的渠道与设备；
    - 保留 source_key 标注事件最初归属，便于审计与路径溯源。
    """
    mapping = build_components(links)

    grouped: dict[str, list] = {}
    for row in rows:
        canonical = mapping.get(row.user_key, row.user_key)
        grouped.setdefault(canonical, []).append(row)

    merged_rows: list[IdentityRow] = []
    merged_components = 0
    absorbed_anonymous_keys: set[str] = set()
    for canonical, group in grouped.items():
        ordered = sorted(group, key=lambda row: (row.event_ts, row.seq))
        first = ordered[0]
        source_keys = {row.user_key for row in group}
        anonymous_in_group = {key for key in source_keys if is_anonymous_key(key)}
        if anonymous_in_group and any(not is_anonymous_key(key) for key in source_keys):
            merged_components += 1
            absorbed_anonymous_keys.update(anonymous_in_group)
        for row in ordered:
            merged_rows.append(
                IdentityRow(
                    user_key=canonical,
                    event_name=row.event_name,
                    event_ts=row.event_ts,
                    seq=row.seq,
                    channel=first.channel,
                    device=first.device,
                    source_key=row.user_key,
                    session_id=getattr(row, "session_id", "") or "",
                )
            )

    stats = {
        "identity_components": len(grouped),
        "merged_components": merged_components,
        "absorbed_anonymous_keys": len(absorbed_anonymous_keys),
        "active_links": sum(1 for item in links if item[2] == IdentityLinkStatus.ACTIVE.value),
    }
    return merged_rows, stats


# ---------------------------------------------------------------------- DB 装配


def load_links(db: Session) -> list[IdentityLink]:
    """全部关联台账行（含中断/冲突，序列化与解析都用同一份快照）。"""
    return list(db.scalars(select(IdentityLink).order_by(IdentityLink.id)).all())


def identity_graph(db: Session) -> list[tuple[str, str, str]]:
    return [(link.anonymous_key, link.account_key, link.status) for link in load_links(db)]


def resolve_event_rows(db: Session, rows: list) -> tuple[list[IdentityRow], dict]:
    """DB 版合成：台账 -> 纯函数核心。"""
    return resolve_rows(rows, identity_graph(db))


def conflict_summary(db: Session) -> dict:
    """台账整体状态计数（结果页/台账页共用）。"""
    links = load_links(db)
    by_status: dict[str, int] = {
        IdentityLinkStatus.ACTIVE.value: 0,
        IdentityLinkStatus.SUSPENDED.value: 0,
        IdentityLinkStatus.CONFLICTED.value: 0,
    }
    deliverable_pairs = 0
    repeated_deliveries = 0
    for link in links:
        by_status[link.status] = by_status.get(link.status, 0) + 1
        deliverable_pairs += 1
        repeated_deliveries += max(link.delivery_count - 1, 0)
    return {
        "total": len(links),
        "by_status": by_status,
        "active": by_status[IdentityLinkStatus.ACTIVE.value],
        "suspended": by_status[IdentityLinkStatus.SUSPENDED.value],
        "conflicted": by_status[IdentityLinkStatus.CONFLICTED.value],
        "repeated_deliveries": repeated_deliveries,
        "deliverable_pairs": deliverable_pairs,
    }
