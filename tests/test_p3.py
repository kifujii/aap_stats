"""P3（絞り込み条件と deps）の単体テスト。"""

import argparse
import io
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from aapstats import cli, filters
from aapstats.errors import ConfigError

from .test_p1 import make_client
from .test_p2 import BASE, page, routes

NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)


def fargs(**kw):
    p = argparse.ArgumentParser()
    filters.add_arguments(p)
    a = p.parse_args([])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def jt(i, name, **kw):
    base = {"id": i, "name": name, "organization": "Org1", "last_job_run": None, "last_job_status": "never updated", "schedule_count": 0, "enabled_schedule_count": 0, "used_in_workflows": [], "created": "2026-01-01T00:00:00Z", "modified": "2026-06-01T00:00:00Z"}
    base.update(kw)
    return base


ROWS = [
    jt(1, "patch-linux", last_job_run="2026-11-20T00:00:00Z", last_job_status="successful", schedule_count=1, enabled_schedule_count=1, project_id=5, project="proj", credential_ids=[20], credentials=["ssh"]),
    jt(2, "patch-win", last_job_run="2026-07-01T00:00:00Z", last_job_status="failed", used_in_workflows=["wf"], organization="Org2", project_id=6, project="proj2", credential_ids=[21], credentials=["winrm"]),
    jt(3, "old-report", schedule_count=1, enabled_schedule_count=0, created="2025-01-01T00:00:00Z", project_id=5, project="proj", credential_ids=[], credentials=[]),
]


def names(rows):
    return [r["name"] for r in rows]


class TestApply(unittest.TestCase):
    def run_apply(self, resource="job_templates", **kw):
        return names(filters.apply([dict(r) for r in ROWS], fargs(**kw), resource, "Asia/Tokyo", NOW))

    def test_org_and_name(self):
        self.assertEqual(self.run_apply(org=["Org2"]), ["patch-win"])
        self.assertEqual(self.run_apply(name="PATCH"), ["patch-linux", "patch-win"])
        self.assertEqual(self.run_apply(name_regex=r"^old"), ["old-report"])

    def test_dates(self):
        self.assertEqual(self.run_apply(created_before="2025-12-31"), ["old-report"])
        self.assertEqual(self.run_apply(created_after="2026-01-01"), ["patch-linux", "patch-win"])

    def test_unused_days_includes_never_run(self):
        self.assertEqual(self.run_apply(unused_days=90), ["patch-win", "old-report"])
        self.assertEqual(self.run_apply(never_run=True), ["old-report"])

    def test_last_status(self):
        self.assertEqual(self.run_apply(last_status="failed"), ["patch-win"])
        self.assertEqual(self.run_apply(last_status="never"), ["old-report"])

    def test_orphan_counts_only_enabled_schedules(self):
        # old-report は無効なスケジュールしかないため孤立とみなす
        self.assertEqual(self.run_apply(orphan=True), ["old-report"])
        self.assertEqual(self.run_apply(no_schedule=True), ["patch-win"])

    def test_uses_by_name_and_id(self):
        self.assertEqual(self.run_apply(uses_project="proj"), ["patch-linux", "old-report"])
        self.assertEqual(self.run_apply(uses_project="6"), ["patch-win"])
        self.assertEqual(self.run_apply(uses_credential="ssh"), ["patch-linux"])
        self.assertEqual(self.run_apply(uses_credential="21"), ["patch-win"])

    def test_uses_project_on_workflow_is_empty(self):
        self.assertEqual(self.run_apply(resource="workflow_job_templates", uses_project="proj"), [])

    def test_sort_none_last_and_limit(self):
        self.assertEqual(self.run_apply(sort="-last_job_run"), ["patch-linux", "patch-win", "old-report"])
        self.assertEqual(self.run_apply(sort="last_job_run"), ["patch-win", "patch-linux", "old-report"])
        self.assertEqual(self.run_apply(sort="name", limit=1), ["old-report"])

    def test_min_max_runs(self):
        rows = [dict(r, run_count=n) for r, n in zip(ROWS, [10, 0, 3])]
        out = filters.apply(rows, fargs(max_runs=3), "job_templates", "Asia/Tokyo", NOW)
        self.assertEqual(names(out), ["patch-win", "old-report"])

    def test_unreferenced_excludes_managed(self):
        rows = [
            {"name": "used", "used_by_templates": 1, "used_by_projects": 0, "used_by_organizations": 0, "used_by_inventory_sources": 0},
            {"name": "free", "used_by_templates": 0, "used_by_projects": 0, "used_by_organizations": 0, "used_by_inventory_sources": 0},
            {"name": "builtin", "managed": True, "used_by_templates": 0, "used_by_projects": 0, "used_by_organizations": 0, "used_by_inventory_sources": 0},
        ]
        out = filters.apply(rows, fargs(unreferenced=True), "execution_environments", "Asia/Tokyo", NOW)
        self.assertEqual(names(out), ["free"])

    def test_unreferenced_project_considers_workflows(self):
        rows = [
            {"name": "wf-sync", "used_by_templates": 0, "used_by_inventory_sources": 0, "used_in_workflows": ["wf"]},
            {"name": "free", "used_by_templates": 0, "used_by_inventory_sources": 0, "used_in_workflows": []},
        ]
        self.assertEqual(names(filters.apply(rows, fargs(unreferenced=True), "projects", "Asia/Tokyo", NOW)), ["free"])


