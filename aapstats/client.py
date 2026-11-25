"""AAP API クライアント（GET のみ。R1-2）。

- 認証: トークン（Bearer）またはユーザー名とパスワード（Basic）
- ページ分割: 応答の next をたどる
- 負荷の制御（R2-5）: 同時リクエスト数（max_workers）と、リクエストの最小間隔（request_interval）
- 一時的なエラー（429、502、503、504、接続エラー）は待ってから再試行する
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

from . import __version__
from .endpoints import RESOURCES, ApiLayout, detect_flavor
from .errors import ApiError, AuthError, PermissionDenied

log = logging.getLogger("aapstats")

MAX_PAGE_SIZE = 200
RETRY_STATUS = {429, 502, 503, 504}
MAX_RETRIES = 4


class AapClient:
    def __init__(self, settings):
        self.settings = settings
        self._local = threading.local()
        self._throttle_lock = threading.Lock()
        self._next_slot = 0.0
        self.layout = None  # connect() で決まる
        self.request_count = 0
        self._count_lock = threading.Lock()
        if not settings.verify_ssl:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            log.warning("証明書の検証を無効にして接続します")

    # ------------------------------------------------------------------ 接続

    def connect(self):
        """接続先の API の構成を判別する（R2-0）。"""
        if self.settings.api_version == "auto":
            root = self.get("/api/", auth=False)
            flavor = detect_flavor(root)
            log.debug("API の構成を判別しました: %s", flavor)
        else:
            flavor = self.settings.api_version
        self.layout = ApiLayout(flavor)
        return self.layout

    # ------------------------------------------------------------------ HTTP

    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = f"aapstats/{__version__}"
            s.headers["Accept"] = "application/json"
            s.verify = self.settings.verify
            self._local.session = s
        return s

    def _auth_kwargs(self):
        if self.settings.token:
            return {"headers": {"Authorization": f"Bearer {self.settings.token}"}}
        return {"auth": (self.settings.username, self.settings.password)}

    def _throttle(self):
        interval = self.settings.request_interval
        if interval <= 0:
            return
        with self._throttle_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + interval
        if wait > 0:
            time.sleep(wait)

    def _url(self, path_or_url):
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return urljoin(self.settings.host + "/", path_or_url.lstrip("/"))

    def get(self, path_or_url, params=None, auth=True):
        """GET して JSON を返す。"""
        url = self._url(path_or_url)
        kwargs = self._auth_kwargs() if auth else {}
        attempt = 0
        while True:
            self._throttle()
            started = time.monotonic()
            try:
                resp = self._session().get(url, params=params, timeout=self.settings.timeout, **kwargs)
            except requests.exceptions.SSLError as e:
                raise ApiError(
                    f"証明書の検証に失敗しました: {url}\n"
                    "社内 CA の場合は --ca-file で CA ファイルを指定してください。"
                    "検証を無効にする場合は --insecure を付けてください",
                    url=url,
                ) from e
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt < MAX_RETRIES:
                    attempt += 1
                    delay = 2**attempt
                    log.warning("接続に失敗したため %d 秒後に再試行します（%d/%d）: %s", delay, attempt, MAX_RETRIES, url)
                    time.sleep(delay)
                    continue
                raise ApiError(f"接続できません: {url}（{e.__class__.__name__}）", url=url) from e
            elapsed = time.monotonic() - started
            with self._count_lock:
                self.request_count += 1
            log.debug("GET %s -> %d (%.2fs)", resp.url, resp.status_code, elapsed)

            if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES:
                attempt += 1
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 2**attempt
                except ValueError:
                    delay = 2**attempt
                log.warning(
                    "サーバーが一時的に応答できないため %.0f 秒後に再試行します（HTTP %d、%d/%d）",
                    delay,
                    resp.status_code,
                    attempt,
                    MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            return self._handle(resp, url)

    @staticmethod
    def _handle(resp, url):
        if resp.status_code == 401:
            raise AuthError(
                "認証に失敗しました。トークン、またはユーザー名とパスワードを確認してください", status=401, url=url
            )
        if resp.status_code == 403:
            raise PermissionDenied(f"権限がありません: {url}", status=403, url=url)
        if resp.status_code == 404:
            raise ApiError(f"見つかりません: {url}", status=404, url=url)
        if resp.status_code >= 400:
            raise ApiError(f"API がエラーを返しました（HTTP {resp.status_code}）: {url}", status=resp.status_code, url=url)
        try:
            return resp.json()
        except ValueError:
            raise ApiError(f"API の応答が JSON ではありません: {url}", status=resp.status_code, url=url) from None

    # ------------------------------------------------------------ リソース

    def _resource_params(self, resource_name, params):
        merged = dict(RESOURCES[resource_name].default_params or {})
        merged.update(params or {})
        return merged

    def paginate(self, path, params=None, page_size=MAX_PAGE_SIZE):
        """一覧 API の結果をすべてたどって 1 件ずつ返す。"""
        params = dict(params or {})
        params["page_size"] = page_size
        url = path
        while url:
            data = self.get(url, params=params)
            for item in data.get("results", []):
                yield item
            url = data.get("next")
            params = None  # next には問い合わせ条件が含まれている

    def count(self, path, params=None):
        """件数だけを取得する（R2-5）。"""
        params = dict(params or {})
        params["page_size"] = 1
        data = self.get(path, params=params)
        return int(data.get("count", 0))

    def first(self, path, params=None):
        """条件に合う先頭の 1 件を返す。なければ None。"""
        params = dict(params or {})
        params["page_size"] = 1
        results = self.get(path, params=params).get("results") or []
        return results[0] if results else None

    def list_resource(self, resource_name, params=None):
        return list(self.paginate(self.layout.resource_path(resource_name), self._resource_params(resource_name, params)))

    def count_resource(self, resource_name, params=None):
        return self.count(self.layout.resource_path(resource_name), self._resource_params(resource_name, params))

    def first_resource(self, resource_name, params=None):
        return self.first(self.layout.resource_path(resource_name), self._resource_params(resource_name, params))

    def map_parallel(self, func, items):
        """func を items に並行して適用する。順序は items と同じ。"""
        items = list(items)
        if self.settings.max_workers <= 1 or len(items) <= 1:
            return [func(i) for i in items]
        with ThreadPoolExecutor(max_workers=self.settings.max_workers) as ex:
            return list(ex.map(func, items))
