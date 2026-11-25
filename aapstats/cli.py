"""コマンドラインの入口（R4-1、R4-5）。"""

import argparse
import logging
import sys

from . import __version__
from .config import API_VERSIONS, load_settings
from .errors import EXIT_OK, EXIT_USAGE, AapStatsError
from .output import FORMATS, Renderer

log = logging.getLogger("aapstats")

EPILOG = """\
接続先と認証情報は、設定ファイル（~/.config/aapstats/config.ini）のプロファイルか、
環境変数（AAP_HOST、AAP_TOKEN または AAP_TOKEN_FILE、AAP_USERNAME と AAP_PASSWORD）で指定します。
パスワードとトークンはコマンドの引数では受け取りません。

例:
  aapstats --profile source info
  aapstats info --format json -o info.json
"""


def _add_common(parser, suppress):
    """共通オプション。サブコマンドの前にも後ろにも書けるようにする。

    サブコマンド側では既定値を SUPPRESS にする。こうしないと、サブコマンドの前に
    書いた値が、サブコマンド側の既定値（None）で上書きされてしまうためである。
    """

    def add(group, *names, **kw):
        if suppress:
            kw["default"] = argparse.SUPPRESS
        group.add_argument(*names, **kw)

    g = parser.add_argument_group("接続")
    add(g, "--config", metavar="PATH", help="設定ファイルのパス（既定: ~/.config/aapstats/config.ini）")
    add(g, "--profile", metavar="NAME", help="設定ファイルのプロファイル名（既定: default）")
    add(g, "--host", metavar="URL", help="接続先の URL（設定ファイル・環境変数より優先）")
    add(g, "--insecure", action="store_true", help="証明書の検証を無効にする")
    add(g, "--ca-file", metavar="PATH", help="CA 証明書のファイル")
    add(
        g,
        "--api-version",
        choices=API_VERSIONS,
        help="API の構成。auto: 自動判別（既定）、gateway: AAP 2.5 以降、legacy: AAP 2.4 以前",
    )
    add(g, "--timeout", type=float, metavar="SEC", help="1 リクエストのタイムアウト秒数（既定: 60）")
    add(g, "--max-workers", type=int, metavar="N", help="同時に送るリクエスト数（既定: 4）")
    add(g, "--request-interval", type=float, metavar="SEC", help="リクエストの最小間隔の秒数（既定: 0）")

    o = parser.add_argument_group("出力")
    add(o, "--format", choices=FORMATS, help="出力形式（既定: table）")
    add(o, "-o", "--output", metavar="PATH", help="結果を保存するファイル（既定: 標準出力）")
    add(o, "--lang", choices=("en", "ja"), help="列名の言語（既定: en）")
    add(o, "--tz", metavar="ZONE", help="日時を表示するタイムゾーン（既定: Asia/Tokyo）")
    add(o, "-v", "--verbose", action="store_true", help="詳細ログ（API の URL と応答時間）を出す")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="aapstats",
        description="AAP の移行判断に使うリソース一覧・統計ツール（読み取り専用）",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_common(parser, suppress=False)

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    p_info = sub.add_parser(
        "info",
        help="接続先、バージョン、リソースの件数、ジョブ履歴の期間を表示する",
        description="接続先、判別したバージョン、リソースの件数、残っているジョブ履歴の期間を表示します。",
    )
    _add_common(p_info, suppress=True)
    p_info.set_defaults(handler="info")

    from .commands.list_cmd import RESOURCE_NAMES

    p_list = sub.add_parser(
        "list",
        help="リソースの一覧を出す",
        description="リソースの一覧を出します。参照先は ID ではなく名前で表示します。",
        epilog="例:\n  aapstats list job_templates\n  aapstats list credentials --wide --format csv -o creds.csv\n"
        "  aapstats list projects --columns name,scm_url,used_by_templates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_list.add_argument("resource", choices=RESOURCE_NAMES, metavar="<resource>", help="対象のリソース: " + ", ".join(RESOURCE_NAMES))
    g = p_list.add_argument_group("列")
    g.add_argument("--columns", metavar="COLS", help="出す列をカンマ区切りで指定する（例: id,name,last_job_run）")
    g.add_argument("--wide", action="store_true", help="すべての列を出す")
    _add_common(p_list, suppress=True)
    p_list.set_defaults(handler="list")
    return parser


def _setup_logging(verbose):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False
    logging.addLevelName(logging.WARNING, "警告")
    logging.addLevelName(logging.ERROR, "エラー")
    logging.addLevelName(logging.INFO, "情報")
    logging.addLevelName(logging.DEBUG, "詳細")


def _handler(name):
    if name == "info":
        from .commands import info

        return info.run
    if name == "list":
        from .commands import list_cmd

        return list_cmd.run
    raise ValueError(name)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    out = None
    try:
        settings = load_settings(args)
        from .client import AapClient

        client = AapClient(settings)
        client.connect()

        if args.output:
            try:
                out = open(args.output, "w", encoding="utf-8", newline="")
            except OSError as e:
                log.error("出力ファイルを開けません: %s（%s）", args.output, e.strerror)
                return EXIT_USAGE
        renderer = Renderer(fmt=args.format or "table", lang=args.lang or "en", tz=settings.timezone, out=out or sys.stdout)
        code = _handler(args.handler)(client, args, renderer)
        if out:
            log.info("結果を保存しました: %s", args.output)
        return code if code is not None else EXIT_OK
    except AapStatsError as e:
        log.error("%s", e)
        return e.exit_code
    except KeyboardInterrupt:
        log.error("中断しました")
        return 130
    finally:
        if out:
            out.close()
