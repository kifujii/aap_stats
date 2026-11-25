"""P5（sync と stats）の単体テスト。"""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from aapstats import cli
from aapstats.config import Settings
from aapstats.store import Store, to_row

from .test_p1 import GATEWAY_ROOT, make_client
from .test_p2 import BASE


def job(i, status="successful", template=10, name="patch", launch="manual", created=None, elapsed=10.0, user="alice", creds=None, org="Org1", org_id=1):
    return {
        "id": i,
        "name": name,
        "status": status,
        "failed": status in ("failed", "error"),
        "launch_type": launch,
        "created": created or f"2026-11-{(i % 28) + 1:02d}T01:00:00.000000Z",
        "elapsed": elapsed,
        "job_template": template,
        "organization": org_id,
        "execution_environment": 7,
        "summary_fields": {
            "job_template": {"name": name} if template else None,
            "organization": {"id": org_id, "name": org},
            "execution_environment": {"name": "ee"},
            "credentials": [{"id": c, "name": f"cred{c}"} for c in (creds or [])],
            "created_by": {"username": user} if user else None,
        },
    }


class FakeJobs:
    """id__gt / id__in / order_by=id に対応した jobs API。"""

    def __init__(self, items, fail_after=None):
        self.items = {j["id"]: j for j in items}
        self.fail_after = fail_after
        self.pages = 0

    def __call__(self, q):
        rows = sorted(self.items.values(), key=lambda j: j["id"])
        if "id__in" in q:
            ids = {int(x) for x in q["id__in"].split(",")}
            rows = [j for j in rows if j["id"] in ids]
        if "id__gt" in q:
            rows = [j for j in rows if j["id"] > int(q["id__gt"])]
        if "created__gte" in q:
            rows = [j for j in rows if j["created"] >= q["created__gte"]]
        size = int(q.get("page_size", 200))
        if size > 1:
            self.pages += 1
            if self.fail_after is not None and self.pages > self.fail_after:
                return 500, {}
        page = rows[:size]
        return 200, {"count": len(rows), "next": "more" if len(rows) > size else None, "results": page}


def routes(jobs_api, wf_api=None):
    return {"/api/": GATEWAY_ROOT, BASE + "jobs/": jobs_api, BASE + "workflow_jobs/": wf_api or FakeJobs([])}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "h.sqlite")

    def run_cli(self, argv, rts):
        c, _ = make_client(rts)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("aapstats.cli.load_settings", return_value=c.settings), mock.patch("aapstats.client.AapClient", return_value=c), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err), mock.patch("aapstats.client.time.sleep"), mock.patch("aapstats.commands.sync.PAGE_SIZE", 2):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def ids(self):
        con = sqlite3.connect(self.db)
        try:
            return [r[0] for r in con.execute("SELECT id FROM jobs WHERE kind='job' ORDER BY id")]
        finally:
            con.close()


class TestToRow(unittest.TestCase):
    def test_deleted_template_uses_job_name(self):
        r = to_row("job", job(1, template=None, name="old-tmpl"), "now")
        self.assertIsNone(r["template_id"])
        self.assertEqual(r["template_name"], "old-tmpl")

    def test_credentials_and_user(self):
        r = to_row("job", job(1, creds=[3, 4]), "now")
        self.assertEqual(json.loads(r["credentials"]), [[3, "cred3"], [4, "cred4"]])
        self.assertEqual(r["launched_by"], "alice")


class TestSync(Base):
    def test_initial_then_incremental(self):
        api = FakeJobs([job(i) for i in range(1, 6)])
        code, out, _ = self.run_cli(["sync", "--db", self.db, "--format", "json"], routes(api))
        self.assertEqual(code, 0)
        self.assertEqual(self.ids(), [1, 2, 3, 4, 5])
        self.assertEqual(json.loads(out)["sync"][0]["fetched"], 5)
        api.items[6] = job(6)
        code, out, _ = self.run_cli(["sync", "--db", self.db, "--format", "json"], routes(api))
        self.assertEqual(json.loads(out)["sync"][0]["fetched"], 1)
        self.assertEqual(self.ids(), [1, 2, 3, 4, 5, 6])

    def test_resume_after_failure(self):
        api = FakeJobs([job(i) for i in range(1, 8)], fail_after=2)
        code, _, err = self.run_cli(["sync", "--db", self.db], routes(api))
        self.assertEqual(code, 1)  # 3 ページ目で失敗
        self.assertEqual(self.ids(), [1, 2, 3, 4])  # 2 ページ分はコミット済み
        api.fail_after = None
        code, _, _ = self.run_cli(["sync", "--db", self.db], routes(api))
        self.assertEqual(code, 0)
        self.assertEqual(self.ids(), list(range(1, 8)))

    def test_refresh_unfinished(self):
        api = FakeJobs([job(1), job(2, status="running")])
        self.run_cli(["sync", "--db", self.db], routes(api))
        api.items[2] = job(2, status="failed")
        code, out, _ = self.run_cli(["sync", "--db", self.db, "--format", "json"], routes(api))
        self.assertEqual(json.loads(out)["sync"][0]["refreshed"], 1)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT status FROM jobs WHERE id=2").fetchone()[0], "failed")
        con.close()

    def test_rejects_other_host(self):
        s = Store(self.db)
        s.check_host("https://other.example.com")
        s.close()
        code, _, err = self.run_cli(["sync", "--db", self.db], routes(FakeJobs([job(1)])))
        self.assertEqual(code, 2)
        self.assertIn("別の接続先", err)

    def test_since_only_on_first_sync(self):
        api = FakeJobs([job(1, created="2026-07-01T00:00:00.000000Z"), job(2, created="2026-09-01T00:00:00.000000Z")])
        self.run_cli(["sync", "--db", self.db, "--since", "2026-08-01"], routes(api))
        self.assertEqual(self.ids(), [2])


