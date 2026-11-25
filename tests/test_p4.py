"""P4（チーム、カスタム認証情報タイプ、Authentication Method、認証のマッピング）の単体テスト。"""

import json
import unittest

from aapstats.errors import PermissionDenied
from aapstats.resources import build_rows
from aapstats.resources_extra import sanitize

from .test_p1 import GATEWAY_ROOT, LEGACY_ROOT, make_client
from .test_p2 import BASE, page

GW = "/api/gateway/v1/"
LB = "/api/v2/"


class TestSanitize(unittest.TestCase):
    def test_masks_by_key_and_value(self):
        conf = {
            "SERVER_URI": ["ldap://x"],
            "BIND_PASSWORD": "plain-text-should-not-leak",
            "SECRET": "s",
            "CLIENT_KEY": "k",
            "nested": {"token": "t", "ok": 1, "other": "$encrypted$"},
            "list": ["a", "$encrypted$"],
            "USER_ATTR_MAP": {"email": "mail"},
        }
        out, masked = sanitize(conf)
        self.assertNotIn("plain-text-should-not-leak", json.dumps(out))
        self.assertEqual(out["SERVER_URI"], ["ldap://x"])
        self.assertEqual(out["USER_ATTR_MAP"], {"email": "mail"})
        self.assertEqual(out["nested"], {"ok": 1})
        self.assertEqual(out["list"], ["a"])
        self.assertEqual(sorted(masked), sorted(["BIND_PASSWORD", "SECRET", "CLIENT_KEY", "nested.token", "nested.other", "list[1]"]))


def gateway_routes():
    team = {"id": 7, "name": "ops", "organization": 1, "summary_fields": {"organization": {"name": "Org1"}, "resource": {"ansible_id": "uuid-ops"}}}
    return {
        "/api/": GATEWAY_ROOT,
        BASE + "teams/": page([team]),
        GW + "teams/": page([{"id": 3, "name": "ops", "summary_fields": {"resource": {"ansible_id": "uuid-ops"}}}]),
        GW + "teams/3/users/": {"count": 4, "results": []},
        BASE + "role_team_assignments/": page(
            [
                {"team": 7, "content_type": "awx.jobtemplate", "object_id": "19", "summary_fields": {"role_definition": {"name": "JobTemplate Execute"}, "content_object": {"name": "patch"}}},
                {"team": 7, "content_type": "awx.inventory", "object_id": "2", "summary_fields": {"role_definition": {"name": "Inventory Use"}, "content_object": {"name": "inv"}}},
            ]
        ),
        BASE + "credential_types/": page(
            [
                {
                    "id": 33,
                    "name": "my-api",
                    "kind": "cloud",
                    "inputs": {"fields": [{"id": "url", "type": "string", "default": "https://default"}, {"id": "token", "secret": True}]},
                    "injectors": {"env": {"API_TOKEN": "{{ token }}"}, "extra_vars": {"api_url": "{{ url }}"}},
                }
            ]
        ),
        BASE + "credentials/": page([{"id": 1, "name": "c1", "credential_type": 33}, {"id": 2, "name": "c2", "credential_type": 1}]),
        GW + "authenticators/": page(
            [
                {
                    "id": 2,
                    "name": "corp-ldap",
                    "type": "ansible_base.authentication.authenticator_plugins.ldap",
                    "enabled": True,
                    "order": 2,
                    "create_objects": True,
                    "remove_users": False,
                    "configuration": {
                        "SERVER_URI": ["ldap://ldap1", "ldap://ldap2"],
                        "BIND_DN": "cn=svc",
                        "BIND_PASSWORD": "$encrypted$",
                        "USER_SEARCH": ["ou=people,dc=x", "SCOPE_SUBTREE", "(uid=%(user)s)"],
                        "GROUP_TYPE": "GroupOfNamesType",
                    },
                }
            ]
        ),
        GW + "authenticator_maps/": page(
            [
                {"id": 10, "name": "admins-team", "authenticator": 2, "map_type": "team", "organization": "Org1", "team": "ops", "role": "Team Member", "triggers": {"groups": {"has_or": ["cn=ops,ou=groups,dc=x"]}}, "revoke": True, "order": 1},
            ]
        ),
    }


def rows(routes, resource):
    c, _ = make_client(routes)
    c.connect()
    return build_rows(c, resource)


