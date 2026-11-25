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
    "kind": "種別",
    "oldest": "最も古い記録",
    "newest": "最も新しい記録",
    "days": "日数",
    "schedule": "スケジュール",
    "enabled": "有効",
    "retention_days": "保持日数",
    "next_run": "次回実行日時",
}


def label(key, lang="en"):
    if lang == "ja":
        return LABELS_JA.get(key, key)
    return key
