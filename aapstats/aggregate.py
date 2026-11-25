"""保存したジョブ履歴の集計（R5-2〜R5-4）。

集計は SQLite の SQL で行う。100 万件規模の履歴を Python のメモリに読み込まないため。

- テンプレート別・リソース別: GROUP BY
- 認証情報別: 1 つのジョブが複数の認証情報を持つため、json_each で展開してから GROUP BY
- 日・週・月・時間帯・曜日: UTC の日時を指定のタイムゾーンの区切りに変換する関数を SQLite に登録し、
  GROUP BY の中で使う。変換は分単位でキャッシュするため、変換の回数は分の種類の数で済む
"""

from datetime import timedelta
from functools import lru_cache

from .output import parse_dt

FINAL = ("successful", "failed", "error", "canceled")
LAUNCH_TYPES = ["manual", "scheduled", "workflow", "relaunch", "callback", "webhook"]
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
TIME_GROUPS = ("day", "week", "month", "hour", "weekday")
RESOURCE_GROUPS = {
    "organization": ("organization_id", "organization_name"),
    "project": ("project_id", "project_name"),
    "inventory": ("inventory_id", "inventory_name"),
    "execution_environment": ("execution_environment_id", "execution_environment_name"),
}
TEMPLATE_TYPE = {"job": "job_template", "workflow_job": "workflow", "project_update": "project", "inventory_update": "inventory_source"}

_TEMPLATE_KEY = "kind || ':' || COALESCE(CAST(template_id AS TEXT), 'name:' || template_name)"


def where_clause(kinds, since=None, until=None, orgs=None, name=None):
    conds = [f"kind IN ({', '.join('?' for _ in kinds)})"]
    args = list(kinds)
    if since:
        conds.append("created >= ?")
        args.append(since)
    if until:
        conds.append("created < ?")
        args.append(until)
    if orgs:
        conds.append(f"organization_name IN ({', '.join('?' for _ in orgs)})")
        args += list(orgs)
    if name:
        conds.append("LOWER(template_name) LIKE ?")
        args.append(f"%{name.lower()}%")
    return " AND ".join(conds), args


class Acc:
    """「結果 × 起動方法」ごとに SQL で数えた小計を、集計単位ごとに足し合わせる。"""

    __slots__ = ("runs", "status", "launch", "users", "templates", "el_sum", "el_n", "el_max", "first", "last")

    def __init__(self):
        self.runs = 0
        self.status = {}
        self.launch = {}
        self.users = set()
        self.templates = set()
        self.el_sum = 0.0
        self.el_n = 0
        self.el_max = None
        self.first = None
        self.last = None

    def add(self, status, launch, user, tkey, n, el_sum, el_n, el_max, first, last):
        self.runs += n
        self.status[status] = self.status.get(status, 0) + n
        lt = launch if launch in LAUNCH_TYPES else "other"
        self.launch[lt] = self.launch.get(lt, 0) + n
        if user:
            self.users.add(user)
        if tkey:
            self.templates.add(tkey)
        if status in FINAL and el_n:
            self.el_sum += el_sum or 0
            self.el_n += el_n
            self.el_max = el_max if self.el_max is None else max(self.el_max, el_max)
        if first and (self.first is None or first < self.first):
            self.first = first
        if last and (self.last is None or last > self.last):
            self.last = last

    def merge(self, other):
        """別の Acc を足し合わせる（名前の変わったテンプレートを 1 つにまとめるときに使う）。"""
        self.runs += other.runs
        for attr in ("status", "launch"):
            mine = getattr(self, attr)
            for k, v in getattr(other, attr).items():
                mine[k] = mine.get(k, 0) + v
        self.users |= other.users
        self.templates |= other.templates
        self.el_sum += other.el_sum
        self.el_n += other.el_n
        if other.el_max is not None:
            self.el_max = other.el_max if self.el_max is None else max(self.el_max, other.el_max)
        if other.first and (self.first is None or other.first < self.first):
            self.first = other.first
        if other.last and (self.last is None or other.last > self.last):
            self.last = other.last

    def result(self):
        st = self.status
        finished = sum(st.get(s, 0) for s in FINAL)
        r = {
            "runs": self.runs,
            "successful": st.get("successful", 0),
            "failed": st.get("failed", 0),
            "error": st.get("error", 0),
            "canceled": st.get("canceled", 0),
            "success_rate": round(st.get("successful", 0) * 100 / finished, 1) if finished else None,
            "failure_rate": round((st.get("failed", 0) + st.get("error", 0)) * 100 / finished, 1) if finished else None,
            "avg_elapsed": round(self.el_sum / self.el_n, 1) if self.el_n else None,
            "max_elapsed": round(self.el_max, 1) if self.el_max is not None else None,
            "total_elapsed": round(self.el_sum, 1),
            **{f"launch_{t}": self.launch.get(t, 0) for t in LAUNCH_TYPES},
            "launch_other": self.launch.get("other", 0),
            "manual_users": len(self.users),
            "first_run": self.first,
            "last_run": self.last,
            "template_count": len(self.templates),
        }
        return r


