"""list の絞り込み、並べ替え、逆引き（R3-1〜R3-6）。

絞り込みは、取得した一覧に対して手元で行う。リソースの数は多くても数百件で、
一覧はどのみち全件取得するため、API の検索条件に変換するより単純で、
リソースの種類による違いも出にくい。

ただし期間内の実行回数（--min-runs / --max-runs）だけは、テンプレートごとに
API で件数を問い合わせる（R2-5）。
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from .errors import ConfigError
from .output import get_zone, parse_dt

log = logging.getLogger("aapstats")

TEMPLATES = ("job_templates", "workflow_job_templates")
REFERENCEABLE = ("projects", "inventories", "credentials", "execution_environments")

# リソースごとに「どこからも使われていない」とみなす列（R3-3 --unreferenced）
UNREFERENCED_COLUMNS = {
    "projects": ["used_by_templates", "used_by_inventory_sources", "used_in_workflows"],
    "inventories": ["used_by_templates", "used_in_workflows"],
    "credentials": ["used_by_templates", "used_by_projects", "used_by_inventory_sources", "used_by_organizations"],
    "execution_environments": ["used_by_templates", "used_by_projects", "used_by_organizations", "used_by_inventory_sources"],
}

# 逆引き（R3-4）: オプション名 → (ジョブテンプレートの列, ワークフローの列)
USES = {
    "uses_project": ("project_id", "project", None, None),
    "uses_inventory": ("inventory_id", "inventory", "inventory_id", "inventory"),
    "uses_credential": ("credential_ids", "credentials", None, None),
    "uses_ee": ("execution_environment_id", "execution_environment", None, None),
}

LAST_STATUS = {
    "successful": {"successful"},
    "failed": {"failed"},
    "error": {"error"},
    "canceled": {"canceled"},
    "never": {"never updated", None, ""},
}

# 期間内の実行回数を数える API（R2-5）
RUN_COUNT_SOURCE = {
    "job_templates": ("jobs", "job_template"),
    "workflow_job_templates": ("workflow_jobs", "workflow_job_template"),
}


def parse_date(value, tz_name, option):
    """YYYY-MM-DD か ISO 8601 の日時を aware な datetime にする。日付だけなら tz の 0 時。"""
    tz = get_zone(tz_name)
    v = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=tz)
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    except ValueError:
        raise ConfigError(f"{option} の日付の形式が正しくありません（例: 2026-08-01）: {value}") from None


def add_arguments(parser):
    g = parser.add_argument_group("絞り込み（共通）")
    g.add_argument("--org", action="append", metavar="NAME", help="組織名で絞り込む（複数指定可）")
    g.add_argument("--name", metavar="TEXT", help="名前の部分一致（大文字と小文字を区別しない）")
    g.add_argument("--name-regex", metavar="REGEX", help="名前の正規表現")
    g.add_argument("--created-before", metavar="DATE", help="この日より前に作成されたもの（例: 2026-01-01）")
    g.add_argument("--created-after", metavar="DATE", help="この日以降に作成されたもの")
    g.add_argument("--modified-before", metavar="DATE", help="この日より前に更新されたもの")
    g.add_argument("--modified-after", metavar="DATE", help="この日以降に更新されたもの")
    g.add_argument("--sort", metavar="COLUMN", help="並べ替える列。降順は先頭に - を付け、= でつなぐ（例: --sort=-last_job_run）")
    g.add_argument("--limit", type=int, metavar="N", help="出す件数の上限")

    g = parser.add_argument_group("絞り込み（利用状況。job_templates と workflow_job_templates）")
    g.add_argument("--unused-days", type=int, metavar="N", help="N 日以上実行されていないもの（一度も実行されていないものを含む）")
    g.add_argument("--never-run", action="store_true", help="一度も実行されていないもの")
    g.add_argument("--last-status", choices=list(LAST_STATUS), help="最後の実行結果で絞り込む")
    g.add_argument("--min-runs", type=int, metavar="N", help="期間内の実行回数が N 回以上のもの")
    g.add_argument("--max-runs", type=int, metavar="N", help="期間内の実行回数が N 回以下のもの")
    g.add_argument("--since", metavar="DATE", help="実行回数を数える期間の始まり（既定: 残っている履歴すべて）")

    g = parser.add_argument_group("絞り込み（参照関係）")
    g.add_argument("--orphan", action="store_true", help="有効なスケジュールからもワークフローからも呼ばれていないテンプレート")
    g.add_argument("--no-schedule", action="store_true", help="スケジュールが 1 つもないテンプレート")
    g.add_argument("--unreferenced", action="store_true", help="どこからも使われていないプロジェクト、インベントリ、認証情報、実行環境")

    g = parser.add_argument_group("逆引き（指定したリソースを使うテンプレート）")
    g.add_argument("--uses-project", metavar="NAME|ID", help="このプロジェクトを使うもの")
    g.add_argument("--uses-inventory", metavar="NAME|ID", help="このインベントリを使うもの")
    g.add_argument("--uses-credential", metavar="NAME|ID", help="この認証情報を使うもの")
    g.add_argument("--uses-ee", metavar="NAME|ID", help="この実行環境を使うもの")


def validate(args, resource):
    """リソースに合わないオプションを指定していないか確かめる。"""
    template_only = {
        "--unused-days": args.unused_days is not None,
        "--never-run": args.never_run,
        "--last-status": args.last_status is not None,
        "--min-runs": args.min_runs is not None,
        "--max-runs": args.max_runs is not None,
        "--since": args.since is not None,
        "--orphan": args.orphan,
        "--no-schedule": args.no_schedule,
        "--uses-project": args.uses_project is not None,
        "--uses-inventory": args.uses_inventory is not None,
        "--uses-credential": args.uses_credential is not None,
        "--uses-ee": args.uses_ee is not None,
    }
    used = [k for k, v in template_only.items() if v]
    if used and resource not in TEMPLATES:
        raise ConfigError(f"{', '.join(used)} は job_templates と workflow_job_templates でだけ使えます")
    if args.unreferenced and resource not in REFERENCEABLE:
        raise ConfigError(f"--unreferenced は {', '.join(REFERENCEABLE)} でだけ使えます")
    if args.org and resource == "organizations":
        raise ConfigError("--org は organizations では使えません。--name を使ってください")
    if args.org and resource in ("credential_types", "authenticators"):
        raise ConfigError(f"{resource} は組織に属さないため、--org は使えません")
    for opt in ("unused_days", "min_runs", "max_runs", "limit"):
        v = getattr(args, opt)
        if v is not None and v < 0:
            raise ConfigError(f"--{opt.replace('_', '-')} には 0 以上を指定してください")
    if args.name_regex:
        try:
            re.compile(args.name_regex)
        except re.error as e:
            raise ConfigError(f"--name-regex の正規表現が正しくありません: {e}") from None


def needs_run_count(args, columns):
    return args.min_runs is not None or args.max_runs is not None or "run_count" in columns


def history_days(client, resource, now):
    """残っている実行履歴の日数。履歴がなければ None。"""
    source = RUN_COUNT_SOURCE.get(resource, ("jobs", None))[0]
    oldest = client.first_resource(source, {"order_by": "created"})
    dt = parse_dt(oldest.get("created")) if oldest else None
    return (now - dt).days if dt else None


def add_run_counts(client, resource, rows, since):
    """期間内の実行回数を、テンプレートごとに件数だけ問い合わせて run_count に入れる（R2-5）。"""
    source, key = RUN_COUNT_SOURCE[resource]
    base = {}
    if since:
        base["created__gte"] = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("期間内の実行回数を数えています（%d 件）", len(rows))

    def one(row):
        return client.count_resource(source, dict(base, **{key: row["id"]}))

    for row, n in zip(rows, client.map_parallel(one, rows)):
        row["run_count"] = n


def _match_ref(value, label, target):
    """逆引きの照合。target が数字なら ID、そうでなければ名前で比べる。"""
    if target.isdigit():
        tid = int(target)
        return tid in value if isinstance(value, list) else value == tid
    if isinstance(label, list):
        return target in label
    return label == target


def _is_empty(v):
    return v in (None, 0, "", []) or v == ()


def apply(rows, args, resource, tz_name, now=None):
    """rows を絞り込み、並べ替えて返す。"""
    now = now or datetime.now(timezone.utc)
    out = rows

    if args.org:
        orgs = set(args.org)
        out = [r for r in out if r.get("organization") in orgs]
    if args.name:
        needle = args.name.lower()
        out = [r for r in out if needle in (r.get("name") or "").lower()]
    if args.name_regex:
        rx = re.compile(args.name_regex)
        out = [r for r in out if rx.search(r.get("name") or "")]

    for opt, key, before in (
        ("created_before", "created", True),
        ("created_after", "created", False),
        ("modified_before", "modified", True),
        ("modified_after", "modified", False),
    ):
        raw = getattr(args, opt)
        if not raw:
            continue
        limit = parse_date(raw, tz_name, "--" + opt.replace("_", "-"))

        def keep(r, key=key, limit=limit, before=before):
            dt = parse_dt(r.get(key))
            if dt is None:
                return False
            return dt < limit if before else dt >= limit

        out = [r for r in out if keep(r)]

    if args.unused_days is not None:
        border = now - timedelta(days=args.unused_days)

        def unused(r):
            dt = parse_dt(r.get("last_job_run"))
            return dt is None or dt < border

        out = [r for r in out if unused(r)]
    if args.never_run:
        out = [r for r in out if not r.get("last_job_run")]
    if args.last_status:
        wanted = LAST_STATUS[args.last_status]
        out = [r for r in out if r.get("last_job_status") in wanted]
    if args.min_runs is not None:
        out = [r for r in out if (r.get("run_count") or 0) >= args.min_runs]
    if args.max_runs is not None:
        out = [r for r in out if (r.get("run_count") or 0) <= args.max_runs]

    if args.orphan:
        out = [r for r in out if not r.get("enabled_schedule_count") and not r.get("used_in_workflows")]
    if args.no_schedule:
        out = [r for r in out if not r.get("schedule_count")]
    if args.unreferenced:
        cols = UNREFERENCED_COLUMNS[resource]
        out = [r for r in out if all(_is_empty(r.get(c)) for c in cols)]
        managed = [r for r in out if r.get("managed")]
        if managed:
            # 組み込みのリソースは AAP が用意するもので、移行するかどうかを選ぶ対象ではない
            log.info("組み込みのリソース %d 件（%s）を除きました", len(managed), ", ".join(r["name"] for r in managed))
            out = [r for r in out if not r.get("managed")]

    for opt, (jt_id, jt_label, wf_id, wf_label) in USES.items():
        target = getattr(args, opt)
        if target is None:
            continue
        id_col, label_col = (jt_id, jt_label) if resource == "job_templates" else (wf_id, wf_label)
        if id_col is None:
            out = []  # ワークフローはプロジェクト・認証情報・実行環境を直接は使わない
            continue
        out = [r for r in out if _match_ref(r.get(id_col), r.get(label_col), target)]

    if args.sort:
        desc = args.sort.startswith("-")
        key = args.sort.lstrip("-")

        def sort_key(r):
            v = r.get(key)
            if isinstance(v, list):
                v = ", ".join(map(str, v))
            # None は昇順でも降順でも最後に置く
            return (v is None, v if v is not None else 0)

        present = [r for r in out if r.get(key) is not None]
        missing = [r for r in out if r.get(key) is None]
        try:
            present.sort(key=sort_key, reverse=desc)
        except TypeError:
            present.sort(key=lambda r: str(r.get(key)), reverse=desc)
        out = present + missing
    if args.limit is not None:
        out = out[: args.limit]
    return out
