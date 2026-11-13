"""FunnelRun 服务：真跑算法 1+2，落步骤表 / 分群矩阵 / 会话路径。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.constants.enums import CHANNEL_LABELS, DEVICE_LABELS, Channel, Device, RunStatus, RunTrigger
from src.models.event import EventLog
from src.models.funnel import FunnelDef
from src.models.funnel_run import FunnelRun
from src.models.session_path import SessionPath
from src.services import funnel_engine, identity_service, path_engine, segment_service
from src.utils.errors import not_found
from src.utils.pathutil import path_label
from src.utils.timeutil import BIZ_TZ, iso

CHANNEL_ORDER = [channel.value for channel in Channel]
DEVICE_ORDER = [device.value for device in Device]
ENGINE_VERSION = "1.1.0"


def _load_identity_grouped(db: Session, vocabulary: set[str]) -> tuple[dict[str, list], dict]:
    """取白名单事件 -> 身份合成 -> 按合成后归属键分组。"""
    rows = db.scalars(
        select(EventLog)
        .where(EventLog.event_name.in_(list(vocabulary)))
        .order_by(EventLog.user_key, EventLog.event_ts, EventLog.seq)
    ).all()
    resolved, identity_stats = identity_service.resolve_event_rows(db, list(rows))
    grouped: dict[str, list] = defaultdict(list)
    for row in resolved:
        grouped[row.user_key].append(row)
    return grouped, identity_stats


def execute(
    db: Session,
    funnel: FunnelDef,
    trigger: str = RunTrigger.MANUAL.value,
    code: str | None = None,
    window_days: int | None = None,
) -> FunnelRun:
    window = window_days or funnel.window_days
    vocabulary = funnel.vocabulary
    grouped, identity_stats = _load_identity_grouped(db, vocabulary)

    result = funnel_engine.run_funnel(grouped, funnel.steps, window, vocabulary)
    segments = segment_service.build_segments(
        result["segment_reached"],
        result["steps"],
        max_loss_step(result["steps"]),
        CHANNEL_ORDER,
        DEVICE_ORDER,
    )
    # 身份合成统计并入丢弃口径块：结果页 / 报告 / 导出共用同一份快照
    result["discard"].update(
        {
            "identity_merged": identity_stats["merged_components"],
            "identity_absorbed_anonymous": identity_stats["absorbed_anonymous_keys"],
            "identity_active_links": identity_stats["active_links"],
        }
    )

    run_code = code or f"RUN-{funnel.code.upper()}-{datetime.now(BIZ_TZ).strftime('%Y%m%d%H%M%S%f')}"
    now = datetime.now(BIZ_TZ).replace(tzinfo=None)
    run = db.scalar(select(FunnelRun).where(FunnelRun.code == run_code))
    if run is None:
        run = FunnelRun(
            code=run_code,
            funnel_id=funnel.id,
            step_counts=[],
            duration_stats={},
            discard={},
            segment_rows=[],
            started_at=now,
        )
        db.add(run)

    run.funnel_id = funnel.id
    run.trigger = trigger
    run.status = RunStatus.SUCCEEDED.value
    run.window_days = window
    run.step_counts = result["steps"]
    run.duration_stats = {
        "duration": result["duration"],
        "transitions": [
            {
                **item,
                "p50_seconds": item["p50_seconds"],
                "p90_seconds": item["p90_seconds"],
            }
            for item in result["transitions"]
        ],
    }
    run.discard = result["discard"]
    run.segment_rows = segments["rows"] + segments["channel_rows"]
    run.entry_users = result["entry_users"]
    run.converted_users = result["converted_users"]
    run.engine_version = ENGINE_VERSION
    run.started_at = now
    run.finished_at = datetime.now(BIZ_TZ).replace(tzinfo=None)
    db.flush()

    # 会话路径物化：同一 run 重跑先清空（seed 幂等）
    db.execute(delete(SessionPath).where(SessionPath.run_id == run.id))
    mappings = []
    for outcome in result["outcomes"].values():
        label = outcome.path_label or path_label(outcome.path_events)
        source_keys = list(outcome.source_keys or [outcome.user_key])
        mappings.append(
            {
                "funnel_id": funnel.id,
                "run_id": run.id,
                "user_key": outcome.user_key,
                "channel": outcome.channel,
                "device": outcome.device,
                "path_key": label[:480],
                "path_json": outcome.path_events,
                "depth": len(outcome.path_events),
                "drop_step_index": outcome.drop_step,
                "terminal_event": outcome.terminal_event or "",
                "is_converted": outcome.converted,
                "source_keys": source_keys,
                "identity_merged": len(source_keys) > 1,
            }
        )
    if mappings:
        db.bulk_insert_mappings(SessionPath, mappings)
    db.flush()
    return run


def max_loss_step(steps: list[dict]) -> int:
    best_index = 1
    best_lost = -1
    for index in range(1, len(steps)):
        lost = steps[index - 1]["users"] - steps[index]["users"]
        if lost > best_lost:
            best_lost = lost
            best_index = index
    return best_index


def serialize(run: FunnelRun, funnel: FunnelDef | None = None) -> dict:
    duration = (run.duration_stats or {}).get("duration", {})
    transitions = (run.duration_stats or {}).get("transitions", [])
    step_rows = run.step_counts or []
    lost_rows = path_engine.drop_steps(step_rows)
    discard = run.discard or {}
    return {
        "id": run.id,
        "code": run.code,
        "funnel_id": run.funnel_id,
        "funnel_code": funnel.code if funnel else None,
        "funnel_name": funnel.name if funnel else None,
        "line_no": funnel.line_no if funnel else None,
        "line_color": funnel.line_color if funnel else None,
        "trigger": run.trigger,
        "status": run.status,
        "window_days": run.window_days,
        "entry_users": run.entry_users,
        "converted_users": run.converted_users,
        "conversion_rate": round(run.converted_users * 100.0 / run.entry_users, 1) if run.entry_users else 0.0,
        "steps": step_rows,
        "transitions": transitions,
        "duration": duration,
        "discard": discard,
        "identity": {
            "merged": discard.get("identity_merged", 0),
            "absorbed_anonymous": discard.get("identity_absorbed_anonymous", 0),
            "active_links": discard.get("identity_active_links", 0),
        },
        "segments": run.segment_rows or [],
        "lost_steps": lost_rows,
        "max_loss_step": max_loss_step(step_rows) if step_rows else 1,
        "engine_version": run.engine_version,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
    }


def list_runs(db: Session, funnel_id: int | None = None, limit: int = 20) -> list[dict]:
    stmt = select(FunnelRun).order_by(FunnelRun.id.desc()).limit(limit)
    if funnel_id:
        stmt = select(FunnelRun).where(FunnelRun.funnel_id == funnel_id).order_by(FunnelRun.id.desc()).limit(limit)
    runs = db.scalars(stmt).all()
    funnels = {funnel.id: funnel for funnel in db.scalars(select(FunnelDef)).all()}
    return [serialize(run, funnels.get(run.funnel_id)) for run in runs]


def get_run(db: Session, run_id: int) -> tuple[FunnelRun, FunnelDef]:
    run = db.get(FunnelRun, run_id)
    if run is None:
        raise not_found({"run_id": run_id})
    funnel = db.get(FunnelDef, run.funnel_id)
    if funnel is None:
        raise not_found({"funnel_id": run.funnel_id})
    return run, funnel


def latest_run(db: Session, funnel_id: int) -> FunnelRun | None:
    return db.scalar(
        select(FunnelRun).where(FunnelRun.funnel_id == funnel_id).order_by(FunnelRun.id.desc()).limit(1)
    )


def path_analysis(db: Session, run: FunnelRun, funnel: FunnelDef, step_index: int | None = None) -> dict:
    """路径分析：Top-K 路径 / 卡点事件 / 前缀树 / 分群差。"""
    steps = run.step_counts or []
    if not steps:
        raise not_found({"run_id": run.id})
    lost_rows = path_engine.drop_steps(steps)
    if step_index is None or step_index < 1 or step_index > len(steps) - 1:
        step_index = max_loss_step(steps)
    cohort = path_engine.analyze_cohort(outcomes_for_run(db, run), steps, step_index)
    segments = segment_service.build_segments(
        {
            (row["channel"], row["device"]): row["reached"]
            for row in (run.segment_rows or [])
        },
        steps,
        step_index,
        CHANNEL_ORDER,
        DEVICE_ORDER,
    )
    return {
        "run": serialize(run, funnel),
        "lost_steps": lost_rows,
        "selected_step": step_index,
        "cohort": cohort,
        "segments": segments,
        "channel_labels": CHANNEL_LABELS,
        "device_labels": DEVICE_LABELS,
    }


def outcomes_for_run(db: Session, run: FunnelRun) -> dict:
    """从物化的 SessionPath 还原轻量 outcome（避免重跑算法 1）。"""
    rows = db.scalars(select(SessionPath).where(SessionPath.run_id == run.id)).all()
    outcomes = {}
    for row in rows:
        outcomes[row.user_key] = type(
            "PathOutcome",
            (),
            {
                "user_key": row.user_key,
                "channel": row.channel,
                "device": row.device,
                "path_label": row.path_key,
                "path_events": row.path_json or [],
                "terminal_event": row.terminal_event,
                "drop_step": row.drop_step_index,
                "reached_step": row.drop_step_index,
                "converted": row.is_converted,
                "source_keys": row.source_keys or [row.user_key],
                "identity_merged": row.identity_merged,
            },
        )()
    return outcomes
