"""演示数据契约：行知教育增长组埋点 fixture 的全部常量。

设计原则：**全部确定性**，不用随机数。用户按「渠道 × 设备 × 路径原型」逐行分配，
因此两次 seed 的每个计数完全一致；主流程验收里的 6000 / 3180 / 742 / 1.8 天 /
31.4% / 58.2% / +12.6pp 都是由这里的计数**构造**出来的，不是写死的结果。
"""
from __future__ import annotations

from datetime import datetime

DEMO_PASSWORD = "Demo@2026"

DEMO_USERS = (
    ("admin@funnelpath.io", "林知远", "admin"),
    ("analyst@funnelpath.io", "苏澜", "analyst"),
    ("viewer@funnelpath.io", "周雨桐", "viewer"),
)

# 数据窗口起点（UTC+8 的 2026-09-01 08:00），所有埋点时间由此推导
ANCHOR = datetime(2026, 9, 1, 8, 0, 0)

TARGET_USERS = 6000
TARGET_EVENTS = 36000
DUPLICATE_EVENTS = 1080          # 3.0% 重复上报
DUPLICATE_STRIDE = 32            # 每 32 个唯一事件复制 1 条
OUT_OF_ORDER_STRIDE = 20         # 每 20 个事件里有 1 个被「提前入库」（5%）

FUNNELS = (
    {
        "code": "signup-trial-pay",
        "name": "注册 → 试听完成 → 付费",
        "description": "K12 素养课主转化链路。跨会话、跨天，是现在手写 SQL 最容易拼错的一段。",
        "line_no": "L1",
        "line_color": "line-a",
        "steps": [
            {"key": "register", "label": "注册", "event": "register"},
            {"key": "trial", "label": "试听完成", "event": "trial_complete"},
            {"key": "pay", "label": "付费", "event": "pay"},
        ],
        "noise_events": [
            "browse_course",
            "trial_start",
            "trial_short_stay",
            "trial_incomplete",
            "pricing_view",
            "consult_chat",
            "course_compare",
            "exit",
        ],
        "window_days": 7,
        "cross_session": True,
        "status": "published",
    },
    {
        "code": "browse-cart-order",
        "name": "浏览商品 → 加购 → 下单",
        "description": "教具商城的辅助链路：验证窗口 3 天时有多少人超窗丢单。",
        "line_no": "L2",
        "line_color": "line-b",
        "steps": [
            {"key": "browse_item", "label": "浏览商品", "event": "browse_item"},
            {"key": "add_cart", "label": "加购", "event": "add_cart"},
            {"key": "order", "label": "下单", "event": "order"},
        ],
        "noise_events": ["exit"],
        "window_days": 3,
        "cross_session": True,
        "status": "published",
    },
    {
        "code": "coupon-redeem",
        "name": "领券 → 核销",
        "description": "运营发券后的核销链路，单会话内完成即可，跨会话开关关闭。",
        "line_no": "L3",
        "line_color": "line-c",
        "steps": [
            {"key": "claim_coupon", "label": "领券", "event": "claim_coupon"},
            {"key": "redeem_coupon", "label": "核销", "event": "redeem_coupon"},
        ],
        "noise_events": ["exit"],
        "window_days": 5,
        "cross_session": False,
        "status": "published",
    },
)

# 路径原型（事件序列）。每个计数都在 SEGMENT_ROWS 里逐行给死。
ARCHETYPE_SHAPES = {
    "pay": ["register", "browse_course", "trial_start", "trial_complete", "pay", "exit"],
    "price": ["register", "browse_course", "trial_complete", "pricing_view", "exit"],
    "consult": ["register", "browse_course", "trial_complete", "consult_chat", "exit"],
    "consult_compare": [
        "register",
        "browse_course",
        "trial_complete",
        "consult_chat",
        "course_compare",
        "exit",
    ],
    "compare": ["register", "browse_course", "trial_complete", "course_compare", "exit"],
    "trial_short": ["register", "browse_course", "trial_short_stay", "exit"],
    "trial_incomplete": ["register", "browse_course", "trial_incomplete", "exit"],
    "browse": ["register", "browse_course", "exit"],
    "register_only": ["register", "exit"],
}

# 「试听未完成」不是一个动作，而是 4 条可区分的前置路径；卡点按末事件聚合，
# 路径榜按具体序列排名 —— 所以 58.2% 的卡点由多条路径共同贡献，单条最大的是
# 「试听页停留<30s」那条（31.4%，验收里的 Top1）。
INCOMPLETE_VARIANTS = (
    ("inc_plain", 700, ["register", "browse_course", "trial_incomplete", "exit"]),
    ("inc_trial_start", 480, ["register", "browse_course", "trial_start", "trial_incomplete", "exit"]),
    ("inc_consult", 300, ["register", "browse_course", "consult_chat", "trial_incomplete", "exit"]),
    (
        "inc_short_stay",
        161,
        ["register", "browse_course", "trial_start", "trial_short_stay", "trial_incomplete", "exit"],
    ),
)

