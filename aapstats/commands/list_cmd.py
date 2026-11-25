"""aapstats list <リソース>: リソースの一覧（R4-1、R4-3、R3-1〜R3-6）。

列の選び方:
- 既定: リソースごとに決めた、よく見る列
- --wide: すべての列
- --columns a,b,c: 指定した列だけ（指定した順）
- json は、--columns を指定しない限りすべての列を出す（他のツールに渡す用途のため）

run_count（期間内の実行回数）は、テンプレートの数だけ API を呼ぶため、
--min-runs / --max-runs を指定したときか、--columns に書いたときだけ数える。
"""

import logging
from datetime import datetime, timezone

from .. import filters
from ..errors import ConfigError
from ..resources import SPECS, build_rows

log = logging.getLogger("aapstats")

RESOURCE_NAMES = list(SPECS)
ON_DEMAND_COLUMNS = {r: ["run_count"] for r in filters.TEMPLATES}


def available_columns(spec):
    return spec.all_columns + ON_DEMAND_COLUMNS.get(spec.name, [])


def select_columns(spec, args, fmt):
    available = available_columns(spec)
    if args.columns:
        cols = [c.strip() for c in args.columns.split(",") if c.strip()]
        unknown = [c for c in cols if c not in available]
        if unknown:
            raise ConfigError(f"{spec.name} に列 {', '.join(unknown)} はありません。使える列: {', '.join(available)}")
        return cols
    cols = list(spec.all_columns if (args.wide or fmt == "json") else spec.default_columns)
    if filters.needs_run_count(args, cols) and "run_count" not in cols:
        cols.append("run_count")
    return cols


def _check_history(client, args, resource, now, since):
    """履歴の保持期間と、指定した日数・期間を突き合わせる（R3-6）。"""
    if args.unused_days is None and since is None and not filters.needs_run_count(args, []):
        return
    days = filters.history_days(client, resource, now)
    if days is None:
        log.warning("実行履歴が残っていません。実行回数はすべて 0 になります")
        return
    if args.unused_days is not None and args.unused_days > days:
        log.warning(
            "残っている実行履歴は %d 日分です。--unused-days %d は、履歴ではなくテンプレートの"
            "最終実行日時（last_job_run）で判定します。last_job_run は履歴を削除しても残ります",
            days,
            args.unused_days,
        )
    if filters.needs_run_count(args, []):
        if since is None:
            log.info("実行回数は、残っている履歴すべて（%d 日分）で数えます", days)
        elif (now - since).days > days:
            log.warning(
                "--since の日付より後の履歴の一部は削除済みです（残っているのは %d 日分）。実行回数は少なく出ます",
                days,
            )


def run(client, args, renderer):
    spec = SPECS[args.resource]
    filters.validate(args, args.resource)
    columns = select_columns(spec, args, renderer.fmt)
    if args.sort and args.sort.lstrip("-") not in available_columns(spec):
        raise ConfigError(f"--sort の列 {args.sort.lstrip('-')} は {spec.name} にありません")

    now = datetime.now(timezone.utc)
    tz = client.settings.timezone
    since = filters.parse_date(args.since, tz, "--since") if args.since else None

    rows = build_rows(client, args.resource)
    total = len(rows)
    _check_history(client, args, args.resource, now, since)

    sort_col = args.sort.lstrip("-") if args.sort else None
    if filters.needs_run_count(args, columns + ([sort_col] if sort_col else [])):
        # 実行回数の問い合わせを減らすため、実行回数を使わない絞り込みを先に済ませる
        pre_args = _without(args, "min_runs", "max_runs", "sort", "limit")
        rows = filters.apply(rows, pre_args, args.resource, tz, now)
        filters.add_run_counts(client, args.resource, rows, since)

    rows = filters.apply(rows, args, args.resource, tz, now)
    if renderer.fmt == "json":
        rows = [{c: r.get(c) for c in columns} for r in rows]
    renderer.rows(rows, columns)
    log.info("%d 件（絞り込み前 %d 件、API リクエスト数 %d）", len(rows), total, client.request_count)
    return 0


def _without(args, *names):
    import argparse

    d = dict(vars(args))
    for n in names:
        d[n] = None
    return argparse.Namespace(**d)
