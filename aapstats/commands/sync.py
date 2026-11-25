"""aapstats sync: ジョブ履歴を手元の SQLite に保存する（R2-5、R5-1）。

- 初回は残っている履歴をすべて取得する（--since で始まりを絞れる）
- 2 回目以降は、新しいジョブと、前回の時点で終わっていなかったジョブだけを取得する
- 途中で止まっても（Ctrl+C など）、次回は続きから取得する
"""

import logging
import time

from .. import filters
from ..errors import ConfigError
from ..store import DEFAULT_KINDS, KINDS, Store, default_db_path, now_iso, utc_iso

log = logging.getLogger("aapstats")

PAGE_SIZE = 200
REFRESH_BATCH = 100
PROGRESS_EVERY_SEC = 10


def parse_kinds(value):
    if not value:
        return list(DEFAULT_KINDS)
    kinds = [k.strip() for k in value.split(",") if k.strip()]
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise ConfigError(f"--kinds に指定できるのは {', '.join(KINDS)} です（指定値: {', '.join(unknown)}）")
    return kinds


def db_path(args, settings):
    if getattr(args, "db", None):
        return args.db
    return str(default_db_path(settings.profile, settings.host))


def _refresh_unfinished(client, store, kind, now):
    """前回の時点で終わっていなかったジョブを取り直す。"""
    resource = KINDS[kind][0]
    ids = store.unfinished_ids(kind)
    if not ids:
        return 0
    from ..store import to_row

    n = 0
    for i in range(0, len(ids), REFRESH_BATCH):
        batch = ids[i : i + REFRESH_BATCH]
        items = client.list_resource(resource, {"id__in": ",".join(map(str, batch))})
        store.upsert([to_row(kind, it, now) for it in items])
        store.conn.commit()
        n += len(items)
    return n


def _fetch_new(client, store, kind, since, now):
    """保存済みの最大 ID より新しいジョブを、ID の昇順に取得する。"""
    from ..store import to_row

    resource = KINDS[kind][0]
    path = client.layout.resource_path(resource)
    last_id = store.max_id(kind)
    params = {"order_by": "id"}
    if since and last_id == 0:
        params["created__gte"] = utc_iso(since)
    remaining = client.count(path, dict(params, id__gt=last_id))
    if remaining == 0:
        return 0
    log.info("%s: 新しい履歴 %d 件を取得します", kind, remaining)

    fetched = 0
    started = time.monotonic()
    last_report = started
    while True:
        data = client.get(path, params=dict(params, id__gt=last_id, page_size=PAGE_SIZE))
        items = data.get("results") or []
        if not items:
            break
        store.upsert([to_row(kind, it, now) for it in items])
        store.conn.commit()  # ページごとにコミットし、途中で止まっても続きから取得できるようにする
        fetched += len(items)
        last_id = items[-1]["id"]
        t = time.monotonic()
        if t - last_report >= PROGRESS_EVERY_SEC:
            rate = fetched / (t - started)
            left = max(remaining - fetched, 0)
            eta = left / rate if rate else 0
            log.info("%s: %d / %d 件（%.0f 件/秒、残り約 %d 分）", kind, fetched, remaining, rate, eta // 60 + 1)
            last_report = t
        if not data.get("next"):
            break
    return fetched


def run(client, args, renderer):
    kinds = parse_kinds(args.kinds)
    tz = client.settings.timezone
    since = filters.parse_date(args.since, tz, "--since") if args.since else None
    path = db_path(args, client.settings)
    store = Store(path)
    try:
        if not store.check_host(client.settings.host):
            raise ConfigError(
                f"保存先 {path} には別の接続先（{store.get_meta('host')}）の履歴が入っています。--db で別のファイルを指定してください"
            )
        log.info("保存先: %s", path)
        now = now_iso()
        results = []
        for kind in kinds:
            if not client.layout.supports(KINDS[kind][0]):
                continue
            refreshed = _refresh_unfinished(client, store, kind, now)
            fetched = _fetch_new(client, store, kind, since, now)
            results.append({"kind": kind, "fetched": fetched, "refreshed": refreshed})
        store.set_meta("last_sync", now)
        store.conn.commit()

        summary = {r["kind"]: r for r in store.summary(kinds)}
        rows = []
        for r in results:
            s = summary.get(r["kind"], {})
            rows.append(dict(r, total=s.get("n", 0), oldest=s.get("oldest"), newest=s.get("newest")))
        renderer.document(
            [
                ("sync", f"■ 取得結果（保存先: {path}）", rows, ["kind", "fetched", "refreshed", "total", "oldest", "newest"]),
            ]
        )
        log.info("API リクエスト数: %d", client.request_count)
        return 0
    finally:
        store.close()


def add_arguments(parser):
    parser.add_argument("--db", metavar="PATH", help="保存先の SQLite ファイル（既定: ~/.local/share/aapstats/<プロファイル名>.sqlite）")
    parser.add_argument(
        "--kinds",
        metavar="KINDS",
        help="取得する履歴の種類をカンマ区切りで指定する。job, workflow_job, project_update, inventory_update（既定: job,workflow_job）",
    )
    parser.add_argument("--since", metavar="DATE", help="初回の取得で、この日以降の履歴だけを取得する（例: 2026-08-01）")

