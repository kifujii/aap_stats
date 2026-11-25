"""P1（接続の土台と info）の単体テスト。API の応答は模擬データで置き換える。"""

import argparse
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aapstats import cli
from aapstats.client import AapClient
from aapstats.config import Settings, load_settings
from aapstats.endpoints import GATEWAY, LEGACY, ApiLayout, detect_flavor
from aapstats.errors import ApiError, AuthError, ConfigError
from aapstats.output import Renderer, format_value, get_zone

GATEWAY_ROOT = {"description": "AAP gateway REST API", "apis": {"gateway": "/api/gateway/", "controller": "/api/controller/"}}
LEGACY_ROOT = {"description": "AWX REST API", "current_version": "/api/v2/", "available_versions": {"v2": "/api/v2/"}}


def ns(**kw):
    base = dict(config=None, profile=None, host=None, insecure=False, ca_file=None, api_version=None, tz=None, timeout=None, max_workers=None, request_interval=None)
    base.update(kw)
    return argparse.Namespace(**base)


class FakeResponse:
    def __init__(self, status, data, url="", headers=None):
        self.status_code = status
        self._data = data
        self.url = url
        self.headers = headers or {}

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    """URL のパスと問い合わせ条件から応答を返す模擬セッション。"""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.verify = True
        self.calls = []

    def get(self, url, params=None, timeout=None, **kw):
        from urllib.parse import parse_qs, urlparse

        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        q.update({k: str(v) for k, v in (params or {}).items()})
        self.calls.append((u.path, q))
        handler = self.routes.get(u.path)
        if handler is None:
            return FakeResponse(404, {"detail": "not found"}, url)
        status, data = handler(q) if callable(handler) else (200, handler)
        return FakeResponse(status, data, url)


def make_client(routes, **settings_kw):
    s = Settings(host="https://aap.example.com", token="dummy", **settings_kw)
    c = AapClient(s)
    session = FakeSession(routes)
    c._session = lambda: session  # 並行処理のスレッドからも模擬セッションを使う
    return c, session


class TestDetect(unittest.TestCase):
    def test_gateway(self):
        self.assertEqual(detect_flavor(GATEWAY_ROOT), GATEWAY)

    def test_legacy(self):
        self.assertEqual(detect_flavor(LEGACY_ROOT), LEGACY)

    def test_unknown(self):
        with self.assertRaises(ApiError):
            detect_flavor({"foo": "bar"})

    def test_paths(self):
        self.assertEqual(ApiLayout(GATEWAY).resource_path("job_templates"), "/api/controller/v2/job_templates/")
        self.assertEqual(ApiLayout(LEGACY).resource_path("job_templates"), "/api/v2/job_templates/")
        self.assertEqual(ApiLayout(GATEWAY).resource_path("authenticators"), "/api/gateway/v1/authenticators/")
        self.assertFalse(ApiLayout(LEGACY).supports("authenticators"))
        with self.assertRaises(ApiError):
            ApiLayout(LEGACY).resource_path("authenticators")


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def write(self, text):
        p = self.dir / "config.ini"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_env(self):
        os.environ.update(AAP_HOST="aap.example.com/", AAP_TOKEN="t")
        s = load_settings(ns(config=str(self.dir / "none.ini")))
        self.assertEqual(s.host, "https://aap.example.com")
        self.assertEqual(s.token, "t")

    def test_profile_with_token_file(self):
        tok = self.dir / "src.token"
        tok.write_text("secret\n", encoding="utf-8")
        cfg = self.write(f"[source]\nhost = https://a.example.com\ntoken_file = {tok}\nverify_ssl = false\n")
        s = load_settings(ns(config=cfg, profile="source"))
        self.assertEqual(s.token, "secret")
        self.assertFalse(s.verify_ssl)
        self.assertEqual(s.profile, "source")

    def test_profile_beats_env_and_cli_beats_profile(self):
        os.environ.update(AAP_HOST="https://env.example.com", AAP_TOKEN="t")
        cfg = self.write("[p]\nhost = https://profile.example.com\n")
        self.assertEqual(load_settings(ns(config=cfg, profile="p")).host, "https://profile.example.com")
        self.assertEqual(load_settings(ns(config=cfg, profile="p", host="https://cli.example.com")).host, "https://cli.example.com")

    def test_missing_profile(self):
        cfg = self.write("[a]\nhost = x\n")
        with self.assertRaises(ConfigError) as cm:
            load_settings(ns(config=cfg, profile="b"))
        self.assertIn("登録済み: a", str(cm.exception))

    def test_no_credentials(self):
        os.environ.update(AAP_HOST="https://a")
        with self.assertRaises(ConfigError):
            load_settings(ns(config=str(self.dir / "none.ini")))

    def test_secret_not_in_repr(self):
        s = Settings(host="h", token="supersecret", password="pw")
        self.assertNotIn("supersecret", repr(s))
        self.assertNotIn("pw'", repr(s))

    def test_bad_api_version(self):
        os.environ.update(AAP_HOST="https://a", AAP_TOKEN="t", AAP_API_VERSION="v9")
        with self.assertRaises(ConfigError):
            load_settings(ns(config=str(self.dir / "none.ini")))


