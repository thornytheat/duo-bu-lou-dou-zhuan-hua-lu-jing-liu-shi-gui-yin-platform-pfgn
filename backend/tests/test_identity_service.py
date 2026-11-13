"""身份合成纯函数核心单测：跨身份路径、首触渠道设备、幂等、冲突、中断/恢复。

不连数据库：build_components / resolve_rows 只吃链接三元组与轻量事件行。
"""
from __future__ import annotations

import pytest

from src.constants.enums import IdentityLinkStatus
from src.services import identity_service
from tests._helpers import ANCHOR, VOCAB_A, Event, ev

ACTIVE = IdentityLinkStatus.ACTIVE.value
SUSPENDED = IdentityLinkStatus.SUSPENDED.value
CONFLICTED = IdentityLinkStatus.CONFLICTED.value


def _link(anon: str, account: str, status: str = ACTIVE) -> tuple[str, str, str]:
    return (anon, account, status)


# --------------------------------------------------------------- 跨身份合成


def test_anonymous_and_account_events_merge_into_one_path():
    """匿名浏览 + 登录后转化：合成一条按事件时间排序的完整轨迹。"""
    rows = [
        ev("anon:dev1", "register", 0, seq=1, channel="feed", device="ios"),
        ev("anon:dev1", "browse_course", 1, seq=2, channel="feed", device="ios"),
        ev("U0001", "trial_complete", 24, seq=3, channel="feed", device="ios"),
        ev("U0001", "pay", 48, seq=4, channel="feed", device="ios"),
        ev("U0001", "exit", 49, seq=5, channel="feed", device="ios"),
    ]
    links = [_link("anon:dev1", "U0001")]
    resolved, stats = identity_service.resolve_rows(rows, links)

    assert stats["merged_components"] == 1
    assert stats["absorbed_anonymous_keys"] == 1
    # 全部事件归属到账号键
    assert {row.user_key for row in resolved} == {"U0001"}
    # 事件顺序按 (ts, seq)，匿名段在前、账号段在后
    assert [row.event_name for row in resolved] == [
        "register", "browse_course", "trial_complete", "pay", "exit"
    ]
    # 溯源保留两个原始标识
    sample = next(row for row in resolved if row.source_key == "anon:dev1")
    assert sample.user_key == "U0001"


def test_channel_and_device_follow_earliest_valid_touch():
    """渠道/设备沿用组内最早事件（最早有效触达），而不是账号侧的渠道。"""
    rows = [
        ev("anon:dev9", "register", 0, seq=1, channel="feed", device="android"),
        ev("anon:dev9", "browse_course", 2, seq=2, channel="feed", device="android"),
        ev("U0009", "trial_complete", 20, seq=3, channel="organic", device="ios"),
    ]
    resolved, _ = identity_service.resolve_rows(rows, [_link("anon:dev9", "U0009")])
    assert all(row.channel == "feed" for row in resolved)
    assert all(row.device == "android" for row in resolved)


def test_unlinked_anonymous_and_account_remain_separate():
    rows = [
        ev("anon:dev2", "register", 0),
        ev("U0002", "register", 0),
    ]
    resolved, stats = identity_service.resolve_rows(rows, [])
    assert stats["merged_components"] == 0
    assert {row.user_key for row in resolved} == {"anon:dev2", "U0002"}


def test_merged_funnel_counts_one_person_not_two():
    """端到端口径：合成后跑漏斗，跨身份的人只计一次。"""
    from src.services import funnel_engine

    rows = [
        ev("anon:dev3", "register", 0, seq=1),
        ev("anon:dev3", "browse_course", 1, seq=2),
        ev("U0003", "trial_complete", 10, seq=3),
        ev("U0003", "pay", 20, seq=4),
    ]
    resolved, _ = identity_service.resolve_rows(rows, [_link("anon:dev3", "U0003")])
    grouped: dict[str, list] = {}
    for row in resolved:
        grouped.setdefault(row.user_key, []).append(row)
    result = funnel_engine.run_funnel(grouped, [
        {"key": "register", "label": "注册", "event": "register"},
        {"key": "trial", "label": "试听完成", "event": "trial_complete"},
        {"key": "pay", "label": "付费", "event": "pay"},
    ], 7, VOCAB_A)
    assert result["entry_users"] == 1
    assert result["converted_users"] == 1


# --------------------------------------------------------------- 幂等投递（合成视角）


def test_repeated_link_delivery_does_not_add_person():
    """同一关联对即使在台账里重复出现，合成结果也不会多出一个人。"""
    rows = [
        ev("anon:dev4", "register", 0),
        ev("U0004", "trial_complete", 5),
    ]
    links_once = [_link("anon:dev4", "U0004")]
    links_twice = links_once + [_link("anon:dev4", "U0004")]
    resolved_once, stats_once = identity_service.resolve_rows(rows, links_once)
    resolved_twice, stats_twice = identity_service.resolve_rows(rows, links_twice)
    assert stats_once["merged_components"] == stats_twice["merged_components"] == 1
    assert len({row.user_key for row in resolved_once}) == 1
    assert len({row.user_key for row in resolved_twice}) == 1
    assert resolved_twice[0].user_key == "U0004"


