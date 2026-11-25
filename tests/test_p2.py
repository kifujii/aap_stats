"""P2（list サブコマンド）の単体テスト。"""

import io
import json
import unittest
from unittest import mock

from aapstats import cli
from aapstats.i18n import LABELS_JA
from aapstats.resources import SPECS, build_rows

from .test_p1 import GATEWAY_ROOT, make_client

BASE = "/api/controller/v2/"


def page(items):
    return {"count": len(items), "next": None, "results": items}


def org(i, name):
    return {"id": i, "name": name}


def routes():
    jt = {
        "id": 10,
        "name": "patch",
        "organization": 1,
        "project": 5,
        "inventory": 3,
        "execution_environment": 7,
        "playbook": "site.yml",
        "status": "successful",
        "last_job_run": "2026-11-01T00:00:00Z",
        "webhook_credential": None,
        "summary_fields": {
            "organization": {"id": 1, "name": "Org1"},
            "project": {"id": 5, "name": "proj"},
            "inventory": {"id": 3, "name": "inv"},
            "execution_environment": {"id": 7, "name": "ee"},
            "credentials": [{"id": 20, "name": "ssh"}, {"id": 21, "name": "vault"}],
            "created_by": {"username": "alice"},
        },
    }
    jt_unused = {"id": 11, "name": "old", "organization": 1, "project": 5, "inventory": None, "status": "never updated", "summary_fields": {"organization": {"id": 1, "name": "Org1"}, "credentials": []}}
    wf = {"id": 30, "name": "wf", "organization": 1, "inventory": 3, "summary_fields": {"organization": {"id": 1, "name": "Org1"}}}
    nodes = [
        {"id": 1, "workflow_job_template": 30, "unified_job_template": 10, "summary_fields": {"workflow_job_template": {"name": "wf"}, "unified_job_template": {"name": "patch"}}},
        {"id": 2, "workflow_job_template": 30, "unified_job_template": 10, "summary_fields": {"workflow_job_template": {"name": "wf"}, "unified_job_template": {"name": "patch"}}},
        {"id": 3, "workflow_job_template": 30, "unified_job_template": None, "summary_fields": {"workflow_job_template": {"name": "wf"}}},
    ]
    schedules = [
        {"id": 100, "name": "nightly", "unified_job_template": 10, "enabled": True, "summary_fields": {"unified_job_template": {"name": "patch", "unified_job_type": "job"}}},
        {"id": 101, "name": "wf-weekly", "unified_job_template": 30, "enabled": False, "summary_fields": {"unified_job_template": {"name": "wf", "unified_job_type": "workflow_job"}}},
    ]
    projects = [{"id": 5, "name": "proj", "organization": 1, "credential": 22, "default_environment": 7, "scm_type": "", "summary_fields": {}}]
    sources = [{"id": 50, "name": "aws-src", "source": "ec2", "inventory": 3, "credential": 23, "execution_environment": None}]
    creds = [
        {"id": 20, "name": "ssh", "organization": 1, "inputs": {"username": "root", "password": "$encrypted$"}, "summary_fields": {"credential_type": {"name": "Machine"}}},
        {"id": 22, "name": "git", "organization": None, "inputs": {"username": "bot"}, "summary_fields": {"credential_type": {"name": "Source Control"}}},
        {"id": 24, "name": "galaxy", "organization": None, "inputs": {}, "summary_fields": {"credential_type": {"name": "Galaxy"}}},
        {"id": 25, "name": "orphan", "organization": 1, "inputs": {}, "summary_fields": {"credential_type": {"name": "Machine"}}},
    ]
    orgs = [dict(org(1, "Org1"), default_environment=7, related={"galaxy_credentials": BASE + "organizations/1/galaxy_credentials/"})]
    ees = [{"id": 7, "name": "ee", "organization": 1, "image": "quay.io/x/ee:1"}, {"id": 8, "name": "unused-ee", "organization": None}]
    invs = [{"id": 3, "name": "inv", "organization": 1, "kind": "", "total_hosts": 4, "summary_fields": {"organization": {"name": "Org1"}}}]
    return {
        "/api/": GATEWAY_ROOT,
        BASE + "job_templates/": page([jt, jt_unused]),
        BASE + "workflow_job_templates/": page([wf]),
        BASE + "workflow_job_template_nodes/": page(nodes),
        BASE + "schedules/": page(schedules),
        BASE + "projects/": page(projects),
        BASE + "inventories/": page(invs),
        BASE + "inventory_sources/": page(sources),
        BASE + "credentials/": page(creds),
        BASE + "organizations/": page(orgs),
        BASE + "organizations/1/galaxy_credentials/": page([{"id": 24}]),
        BASE + "execution_environments/": page(ees),
        BASE + "teams/": page([{"id": 1, "organization": 1}]),
    }


