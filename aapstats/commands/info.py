"""aapstats info: 接続先と全体像の表示（R4-1）。

- 接続先、判別したバージョン、実行ユーザーの権限
- リソースごとの件数
- 残っているジョブ履歴の期間（R3-6、R5-1）
- 履歴を削除する管理ジョブ（Cleanup Job Details）の設定
"""

import logging
from datetime import datetime, timezone

from ..endpoints import CONTROLLER, RESOURCES
from ..errors import ApiError, PermissionDenied
from ..output import parse_dt

log = logging.getLogger("aapstats")

# 件数を表示するリソース（表示順）
COUNT_RESOURCES = [
    "organizations",
    "job_templates",
    "workflow_job_templates",
    "schedules",
    "projects",
    "inventories",
    "credentials",
    "credential_types",
    "execution_environments",
    "teams",
    "authenticators",
]

HISTORY_RESOURCES = ["jobs", "workflow_jobs"]

# R5-5 の既定の判定日数。履歴がこれより短いと、実行回数での判定に注記が必要になる
DEFAULT_UNUSED_DAYS = 90


def _versions(client):
    rows = []
    for svc, path in client.layout.ping_paths().items():
        try:
            data = client.get(path, auth=False)
            rows.append({"item": f"{svc}_version", "value": data.get("version", "")})
        except ApiError as e:
            rows.append({"item": f"{svc}_version", "value": f"取得できません（{e}）"})
    return rows


def _me(client):

    data = client.get(client.layout.path(CONTROLLER, "me/"))
    results = data.get("results") or []
    return results[0] if results else {}


def _role(me):
    if me.get("is_superuser"):
        return "システム管理者"
    if me.get("is_system_auditor"):
        return "システム監査者"
    return "一般ユーザー"


def _count_rows(client):
    def one(name):
        r = RESOURCES[name]
        row = {"resource": name, "label": r.label, "count": None, "note": ""}
        if not client.layout.supports(name):
            row["note"] = "このバージョンの API では取得できません"
            return row
        try:
            row["count"] = client.count_resource(name)
        except PermissionDenied:
            row["note"] = "権限がないため取得できません"
        except ApiError as e:
            row["note"] = f"取得できません（{e}）"
        if name == "credential_types":
            row["note"] = row["note"] or "組み込みのタイプを除く"
        return row

    return client.map_parallel(one, COUNT_RESOURCES)


def _history_rows(client, now):
    def one(name):
        row = {"resource": name, "label": RESOURCES[name].label, "count": None, "oldest": None, "newest": None, "days": None, "note": ""}
        try:
            row["count"] = client.count_resource(name)
            if row["count"]:
                oldest = client.first_resource(name, {"order_by": "created"})
                newest = client.first_resource(name, {"order_by": "-created"})
                row["oldest"] = oldest.get("created") if oldest else None
                row["newest"] = newest.get("created") if newest else None
                dt = parse_dt(row["oldest"])
                if dt:
                    row["days"] = (now - dt).days
        except PermissionDenied:
            row["note"] = "権限がないため取得できません"
        except ApiError as e:
            row["note"] = f"取得できません（{e}）"
        return row

    return client.map_parallel(one, HISTORY_RESOURCES)


def _cleanup_rows(client):
    """履歴を削除する管理ジョブのスケジュール（Cleanup Job Details）を取得する。"""
    rows = []
    try:
        templates = client.list_resource("system_job_templates", {"job_type": "cleanup_jobs"})
    except PermissionDenied:
        return [{"name": "Cleanup Job Details", "note": "権限がないため取得できません（システム管理者で確認してください）"}]
    except ApiError as e:
        return [{"name": "Cleanup Job Details", "note": f"取得できません（{e}）"}]
    for t in templates:
        sched_url = (t.get("related") or {}).get("schedules")
        schedules = list(client.paginate(sched_url)) if sched_url else []
        if not schedules:
            rows.append({"name": t.get("name"), "schedule": "", "enabled": None, "retention_days": None, "next_run": None, "note": "スケジュールなし（履歴は自動では削除されません）"})
        for s in schedules:
            extra = s.get("extra_data") or {}
            rows.append(
                {
                    "name": t.get("name"),
                    "schedule": s.get("name"),
                    "enabled": s.get("enabled"),
                    "retention_days": extra.get("days"),
                    "next_run": s.get("next_run"),
                    "note": "",
                }
            )
    return rows


def run(client, args, renderer):
    now = datetime.now(timezone.utc)
    s = client.settings

    me = {}
    try:
        me = _me(client)
    except ApiError as e:
        log.warning("実行ユーザーの情報を取得できません: %s", e)

    conn = [
        {"item": "host", "value": s.host},
        {"item": "profile", "value": s.profile or "（指定なし）"},
        {"item": "api_layout", "value": f"{client.layout.label}（{'自動判別' if s.api_version == 'auto' else '手動指定'}）"},
        *_versions(client),
        {"item": "user", "value": me.get("username", "")},
        {"item": "user_role", "value": _role(me) if me else ""},
        {"item": "timezone", "value": s.timezone},
    ]

    if me and not (me.get("is_superuser") or me.get("is_system_auditor")):
        log.warning(
            "実行ユーザーはシステム管理者でもシステム監査者でもありません。"
            "権限のないリソースは一覧から抜けます（R1-6）"
        )

    counts = _count_rows(client)
    history = _history_rows(client, now)
    cleanup = _cleanup_rows(client)

    days = [h["days"] for h in history if h["days"] is not None]
    if days:
        span = max(days)
        if span < DEFAULT_UNUSED_DAYS:
            log.warning(
                "残っているジョブ履歴は %d 日分で、判定の既定値の %d 日に足りません。"
                "履歴にない期間の判定は、テンプレートの最終実行日時（last_job_run）で補います（R3-6）",
                span,
                DEFAULT_UNUSED_DAYS,
            )

    sections = [
        ("connection", "■ 接続先", conn, ["item", "value"]),
        ("resources", "■ リソースの件数", counts, ["resource", "label", "count", "note"]),
        ("history", "■ ジョブ履歴の期間", history, ["resource", "label", "count", "oldest", "newest", "days", "note"]),
        ("cleanup", "■ 履歴の削除設定（Cleanup Job Details）", cleanup, ["name", "schedule", "enabled", "retention_days", "next_run", "note"]),
    ]
    renderer.document(sections)
    log.debug("API リクエスト数: %d", client.request_count)
    return 0
