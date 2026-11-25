"""終了コードと例外の定義（R4-5）。"""

EXIT_OK = 0
EXIT_CONNECTION = 1  # 接続・認証のエラー
EXIT_USAGE = 2  # 引数・設定の誤り


class AapStatsError(Exception):
    exit_code = EXIT_CONNECTION


class ConfigError(AapStatsError):
    exit_code = EXIT_USAGE


class ApiError(AapStatsError):
    exit_code = EXIT_CONNECTION

    def __init__(self, message, status=None, url=None):
        super().__init__(message)
        self.status = status
        self.url = url


class AuthError(ApiError):
    pass


class PermissionDenied(ApiError):
    pass
