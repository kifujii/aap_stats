"""ジョブ履歴の手元保存（SQLite。R2-5、R5-1）。

差分の取り方:
- 新しいジョブ: 保存済みの最大 ID より大きいものを、ID の昇順に取得する（キーセット方式）。
  ページ番号でたどらないため、取得中に新しいジョブが増えても取りこぼしや重複が起きない
- 前回の取得時点で終わっていなかったジョブ: ID を指定して取り直す。
  ジョブの modified はジョブの終了時に更新されないため、「前回より後に更新されたもの」では取れない
- 1 ページごとにコミットするため、途中で止まっても次回は続きから取得できる

AAP 側で古い履歴が削除されても、保存済みの行は消さない。定期的に sync すれば、
AAP の保持期間より長い期間の統計を取れる。
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

FINAL_STATUSES = ("successful", "failed", "error", "canceled")

# 保存するジョブの種類 → (API のリソース名, テンプレートの項目名)
KINDS = {
    "job": ("jobs", "job_template"),
    "workflow_job": ("workflow_jobs", "workflow_job_template"),
    "project_update": ("project_updates", "project"),
    "inventory_update": ("inventory_updates", "inventory_source"),
}
DEFAULT_KINDS = ("job", "workflow_job")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    kind TEXT NOT NULL,
    id INTEGER NOT NULL,
    name TEXT,
    status TEXT,
    failed INTEGER,
    launch_type TEXT,
    created TEXT,
    started TEXT,
    finished TEXT,
    elapsed REAL,
    template_id INTEGER,
    template_name TEXT,
    organization_id INTEGER,
    organization_name TEXT,
    project_id INTEGER,
    project_name TEXT,
    inventory_id INTEGER,
    inventory_name TEXT,
    execution_environment_id INTEGER,
    execution_environment_name TEXT,
    credentials TEXT,
    launched_by TEXT,
    source_workflow_job_id INTEGER,
    synced_at TEXT,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs (created);
CREATE INDEX IF NOT EXISTS jobs_template ON jobs (kind, template_id);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

COLUMNS = [
    "kind",
    "id",
    "name",
    "status",
    "failed",
    "launch_type",
    "created",
    "started",
    "finished",
    "elapsed",
    "template_id",
    "template_name",
    "organization_id",
    "organization_name",
    "project_id",
    "project_name",
    "inventory_id",
    "inventory_name",
    "execution_environment_id",
    "execution_environment_name",
    "credentials",
    "launched_by",
    "source_workflow_job_id",
    "synced_at",
]


def default_db_path(profile=None, host=None):
    """既定の保存先。接続先ごとに分ける（移行元と移行先の履歴が混ざらないように）。"""
    base = Path(os.environ.get("AAPSTATS_DATA_DIR", "~/.local/share/aapstats")).expanduser()
    key = profile or (urlparse(host).netloc if host else None) or "default"
    key = re.sub(r"[^A-Za-z0-9._-]", "_", key)
    return base / f"{key}.sqlite"


def _sf(item, *path):
    cur = item.get("summary_fields") or {}
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def to_row(kind, item, now):
    """API のジョブ 1 件を、保存する行にする。"""
    tmpl_key = KINDS[kind][1]
    template_id = item.get(tmpl_key) or item.get("unified_job_template")
    template_name = _sf(item, tmpl_key, "name") or _sf(item, "unified_job_template", "name") or item.get("name")
    creds = [[c.get("id"), c.get("name")] for c in (_sf(item, "credentials") or [])]
    return {
        "kind": kind,
        "id": item["id"],
        "name": item.get("name"),
        "status": item.get("status"),
        "failed": 1 if item.get("failed") else 0,
        "launch_type": item.get("launch_type"),
        "created": item.get("created"),
        "started": item.get("started"),
        "finished": item.get("finished"),
        "elapsed": item.get("elapsed"),
        "template_id": template_id,
        "template_name": template_name,
        "organization_id": item.get("organization") if isinstance(item.get("organization"), int) else _sf(item, "organization", "id"),
        "organization_name": _sf(item, "organization", "name"),
        "project_id": item.get("project"),
        "project_name": _sf(item, "project", "name"),
        "inventory_id": item.get("inventory"),
        "inventory_name": _sf(item, "inventory", "name"),
        "execution_environment_id": item.get("execution_environment"),
        "execution_environment_name": _sf(item, "execution_environment", "name"),
        "credentials": json.dumps(creds, ensure_ascii=False),
        "launched_by": _sf(item, "created_by", "username"),
        "source_workflow_job_id": _sf(item, "source_workflow_job", "id"),
        "synced_at": now,
    }


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        try:
            os.chmod(self.path, 0o600)  # お客様環境のデータを含むため、本人だけが読めるようにする
        except OSError:
            pass

    def close(self):
        self.conn.close()

    # -------------------------------------------------------------- meta

    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def check_host(self, host):
        """別の接続先の履歴が混ざらないよう、保存先と接続先の組み合わせを確かめる。"""
        saved = self.get_meta("host")
        if saved is None:
            self.set_meta("host", host)
            self.conn.commit()
            return True
        return saved == host

    # -------------------------------------------------------------- jobs

    def upsert(self, rows):
        if not rows:
            return
        placeholders = ", ".join("?" for _ in COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c not in ("kind", "id"))
        self.conn.executemany(
            f"INSERT INTO jobs ({', '.join(COLUMNS)}) VALUES ({placeholders}) ON CONFLICT(kind, id) DO UPDATE SET {updates}",
            [[r[c] for c in COLUMNS] for r in rows],
        )

    def max_id(self, kind):
        row = self.conn.execute("SELECT MAX(id) AS m FROM jobs WHERE kind = ?", (kind,)).fetchone()
        return row["m"] or 0

    def unfinished_ids(self, kind):
        q = f"SELECT id FROM jobs WHERE kind = ? AND (status IS NULL OR status NOT IN ({', '.join('?' for _ in FINAL_STATUSES)}))"
        return [r["id"] for r in self.conn.execute(q, (kind, *FINAL_STATUSES))]

    def summary(self, kinds=None):
        q = "SELECT kind, COUNT(*) AS n, MIN(created) AS oldest, MAX(created) AS newest FROM jobs"
        args = []
        if kinds:
            q += f" WHERE kind IN ({', '.join('?' for _ in kinds)})"
            args = list(kinds)
        q += " GROUP BY kind ORDER BY kind"
        return [dict(r) for r in self.conn.execute(q, args)]

    def has_jobs(self, kinds):
        q = f"SELECT 1 FROM jobs WHERE kind IN ({', '.join('?' for _ in kinds)}) LIMIT 1"
        return self.conn.execute(q, list(kinds)).fetchone() is not None

    def stored_range(self):
        """保存済みの最も古い記録と最も新しい記録（日時のインデックスを使うため速い）。"""
        row = self.conn.execute("SELECT MIN(created), MAX(created) FROM jobs").fetchone()
        return row[0], row[1]

    def iter_jobs(self, kinds, since=None, until=None):
        q = f"SELECT * FROM jobs WHERE kind IN ({', '.join('?' for _ in kinds)})"
        args = list(kinds)
        if since:
            q += " AND created >= ?"
            args.append(since)
        if until:
            q += " AND created < ?"
            args.append(until)
        for r in self.conn.execute(q, args):
            yield dict(r)


def utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now_iso():
    return utc_iso(datetime.now(timezone.utc))