# 会走到「试听完成」的用户原型（这 3180 人贡献 register→trial 的步间耗时样本）
TRIAL_REACHING = ("pay", "price", "consult", "consult_compare", "compare")
# 只到注册就流失的原型（这 2820 人贡献「试听完成」步的流失队列）
TRIAL_DROPPING = ("trial_short", "trial_incomplete", "browse", "register_only")

# 渠道 × 设备 × 路径原型的人数矩阵（行内 conv/drop 之和 = users）
SEGMENT_ROWS = (
    {
        "channel": "organic",
        "device": "ios",
        "conv": {"pay": 140, "price": 165, "consult": 95, "consult_compare": 50, "compare": 130},
        "drop": {"trial_short": 90, "trial_incomplete": 193, "browse": 27, "register_only": 10},
    },
    {
        "channel": "organic",
        "device": "android",
        "conv": {"pay": 150, "price": 175, "consult": 100, "consult_compare": 55, "compare": 140},
        "drop": {"trial_short": 109, "trial_incomplete": 228, "browse": 31, "register_only": 12},
    },
    {
        "channel": "feed",
        "device": "ios",
        "conv": {"pay": 95, "price": 120, "consult": 70, "consult_compare": 37, "compare": 102},
        "drop": {"trial_short": 207, "trial_incomplete": 355, "browse": 41, "register_only": 23},
    },
    {
        "channel": "feed",
        "device": "android",
        "conv": {"pay": 86, "price": 109, "consult": 63, "consult_compare": 34, "compare": 92},
        "drop": {"trial_short": 190, "trial_incomplete": 320, "browse": 35, "register_only": 21},
    },
    {
        "channel": "social",
        "device": "ios",
        "conv": {"pay": 118, "price": 139, "consult": 80, "consult_compare": 43, "compare": 110},
        "drop": {"trial_short": 100, "trial_incomplete": 180, "browse": 20, "register_only": 10},
    },
    {
        "channel": "social",
        "device": "android",
        "conv": {"pay": 84, "price": 99, "consult": 57, "consult_compare": 31, "compare": 79},
        "drop": {"trial_short": 80, "trial_incomplete": 145, "browse": 15, "register_only": 10},
    },
    {
        "channel": "referral",
        "device": "ios",
        "conv": {"pay": 48, "price": 57, "consult": 33, "consult_compare": 18, "compare": 44},
        "drop": {"trial_short": 60, "trial_incomplete": 120, "browse": 12, "register_only": 8},
    },
    {
        "channel": "referral",
        "device": "android",
        "conv": {"pay": 21, "price": 36, "consult": 22, "consult_compare": 12, "compare": 41},
        "drop": {"trial_short": 50, "trial_incomplete": 100, "browse": 12, "register_only": 6},
    },
)

# 步间耗时桶：3180 个「注册→试听完成」+ 742 个「试听完成→付费」
D1_SHORT_COUNT = 1450
D1_EXACT_COUNT = 300
D1_LONG_COUNT = 1430
D1_EXACT_HOURS = 43.2          # 1.8 天，正是验收里的步间 P50
D2_SHORT_COUNT = 510
D2_LONG_COUNT = 232

# 漏斗 B（浏览→加购→下单）：前 1820 个用户参与
B_ORDER_IN_WINDOW = 280
B_ORDER_EXPIRED = 20           # 第 4.5 天下单，窗口 3 天 → expired
B_CART_ABANDON = 500
B_BROWSE_ONLY = 400
B_USER_SPAN = (0, B_ORDER_IN_WINDOW + B_ORDER_EXPIRED + B_CART_ABANDON + B_BROWSE_ONLY)

# 漏斗 C（领券→核销）：索引 4200 起的 953 个用户参与
C_REDEEM = 603
C_CLAIM_ONLY = 350
C_USER_START = 4200
C_USER_SPAN = (C_USER_START, C_USER_START + C_REDEEM + C_CLAIM_ONLY)

# 自检期望值（verify_fixture.py 与 seed 都会校验）
# unique_events 含身份场景新增的 24 条 login + 12 条 app_open（均在漏斗白名单外）；
# 总事件再叠加 1080 条重复上报（其中 1 条复制到了 login，故 login 实际 25 条）
EXPECTED = {
    "users": 6000,
    "unique_events": 34956,
    "duplicate_events": DUPLICATE_EVENTS,
    "events": 36036,
    "funnel_entry": 6000,
    "trial_users": 3180,
    "pay_users": 742,
    "trial_drop_cohort": 2820,
    "step_p50_days": 1.8,
    "top_path_share_pct": 31.4,
    "choke_share_pct": 58.2,
    "max_gap_pp": 12.6,
    "report_paths": 8,
    # 身份关联：24 个跨身份轨迹合成，吸收 24 个匿名标识，30 条台账（含重复投递 1 次）
    "identity_merged": 24,
    "identity_absorbed_anonymous": 24,
    "identity_links": 30,
    "identity_conflict_links": 4,
    "identity_suspended_links": 2,
    "identity_active_links": 24,
}

