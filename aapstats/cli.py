"""コマンドラインの入口（R4-1、R4-5）。"""

import argparse
import logging
import sys

from . import __version__
from .config import API_VERSIONS, load_settings
from .errors import EXIT_OK, EXIT_USAGE, AapStatsError
from .output import FORMATS, Renderer

log = logging.getLogger("aapstats")

# AAP に接続しないコマンド
OFFLINE_COMMANDS = {"stats"}

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
        "  aapstats list projects --columns name,scm_url,used_by_templates\n"
        "  aapstats list job_templates --unused-days 90 --orphan\n"
        "  aapstats list job_templates --since 2026-08-01 --max-runs 0\n"
        "  aapstats list job_templates --columns name,run_count --sort=-run_count --limit 10\n"
        "  aapstats list credentials --unreferenced\n"
        "  aapstats list job_templates --uses-credential linux-ssh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_list.add_argument("resource", choices=RESOURCE_NAMES, metavar="<resource>", help="対象のリソース: " + ", ".join(RESOURCE_NAMES))
    g = p_list.add_argument_group("列")
    g.add_argument("--columns", metavar="COLS", help="出す列をカンマ区切りで指定する（例: id,name,last_job_run）")
    g.add_argument("--wide", action="store_true", help="すべての列を出す")
    from . import filters

    filters.add_arguments(p_list)
    _add_common(p_list, suppress=True)
    p_list.set_defaults(handler="list")

    p_deps = sub.add_parser(
        "deps",
        help="テンプレートを移行するときに一緒に必要なリソースを出す",
        description="ジョブテンプレートまたはワークフローを 1 つ指定し、移行先で動かすために一緒に移行が必要なリソースをツリーで出します。",
        epilog="例:\n  aapstats deps job_template write-note-linux\n  aapstats deps workflow_job_template 23 --format csv -o deps.csv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_deps.add_argument("resource", metavar="<resource>", help="job_template（jt）または workflow_job_template（wfjt）")
    p_deps.add_argument("target", metavar="<name|id>", help="テンプレートの名前または ID")
    _add_common(p_deps, suppress=True)
    p_deps.set_defaults(handler="deps")

    from .commands import stats as stats_cmd
    from .commands import sync as sync_cmd

    p_sync = sub.add_parser(
        "sync",
        help="ジョブ履歴を手元の SQLite に保存する（2 回目以降は差分だけ）",
        description="ジョブ履歴を手元の SQLite に保存します。途中で止まっても、次回は続きから取得します。"
        "AAP 側で古い履歴が削除されても、保存済みの履歴は残ります。",
        epilog="例:\n  aapstats --profile source sync\n  aapstats --profile source sync --since 2026-08-01 --kinds job,workflow_job,project_update",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sync_cmd.add_arguments(p_sync)
    _add_common(p_sync, suppress=True)
    p_sync.set_defaults(handler="sync")

    p_stats = sub.add_parser(
        "stats",
        help="保存したジョブ履歴から統計を出す（AAP には接続しない）",
        description="aapstats sync で保存したジョブ履歴から統計を出します。AAP には接続しません。",
        epilog="例:\n  aapstats --profile source stats\n  aapstats --profile source stats --group-by month\n"
        "  aapstats --profile source stats --top 10\n  aapstats --profile source stats --since 2026-08-01 --format csv -o stats.csv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    stats_cmd.add_arguments(p_stats)
    _add_common(p_stats, suppress=True)
    p_stats.set_defaults(handler="stats")

    from .commands import report as report_cmd

    p_report = sub.add_parser(
        "report",
        help="テンプレートと関連リソースを「移行対象・要確認・移行不要候補」に分類する",
        description="テンプレートを最終実行日時と参照関係で「移行対象・要確認・移行不要候補」に分類し、"
        "その結果を関連リソース（プロジェクト、インベントリ、認証情報、認証情報タイプ、実行環境、チーム）に広げます。"
        "ツールが出すのは候補です。移行するかどうかは人が決めてください。",
        epilog="例:\n  aapstats --profile source report\n  aapstats --profile source report --format csv -o report.csv\n"
        "  aapstats --profile source report --unused-days 180 --only unneeded,review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    report_cmd.add_arguments(p_report)
    _add_common(p_report, suppress=True)
    p_report.set_defaults(handler="report")
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
    if name == "report":
        from .commands import report

        return report.run
    if name == "sync":
        from .commands import sync

        return sync.run
    if name == "stats":
        from .commands import stats

        return stats.run
    if name == "deps":
        from .commands import deps

        return deps.run
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
        offline = args.handler in OFFLINE_COMMANDS
        settings = load_settings(args, require_auth=not offline)
        if offline:
            # AAP に接続しないコマンド。設定はタイムゾーンと保存先の決定にだけ使う
            client = None
            args._settings = settings
        else:
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