class TestGateway(unittest.TestCase):
    def test_teams(self):
        (t,) = rows(gateway_routes(), "teams")
        self.assertEqual(t["member_count"], 4)
        self.assertEqual(t["role_count"], 2)
        self.assertEqual(t["roles"], ["Inventory Use: inv", "JobTemplate Execute: patch"])
        self.assertEqual(t["role_targets"][1], {"role": "JobTemplate Execute", "type": "jobtemplate", "id": 19, "name": "patch"})

    def test_credential_types_only_names(self):
        (t,) = rows(gateway_routes(), "credential_types")
        self.assertEqual(t["input_fields"], ["url", "token"])
        self.assertEqual(t["secret_fields"], ["token"])
        self.assertEqual(t["injector_env"], ["API_TOKEN"])
        self.assertEqual(t["injector_extra_vars"], ["api_url"])
        self.assertEqual(t["credentials"], ["c1"])
        self.assertNotIn("https://default", json.dumps(t))  # 入力項目の既定値は出さない

    def test_authenticators(self):
        (a,) = rows(gateway_routes(), "authenticators")
        self.assertEqual(a["type"], "ldap")
        self.assertEqual(a["server_uri"], ["ldap://ldap1", "ldap://ldap2"])
        self.assertEqual(a["user_search"], "ou=people,dc=x SCOPE_SUBTREE (uid=%(user)s)")
        self.assertEqual(a["masked_keys"], ["BIND_PASSWORD"])
        self.assertNotIn("BIND_PASSWORD", a["configuration"])
        self.assertEqual(a["map_count"], 1)

    def test_authenticator_maps(self):
        (m,) = rows(gateway_routes(), "authenticator_maps")
        self.assertEqual(m["authenticator"], "corp-ldap")
        self.assertEqual((m["map_type"], m["organization"], m["team"]), ("team", "Org1", "ops"))
        self.assertTrue(m["revoke"])

    def test_every_column_has_value_key_and_label(self):
        from aapstats.i18n import LABELS_JA
        from aapstats.resources import SPECS

        for name in ("teams", "credential_types", "authenticators", "authenticator_maps"):
            with self.subTest(resource=name):
                out = rows(gateway_routes(), name)
                self.assertEqual(set(SPECS[name].all_columns) - set(out[0]), set())
                self.assertEqual({c for c in SPECS[name].all_columns if c not in LABELS_JA}, set())

    def test_authenticators_permission_message(self):
        r = gateway_routes()
        r[GW + "authenticators/"] = lambda q: (403, {})
        with self.assertRaises(PermissionDenied) as cm:
            rows(r, "authenticators")
        self.assertIn("システム管理者", str(cm.exception))


def legacy_routes():
    return {
        "/api/": LEGACY_ROOT,
        LB + "teams/": page([{"id": 5, "name": "ops", "summary_fields": {"organization": {"name": "Org1"}}, "related": {"roles": LB + "teams/5/roles/"}}]),
        LB + "teams/5/roles/": page([{"name": "Execute", "summary_fields": {"resource_type": "job_template", "resource_id": 19, "resource_name": "patch"}}]),
        LB + "teams/5/users/": {"count": 2, "results": []},
        LB + "settings/ldap/": {
            "AUTH_LDAP_SERVER_URI": "ldap://primary",
            "AUTH_LDAP_BIND_DN": "cn=svc",
            "AUTH_LDAP_BIND_PASSWORD": "$encrypted$",
            "AUTH_LDAP_USER_SEARCH": ["ou=people", "SCOPE_SUBTREE", "(uid=%(user)s)"],
            "AUTH_LDAP_ORGANIZATION_MAP": {"Org1": {"admins": "cn=admins", "users": True}},
            "AUTH_LDAP_TEAM_MAP": {"ops": {"organization": "Org1", "users": "cn=ops", "remove": True}},
            "AUTH_LDAP_1_SERVER_URI": "ldap://secondary",
            "AUTH_LDAP_1_BIND_PASSWORD": "$encrypted$",
            "AUTH_LDAP_2_SERVER_URI": "",
        },
    }


class TestLegacy(unittest.TestCase):
    def test_teams_from_roles_endpoint(self):
        (t,) = rows(legacy_routes(), "teams")
        self.assertEqual(t["member_count"], 2)
        self.assertEqual(t["roles"], ["Execute: patch"])

    def test_ldap_servers_from_settings(self):
        out = rows(legacy_routes(), "authenticators")
        self.assertEqual([a["name"] for a in out], ["LDAP（既定）", "LDAP 1"])
        primary = out[0]
        self.assertEqual(primary["server_uri"], "ldap://primary")
        self.assertEqual(primary["masked_keys"], ["BIND_PASSWORD"])
        self.assertEqual(primary["map_count"], 2)
        self.assertNotIn("1_SERVER_URI", primary["configuration"])  # 追加サーバーの設定が混ざらない

    def test_ldap_maps_from_settings(self):
        out = rows(legacy_routes(), "authenticator_maps")
        self.assertEqual({(m["map_type"], m["name"], m["organization"]) for m in out}, {("organization", "Org1", "Org1"), ("team", "ops", "Org1")})
        team = [m for m in out if m["map_type"] == "team"][0]
        self.assertEqual(team["triggers"], {"users": "cn=ops", "remove": True})


if __name__ == "__main__":
    unittest.main()