class TestStats(Base):
    def setUp(self):
        super().setUp()
        s = Store(self.db)
        s.check_host("https://aap.example.com")
        rows = [
            job(1, launch="manual", user="alice", elapsed=10, creds=[3], created="2026-11-02T01:00:00.000000Z"),  # 月曜 10 時（日本時間）
            job(2, launch="scheduled", user=None, elapsed=20, creds=[3], created="2026-11-03T01:00:00.000000Z"),
            job(3, status="failed", launch="relaunch", user="bob", elapsed=30, created="2026-11-03T16:00:00.000000Z"),  # 日本時間では 11/4
            job(4, template=None, name="gone", launch="workflow", user=None, elapsed=5, created="2026-11-10T01:00:00.000000Z"),
            job(5, template=20, name="other", status="running", elapsed=None, org="Org2", org_id=2, created="2026-11-10T02:00:00.000000Z"),
        ]
        s.upsert([to_row("job", r, "now") for r in rows])
        s.set_meta("last_sync", "2026-11-25T00:00:00.000000Z")
        s.conn.commit()
        s.close()

    def stats(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        settings = Settings(host=None, timezone="Asia/Tokyo")
        with mock.patch("aapstats.cli.load_settings", return_value=settings), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = cli.main(["stats", "--db", self.db, "--format", "json", *extra])
        return code, json.loads(out.getvalue()) if code == 0 else None, err.getvalue()

    def test_offline_and_template_stats(self):
        with mock.patch("aapstats.client.AapClient", side_effect=AssertionError("接続してはいけない")):
            code, data, _ = self.stats()
        self.assertEqual(code, 0)
        head = {r["item"]: r["value"] for r in data["summary"]}
        self.assertEqual(head["job_count"], 5)
        rows = {r["template"]: r for r in data["stats"]}
        p = rows["patch"]
        self.assertEqual((p["runs"], p["successful"], p["failed"]), (3, 2, 1))
        self.assertEqual(p["success_rate"], 66.7)
        self.assertEqual((p["launch_manual"], p["launch_scheduled"], p["launch_relaunch"]), (1, 1, 1))
        self.assertEqual(p["manual_users"], 2)  # alice（手動）と bob（再実行）
        self.assertEqual((p["avg_elapsed"], p["max_elapsed"], p["total_elapsed"]), (20.0, 30.0, 60.0))
        self.assertTrue(rows["gone"]["template_deleted"])
        self.assertIsNone(rows["other"]["success_rate"])  # 終わったジョブがない

    def test_group_by_weekday_uses_timezone(self):
        _, data, _ = self.stats("--group-by", "weekday")
        self.assertEqual([(r["group"], r["runs"]) for r in data["stats"]], [("月", 1), ("火", 3), ("水", 1)])  # job3 は UTC では火曜、日本時間では水曜

    def test_group_by_credential_and_org(self):
        _, data, _ = self.stats("--group-by", "credential")
        self.assertEqual({r["group"]: r["runs"] for r in data["stats"]}, {"cred3": 2, "（なし）": 3})
        _, data, _ = self.stats("--group-by", "organization")
        self.assertEqual({r["group"]: r["runs"] for r in data["stats"]}, {"Org1": 4, "Org2": 1})

    def test_since_until_and_filters(self):
        _, data, err = self.stats("--since", "2026-11-04", "--until", "2026-11-10")
        self.assertEqual([r["template"] for r in data["stats"]], ["patch"])  # 11/4 0 時（日本時間）以降、11/10 より前
        _, data, err = self.stats("--since", "2026-01-01")
        self.assertIn("保存されていません", err)
        _, data, _ = self.stats("--org", "Org2")
        self.assertEqual([r["template"] for r in data["stats"]], ["other"])

    def test_top(self):
        _, data, _ = self.stats("--top", "1")
        self.assertEqual(data["top_runs"][0]["template"], "patch")
        self.assertEqual(data["top_avg_elapsed"][0]["template"], "patch")
        self.assertEqual(data["top_failure_rate"][0]["template"], "patch")
        _, data, _ = self.stats("--top", "5", "--by", "runs")
        self.assertEqual(list(data), ["summary", "top_runs"])

    def test_missing_db(self):
        code, _, err = self.stats("--db", os.path.join(self.tmp.name, "none.sqlite"))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