class TestClient(unittest.TestCase):
    def test_pagination_follows_next(self):
        def jt(q):
            if q.get("page") == "2":
                return 200, {"count": 3, "next": None, "results": [{"id": 3}]}
            return 200, {"count": 3, "next": "/api/controller/v2/job_templates/?page=2&page_size=200", "results": [{"id": 1}, {"id": 2}]}

        c, session = make_client({"/api/": GATEWAY_ROOT, "/api/controller/v2/job_templates/": jt})
        c.connect()
        self.assertEqual([r["id"] for r in c.list_resource("job_templates")], [1, 2, 3])
        self.assertEqual(session.calls[1][1]["page_size"], "200")

    def test_count_uses_page_size_1_and_default_params(self):
        c, session = make_client({"/api/": GATEWAY_ROOT, "/api/controller/v2/credential_types/": {"count": 7, "results": []}})
        c.connect()
        self.assertEqual(c.count_resource("credential_types"), 7)
        _, q = session.calls[-1]
        self.assertEqual(q["page_size"], "1")
        self.assertEqual(q["managed"], "false")

    def test_manual_api_version_skips_detection(self):
        c, session = make_client({}, api_version="legacy")
        self.assertEqual(c.connect().flavor, LEGACY)
        self.assertEqual(session.calls, [])

    def test_401(self):
        c, _ = make_client({"/api/": GATEWAY_ROOT, "/api/controller/v2/jobs/": lambda q: (401, {})})
        c.connect()
        with self.assertRaises(AuthError):
            c.count_resource("jobs")

    @mock.patch("aapstats.client.time.sleep")
    def test_retry_on_503(self, sleep):
        state = {"n": 0}

        def flaky(q):
            state["n"] += 1
            return (503, {}) if state["n"] < 3 else (200, {"count": 1, "results": []})

        c, _ = make_client({"/api/": GATEWAY_ROOT, "/api/controller/v2/jobs/": flaky})
        c.connect()
        self.assertEqual(c.count_resource("jobs"), 1)
        self.assertEqual(sleep.call_count, 2)


class TestOutput(unittest.TestCase):
    def test_datetime_to_jst(self):
        self.assertEqual(format_value("2026-11-25T06:10:13.030192Z", get_zone("Asia/Tokyo")), "2026-11-25 15:10:13")

    def test_csv_has_bom_and_ja_header(self):
        buf = io.StringIO()
        Renderer("csv", "ja", out=buf).rows([{"id": 1, "name": "月次"}], ["id", "name"])
        self.assertTrue(buf.getvalue().startswith("\ufeffID,名前\r\n"))

    def test_json_keeps_english_keys(self):
        buf = io.StringIO()
        Renderer("json", "ja", out=buf).rows([{"id": 1, "created": "2026-01-01T00:00:00Z"}], ["id", "created"])
        self.assertEqual(json.loads(buf.getvalue()), [{"id": 1, "created": "2026-01-01T00:00:00Z"}])

    def test_table_aligns_wide_chars(self):
        buf = io.StringIO()
        Renderer("table", out=buf).rows([{"name": "日本語"}, {"name": "abc"}], ["name"])
        self.assertTrue(buf.getvalue().count("|") >= 6)
        self.assertIn("| 日本語 |", buf.getvalue())
        self.assertIn("| abc    |", buf.getvalue())


class TestInfoCommand(unittest.TestCase):
    def routes(self):
        base = "/api/controller/v2/"

        def counted(n):
            return lambda q: (200, {"count": n, "next": None, "results": []})

        def history(q):
            if q.get("order_by") == "created":
                return 200, {"count": 10, "results": [{"created": "2026-08-01T00:00:00Z"}]}
            return 200, {"count": 10, "results": [{"created": "2026-11-24T00:00:00Z"}]}

        r = {
            "/api/": GATEWAY_ROOT,
            "/api/gateway/v1/ping/": {"version": "2.7"},
            base + "ping/": {"version": "4.7.0"},
            base + "me/": {"results": [{"username": "auditor", "is_superuser": False, "is_system_auditor": True}]},
            base + "jobs/": history,
            base + "workflow_jobs/": history,
            base + "system_job_templates/": {"count": 1, "next": None, "results": [{"name": "Cleanup Job Details", "related": {"schedules": base + "system_job_templates/1/schedules/"}}]},
            base + "system_job_templates/1/schedules/": {"count": 1, "next": None, "results": [{"name": "Cleanup Job Schedule", "enabled": True, "extra_data": {"days": "120"}, "next_run": "2026-11-30T00:00:00Z"}]},
            "/api/gateway/v1/authenticators/": lambda q: (403, {}),
        }
        for name in ["organizations", "job_templates", "workflow_job_templates", "schedules", "projects", "inventories", "credentials", "credential_types", "execution_environments", "teams"]:
            r[base + name + "/"] = counted(5)
        return r

    def test_info_json(self):
        c, _ = make_client(self.routes())
        buf = io.StringIO()
        with mock.patch("aapstats.cli.load_settings", return_value=c.settings), mock.patch("aapstats.client.AapClient", return_value=c), mock.patch("sys.stdout", buf):
            code = cli.main(["info", "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        conn = {r["item"]: r["value"] for r in data["connection"]}
        self.assertEqual(conn["gateway_version"], "2.7")
        self.assertEqual(conn["user_role"], "システム監査者")
        res = {r["resource"]: r for r in data["resources"]}
        self.assertEqual(res["job_templates"]["count"], 5)
        self.assertIsNone(res["authenticators"]["count"])
        self.assertIn("権限", res["authenticators"]["note"])
        self.assertEqual(data["cleanup"][0]["retention_days"], "120")
        self.assertEqual(data["history"][0]["oldest"], "2026-08-01T00:00:00Z")

    def test_info_table_runs(self):
        c, _ = make_client(self.routes())
        buf = io.StringIO()
        with mock.patch("aapstats.cli.load_settings", return_value=c.settings), mock.patch("aapstats.client.AapClient", return_value=c), mock.patch("sys.stdout", buf):
            code = cli.main(["info", "--lang", "ja"])
        self.assertEqual(code, 0)
        self.assertIn("■ リソースの件数", buf.getvalue())
        self.assertIn("件数", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
