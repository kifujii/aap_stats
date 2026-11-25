"""接続設定の読み込み（R1-5、R4-4）。

優先順位（高い順）:
  1. コマンドライン引数（--host など。パスワードとトークンは引数で受け取らない）
  2. 設定ファイルのプロファイル（--profile で選択。省略時は [default]）
  3. 環境変数（AAP_HOST、AAP_TOKEN など）

設定ファイルの例（~/.config/aapstats/config.ini）:

    [source]
    host = https://aap-old.example.com
    token_file = ~/.config/aapstats/source.token
    verify_ssl = true
    ca_file = /etc/pki/tls/certs/internal-ca.pem
    api_version = auto

秘密の値（token、password）は設定ファイルに直接書くこともできるが、
token_file / password_file で別ファイルに分け、権限を 600 にすることを勧める。
"""

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("AAPSTATS_CONFIG", "~/.config/aapstats/config.ini")
).expanduser()

API_VERSIONS = ("auto", "gateway", "legacy")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass
class Settings:
    host: str
    token: Optional[str] = field(default=None, repr=False)
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    verify_ssl: bool = True
    ca_file: Optional[str] = None
    api_version: str = "auto"
    timezone: str = "Asia/Tokyo"
    timeout: float = 60.0
    max_workers: int = 4
    request_interval: float = 0.0
    profile: Optional[str] = None

    @property
    def verify(self):
        """requests の verify 引数に渡す値。"""
        if not self.verify_ssl:
            return False
        return self.ca_file or True


def _parse_bool(value, name):
    v = str(value).strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ConfigError(f"{name} には true か false を指定してください（指定値: {value}）")


def _read_secret_file(path, name):
    p = Path(path).expanduser()
    try:
        value = p.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ConfigError(f"{name} のファイルを読めません: {p}（{e.strerror}）") from None
    if not value:
        raise ConfigError(f"{name} のファイルが空です: {p}")
    return value


def load_settings(args) -> Settings:
    """引数・設定ファイル・環境変数から Settings を組み立てる。"""
    config_path = Path(args.config).expanduser() if args.config else DEFAULT_CONFIG_PATH
    section = {}
    if args.profile and not config_path.exists():
        raise ConfigError(f"設定ファイルが見つかりません: {config_path}")
    if config_path.exists():
        parser = configparser.ConfigParser()
        try:
            parser.read(config_path, encoding="utf-8")
        except configparser.Error as e:
            raise ConfigError(f"設定ファイルの書式が正しくありません: {config_path}（{e}）") from None
        name = args.profile or "default"
        if parser.has_section(name):
            section = dict(parser.items(name))
        elif args.profile:
            available = ", ".join(parser.sections()) or "なし"
            raise ConfigError(
                f"プロファイル '{args.profile}' が {config_path} にありません（登録済み: {available}）"
            )

    env = os.environ

    def pick(cli_value, key, env_key):
        if cli_value is not None:
            return cli_value
        if section.get(key) not in (None, ""):
            return section[key]
        return env.get(env_key) or None

    host = pick(getattr(args, "host", None), "host", "AAP_HOST")
    if not host:
        raise ConfigError(
            "接続先が指定されていません。--host、設定ファイルの host、環境変数 AAP_HOST のいずれかで指定してください"
        )
    if not host.startswith(("http://", "https://")):
        host = "https://" + host
    host = host.rstrip("/")

    token = pick(None, "token", "AAP_TOKEN")
    token_file = pick(None, "token_file", "AAP_TOKEN_FILE")
    if not token and token_file:
        token = _read_secret_file(token_file, "token_file")

    username = pick(None, "username", "AAP_USERNAME")
    password = pick(None, "password", "AAP_PASSWORD")
    password_file = pick(None, "password_file", "AAP_PASSWORD_FILE")
    if not password and password_file:
        password = _read_secret_file(password_file, "password_file")

    if not token and not (username and password):
        raise ConfigError(
            "認証情報がありません。トークン（token / token_file / AAP_TOKEN）か、"
            "ユーザー名とパスワード（username と password / password_file、AAP_USERNAME と AAP_PASSWORD）を設定してください"
        )

    verify_raw = pick(None, "verify_ssl", "AAP_VERIFY_SSL")
    verify_ssl = True if verify_raw is None else _parse_bool(verify_raw, "verify_ssl")
    if getattr(args, "insecure", False):
        verify_ssl = False

    ca_file = pick(getattr(args, "ca_file", None), "ca_file", "AAP_CA_FILE")
    if ca_file:
        ca_file = str(Path(ca_file).expanduser())
        if not Path(ca_file).is_file():
            raise ConfigError(f"CA ファイルが見つかりません: {ca_file}")

    api_version = pick(getattr(args, "api_version", None), "api_version", "AAP_API_VERSION") or "auto"
    if api_version not in API_VERSIONS:
        raise ConfigError(f"api_version は {', '.join(API_VERSIONS)} のいずれかです（指定値: {api_version}）")

    timezone = pick(getattr(args, "tz", None), "timezone", "AAP_TIMEZONE") or "Asia/Tokyo"

    def num(cli_value, key, cast, default):
        raw = pick(cli_value, key, "AAP_" + key.upper())
        if raw is None:
            return default
        try:
            value = cast(raw)
        except ValueError:
            raise ConfigError(f"{key} には数値を指定してください（指定値: {raw}）") from None
        if value < 0:
            raise ConfigError(f"{key} には 0 以上の値を指定してください（指定値: {raw}）")
        return value

    max_workers = num(getattr(args, "max_workers", None), "max_workers", int, 4)
    if max_workers < 1:
        raise ConfigError("max_workers には 1 以上を指定してください")

    return Settings(
        host=host,
        token=token,
        username=username if not token else None,
        password=password if not token else None,
        verify_ssl=verify_ssl,
        ca_file=ca_file,
        api_version=api_version,
        timezone=timezone,
        timeout=num(getattr(args, "timeout", None), "timeout", float, 60.0),
        max_workers=max_workers,
        request_interval=num(getattr(args, "request_interval", None), "request_interval", float, 0.0),
        profile=args.profile,
    )
