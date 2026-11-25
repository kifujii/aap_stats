"""aapstats report: 移行判定（R5-5、R5-6）。

判定の考え方は report.py を参照。

出力:
- table: サマリー（種類ごとの件数と分類の内訳）と、種類ごとの判定結果
- csv  : 判定結果を 1 つの表にまとめ、空欄の decision 列を付ける（Excel で移行するかどうかを書き込む）
- json : {"summary": [...], "items": [...]}
"""

import logging
from datetime import datetime, timezone

from .. import filters
from ..errors import ConfigError
from ..report import CODE, PREFETCH, TYPE_LABEL, TYPE_ORDER, Judge, summary
from ..resources import Context

log = logging.getLogger("aapstats")

ITEM_COLUMNS = ["resource_label", "id", "name", "organization", "classification", "reason", "last_job_run", "decision"]
JSON_COLUMNS = ["resource_type", "resource_label", "id", "name", "organization", "classification", "classification_code", "reason", "last_job_run", "decision"]
SUMMARY_COLUMNS = ["resource_label", "count", "migrate", "review", "unneeded"]


def run(client, args, renderer):
    days = client.settings.unused_days
    if days < 1:
        raise ConfigError("--unused-days には 1 以上を指定してください")
    only = None
    if args.only:
        only = {c.strip() for c in args.only.split(",") if c.strip()}
        bad = only - set(CODE.values())
        if bad:
            raise ConfigError(f"--only に指定できるのは {', '.join(CODE.values())} です（指定値: {', '.join(sorted(bad))}）")
    types = None
    if args.resource:
        types = {t.strip() for t in args.resource.split(",") if t.strip()}
        bad = types - set(TYPE_ORDER)
        if bad:
            raise ConfigError(f"--resource に指定できるのは {', '.join(TYPE_ORDER)} です（指定値: {', '.join(sorted(bad))}）")

    now = datetime.now(timezone.utc)
    ctx = Context(client)
    names = PREFETCH + (["role_team_assignments", "gateway_teams", "authenticator_maps"] if client.layout.supports("role_team_assignments") else [])
    ctx.prefetch(names)

    hist = filters.history_days(client, "job_templates", now)
    if hist is not None and hist < days:
        log.warning(
            "残っているジョブ履歴は %d 日分で、判定の %d 日に足りません。実行の有無はテンプレートの最終実行日時"
            "（last_job_run）で判定しますが、「すべて失敗」の判定は残っている %d 日分だけで行います",
            hist,
            days,
            hist,
        )

    items = Judge(ctx, days, now, check_failures=not args.no_failure_check).run()
    summ = summary(items)  # サマリーは絞り込み前の全体で出す
    if types:
        items = [i for i in items if i["resource_type"] in types]
    if only:
        items = [i for i in items if i["classification_code"] in only]

    cond = [
        {"item": "host", "value": client.settings.host},
        {"item": "unused_days", "value": days},
        {"item": "judged_at", "value": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"item": "history_days", "value": hist},
        {"item": "failure_check", "value": not args.no_failure_check},
    ]

    if renderer.fmt == "json":
        import json

        json.dump(
            {"conditions": cond, "summary": summ, "items": [{c: i.get(c) for c in JSON_COLUMNS} for i in items]},
            renderer.out,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        renderer.out.write("\n")
    elif renderer.fmt == "csv":
        renderer.rows(items, ITEM_COLUMNS)
    else:
        sections = [
            ("conditions", "■ 判定の条件", cond, ["item", "value"]),
            ("summary", "■ サマリー", summ, SUMMARY_COLUMNS),
        ]
        for rtype in TYPE_ORDER:
            sub = [i for i in items if i["resource_type"] == rtype]
            if sub:
                cols = [c for c in ITEM_COLUMNS if c not in ("resource_label", "decision") and (c != "last_job_run" or rtype in ("job_template", "workflow_job_template"))]
                sections.append((rtype, f"■ {TYPE_LABEL[rtype]}", sub, cols))
        renderer.document(sections)
    log.info("判定したリソース: %d 件（API リクエスト数 %d）", len(items), client.request_count)
    return 0


def add_arguments(parser):
    parser.add_argument("--unused-days", type=int, metavar="N", help="この日数より前に最後に実行されたテンプレートを未使用とみなす（既定: 90。設定ファイルの unused_days でも指定可）")
    parser.add_argument("--no-failure-check", action="store_true", help="「直近の実行がすべて失敗」の判定をしない（テンプレートごとの API の問い合わせを省く）")
    parser.add_argument("--only", metavar="CODES", help="出す分類をカンマ区切りで指定する: migrate, review, unneeded")
    parser.add_argument("--resource", metavar="TYPES", help="出すリソースの種類をカンマ区切りで指定する: " + ", ".join(TYPE_ORDER))