# --------------------------------------------------------------- 冲突：不强行合并


def test_one_anonymous_to_two_accounts_is_not_merged():
    """一个匿名标识指向两个账号：两条链接都 conflicted，两边各自保留原结果。"""
    rows = [
        ev("anon:shared", "register", 0, channel="feed"),
        ev("U0101", "trial_complete", 5, channel="organic"),
        ev("U0102", "trial_complete", 6, channel="social"),
    ]
    links = [
        _link("anon:shared", "U0101", CONFLICTED),
        _link("anon:shared", "U0102", CONFLICTED),
    ]
    resolved, stats = identity_service.resolve_rows(rows, links)
    assert stats["merged_components"] == 0
    keys = {row.user_key for row in resolved}
    # 三个身份各自独立，没有被并成一个
    assert keys == {"anon:shared", "U0101", "U0102"}
    # 渠道设备不被跨身份覆盖
    u101 = [row for row in resolved if row.user_key == "U0101"]
    assert all(row.channel == "organic" for row in u101)


def test_conflicted_link_ignored_even_if_sibling_active():
    """冲突链接不参与并查集，active 的另一条正常链接不受牵连。"""
    mapping = identity_service.build_components([
        _link("anon:x", "U0201", CONFLICTED),
        _link("anon:y", "U0202", ACTIVE),
    ])
    assert mapping["anon:x"] == "anon:x"
    assert mapping["U0201"] == "U0201"
    assert mapping["anon:y"] == "U0202"
    assert mapping["U0202"] == "U0202"


# --------------------------------------------------------------- 中断 / 恢复


def test_suspended_link_is_excluded_from_merge():
    rows = [
        ev("anon:dev5", "register", 0),
        ev("U0005", "trial_complete", 8),
    ]
    resolved, stats = identity_service.resolve_rows(rows, [_link("anon:dev5", "U0005", SUSPENDED)])
    assert stats["merged_components"] == 0
    assert {row.user_key for row in resolved} == {"anon:dev5", "U0005"}


def test_resume_restores_merge():
    """同一条链接从 suspended 切回 active，下一次合成立即恢复合并。"""
    rows = [
        ev("anon:dev6", "register", 0),
        ev("U0006", "pay", 12),
    ]
    suspended = identity_service.resolve_rows(rows, [_link("anon:dev6", "U0006", SUSPENDED)])
    resumed = identity_service.resolve_rows(rows, [_link("anon:dev6", "U0006", ACTIVE)])
    assert suspended[1]["merged_components"] == 0
    assert resumed[1]["merged_components"] == 1
    assert {row.user_key for row in resumed[0]} == {"U0006"}


# --------------------------------------------------------------- 并查集边界


def test_canonical_prefers_account_key():
    mapping = identity_service.build_components([_link("anon:a", "U0301")])
    assert mapping["anon:a"] == "U0301"
    assert mapping["U0301"] == "U0301"


def test_pure_anonymous_component_uses_smallest_key():
    # 没有账号键的组件（理论上不会由链接产生，但函数需稳定降级）
    mapping = identity_service.build_components([])
    assert mapping == {}


def test_multiple_anon_to_one_account_merge_together():
    """同一账号在两台设备各有一个匿名标识：三者合成一个人。"""
    rows = [
        ev("anon:phone", "register", 0),
        ev("anon:tablet", "browse_course", 2),
        ev("U0401", "trial_complete", 10),
    ]
    links = [_link("anon:phone", "U0401"), _link("anon:tablet", "U0401")]
    resolved, stats = identity_service.resolve_rows(rows, links)
    assert stats["merged_components"] == 1
    assert {row.user_key for row in resolved} == {"U0401"}
    assert sorted(stats and {row.source_key for row in resolved}) == ["U0401", "anon:phone", "anon:tablet"]


def test_earliest_touch_breaks_tie_by_timestamp_then_seq():
    """同秒事件退化为 seq 决定最早触达。"""
    rows = [
        Event(user_key="anon:dev7", event_name="register", event_ts=ANCHOR, seq=9,
              channel="social", device="android"),
        Event(user_key="U0007", event_name="browse_course", event_ts=ANCHOR, seq=2,
              channel="feed", device="ios"),
    ]
    resolved, _ = identity_service.resolve_rows(rows, [_link("anon:dev7", "U0007")])
    # seq=2 的账号事件更早
    assert all(row.channel == "feed" and row.device == "ios" for row in resolved)
