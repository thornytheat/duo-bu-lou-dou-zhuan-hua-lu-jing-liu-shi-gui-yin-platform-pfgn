"""确定性埋点 fixture 生成（纯标准库，可离线自检）。

不连数据库：seed_demo.py 与 scripts/verify_fixture.py 共用这一份生成逻辑。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from scripts import demo_config as cfg


def _d1_buckets() -> list[float]:
    short = [3 + (index * 13) % 20 for index in range(cfg.D1_SHORT_COUNT)]
    exact = [cfg.D1_EXACT_HOURS] * cfg.D1_EXACT_COUNT
    long_ = [46 + (index * 17) % 39 for index in range(cfg.D1_LONG_COUNT)]
    return short + exact + long_


def _d2_buckets() -> list[float]:
    short = [5 + (index * 11) % 34 for index in range(cfg.D2_SHORT_COUNT)]
    long_ = [48 + (index * 7) % 20 for index in range(cfg.D2_LONG_COUNT)]
    return short + long_


def _t0(index: int) -> datetime:
    return cfg.ANCHOR + timedelta(
        days=(index * 7) % 45,
        hours=8 + (index * 5) % 12,
        minutes=(index * 13) % 60,
    )


def build_user_rows() -> list[dict]:
    """按 SEGMENT_ROWS 展开 6,000 个用户（顺序即 user_key）。"""
    users: list[dict] = []
    for row in cfg.SEGMENT_ROWS:
        for archetype, count in row["conv"].items():
            for _ in range(count):
                users.append(
                    {
                        "channel": row["channel"],
                        "device": row["device"],
                        "archetype": archetype,
                    }
                )
        for archetype, count in row["drop"].items():
            for _ in range(count):
                users.append(
                    {
                        "channel": row["channel"],
                        "device": row["device"],
                        "archetype": archetype,
                    }
                )
    for index, user in enumerate(users):
        user["index"] = index
        user["user_key"] = f"U{index + 1:04d}"
        user["t0"] = _t0(index)
    return users


def _session_id(user_key: str, base: datetime, ts: datetime) -> str:
    return f"{user_key}-D{(ts.date() - base.date()).days}"


def _row(user: dict, event: str, ts: datetime, seq: int) -> dict:
    return {
        "user_key": user["user_key"],
        "event_name": event,
        "event_ts": ts,
        "seq": seq,
        "session_id": _session_id(user["user_key"], user["t0"], ts),
        "channel": user["channel"],
        "device": user["device"],
        "source": "fixture",
    }


def _incomplete_schedule() -> list[list[str]]:
    """把 1,641 个「试听未完成」用户按固定比例摊到 4 条路径变体上（确定性置换）。"""
    shapes: list[list[str]] = []
    for _key, count, shape in cfg.INCOMPLETE_VARIANTS:
        for _ in range(count):
            shapes.append(shape)
    return [shapes[(index * 5) % len(shapes)] for index in range(len(shapes))]


def build_funnel_a_events(users: list[dict]) -> list[dict]:
    d1 = _d1_buckets()
    d2 = _d2_buckets()
    incomplete_schedule = _incomplete_schedule()
    trial_counter = 0
    pay_counter = 0
    incomplete_counter = 0
    rows: list[dict] = []
    for user in users:
        archetype = user["archetype"]
        t0 = user["t0"]
        index = user["index"]
        reaches_trial = archetype in cfg.TRIAL_REACHING
        hours = d1[(trial_counter * 7) % len(d1)] if reaches_trial else 0.0
        if reaches_trial:
            trial_counter += 1
        pay_hours = 0.0
        if archetype == "pay":
            pay_hours = d2[(pay_counter * 3) % len(d2)]
            pay_counter += 1
        drop_off = float(2 + (index * 11) % 60)

        if archetype == "trial_incomplete":
            shape = incomplete_schedule[incomplete_counter % len(incomplete_schedule)]
            incomplete_counter += 1
        else:
            shape = cfg.ARCHETYPE_SHAPES[archetype]

        timeline: list[tuple[str, float]] = []
        for event in shape:
            previous = timeline[-1][1] if timeline else 0.0
            if event == "register":
                offset = 0.0
            elif event == "browse_course":
                offset = 0.33
            elif event == "trial_start":
                offset = hours * 0.85 if reaches_trial else drop_off * 0.4
            elif event == "trial_complete":
                offset = hours
            elif event == "pay":
                offset = hours + pay_hours
            elif event == "pricing_view":
                offset = hours + 2
            elif event == "consult_chat":
                offset = hours + 3 if reaches_trial else drop_off * 0.5
            elif event == "course_compare":
                offset = hours + (6 if archetype == "consult_compare" else 4)
            elif event == "trial_short_stay":
                offset = drop_off * 0.7
            elif event == "trial_incomplete":
                offset = drop_off
            elif event == "exit":
                offset = previous + 0.17
            else:  # pragma: no cover - 防御
                offset = previous + 1
            timeline.append((event, offset))

        for event, offset in timeline:
            rows.append(_row(user, event, t0 + timedelta(hours=offset), 0))
    return rows


def build_funnel_b_events(users: list[dict]) -> list[dict]:
    start, end = cfg.B_USER_SPAN
    rows: list[dict] = []
    for index in range(start, end):
        user = users[index]
        base = user["t0"] + timedelta(days=2, hours=5)
        offset = index - start
        if offset < cfg.B_ORDER_IN_WINDOW:
            order_ts = base + timedelta(days=2)
        elif offset < cfg.B_ORDER_IN_WINDOW + cfg.B_ORDER_EXPIRED:
            order_ts = base + timedelta(hours=108)
        else:
            order_ts = None
        rows.append(_row(user, "browse_item", base, 0))
        if order_ts is None:
            if offset >= cfg.B_ORDER_IN_WINDOW + cfg.B_ORDER_EXPIRED + cfg.B_CART_ABANDON:
                rows.append(_row(user, "exit", base + timedelta(minutes=15), 0))
                continue
            add_cart = base + timedelta(hours=6)
            rows.append(_row(user, "add_cart", add_cart, 0))
            rows.append(_row(user, "exit", add_cart + timedelta(minutes=10), 0))
            continue
        add_cart = base + timedelta(hours=6)
        rows.append(_row(user, "add_cart", add_cart, 0))
        rows.append(_row(user, "order", order_ts, 0))
        rows.append(_row(user, "exit", order_ts + timedelta(minutes=10), 0))
    return rows


def build_funnel_c_events(users: list[dict]) -> list[dict]:
    start, end = cfg.C_USER_SPAN
    rows: list[dict] = []
    for index in range(start, end):
        user = users[index]
        base = user["t0"] + timedelta(days=1, hours=9)
        offset = index - start
        rows.append(_row(user, "claim_coupon", base, 0))
        if offset < cfg.C_REDEEM:
            redeem = base + timedelta(hours=18)
            rows.append(_row(user, "redeem_coupon", redeem, 0))
            rows.append(_row(user, "exit", redeem + timedelta(minutes=10), 0))
        else:
            rows.append(_row(user, "exit", base + timedelta(minutes=20), 0))
    return rows


def _anonymous_row(anonymous_key: str, event: str, ts: datetime, seq: int,
                   channel: str, device: str, session_id: str) -> dict:
    return {
        "user_key": anonymous_key,
        "event_name": event,
        "event_ts": ts,
        "seq": seq,
        "session_id": session_id,
        "channel": channel,
        "device": device,
        "source": "fixture",
    }


def _merge_index_by_account() -> dict[str, int]:
    return {account: index for index, account in enumerate(cfg.merge_accounts())}


def split_anonymous_timelines(users: list[dict], rows: list[dict]) -> list[dict]:
    """把 24 个用户在「注册」处切成匿名段 + 账号段，模拟先浏览后登录。

    注册事件及其之前的事件归匿名标识（注册 = 匿名访客留下的最后一个动作），
    注册之后的事件归登录账号；切分前后事件总量与时间完全不变，只改 user_key，
    因此身份合成还原后漏斗验收值应逐一对齐。另补一条注册前的 login 埋点
    （白名单外），让匿名侧在漏斗之外也有可追溯的身份事件。
    """
    merge_index = _merge_index_by_account()
    target_accounts = set(merge_index)
    split_rows: list[dict] = []
    for row in rows:
        account = row["user_key"]
        if account not in target_accounts:
            split_rows.append(row)
            continue
        user = users[int(account[1:]) - 1]
        anonymous_key = f"anon:dev{int(account[1:]):05d}"
        register_ts = next(item["event_ts"] for item in rows
                           if item["user_key"] == account and item["event_name"] == "register")
        if row["event_ts"] <= register_ts:
            # 注册（含）之前：匿名访客轨迹
            split_rows.append(
                _anonymous_row(
                    anonymous_key,
                    row["event_name"],
                    row["event_ts"],
                    row["seq"],
                    row["channel"],
                    row["device"],
                    row["session_id"],
                )
            )
        else:
            # 注册之后：登录账号轨迹
            split_rows.append(row)
        if row["event_name"] == "register":
            # 登录埋点发生在注册前半小时，归属匿名标识，且在漏斗白名单之外
            split_rows.append(
                _anonymous_row(
                    anonymous_key,
                    "login",
                    register_ts + timedelta(hours=cfg.IDENTITY_LOGIN_HOURS),
                    row["seq"],
                    row["channel"],
                    row["device"],
                    f"{anonymous_key}-pre",
                )
            )
    return split_rows


def build_identity_outline_events(users: list[dict]) -> list[dict]:
    """冲突组 / 中断组匿名标识的白名单外事件（app_open），不影响漏斗计数。"""
    extra: list[dict] = []
    anonymous_keys = list(cfg.CONFLICT_ANONYMOUS_KEYS) + list(cfg.SUSPEND_ANONYMOUS_KEYS)
    # 借用前几个用户的时间基准与渠道设备，保证确定性
    for position, anonymous_key in enumerate(anonymous_keys):
        user = users[position]
        for offset in cfg.IDENTITY_APP_OPEN_HOURS:
            extra.append(
                _anonymous_row(
                    anonymous_key,
                    "app_open",
                    user["t0"] + timedelta(hours=offset),
                    0,
                    user["channel"],
                    user["device"],
                    f"{anonymous_key}-pre",
                )
            )
    return extra


def build_fixture() -> tuple[list[dict], list[dict]]:
    """返回 (users, rows)。rows 已含 3% 重复上报与 5% 乱序 seq。"""
    users = build_user_rows()
    unique_rows = (
        build_funnel_a_events(users)
        + build_funnel_b_events(users)
        + build_funnel_c_events(users)
    )
    # 身份场景：切分匿名/账号轨迹 + 冲突/中断组的白名单外事件
    unique_rows = split_anonymous_timelines(users, unique_rows)
    unique_rows = unique_rows + build_identity_outline_events(users)

    # 重复上报：每 32 个唯一事件复制 1 条（同用户同事件同秒，靠算法折叠）
    rows: list[dict] = []
    duplicates = 0
    for index, row in enumerate(unique_rows):
        rows.append(row)
        if index % cfg.DUPLICATE_STRIDE == 0 and duplicates < cfg.DUPLICATE_EVENTS:
            rows.append(dict(row))
            duplicates += 1

    # 入库顺序：正常 seq = row_index*10；每 20 个事件有 1 个「提前 4 位入库」
    for row_index, row in enumerate(rows):
        if row_index % cfg.OUT_OF_ORDER_STRIDE == 0 and row_index >= 4:
            row["seq"] = (row_index - 4) * 10 + 3
        else:
            row["seq"] = row_index * 10
        row["seed_key"] = f"seed:e{row_index:06d}"
    return users, rows
