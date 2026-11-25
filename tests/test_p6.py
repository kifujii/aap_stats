"""P6（report: 移行判定）の単体テスト。"""

import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from aapstats import cli

from .test_p1 import GATEWAY_ROOT, make_client
from .test_p2 import BASE, page

GW = "/api/gateway/v1/"
NOW = datetime.now(timezone.utc)


def ago(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def jt(i, name, last=None, project=5, inventory=None, creds=(), ee=None, org=1):
    return {
        "id": i,
        "name": name,
        "last_job_run": last,
        "project": project,
        "inventory": inventory,
        "execution_environment": ee,
        "organization": org,
        "summary_fields": {"organization": {"name": "Org1"}, "credentials": [{"id": c} for c in creds]},
    }


def wf(i, name, last=None):
    return {"id": i, "name": name, "last_job_run": last, "organization": 1, "summary_fields": {"organization": {"name": "Org1"}}}


def node(wf_id, ujt):
    return {"workflow_job_template": wf_id, "unified_job_template": ujt}


TEMPLATES = [
    jt(10, "active", last=ago(5), inventory=3, creds=[20]),
    jt(11, "all-failed", last=ago(3), project=6),
    jt(12, "sched-only", last=ago(200)),
    jt(13, "disabled-sched", last=ago(200)),
    jt(14, "in-dead-wf", last=None, project=6, creds=[21]),
    jt(15, "in-live-wf", last=ago(150)),
    jt(16, "never", last=None, project=6, ee=8),
]
WORKFLOWS = [wf(30, "live-wf", last=ago(1)), wf(31, "dead-wf", last=None), wf(32, "nested-dead", last=None)]
NODES = [node(30, 15), node(31, 14), node(32, 31)]


def routes():
    def jobs(q):
        if q.get("order_by") == "created":
            return 200, page([{"created": ago(120)}])
        if q.get("order_by") == "-id":  # 最後のジョブの実行環境
            return 200, page([{"id": 1, "execution_environment": 7}])
        tid = q.get("job_template")
        runs = {"10": 4, "11": 2}.get(tid, 0)
        if q.get("status") == "successful":
            return 200, {"count": {"10": 3, "11": 0}.get(tid, 0), "results": []}
        return 200, {"count": runs, "results": []}

    def wf_jobs(q):
        if q.get("status") == "successful":
            return 200, {"count": 1, "results": []}
        return 200, {"count": 1 if q.get("workflow_job_template") == "30" else 0, "results": []}

    return {
        "/api/": GATEWAY_ROOT,
        BASE + "job_templates/": page(TEMPLATES),
        BASE + "workflow_job_templates/": page(WORKFLOWS),
        BASE + "workflow_job_template_nodes/": page(NODES),
        BASE + "schedules/": page(
            [
                {"name": "nightly", "unified_job_template": 12, "enabled": True},
                {"name": "off", "unified_job_template": 13, "enabled": False},
            ]
        ),
        BASE + "projects/": page(
            [
                {"id": 5, "name": "main-proj", "credential": 22, "summary_fields": {}},
                {"id": 6, "name": "old-proj", "credential": 23, "summary_fields": {}},
                {"id": 9, "name": "src-proj", "summary_fields": {}},
            ]
        ),
        BASE + "inventories/": page([{"id": 3, "name": "inv", "summary_fields": {}}, {"id": 4, "name": "unused-inv", "summary_fields": {}}]),
        BASE + "inventory_sources/": page([{"id": 50, "name": "src", "inventory": 3, "source_project": 9, "credential": 24}]),
        BASE + "credentials/": page(
            [
                {"id": 20, "name": "ssh", "credential_type": 1},
                {"id": 21, "name": "dead-cred", "credential_type": 90},
                {"id": 22, "name": "scm-main", "credential_type": 2},
                {"id": 23, "name": "scm-old", "credential_type": 2},
                {"id": 24, "name": "src-cred", "credential_type": 3},
                {"id": 25, "name": "galaxy", "credential_type": 4},
                {"id": 26, "name": "builtin", "credential_type": 4, "managed": True},
            ]
        ),
        BASE + "credential_types/": page([{"id": 90, "name": "my-type"}]),
        BASE + "execution_environments/": page(
            [{"id": 7, "name": "installer-default"}, {"id": 8, "name": "never-ee"}, {"id": 3, "name": "control-plane", "managed": True}]
        ),
        BASE + "organizations/": page([{"id": 1, "name": "Org1", "related": {"galaxy_credentials": BASE + "organizations/1/galaxy_credentials/"}}]),
        BASE + "organizations/1/galaxy_credentials/": page([{"id": 25}]),
        BASE + "settings/system/": {"DEFAULT_EXECUTION_ENVIRONMENT": None},
        BASE + "teams/": page(
            [
                {"id": 1, "name": "ldap-team", "summary_fields": {"resource": {"ansible_id": "a1"}}},
                {"id": 2, "name": "dead-team", "summary_fields": {"resource": {"ansible_id": "a2"}}},
                {"id": 3, "name": "org-team", "summary_fields": {"resource": {"ansible_id": "a3"}}},
                {"id": 4, "name": "empty-team", "summary_fields": {"resource": {"ansible_id": "a4"}}},
            ]
        ),
        GW + "teams/": page([]),
        BASE + "role_team_assignments/": page(
            [
                {"team": 2, "content_type": "awx.jobtemplate", "object_id": "16", "summary_fields": {"role_definition": {"name": "JobTemplate Execute"}, "content_object": {"name": "never"}}},
                {"team": 3, "content_type": "shared.organization", "object_id": "1", "summary_fields": {"role_definition": {"name": "Organization Member"}, "content_object": {"name": "Org1"}}},
            ]
        ),
        GW + "authenticator_maps/": page([{"id": 1, "map_type": "team", "team": "ldap-team", "organization": "Org1"}]),
        BASE + "jobs/": jobs,
        BASE + "workflow_jobs/": wf_jobs,
    }


def run_report(*argv, rts=None):
    c, _ = make_client(rts or routes())
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("aapstats.cli.load_settings", return_value=c.settings), mock.patch("aapstats.client.AapClient", return_value=c), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
        code = cli.main(["report", *argv])
    return code, out.getvalue(), err.getvalue()


def judged(*argv):
    code, out, err = run_report("--format", "json", *argv)
    assert code == 0, err
    data = json.loads(out)
    return {(i["resource_type"], i["name"]): i for i in data["items"]}, data


class TestTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items, cls.data = judged()

    def code(self, rtype, name):
        return self.items[(rtype, name)]["classification_code"]

    def test_active_is_migrate(self):
        self.assertEqual(self.code("job_template", "active"), "migrate")

    def test_all_failed_is_review(self):
        i = self.items[("job_template", "all-failed")]
        self.assertEqual(i["classification_code"], "review")
        self.assertIn("すべて失敗", i["reason"])

    def test_enabled_schedule_is_review_but_disabled_is_not(self):
        self.assertEqual(self.code("job_template", "sched-only"), "review")
        self.assertIn("nightly", self.items[("job_template", "sched-only")]["reason"])
        self.assertEqual(self.code("job_template", "disabled-sched"), "unneeded")

    def test_workflow_propagation(self):
        self.assertEqual(self.code("job_template", "in-live-wf"), "review")
        self.assertEqual(self.code("workflow_job_template", "dead-wf"), "unneeded")  # 呼び出し元 nested-dead も不要
        self.assertEqual(self.code("job_template", "in-dead-wf"), "unneeded")
        self.assertIn("dead-wf", self.items[("job_template", "in-dead-wf")]["reason"])
        self.assertEqual(self.code("job_template", "never"), "unneeded")

    def test_related_resources(self):
        c = self.code
        self.assertEqual(c("project", "main-proj"), "migrate")  # active から
        self.assertEqual(c("project", "old-proj"), "review")  # all-failed（要確認）が最も強い
        self.assertEqual(c("project", "src-proj"), "migrate")  # 移行対象のインベントリのソース
        self.assertEqual(c("inventory", "inv"), "migrate")
        self.assertEqual(c("inventory", "unused-inv"), "unneeded")
        self.assertEqual(c("credential", "scm-main"), "migrate")  # 移行対象のプロジェクトの SCM 認証情報
        self.assertEqual(c("credential", "scm-old"), "review")
        self.assertEqual(c("credential", "src-cred"), "migrate")  # インベントリソースの認証情報
        self.assertEqual(c("credential", "galaxy"), "migrate")  # 組織の Galaxy 認証情報
        self.assertEqual(c("credential", "dead-cred"), "unneeded")
        self.assertEqual(c("credential_type", "my-type"), "unneeded")  # 移行不要候補の認証情報からだけ
        self.assertNotIn(("credential", "builtin"), self.items)  # 組み込みは判定しない
        self.assertNotIn(("execution_environment", "control-plane"), self.items)

    def test_execution_environment_from_last_job(self):
        i = self.items[("execution_environment", "installer-default")]
        self.assertEqual(i["classification_code"], "migrate")
        self.assertIn("最後のジョブ", i["reason"])
        self.assertEqual(self.code("execution_environment", "never-ee"), "unneeded")

    def test_teams(self):
        c = self.code
        self.assertEqual(c("team", "ldap-team"), "migrate")
        self.assertIn("マッピング", self.items[("team", "ldap-team")]["reason"])
        self.assertEqual(c("team", "dead-team"), "unneeded")
        self.assertEqual(c("team", "org-team"), "migrate")
        self.assertEqual(c("team", "empty-team"), "unneeded")

    def test_summary_and_conditions(self):
        summ = {r["resource_label"]: r for r in self.data["summary"]}
        jt_sum = summ["ジョブテンプレート"]
        self.assertEqual((jt_sum["count"], jt_sum["migrate"], jt_sum["review"], jt_sum["unneeded"]), (7, 1, 3, 3))
        cond = {r["item"]: r["value"] for r in self.data["conditions"]}
        self.assertEqual(cond["unused_days"], 90)


class TestOptions(unittest.TestCase):
    def test_unused_days_option(self):
        # 400 日にすると、200 日前・150 日前に実行したテンプレートも移行対象になる
        rts = routes()
        c, _ = make_client(rts)
        c.settings.unused_days = 400
        out = io.StringIO()
        with mock.patch("aapstats.cli.load_settings", return_value=c.settings), mock.patch("aapstats.client.AapClient", return_value=c), mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
            cli.main(["report", "--format", "json"])
        items = {(i["resource_type"], i["name"]): i for i in json.loads(out.getvalue())["items"]}
        self.assertEqual(items[("job_template", "sched-only")]["classification_code"], "migrate")

    def test_only_and_resource_filters_keep_full_summary(self):
        items, data = judged("--only", "unneeded", "--resource", "job_template")
        self.assertEqual({i["classification_code"] for i in items.values()}, {"unneeded"})
        self.assertEqual({i["resource_type"] for i in items.values()}, {"job_template"})
        self.assertGreater(len(data["summary"]), 1)

    def test_csv_has_empty_decision_column(self):
        code, out, _ = run_report("--format", "csv", "--lang", "ja")
        header = out.splitlines()[0]
        self.assertTrue(header.endswith("移行の判断"))
        self.assertTrue(all(line.endswith(",") for line in out.splitlines()[1:]))

    def test_no_failure_check(self):
        items, _ = judged("--no-failure-check")
        self.assertEqual(items[("job_template", "all-failed")]["classification_code"], "migrate")

    def test_bad_only(self):
        self.assertEqual(run_report("--only", "maybe")[0], 2)


if __name__ == "__main__":
    unittest.main()