# ---------------------------------------------------------------- 身份关联场景
# 匿名访客「先看课、后登录」：同一自然人在登录前用 anon: 标识、登录后用账号标识。
# 1) MERGE_* 个用户的轨迹在 register 处切两段，登录前事件挂匿名标识，登录后挂账号；
#    active 关联生效后，身份合成应把人数还原成切分前的 6,000 / 3,180 / 742；
# 2) CONFLICT_* 组：同一个匿名标识先后指向两个账号，台账必须标 conflicted 且不合并；
# 3) SUSPEND_* 组：关联被中断，不参与合成，恢复后重新合成。
# 冲突/中断组只产生漏斗白名单外的 app_open，不改变漏斗验收值。
IDENTITY_MERGE_USERS = 24
IDENTITY_MERGE_BASE = 1300           # 选号起点：落在只有漏斗 A 事件的 [1200, 4200) 区间
IDENTITY_MERGE_STRIDE = 113          # 线性选号，24 人互不重叠，覆盖多种路径原型
IDENTITY_CONFLICT_GROUPS = 2
IDENTITY_SUSPEND_GROUPS = 2
IDENTITY_APP_OPEN_HOURS = (-26.0, -20.0, -8.0)
IDENTITY_LOGIN_HOURS = -0.5

# 身份关联台账（seed 时按此投递；含一组重复投递验证幂等、两组冲突、两组中断）
def identity_link_specs() -> list[dict]:
    """返回台账投递序列：dict(anonymous_key, account_key, action, times, note)。"""
    specs: list[dict] = []
    for index in range(IDENTITY_MERGE_USERS):
        user_index = IDENTITY_MERGE_BASE + index * IDENTITY_MERGE_STRIDE
        account = f"U{user_index + 1:04d}"
        specs.append(
            {
                "anonymous_key": f"anon:dev{user_index + 1:05d}",
                "account_key": account,
                "action": "deliver",
                "times": 2 if index == 0 else 1,  # 首组重复投递一次，验证幂等
                "note": "登录后绑定设备匿名标识",
            }
        )
    # 冲突：anon:conflict-a 同时指向 U5001 / U5002；anon:conflict-b 指向 U5003 / U5004
    conflict_pairs = [
        ("anon:conflict-a", "U5001"),
        ("anon:conflict-a", "U5002"),
        ("anon:conflict-b", "U5003"),
        ("anon:conflict-b", "U5004"),
    ]
    for anonymous_key, account_key in conflict_pairs:
        specs.append(
            {
                "anonymous_key": anonymous_key,
                "account_key": account_key,
                "action": "deliver",
                "times": 1,
                "note": "同一匿名标识指向多个账号（冲突）",
            }
        )
    # 中断：投递后由 seed 置 suspended
    suspend_pairs = [
        ("anon:paused-a", "U5005"),
        ("anon:paused-b", "U5006"),
    ]
    for anonymous_key, account_key in suspend_pairs:
        specs.append(
            {
                "anonymous_key": anonymous_key,
                "account_key": account_key,
                "action": "deliver",
                "times": 1,
                "note": "关联中断（历史结果保留）",
            }
        )
    return specs


# seed 后需要立即置为中断的关联对
SUSPEND_PAIRS = [("anon:paused-a", "U5005"), ("anon:paused-b", "U5006")]
# 冲突组的匿名标识（fixture 给它们造白名单外的启动事件）
CONFLICT_ANONYMOUS_KEYS = ["anon:conflict-a", "anon:conflict-b"]
SUSPEND_ANONYMOUS_KEYS = ["anon:paused-a", "anon:paused-b"]


def merge_accounts() -> list[str]:
    """24 个被切分轨迹的账号键（与 fixture 切分、台账投递严格对齐）。"""
    return [
        f"U{IDENTITY_MERGE_BASE + index * IDENTITY_MERGE_STRIDE + 1:04d}"
        for index in range(IDENTITY_MERGE_USERS)
    ]

# 种子报告：模板化结论（数值由算法回填，不在文案里写死）
SEED_REPORT_CONCLUSION = (
    "「试听完成」是本链路最大的单点流失：从注册到试听完成的 7 天窗口里，"
    "多数人卡在试听页本身（停留过短或中途退出），而不是价格。"
    "信息流渠道 × iOS 的流失率显著高于整体，建议先做试听页的前 30 秒引导与"
    "信息流素材的承诺对齐，再评估价格页改版。"
)
