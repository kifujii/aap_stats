"""列名の日本語表示（R4-6）。

列名は既定で英語（API の項目名）とし、--lang ja のときにこの表で日本語にする。
表にない列名は英語のまま出す。
"""

LABELS_JA = {
    # 共通
    "id": "ID",
    "name": "名前",
    "description": "説明",
    "type": "種類",
    "organization": "組織",
    "created": "作成日時",
    "created_by": "作成者",
    "modified": "更新日時",
    "modified_by": "更新者",
    "section": "区分",
    # info
    "item": "項目",
    "value": "値",
    "resource": "リソース",
    "label": "名称",
    "count": "件数",
    "note": "備考",
    "required_by": "必要とする親",
    "kind": "種別",
    "oldest": "最も古い記録",
    "newest": "最も新しい記録",
    "days": "日数",
    "schedule": "スケジュール",
    "enabled": "有効",
    "retention_days": "保持日数",
    "next_run": "次回実行日時",
    # list: 参照先
    "project": "プロジェクト",
    "playbook": "Playbook",
    "inventory": "インベントリ",
    "credentials": "認証情報",
    "credential_type": "認証情報タイプ",
    "execution_environment": "実行環境",
    "template": "対象テンプレート",
    "template_type": "対象の種類",
    "organization_id": "組織ID",
    "project_id": "プロジェクトID",
    "inventory_id": "インベントリID",
    "credential_ids": "認証情報ID",
    "credential_type_id": "認証情報タイプID",
    "execution_environment_id": "実行環境ID",
    "template_id": "対象テンプレートID",
    "node_template_ids": "ノードのテンプレートID",
    "scm_credential_id": "SCM認証情報ID",
    "default_environment_id": "既定の実行環境ID",
    # list: 実行状況
    "last_job_run": "最終実行日時",
    "last_job_status": "最終結果",
    "next_job_run": "次回実行日時",
    "schedule_count": "スケジュール数",
    "enabled_schedule_count": "有効なスケジュール数",
    "run_count": "期間内の実行回数",
    "used_in_workflows": "呼び出し元ワークフロー",
    "node_count": "ノード数",
    "node_templates": "ノードのテンプレート",
    # list: テンプレートの設定
    "job_type": "ジョブの種類",
    "survey_enabled": "サーベイ",
    "ask_inventory_on_launch": "起動時にインベントリを確認",
    "ask_credential_on_launch": "起動時に認証情報を確認",
    "webhook_service": "Webhook",
    # list: スケジュール
    "rrule": "繰り返し設定",
    "timezone": "タイムゾーン",
    "dtstart": "開始日時",
    "dtend": "終了日時",
    # list: プロジェクト
    "scm_type": "SCMの種類",
    "scm_url": "SCMのURL",
    "scm_branch": "ブランチ",
    "last_updated": "最終同期日時",
    "status": "状態",
    "scm_credential": "SCM認証情報",
    "default_environment": "既定の実行環境",
    "scm_update_on_launch": "起動時に同期",
    # list: インベントリ
    "total_hosts": "ホスト数",
    "total_groups": "グループ数",
    "inventory_sources": "インベントリソース",
    "host_filter": "ホストフィルター",
    # list: 認証情報・実行環境
    "managed": "組み込み",
    "image": "イメージ",
    "pull": "プルの方針",
    "used_by_templates": "使用テンプレート数",
    "used_by_projects": "使用プロジェクト数",
    "used_by_organizations": "使用組織数",
    "used_by_inventory_sources": "使用インベントリソース数",
    # list: 組織
    "job_template_count": "ジョブテンプレート数",
    "workflow_job_template_count": "ワークフロー数",
    "project_count": "プロジェクト数",
    "inventory_count": "インベントリ数",
    "credential_count": "認証情報数",
    "execution_environment_count": "実行環境数",
    "team_count": "チーム数",
}


def label(key, lang="en"):
    if lang == "ja":
        return LABELS_JA.get(key, key)
    return key
