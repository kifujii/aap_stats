"""aapstats list <リソース>: リソースの一覧（R4-1、R4-3）。

列の選び方:
- 既定: リソースごとに決めた、よく見る列
- --wide: すべての列
- --columns a,b,c: 指定した列だけ（指定した順）
- json は、--columns を指定しない限りすべての列を出す（他のツールに渡す用途のため）
"""

import logging

from ..errors import ConfigError
from ..resources import SPECS, build_rows

log = logging.getLogger("aapstats")

RESOURCE_NAMES = list(SPECS)


def select_columns(spec, args, fmt):
    if args.columns:
        cols = [c.strip() for c in args.columns.split(",") if c.strip()]
        unknown = [c for c in cols if c not in spec.all_columns]
        if unknown:
            raise ConfigError(
                f"{spec.name} に列 {', '.join(unknown)} はありません。使える列: {', '.join(spec.all_columns)}"
            )
        return cols
    if args.wide or fmt == "json":
        return spec.all_columns
    return spec.default_columns


def run(client, args, renderer):
    spec = SPECS[args.resource]
    columns = select_columns(spec, args, renderer.fmt)
    rows = build_rows(client, args.resource)
    if renderer.fmt == "json":
        rows = [{c: r.get(c) for c in columns} for r in rows]
    renderer.rows(rows, columns)
    log.info("%d 件（API リクエスト数: %d）", len(rows), client.request_count)
    return 0
