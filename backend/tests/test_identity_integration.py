"""身份合成 × 漏斗/路径引擎集成测试：合成后的轨迹驱动状态机与路径归因。"""
from __future__ import annotations

from collections import defaultdict

from src.services import funnel_engine, identity_service, path_engine
from tests._helpers import STEPS3, VOCAB_A, ev


def _run_with_identity(rows, links):
    resolved, stats = identity_service.resolve_rows(rows, links)
    grouped: dict[str, list] = defaultdict(list)
    for row in resolved:
        grouped[row.user_key].append(row)
    result = funnel_engine.run_funnel(grouped, STEPS3, 7, VOCAB_A)
    return result, stats


def test_merged_timeline_feeds_funnel_and_path_tree():
    rows = [
        # 自然人 A：匿名段（注册+浏览），账号段（试听完成+价格页+退出）
        ev("anon:a1", "register", 0, seq=1, channel="feed", device="ios"),
        ev("anon:a1", "browse_course", 1, seq=2, channel="feed", device="ios"),
        ev("U0001", "trial_complete", 24, seq=3, channel="feed", device="ios"),
        ev("U0001", "pricing_view", 26, seq=4, channel="feed", device="ios"),
        ev("U0001", "exit", 27, seq=5, channel="feed", device="ios"),
        # 自然人 B：纯账号，停在注册
        ev("U0002", "register", 0, seq=1),
        ev("U0002", "exit", 1, seq=2),
    ]
    links = [("anon:a1", "U0001", "active")]
    result, stats = _run_with_identity(rows, links)

    assert stats["merged_components"] == 1
    assert result["entry_users"] == 2
    # A 的完整路径被识别为试听完成后流失（drop_step=2），而不是断在匿名段
    outcome_a = result["outcomes"]["U0001"]
    assert [item["event"] for item in outcome_a.path_events] == [
        "register", "browse_course", "trial_complete", "pricing_view", "exit"
    ]
    assert outcome_a.source_keys == ["U0001", "anon:a1"]
    assert outcome_a.channel == "feed" and outcome_a.device == "ios"

    cohort = path_engine.analyze_cohort(result["outcomes"], result["steps"], 2)
    assert cohort["cohort_users"] == 1
    assert cohort["paths"][0]["path"] == "注册 → 浏览课程 → 试听完成 → 价格页 → 退出"


def test_conflicted_identities_keep_two_independent_outcomes():
    rows = [
        ev("anon:shared", "register", 0, seq=1, channel="feed"),
        ev("U0101", "register", 3, seq=2, channel="organic"),
        ev("U0101", "browse_course", 4, seq=3, channel="organic"),
        ev("U0102", "register", 5, seq=4, channel="social"),
    ]
    links = [
        ("anon:shared", "U0101", "conflicted"),
        ("anon:shared", "U0102", "conflicted"),
    ]
    result, stats = _run_with_identity(rows, links)
    assert stats["merged_components"] == 0
    # 三个身份各自计一次进入，没有被强行并成一个
    assert result["entry_users"] == 3
    assert set(result["outcomes"]) == {"anon:shared", "U0101", "U0102"}


def test_suspend_and_resume_change_merge_for_next_run():
    rows = [
        ev("anon:z1", "register", 0),
        ev("U0009", "trial_complete", 20),
    ]
    suspended_result, suspended_stats = _run_with_identity(
        rows, [("anon:z1", "U0009", "suspended")]
    )
    resumed_result, resumed_stats = _run_with_identity(
        rows, [("anon:z1", "U0009", "active")]
    )
    # 中断：两条身份独立；账号侧缺注册事件计 never_entered，试听被低估
    assert suspended_stats["merged_components"] == 0
    assert suspended_result["discard"]["never_entered"] == 1
    assert suspended_result["steps"][1]["users"] == 0
    # 恢复：合成一个人，试听正确计入；历史 run 的物化结果不受影响（两次独立计算）
    assert resumed_stats["merged_components"] == 1
    assert resumed_result["entry_users"] == 1
    assert resumed_result["steps"][1]["users"] == 1
    assert resumed_result["discard"]["never_entered"] == 0


def test_first_touch_channel_drives_segment_attribution():
    rows = [
        ev("anon:m1", "register", 0, channel="referral", device="android"),
        ev("U0501", "trial_complete", 10, channel="organic", device="ios"),
    ]
    result, _ = _run_with_identity(rows, [("anon:m1", "U0501", "active")])
    outcome = result["outcomes"]["U0501"]
    # 分群归属按最早有效触达：referral × android，而不是账号侧的 organic × ios
    assert outcome.channel == "referral"
    assert outcome.device == "android"
    (channel, device), reached = next(iter(result["segment_reached"].items()))
    assert (channel, device) == ("referral", "android")