def rows_by_name(resource):
    c, _ = make_client(routes())
    c.connect()
    return {r["name"]: r for r in build_rows(c, resource)}


class TestBuildRows(unittest.TestCase):
    def test_job_templates(self):
        r = rows_by_name("job_templates")
        p = r["patch"]
        self.assertEqual(p["project"], "proj")
        self.assertEqual(p["credentials"], ["ssh", "vault"])
        self.assertEqual(p["schedule_count"], 1)
        self.assertEqual(p["used_in_workflows"], ["wf"])
        self.assertEqual(p["created_by"], "alice")
        self.assertEqual(r["old"]["schedule_count"], 0)
        self.assertEqual(r["old"]["used_in_workflows"], [])

    def test_workflow(self):
        w = rows_by_name("workflow_job_templates")["wf"]
        self.assertEqual(w["node_count"], 3)
        self.assertEqual(w["node_templates"], ["patch"])
        self.assertEqual(w["schedule_count"], 1)

    def test_project_counts(self):
        p = rows_by_name("projects")["proj"]
        self.assertEqual(p["used_by_templates"], 2)
        self.assertEqual(p["scm_type"], "manual")

    def test_inventory(self):
        i = rows_by_name("inventories")["inv"]
        self.assertEqual(i["used_by_templates"], 2)  # ジョブテンプレート 1 件とワークフロー 1 件
        self.assertEqual(i["kind"], "normal")
        self.assertEqual(i["inventory_sources"], ["aws-src (ec2)"])

    def test_credentials_usage_and_no_inputs(self):
        r = rows_by_name("credentials")
        self.assertEqual(r["ssh"]["used_by_templates"], 1)
        self.assertEqual(r["ssh"]["organization"], "Org1")
        self.assertEqual(r["git"]["used_by_projects"], 1)
        self.assertEqual(r["galaxy"]["used_by_organizations"], 1)
        self.assertEqual(
            [r["orphan"][k] for k in ("used_by_templates", "used_by_projects", "used_by_inventory_sources", "used_by_organizations")],
            [0, 0, 0, 0],
        )
        for row in r.values():
            self.assertNotIn("inputs", row)
            self.assertNotIn("root", json.dumps(row))

    def test_execution_environments(self):
        r = rows_by_name("execution_environments")
        self.assertEqual(r["ee"]["used_by_templates"], 1)
        self.assertEqual(r["ee"]["used_by_projects"], 1)
        self.assertEqual(r["ee"]["used_by_organizations"], 1)
        self.assertEqual(r["unused-ee"]["used_by_templates"], 0)

    def test_organizations(self):
        o = rows_by_name("organizations")["Org1"]
        self.assertEqual(o["job_template_count"], 2)
        self.assertEqual(o["credential_count"], 2)
        self.assertEqual(o["team_count"], 1)

    def test_every_column_has_value_key_and_label(self):
        p2 = ["job_templates", "workflow_job_templates", "schedules", "projects", "inventories", "credentials", "execution_environments", "organizations"]
        for name in p2:
            spec = SPECS[name]
            with self.subTest(resource=name):
                rows = list(rows_by_name(name).values())
                self.assertTrue(rows)
                self.assertEqual(set(spec.all_columns) - set(rows[0]), set())
                self.assertEqual({c for c in spec.all_columns if c not in LABELS_JA}, set())


class TestListCommand(unittest.TestCase):
    def run_cli(self, argv):
        c, _ = make_client(routes())
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("aapstats.cli.load_settings", return_value=c.settings), mock.patch("aapstats.client.AapClient", return_value=c), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_default_columns_table(self):
        code, out, _ = self.run_cli(["list", "job_templates"])
        self.assertEqual(code, 0)
        header = out.splitlines()[1]
        self.assertIn("last_job_status", header)
        self.assertNotIn("created_by", header)

    def test_columns_option_and_order(self):
        code, out, _ = self.run_cli(["list", "projects", "--format", "csv", "--columns", "used_by_templates,name"])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "\ufeffused_by_templates,name")

    def test_unknown_column(self):
        code, _, err = self.run_cli(["list", "projects", "--columns", "name,bogus"])
        self.assertEqual(code, 2)
        self.assertIn("bogus", err)

    def test_json_has_all_columns(self):
        code, out, _ = self.run_cli(["list", "credentials", "--format", "json"])
        data = json.loads(out)
        self.assertEqual(list(data[0]), SPECS["credentials"].all_columns)

    def test_lang_ja(self):
        code, out, _ = self.run_cli(["list", "organizations", "--lang", "ja", "--format", "csv"])
        self.assertTrue(out.startswith("\ufeffID,名前,ジョブテンプレート数"))


if __name__ == "__main__":
    unittest.main()