class TestValidate(unittest.TestCase):
    def test_template_only_options(self):
        with self.assertRaises(ConfigError):
            filters.validate(fargs(never_run=True), "projects")
        filters.validate(fargs(never_run=True), "workflow_job_templates")

    def test_unreferenced_only_for_resources(self):
        with self.assertRaises(ConfigError):
            filters.validate(fargs(unreferenced=True), "job_templates")

    def test_bad_regex(self):
        with self.assertRaises(ConfigError):
            filters.validate(fargs(name_regex="("), "projects")

    def test_bad_date(self):
        with self.assertRaises(ConfigError):
            filters.parse_date("2026/01/01", "Asia/Tokyo", "--since")


def p3_routes():
    r = routes()
    jobs_calls = []

    def jobs(q):
        jobs_calls.append(q)
        if q.get("order_by") == "created":
            return 200, page([{"created": "2026-10-01T00:00:00Z"}])
        counts = {"10": 7, "11": 0}
        return 200, {"count": counts.get(q.get("job_template"), 0), "results": []}

    r[BASE + "jobs/"] = jobs
    r[BASE + "credential_types/"] = page([{"id": 90, "name": "my-api"}])
    r[BASE + "credentials/"]["results"].append({"id": 21, "name": "vault", "organization": 1, "credential_type": 90, "summary_fields": {"credential_type": {"name": "my-api"}}})
    return r, jobs_calls


class TestCommands(unittest.TestCase):
    def run_cli(self, argv, rts=None):
        rts = rts or p3_routes()[0]
        c, _ = make_client(rts)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("aapstats.cli.load_settings", return_value=c.settings), mock.patch("aapstats.client.AapClient", return_value=c), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_list_run_count_and_since(self):
        rts, calls = p3_routes()
        code, out, err = self.run_cli(["list", "job_templates", "--since", "2026-09-01", "--min-runs", "1", "--format", "json"], rts)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([(d["name"], d["run_count"]) for d in data], [("patch", 7)])
        count_calls = [q for q in calls if "job_template" in q]
        self.assertEqual(len(count_calls), 2)
        self.assertEqual(count_calls[0]["created__gte"], "2026-08-31T15:00:00Z")  # 日本時間の 0 時
        self.assertIn("一部は削除済み", err)

    def test_list_rejects_unknown_sort(self):
        code, _, err = self.run_cli(["list", "projects", "--sort", "bogus"])
        self.assertEqual(code, 2)
        self.assertIn("bogus", err)

    def test_deps_job_template(self):
        code, out, _ = self.run_cli(["deps", "jt", "patch", "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        kinds = {(r["kind"], r["name"]) for r in data["resources"]}
        self.assertIn(("project", "proj"), kinds)
        self.assertIn(("inventory", "inv"), kinds)
        self.assertIn(("inventory_source", "aws-src"), kinds)
        self.assertIn(("credential", "ssh"), kinds)
        self.assertIn(("credential", "git"), kinds)  # プロジェクトの SCM 認証情報
        self.assertIn(("credential_type", "my-api"), kinds)  # カスタムの認証情報タイプ
        self.assertIn(("execution_environment", "ee"), kinds)
        self.assertIn(("schedule", "nightly"), kinds)
        self.assertNotIn(("job_template", "patch"), kinds)  # 自分自身は一覧に含めない

    def test_deps_workflow_collapses_repeats(self):
        code, out, _ = self.run_cli(["deps", "wfjt", "wf"])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("[ジョブテンプレート] patch (id=10)  … Playbook"), 1)
        self.assertIn("（上に展開済み）", out)

    def test_deps_not_found_and_bad_kind(self):
        self.assertEqual(self.run_cli(["deps", "jt", "nope"])[0], 2)
        self.assertEqual(self.run_cli(["deps", "project", "proj"])[0], 2)


if __name__ == "__main__":
    unittest.main()
