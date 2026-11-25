"""結果の出力（R4-2、R4-3、R4-6）。

- table: 画面表示用。rich が入っていれば使い、なければ標準機能で罫線付きの表を出す
- csv  : Excel で開く用。BOM 付き UTF-8
- json : 他のツールに渡す用。キーは英語、日時は API の値（UTC の ISO 8601）のまま
"""

import csv
import io
import json
import re
import sys
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ConfigError
from .i18n import label

FORMATS = ("table", "csv", "json")
MAX_CELL_WIDTH = 60

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def get_zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"タイムゾーン '{name}' が見つかりません（例: Asia/Tokyo、UTC）") from None


def parse_dt(value):
    """API の日時文字列を aware な datetime にする。"""
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def format_value(value, tz):
    """table / csv 用に値を文字列にする。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(v, tz) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, str) and _ISO_RE.match(value):
        dt = parse_dt(value)
        if dt is not None:
            return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _truncate(text, limit):
    if _width(text) <= limit:
        return text
    out, w = [], 0
    for c in text:
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if w + cw > limit - 1:
            break
        out.append(c)
        w += cw
    return "".join(out) + "…"


def _pad(text, width, align_right=False):
    space = " " * (width - _width(text))
    return space + text if align_right else text + space


class Renderer:
    def __init__(self, fmt="table", lang="en", tz="Asia/Tokyo", out=None):
        if fmt not in FORMATS:
            raise ConfigError(f"--format は {', '.join(FORMATS)} のいずれかです")
        self.fmt = fmt
        self.lang = lang
        self.tz = get_zone(tz)
        self.out = out or sys.stdout
        self._wrote_table = False

    def header(self, key):
        return label(key, self.lang)

    def rows(self, rows, columns, title=None):
        """行の一覧を出力する。rows は dict のリスト。"""
        if self.fmt == "json":
            json.dump(rows, self.out, ensure_ascii=False, indent=2, default=str)
            self.out.write("\n")
        elif self.fmt == "csv":
            self._csv(rows, columns)
        else:
            self._table(rows, columns, title)

    def document(self, sections):
        """複数の表をまとめて出力する（info など）。

        sections: [(key, title, rows, columns), ...]
        json のときは {key: rows} の 1 つのオブジェクトにする。
        """
        if self.fmt == "json":
            json.dump({k: r for k, _, r, _ in sections}, self.out, ensure_ascii=False, indent=2, default=str)
            self.out.write("\n")
            return
        if self.fmt == "csv":
            # CSV は 1 表しか表せないため、section 列を付けて縦につなぐ
            merged_cols = ["section"]
            for _, _, _, cols in sections:
                merged_cols += [c for c in cols if c not in merged_cols]
            merged = [dict(r, section=k) for k, _, rows, _ in sections for r in rows]
            self._csv(merged, merged_cols)
            return
        for _, title, rows, cols in sections:
            self._table(rows, cols, title)

    # ------------------------------------------------------------------

    def _csv(self, rows, columns):
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow([self.header(c) for c in columns])
        for r in rows:
            writer.writerow([format_value(r.get(c), self.tz) for c in columns])
        self.out.write("\ufeff" + buf.getvalue())

    def _table(self, rows, columns, title=None):
        headers = [self.header(c) for c in columns]
        cells = [[_truncate(format_value(r.get(c), self.tz), MAX_CELL_WIDTH) for c in columns] for r in rows]
        numeric = [all(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool) for r in rows if r.get(c) is not None) and any(r.get(c) is not None for r in rows) for c in columns]

        try:
            from rich.console import Console
            from rich.table import Table
        except ImportError:
            Console = None

        if self._wrote_table:
            self.out.write("\n")
        self._wrote_table = True

        if Console is not None:
            table = Table(title=title, title_justify="left", show_lines=False)
            for h, is_num in zip(headers, numeric):
                table.add_column(h, justify="right" if is_num else "left", overflow="fold")
            for row in cells:
                table.add_row(*row)
            Console(file=self.out, soft_wrap=False).print(table)
            return

        widths = [max([_width(h)] + [_width(row[i]) for row in cells]) for i, h in enumerate(headers)]
        sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        lines = []
        if title:
            lines.append(title)
        lines.append(sep)
        lines.append("| " + " | ".join(_pad(h, w) for h, w in zip(headers, widths)) + " |")
        lines.append(sep.replace("-", "="))
        for row in cells:
            lines.append("| " + " | ".join(_pad(v, w, n) for v, w, n in zip(row, widths, numeric)) + " |")
        lines.append(sep)
        if not cells:
            lines.append("（該当なし）")
        self.out.write("\n".join(lines) + "\n")
