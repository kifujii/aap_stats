"""aapstats stats: 手元に保存したジョブ履歴の統計（R5-1〜R5-4）。

AAP には接続しない。aapstats sync で保存した SQLite だけを読む。

- 既定: テンプレートごとの利用統計（R5-2）
- --group-by: 組織、プロジェクト、インベントリ、実行環境、認証情報、日、週、月、時間帯、曜日（R5-3）
- --top N: 実行回数、失敗率、平均実行時間の上位 N 件（R5-4）

結果の冒頭には、集計した期間と件数を必ず出す（R5-1）。
集計そのものは aggregate.py で SQL として行う。
"""

import logging
import os

from .. import aggregate, filters
from ..errors import ConfigError
from ..output import get_zone
from ..store import Store, utc_iso
from .sync import db_path, parse_kinds

log = logging.getLogger("aapstats")

GROUPS = ["template"] + list(aggregate.RESOURCE_GROUPS) + ["credential"] + list(aggregate.TIME_GROUPS)

RANKINGS = {
    "runs": ("実行回数が多いテンプレート", lambda r: (r["runs"],)),
    "failure_rate": ("失敗率が高いテンプレート", lambda r: (r["failure_rate"] or 0, r["runs"])),
    "avg_elapsed": ("平均実行時間が長いテンプレート", lambda r: (r["avg_elapsed"] or 0,)),
}

LAUNCH_COLUMNS = [f"launch_{t}" for t in aggregate.LAUNCH_TYPES] + ["launch_other"]
TEMPLATE_COLUMNS = [
    "template_type",
    "template_id",
    "template",
    "organization",
    "runs",
    "successful",
    "failed",
    "error",
    "canceled",
    "success_rate",
    "avg_elapsed",
    "max_elapsed",
    "total_elapsed",
    *LAUNCH_COLUMNS,
    "manual_users",
    "first_run",
    "last_run",
    "template_deleted",
]
TEMPLATE_DEFAULT = [
    "template_type",
    "template",
    "organization",
    "runs",
    "successful",
    "failed",
    "success_rate",
    "avg_elapsed",
    "launch_manual",
    "launch_scheduled",
    "launch_workflow",
    "manual_users",
    "last_run",
    "template_deleted",
]
GROUP_COLUMNS = ["group", "runs", "successful", "failed", "error", "canceled", "success_rate", "avg_elapsed", "total_elapsed", "template_count"]


def run(client, args, renderer):
    settings = client.settings if client else args._settings
    tz_name = settings.timezone
    tz = get_zone(tz_name)
    kinds = parse_kinds(args.kinds)

    path = db_path(args, settings)
    if not os.path.exists(path):
        raise ConfigError(f"保存先 {path} がありません。先に aapstats sync で履歴を取得してください")
    if args.group_by and args.top:
        raise ConfigError("--group-by と --top は同時に使えません")
    if args.group_by and args.group_by not in GROUPS:
        raise ConfigError(f"--group-by に指定できるのは {', '.join(GROUPS)} です")
    since = filters.parse_date(args.since, tz_name, "--since") if args.since else None
    until = filters.parse_date(args.until, tz_name, "--until") if args.until else None
    since_s = utc_iso(since) if since else None
    until_s = utc_iso(until) if until else None

    store = Store(path)
    try:
        if not store.has_jobs(kinds):
            raise ConfigError(f"保存先 {path} に {', '.join(kinds)} の履歴がありません。aapstats sync を実行してください")
        where, wargs = aggregate.where_clause(kinds, since_s, until_s, args.org, args.name)
        conn = store.conn
        group = args.group_by or "template"
        if group == "template":
            rows, (count, first, last) = aggregate.template_stats(conn, where, wargs)
        elif group == "credential":
            rows, (count, first, last) = aggregate.credential_stats(conn, where, wargs)
        elif group in aggregate.RESOURCE_GROUPS:
            rows, (count, first, last) = aggregate.resource_stats(conn, group, where, wargs)
        else:
            rows, (count, first, last) = aggregate.time_stats(conn, group, tz, where, wargs)
        for r in rows:
            r.pop("_key", None)
        last_sync = store.get_meta("last_sync")
        host = store.get_meta("host")
        oldest, newest = store.stored_range()
    finally:
        store.close()

    if since_s and oldest and since_s < oldest:
        log.warning("--since の日付より前の履歴は保存されていません（保存済みの最も古い記録: %s）。集計は保存済みの範囲で行います", oldest)

    head = [
        {"item": "host", "value": host},
        {"item": "db", "value": path},
        {"item": "last_sync", "value": last_sync},
        {"item": "kinds", "value": ", ".join(kinds)},
        {"item": "period_since", "value": since_s or "指定なし"},
        {"item": "period_until", "value": until_s or "指定なし"},
        {"item": "stored_oldest", "value": oldest},
        {"item": "stored_newest", "value": newest},
        {"item": "first_job", "value": first},
        {"item": "last_job", "value": last},
        {"item": "job_count", "value": count},
    ]
    sections = [("summary", "■ 集計の条件", head, ["item", "value"])]

    if group != "template":
        sections.append(("stats", f"■ {group} ごとの統計", rows, GROUP_COLUMNS))
    elif args.top:
        for key in [args.by] if args.by else list(RANKINGS):
            title, sort_key = RANKINGS[key]
            cand = [r for r in rows if r["runs"] >= (args.min_runs or 1)]
            if key != "runs":
                cand = [r for r in cand if r[key] is not None]
            top = sorted(cand, key=sort_key, reverse=True)[: args.top]
            rate = "failure_rate" if key == "failure_rate" else "success_rate"
            cols = ["template_type", "template", "organization", "runs", rate, "avg_elapsed", "last_run", "template_deleted"]
            sections.append((f"top_{key}", f"■ {title}（上位 {args.top} 件）", top, cols))
    else:
        cols = TEMPLATE_COLUMNS if (args.wide or renderer.fmt == "json") else TEMPLATE_DEFAULT
        if renderer.fmt == "json":
            rows = [{c: r.get(c) for c in cols} for r in rows]
        sections.append(("stats", "■ テンプレートごとの統計", rows, cols))

    renderer.document(sections)
    return 0


def add_arguments(parser):
    parser.add_argument("--db", metavar="PATH", help="保存先の SQLite ファイル（既定: ~/.local/share/aapstats/<プロファイル名>.sqlite）")
    parser.add_argument("--kinds", metavar="KINDS", help="集計する履歴の種類（既定: job,workflow_job）")
    parser.add_argument("--since", metavar="DATE", help="この日以降のジョブを集計する")
    parser.add_argument("--until", metavar="DATE", help="この日より前のジョブを集計する")
    parser.add_argument("--org", action="append", metavar="NAME", help="組織名で絞り込む（複数指定可）")
    parser.add_argument("--name", metavar="TEXT", help="テンプレート名の部分一致")
    parser.add_argument("--group-by", metavar="KEY", help="集計の単位: " + ", ".join(GROUPS))
    parser.add_argument("--top", type=int, metavar="N", help="上位 N 件のランキングを出す（実行回数、失敗率、平均実行時間）")
    parser.add_argument("--by", choices=list(RANKINGS), help="--top で出すランキングを 1 つに絞る")
    parser.add_argument("--min-runs", type=int, metavar="N", help="--top で、実行回数が N 回以上のテンプレートだけを対象にする（既定: 1）")
    parser.add_argument("--wide", action="store_true", help="すべての列を出す")
