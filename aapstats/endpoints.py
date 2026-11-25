"""API のパスの違いをまとめる（R2-0）。

AAP のバージョンによる API の違いは、このファイルだけで吸収する。

- gateway: AAP 2.5 以降。Platform Gateway の配下に Controller の API がある。
    Controller: /api/controller/v2/
    Gateway   : /api/gateway/v1/
- legacy : AAP 2.4 以前。Controller の API に直接つなぐ。
    Controller: /api/v2/
"""

from dataclasses import dataclass
from typing import Dict, Optional

from .errors import ApiError

GATEWAY = "gateway"
LEGACY = "legacy"

CONTROLLER = "controller"
GATEWAY_SVC = "gateway"

SERVICE_BASES = {
    GATEWAY: {CONTROLLER: "/api/controller/v2/", GATEWAY_SVC: "/api/gateway/v1/"},
    LEGACY: {CONTROLLER: "/api/v2/"},
}


@dataclass(frozen=True)
class Resource:
    """論理的なリソース名と、取得元のサービス・パス。"""

    name: str
    service: str
    path: str
    label: str
    # このフィルタを常に付けて取得する（例: 組み込みでない認証情報タイプだけ）
    default_params: Optional[Dict[str, str]] = None
    # 対応しているフレーバー。None はすべて
    flavors: Optional[tuple] = None


RESOURCES = {
    r.name: r
    for r in [
        Resource("job_templates", CONTROLLER, "job_templates/", "ジョブテンプレート"),
        Resource("workflow_job_templates", CONTROLLER, "workflow_job_templates/", "ワークフロージョブテンプレート"),
        Resource("schedules", CONTROLLER, "schedules/", "スケジュール"),
        Resource("projects", CONTROLLER, "projects/", "プロジェクト"),
        Resource("inventories", CONTROLLER, "inventories/", "インベントリ"),
        Resource("inventory_sources", CONTROLLER, "inventory_sources/", "インベントリソース"),
        Resource("credentials", CONTROLLER, "credentials/", "認証情報"),
        Resource("execution_environments", CONTROLLER, "execution_environments/", "実行環境"),
        Resource("organizations", CONTROLLER, "organizations/", "組織"),
        Resource("teams", CONTROLLER, "teams/", "チーム"),
        Resource(
            "credential_types",
            CONTROLLER,
            "credential_types/",
            "カスタム認証情報タイプ",
            default_params={"managed": "false"},
        ),
        Resource(
            "authenticators", GATEWAY_SVC, "authenticators/", "Authentication Method", flavors=(GATEWAY,)
        ),
        Resource(
            "authenticator_maps", GATEWAY_SVC, "authenticator_maps/", "認証のマッピング", flavors=(GATEWAY,)
        ),
        Resource("gateway_teams", GATEWAY_SVC, "teams/", "チーム（Gateway）", flavors=(GATEWAY,)),
        # 2.5 以降のロール割り当て（DAB RBAC）。2.4 以前は teams/<id>/roles/ から取る
        Resource(
            "role_team_assignments", CONTROLLER, "role_team_assignments/", "チームのロール割り当て", flavors=(GATEWAY,)
        ),
        # 統計の材料
        Resource("jobs", CONTROLLER, "jobs/", "ジョブ実行履歴"),
        Resource("workflow_jobs", CONTROLLER, "workflow_jobs/", "ワークフロー実行履歴"),
        Resource("project_updates", CONTROLLER, "project_updates/", "プロジェクト同期の履歴"),
        Resource("inventory_updates", CONTROLLER, "inventory_updates/", "インベントリ同期の履歴"),
        Resource("workflow_job_template_nodes", CONTROLLER, "workflow_job_template_nodes/", "ワークフローノード"),
        Resource("system_job_templates", CONTROLLER, "system_job_templates/", "管理ジョブ"),
    ]
}


class ApiLayout:
    """フレーバーに応じて URL のパスを組み立てる。"""

    def __init__(self, flavor: str):
        if flavor not in SERVICE_BASES:
            raise ValueError(f"unknown flavor: {flavor}")
        self.flavor = flavor

    @property
    def label(self):
        return "AAP 2.5 以降（Gateway 経由）" if self.flavor == GATEWAY else "AAP 2.4 以前（Controller 直接）"

    def has_service(self, service: str) -> bool:
        return service in SERVICE_BASES[self.flavor]

    def base(self, service: str) -> str:
        try:
            return SERVICE_BASES[self.flavor][service]
        except KeyError:
            raise ApiError(f"このバージョン（{self.label}）には {service} の API がありません") from None

    def path(self, service: str, sub: str = "") -> str:
        return self.base(service) + sub.lstrip("/")

    def supports(self, resource_name: str) -> bool:
        r = RESOURCES[resource_name]
        if r.flavors is not None and self.flavor not in r.flavors:
            return False
        return self.has_service(r.service)

    def resource_path(self, resource_name: str) -> str:
        r = RESOURCES[resource_name]
        if not self.supports(resource_name):
            raise ApiError(f"このバージョン（{self.label}）では {r.label} を API から取得できません")
        return self.path(r.service, r.path)

    def ping_paths(self):
        """バージョン確認に使う ping のパス（サービス名 → パス）。"""
        return {svc: base + "ping/" for svc, base in SERVICE_BASES[self.flavor].items()}


def detect_flavor(api_root: dict) -> str:
    """/api/ の応答からフレーバーを判別する。"""
    if isinstance(api_root, dict):
        apis = api_root.get("apis")
        if isinstance(apis, dict) and CONTROLLER in apis:
            return GATEWAY
        if api_root.get("current_version") == "/api/v2/" or "v2" in (api_root.get("available_versions") or {}):
            return LEGACY
    raise ApiError(
        "/api/ の応答から AAP のバージョンを判別できません。--api-version gateway（2.5 以降）か "
        "--api-version legacy（2.4 以前）を指定してください"
    )