def _grouped(conn, key_exprs, where, args, with_user=False, with_template=True, from_sql="jobs"):
    """key_exprs ごとに小計を取り、{key: Acc} を返す。"""
    keys = ", ".join(f"{e} AS k{i}" for i, e in enumerate(key_exprs))
    user = "CASE WHEN launch_type IN ('manual', 'relaunch') THEN launched_by END" if with_user else "NULL"
    tkey = _TEMPLATE_KEY if with_template else "NULL"
    gkeys = ", ".join(f"k{i}" for i in range(len(key_exprs)))
    q = f"""
        SELECT {keys}, status, launch_type, {user} AS u, {tkey} AS t,
               COUNT(*), SUM(elapsed), COUNT(elapsed), MAX(elapsed), MIN(created), MAX(created)
        FROM {from_sql} WHERE {where}
        GROUP BY {gkeys}, status, launch_type, u, t
    """
    n = len(key_exprs)
    accs = {}
    for row in conn.execute(q, args):
        key = tuple(row[:n])
        a = accs.get(key)
        if a is None:
            a = accs[key] = Acc()
        a.add(*row[n:])
    return accs


def totals(accs):
    """全体の件数と、最初と最後のジョブの日時。"""
    count = sum(a.runs for a in accs.values())
    first = min((a.first for a in accs.values() if a.first), default=None)
    last = max((a.last for a in accs.values() if a.last), default=None)
    return count, first, last


def template_stats(conn, where, args):
    # 名前と組織も GROUP BY に加えて一度の走査で取り、名前が途中で変わったテンプレートは後で 1 つにまとめる。
    # 名前と組織は、最後に実行したときのものを使う
    key = ["kind", "template_id", "CASE WHEN template_id IS NULL THEN template_name END", "template_name", "organization_name"]
    parts = _grouped(conn, key, where, args, with_user=True, with_template=False)
    accs, names = {}, {}
    for (kind, tid, deleted, name, org), a in parts.items():
        k = (kind, tid, deleted)
        if k in accs:
            accs[k].merge(a)
        else:
            accs[k] = a
        if k not in names or (a.last or "") > names[k][0]:
            names[k] = (a.last or "", name, org)
    rows = []
    for k, a in accs.items():
        r = a.result()
        r.pop("template_count")
        _, name, org = names[k]
        rows.append({"template_type": TEMPLATE_TYPE.get(k[0], k[0]), "template_id": k[1], "template": name, "organization": org, **r, "template_deleted": k[1] is None})
    rows.sort(key=lambda r: (-r["runs"], r["template"] or ""))
    return rows, totals(accs)


def _group_rows(accs, label):
    rows = []
    for k, a in accs.items():
        rows.append({"group": label(k), **a.result(), "_key": k})
    return rows


def resource_stats(conn, group, where, args):
    id_col, name_col = RESOURCE_GROUPS[group]
    accs = _grouped(conn, [id_col], where, args)
    names = dict(conn.execute(f"SELECT {id_col}, MAX({name_col}) FROM jobs WHERE {where} GROUP BY {id_col}", args).fetchall())
    rows = _group_rows(accs, lambda k: names.get(k[0]) or (f"ID {k[0]}" if k[0] is not None else "（なし）"))
    rows.sort(key=lambda r: (-r["runs"], r["group"]))
    return rows, totals(accs)


def credential_stats(conn, where, args):
    # 1 つのジョブが複数の認証情報を持つため、json_each で展開する。認証情報のないジョブは「（なし）」にまとめる。
    # 1 つのジョブが複数の行に数えられるため、全体の件数は展開前の表から別に数える
    from_sql = "jobs LEFT JOIN json_each(jobs.credentials) AS c"
    cid = "json_extract(c.value, '$[0]')"
    accs = _grouped(conn, [cid], where, args, from_sql=from_sql)
    names = dict(conn.execute(f"SELECT {cid}, MAX(json_extract(c.value, '$[1]')) FROM {from_sql} WHERE {where} GROUP BY 1", args).fetchall())
    rows = _group_rows(accs, lambda k: (names.get(k[0]) or f"ID {k[0]}") if k[0] is not None else "（なし）")
    rows.sort(key=lambda r: (-r["runs"], r["group"]))
    return rows, totals(_grouped(conn, ["1"], where, args, with_template=False))


def _bucket_function(group, tz):
    @lru_cache(maxsize=None)
    def by_minute(minute):
        dt = parse_dt(minute + ":00Z") if minute else None
        if dt is None:
            return None
        local = dt.astimezone(tz)
        if group == "day":
            return local.date().isoformat()
        if group == "week":
            return (local.date() - timedelta(days=local.weekday())).isoformat()
        if group == "month":
            return local.strftime("%Y-%m")
        if group == "hour":
            return f"{local.hour:02d}"
        return str(local.weekday())

    return by_minute


def time_stats(conn, group, tz, where, args):
    conn.create_function("local_bucket", 1, _bucket_function(group, tz), deterministic=True)
    accs = _grouped(conn, ["local_bucket(substr(created, 1, 16))"], where + " AND created IS NOT NULL", args)
    rows = []
    for (b,), a in sorted(accs.items(), key=lambda kv: kv[0][0] or ""):
        if group == "week":
            label = f"{b} の週"
        elif group == "hour":
            label = f"{b}時"
        elif group == "weekday":
            label = WEEKDAYS[int(b)]
        else:
            label = b
        rows.append({"group": label, **a.result()})
    return rows, totals(accs)
