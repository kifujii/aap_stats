"""プロファイルを指定しないときの挙動（R9-1、R9-2、R9-4）の単体テスト。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aapstats.config import load_settings
from aapstats.errors import ConfigError
from aapstats.store import default_db_path

from .test_p1 import ns


class TestDefaultProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, text):
        p = self.dir / "config.ini"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_missing_profile_lists_registered_profiles(self):
        cfg = self.config("[source]\nhost = https://a\ntoken = t\n\n[target]\nhost = https://b\ntoken = t\n")
        with self.assertRaises(ConfigError) as cm:
            load_settings(ns(config=cfg))
        msg = str(cm.exception)
        self.assertIn("プロファイルが指定されていません", msg)
        self.assertIn("source, target", msg)
        self.assertIn("[default]", msg)

    def test_lowercase_default_section(self):
        cfg = self.config("[default]\nhost = https://a.example.com\ntoken = t\n\n[target]\nhost = https://b\ntoken = t\n")
        s = load_settings(ns(config=cfg))
        self.assertEqual(s.host, "https://a.example.com")
        self.assertEqual(s.profile, "default")

    def test_uppercase_default_section(self):
        cfg = self.config("[DEFAULT]\nhost = https://a.example.com\ntoken = t\n")
        s = load_settings(ns(config=cfg))
        self.assertEqual(s.host, "https://a.example.com")
        self.assertEqual(s.profile, "default")

    def test_lowercase_wins_over_uppercase(self):
        cfg = self.config("[DEFAULT]\nhost = https://upper\ntoken = t\n\n[default]\nhost = https://lower\n")
        self.assertEqual(load_settings(ns(config=cfg)).host, "https://lower")

    def test_explicit_profile_still_inherits_uppercase_default(self):
        # configparser の仕様どおり、[DEFAULT] の値は名前付きのプロファイルにも引き継がれる
        cfg = self.config("[DEFAULT]\ntoken = shared\n\n[source]\nhost = https://a\n")
        s = load_settings(ns(config=cfg, profile="source"))
        self.assertEqual((s.host, s.token, s.profile), ("https://a", "shared", "source"))

    def test_env_only_without_profile(self):
        cfg = self.config("[source]\nhost = https://a\ntoken = t\n")
        os.environ.update(AAP_HOST="https://env.example.com", AAP_TOKEN="t")
        s = load_settings(ns(config=cfg))
        self.assertEqual(s.host, "https://env.example.com")
        self.assertIsNone(s.profile)

    def test_no_config_file_keeps_original_message(self):
        with self.assertRaises(ConfigError) as cm:
            load_settings(ns(config=str(self.dir / "none.ini")))
        self.assertIn("接続先が指定されていません", str(cm.exception))

    def test_offline_uses_default_profile_name(self):
        cfg = self.config("[default]\ntimezone = UTC\n")
        s = load_settings(ns(config=cfg), require_auth=False)
        self.assertEqual((s.profile, s.timezone), ("default", "UTC"))

    def test_offline_without_profile_is_rejected_unless_db_given(self):
        cfg = self.config("[source]\nhost = https://a\ntoken = t\n")
        with self.assertRaises(ConfigError) as cm:
            load_settings(ns(config=cfg), require_auth=False)
        self.assertIn("プロファイルが指定されていません", str(cm.exception))
        s = load_settings(ns(config=cfg, db="/tmp/x.sqlite"), require_auth=False)
        self.assertIsNone(s.profile)

    def test_history_file_name(self):
        with mock.patch.dict(os.environ, {"AAPSTATS_DATA_DIR": str(self.dir)}):
            self.assertEqual(default_db_path("default", "https://a.example.com").name, "default.sqlite")
            self.assertEqual(default_db_path("source", "https://a.example.com").name, "source.sqlite")
            self.assertEqual(default_db_path(None, "https://a.example.com").name, "a.example.com.sqlite")


if __name__ == "__main__":
    unittest.main()
