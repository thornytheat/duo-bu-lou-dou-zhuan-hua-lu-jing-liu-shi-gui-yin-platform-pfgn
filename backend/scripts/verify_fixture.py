"""离线自检：不连数据库，直接对 fixture 跑算法 1+2，核对验收数值。

身份关联口径同样在这里离线复算：先按台账规格（active / conflicted / suspended）
合成事件，再跑漏斗；切分前的 6000 / 3180 / 742 等验收值必须逐一对齐。

用法（宿主机或容器内）：
    cd backend && python -m scripts.verify_fixture
"""
from __future__ import annotations

import sys
from collections import defaultdict

from scripts import demo_config as cfg
from scripts.fixture import build_fixture
from src.constants.enums import IdentityLinkStatus
from src.services import funnel_engine, identity_service, path_engine, segment_service


class Row:
    __slots__ = ("user_key", "event_name", "event_ts", "seq", "channel", "device")

    def __init__(self, data: dict):
        self.user_key = data["user_key"]
        self.event_name = data["event_name"]
        self.event_ts = data["event_ts"]
        self.seq = data["seq"]
        self.channel = data["channel"]
        self.device = data["device"]


def _identity_links() -> list[tuple[str, str, str]]:
    """按 seed 的台账规格构造链接三元组：冲突组 conflicted、中断组 suspended。"""
    links: list[tuple[str, str, str]] = []
    for spec in cfg.identity_link_specs():
        if spec["note"].startswith("同一匿名标识指向多个账号"):
            status = IdentityLinkStatus.CONFLICTED.value
        elif spec["note"].startswith("关联中断"):
            status = IdentityLinkStatus.SUSPENDED.value
        else:
            status = IdentityLinkStatus.ACTIVE.value
        links.append((spec["anonymous_key"], spec["account_key"], status))
    return links


def main() -> int:
    users, rows = build_fixture()
    print(f"users={len(users)} rows={len(rows)}")
    if len(users) != cfg.EXPECTED["users"] or len(rows) != cfg.EXPECTED["events"]:
        print("FAIL fixture size")
        return 1

    funnel = cfg.FUNNELS[0]
    vocabulary = {step["event"] for step in funnel["steps"]} | set(funnel["noise_events"])
    raw = [Row(row) for row in rows if row["event_name"] in vocabulary]

    # ---- 不做身份合成：切分后 24 个账号缺注册前事件，进入人数会偏低 ----
    raw_grouped: dict[str, list] = defaultdict(list)
    for row in raw:
        raw_grouped[row.user_key].append(row)
    raw_result = funnel_engine.run_funnel(raw_grouped, funnel["steps"], 7, vocabulary, 6)
    print("without identity resolution entry:", raw_result["entry_users"])

    # ---- 按台账合成：跨身份轨迹还原，人数回到切分前 ----
    resolved, identity_stats = identity_service.resolve_rows(raw, _identity_links())
    grouped: dict[str, list] = defaultdict(list)
    for row in resolved:
        grouped[row.user_key].append(row)
    result = funnel_engine.run_funnel(grouped, funnel["steps"], 7, vocabulary, 6)
    steps = result["steps"]
    duration = result["duration"]
    print("identity stats:", identity_stats)
    print("steps:", [(step["label"], step["users"], step["rate_from_prev"]) for step in steps])
    print("p50 days:", duration["p50_days"], "samples:", duration["samples"])
    print("discard:", result["discard"])

    cohort = path_engine.analyze_cohort(result["outcomes"], steps, 1)
    print("cohort:", cohort["cohort_users"], "distinct paths:", cohort["distinct_paths"])
    for path in cohort["paths"]:
        print(f"  #{path['rank']} {path['share_pct']}%  {path['path']}")
    for choke in cohort["choke_points"]:
        print(f"  choke {choke['label']} {choke['count']} {choke['share_pct']}%")

    segments = segment_service.build_segments(
        result["segment_reached"], steps, 1,
        ["organic", "feed", "social", "referral"], ["ios", "android"],
    )
    print("overall drop rate:", segments["overall_drop_rate"], "max gap:", segments["max_gap_pp"])
    for row in segments["rows"]:
        print(f"  {row['channel_label']}·{row['device_label']} {row['users']} drop {row['drop_rate']}% gap {row['gap_pp']}pp")

    merged = path_engine.report_paths(result["outcomes"], steps, top_k=8)
    print("report paths:", len(merged))

    for spec in cfg.FUNNELS[1:]:
        vocab = {step["event"] for step in spec["steps"]} | set(spec["noise_events"])
        spec_raw = [Row(row) for row in rows if row["event_name"] in vocab]
        spec_resolved, _ = identity_service.resolve_rows(spec_raw, _identity_links())
        grouped_b: dict[str, list] = defaultdict(list)
        for row in spec_resolved:
            grouped_b[row.user_key].append(row)
        other = funnel_engine.run_funnel(grouped_b, spec["steps"], spec["window_days"], vocab, 6)
        print(
            spec["code"],
            [(step["label"], step["users"]) for step in other["steps"]],
            "expired:", other["discard"]["expired"],
        )

    checks = {
        "entry": steps[0]["users"] == cfg.EXPECTED["funnel_entry"],
        "trial": steps[1]["users"] == cfg.EXPECTED["trial_users"],
        "pay": steps[2]["users"] == cfg.EXPECTED["pay_users"],
        "p50": duration["p50_days"] == cfg.EXPECTED["step_p50_days"],
        "top_path": cohort["paths"][0]["share_pct"] == cfg.EXPECTED["top_path_share_pct"],
        "choke": cohort["choke_points"][0]["share_pct"] == cfg.EXPECTED["choke_share_pct"],
        "gap": segments["max_gap_pp"] == cfg.EXPECTED["max_gap_pp"],
        "paths": len(merged) == cfg.EXPECTED["report_paths"],
        "identity_merged": identity_stats["merged_components"] == cfg.EXPECTED["identity_merged"],
        "identity_absorbed": identity_stats["absorbed_anonymous_keys"] == cfg.EXPECTED["identity_absorbed_anonymous"],
    }
    # 不合成时：24 个账号缺注册事件 -> 计 never_entered，试听/付费被低估；
    # 合成后跨身份轨迹还原，never_entered 清零，试听/付费回到验收值。
    checks["raw_never_entered_24"] = raw_result["discard"]["never_entered"] == cfg.EXPECTED["identity_merged"]
    checks["raw_trial_understated"] = raw_result["steps"][1]["users"] < result["steps"][1]["users"]
    checks["raw_pay_understated"] = raw_result["steps"][2]["users"] <= result["steps"][2]["users"]
    checks["raw_never_entered_cleared"] = result["discard"]["never_entered"] == 0
    print(checks)
    if not all(checks.values()):
        print("FAIL", [key for key, value in checks.items() if not value])
        return 1
    print("OK fixture matches acceptance numbers (with identity resolution)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
