"""aapstats deps <リソース> <名前またはID>: 移行に一緒に必要なリソース（R4-1）。

ジョブテンプレートまたはワークフローを 1 つ指定し、それを移行先で動かすために
一緒に移行が必要なリソースをツリーで出す。

  ジョブテンプレート
    ├ 組織
    ├ プロジェクト ─ SCM 認証情報、既定の実行環境
    ├ インベントリ ─ インベントリソース ─ 認証情報、ソースのプロジェクト、実行環境
    ├ 認証情報 ─ 認証情報タイプ（カスタムのとき）
    ├ 実行環境 ─ レジストリの認証情報
    ├ Webhook の認証情報
    └ スケジュール
  ワークフロー
    ├ 組織、インベントリ、Webhook の認証情報、スケジュール
    └ ノード ─ ジョブテンプレート / ワークフロー / プロジェクト同期 / インベントリ同期（それぞれの依存を再帰的に）

出力:
- table: ツリーと、種類ごとにまとめた一覧
- json : ツリー（入れ子）と一覧
- csv  : 種類ごとにまとめた一覧（Excel で移行対象の確認に使う）
"""

import logging

from ..errors import ConfigError
from ..resources import Context, _sf

log = logging.getLogger("aapstats")

ALIASES = {
    "job_template": "job_templates",
    "job_templates": "job_templates",
    "jt": "job_templates",
    "workflow_job_template": "workflow_job_templates",
    "workflow_job_templates": "workflow_job_templates",
    "wfjt": "workflow_job_templates",
}

KIND_LABEL = {
    "job_template": "ジョブテンプレート",
    "workflow_job_template": "ワークフロー",
    "organization": "組織",
    "project": "プロジェクト",
    "inventory": "インベントリ",
    "inventory_source": "インベントリソース",
    "credential": "認証情報",
    "credential_type": "認証情報タイプ",
    "execution_environment": "実行環境",
    "schedule": "スケジュール",
    "approval": "承認ノード",
}

# 一覧に出す順
KIND_ORDER = list(KIND_LABEL)

PREFETCH = [
    "job_templates",
    "workflow_job_templates",
    "workflow_job_template_nodes",
    "schedules",
    "projects",
    "inventories",
    "inventory_sources",
    "credentials",
    "credential_types",
    "execution_environments",
    "organizations",
]


class Graph:
    def __init__(self, ctx):
        self.ctx = ctx
        self.by_id = {name: {x["id"]: x for x in ctx.items(name)} for name in PREFETCH if name != "credential_types"}
        # credential_types は既定の取得条件でカスタムだけになる。組み込みのタイプは名前だけ認証情報から取る
        self.custom_types = {t["id"]: t for t in ctx.items("credential_types")}
        self.schedules = {}
        for s in ctx.items("schedules"):
            self.schedules.setdefault(s.get("unified_job_template"), []).append(s)
        self.nodes = ctx.nodes_by_workflow()
        self.sources_by_inventory = {}
        for src in ctx.items("inventory_sources"):
            self.sources_by_inventory.setdefault(src.get("inventory"), []).append(src)
        self.flat = {}  # (kind, id) → {"kind", "id", "name", "required_by": set()}
        self.expanded = set()  # ツリーで中身を展開済みの (kind, id)
        self.root = None

    # ---------------------------------------------------------------- 共通

    def node(self, kind, oid, name, parent=None, note=None, children=None):
        """ツリーの節を作り、一覧（flat）にも登録する。

        同じリソースが 2 回目以降に出てきたときは、中身を展開せず「上に展開済み」とする。
        一覧の required_by には、展開したかどうかにかかわらず参照元をすべて残す。
        """
        children = children or []
        if oid is not None:
            entry = self.flat.setdefault((kind, oid), {"kind": kind, "id": oid, "name": name, "required_by": set(), "note": note})
            if parent:
                entry["required_by"].add(parent)
            if children:
                if (kind, oid) in self.expanded:
                    return {"kind": kind, "id": oid, "name": name, "note": "（上に展開済み）", "children": []}
                self.expanded.add((kind, oid))
        return {"kind": kind, "id": oid, "name": name, "note": note, "children": children}

    def missing(self, kind, oid, parent):
        # 参照先が見えない（削除済み、または権限がない）
        return self.node(kind, oid, f"（ID {oid} を取得できません）", parent, note="削除済みか、権限がないため見えません")

    def _org(self, oid, parent, children):
        """組織を登録する。ツリーには最上位のテンプレートの組織だけを表示する（ほぼ全行に付いて読みにくいため）。"""
        org = self.by_id["organizations"].get(oid)
        n = self.node("organization", oid, org["name"], parent) if org else self.missing("organization", oid, parent)
        if parent == self.root:
            children.append(n)

    def _schedules(self, ujt_id, parent):
        out = []
        for s in self.schedules.get(ujt_id, []):
            note = None if s.get("enabled") else "無効"
            out.append(self.node("schedule", s["id"], s["name"], parent, note=note))
        return out

    # ---------------------------------------------------------- 認証情報・EE

    def credential(self, cid, parent):
        c = self.by_id["credentials"].get(cid)
        if not c:
            return self.missing("credential", cid, parent)
        label = f"{c['name']}"
        children = []
        ctype = c.get("credential_type")
        if ctype in self.custom_types:
            children.append(self.node("credential_type", ctype, self.custom_types[ctype]["name"], label, note="カスタム（移行先で作り直しが必要）"))
        note = _sf(c, "credential_type", "name")
        return self.node("credential", cid, label, parent, note=note, children=children)

    def ee(self, eid, parent):
        e = self.by_id["execution_environments"].get(eid)
        if not e:
            return self.missing("execution_environment", eid, parent)
        children = [self.credential(e["credential"], e["name"])] if e.get("credential") else []
        note = "組み込み" if e.get("managed") else e.get("image")
        return self.node("execution_environment", eid, e["name"], parent, note=note, children=children)

    # ------------------------------------------------------ プロジェクト・在庫

    def project(self, pid, parent):
        p = self.by_id["projects"].get(pid)
        if not p:
            return self.missing("project", pid, parent)
        children = []
        if p.get("credential"):
            children.append(self.credential(p["credential"], p["name"]))
        if p.get("default_environment"):
            children.append(self.ee(p["default_environment"], p["name"]))
        if p.get("organization"):
            self._org(p["organization"], p["name"], children)
        note = f"{p.get('scm_type') or 'manual'} {p.get('scm_url') or ''}".strip()
        return self.node("project", pid, p["name"], parent, note=note, children=children)

    def inventory_source(self, src, parent, include_inventory=False):
        children = []
        if include_inventory and src.get("inventory"):
            children.append(self.inventory(src["inventory"], src["name"]))
        if src.get("credential"):
            children.append(self.credential(src["credential"], src["name"]))
        if src.get("source_project"):
            children.append(self.project(src["source_project"], src["name"]))
        if src.get("execution_environment"):
            children.append(self.ee(src["execution_environment"], src["name"]))
        return self.node("inventory_source", src["id"], src["name"], parent, note=src.get("source"), children=children)

    def inventory(self, iid, parent):
        i = self.by_id["inventories"].get(iid)
        if not i:
            return self.missing("inventory", iid, parent)
        children = [self.inventory_source(s, i["name"]) for s in self.sources_by_inventory.get(iid, [])]
        kind = i.get("kind") or "normal"
        note = f"{kind}、ホスト {i.get('total_hosts', 0)} 台"
        return self.node("inventory", iid, i["name"], parent, note=note, children=children)

    # -------------------------------------------------------------- テンプレート

    def job_template(self, tid, parent=None, seen=None):
        t = self.by_id["job_templates"].get(tid)
        if not t:
            return self.missing("job_template", tid, parent)
        name = t["name"]
        children = []
        if t.get("organization"):
            self._org(t["organization"], name, children)
        if t.get("project"):
            children.append(self.project(t["project"], name))
        if t.get("inventory"):
            children.append(self.inventory(t["inventory"], name))
        elif t.get("ask_inventory_on_launch"):
            children.append({"kind": "inventory", "id": None, "name": "（起動時に指定）", "note": None, "children": []})
        for c in _sf(t, "credentials") or []:
            children.append(self.credential(c["id"], name))
        if t.get("execution_environment"):
            children.append(self.ee(t["execution_environment"], name))
        if t.get("webhook_credential"):
            children.append(self.credential(t["webhook_credential"], name))
        children += self._schedules(tid, name)
        note = f"Playbook: {t.get('playbook')}"
        return self.node("job_template", tid, name, parent, note=note, children=children)

    def workflow(self, wid, parent=None, seen=None):
        seen = set(seen or ())
        w = self.by_id["workflow_job_templates"].get(wid)
        if not w:
            return self.missing("workflow_job_template", wid, parent)
        name = w["name"]
        if wid in seen:
            return self.node("workflow_job_template", wid, name, parent, note="（循環参照のため省略）")
        seen.add(wid)
        children = []
        if w.get("organization"):
            self._org(w["organization"], name, children)
        if w.get("inventory"):
            children.append(self.inventory(w["inventory"], name))
        if w.get("webhook_credential"):
            children.append(self.credential(w["webhook_credential"], name))
        for n in sorted(self.nodes.get(wid, []), key=lambda n: n["id"]):
            children.append(self.workflow_node(n, name, seen))
        children += self._schedules(wid, name)
        note = f"ノード {len(self.nodes.get(wid, []))} 個"
        return self.node("workflow_job_template", wid, name, parent, note=note, children=children)

    def workflow_node(self, n, parent, seen):
        ujt = n.get("unified_job_template")
        utype = _sf(n, "unified_job_template", "unified_job_type")
        uname = _sf(n, "unified_job_template", "name")
        if ujt is None:
            return {"kind": "approval", "id": None, "name": "（テンプレートなし）", "note": None, "children": []}
        if utype == "job" or ujt in self.by_id["job_templates"]:
            return self.job_template(ujt, parent, seen)
        if utype == "workflow_job" or ujt in self.by_id["workflow_job_templates"]:
            return self.workflow(ujt, parent, seen)
        if utype == "project_update" or ujt in self.by_id["projects"]:
            return self.project(ujt, parent)
        if utype == "inventory_update" or ujt in self.by_id["inventory_sources"]:
            src = self.by_id["inventory_sources"].get(ujt)
            return self.inventory_source(src, parent, include_inventory=True) if src else self.missing("inventory_source", ujt, parent)
        if utype == "workflow_approval":
            return {"kind": "approval", "id": ujt, "name": uname, "note": "承認ノード（ワークフローと一緒に移行される）", "children": []}
        return {"kind": utype or "unknown", "id": ujt, "name": uname, "note": None, "children": []}


def resolve(ctx, resource, target):
    items = ctx.items(resource)
    if target.isdigit():
        hit = [x for x in items if x["id"] == int(target)]
    else:
        hit = [x for x in items if x["name"] == target]
    if not hit:
        raise ConfigError(f"{resource} に '{target}' が見つかりません")
    if len(hit) > 1:
        cands = ", ".join(f"ID {x['id']}（組織 {_sf(x, 'organization', 'name')}）" for x in hit)
        raise ConfigError(f"'{target}' という名前が複数あります。ID で指定してください: {cands}")
    return hit[0]


def render_tree(tree, out):
    def line(node):
        name = node["name"]
        if node.get("id") is not None:
            name = f"{name} (id={node['id']})"
        note = f"  … {node['note']}" if node.get("note") else ""
        return f"[{KIND_LABEL.get(node['kind'], node['kind'])}] {name}{note}"

    lines = [line(tree)]

    def walk(children, prefix):
        for i, child in enumerate(children):
            last = i == len(children) - 1
            lines.append(prefix + ("└─ " if last else "├─ ") + line(child))
            walk(child["children"], prefix + ("   " if last else "│  "))

    walk(tree["children"], "")
    out.write("\n".join(lines) + "\n")


def flat_rows(graph, root_kind, root_id):
    rows = []
    for (kind, oid), e in graph.flat.items():
        if (kind, oid) == (root_kind, root_id):
            continue
        rows.append(
            {
                "kind": kind,
                "label": KIND_LABEL.get(kind, kind),
                "id": oid,
                "name": e["name"],
                "note": e.get("note"),
                "required_by": sorted(e["required_by"]),
            }
        )
    rows.sort(key=lambda r: (KIND_ORDER.index(r["kind"]) if r["kind"] in KIND_ORDER else 99, r["name"] or ""))
    return rows


FLAT_COLUMNS = ["label", "id", "name", "note", "required_by"]


def run(client, args, renderer):
    resource = ALIASES.get(args.resource)
    if not resource:
        raise ConfigError(f"deps に指定できるのは job_template か workflow_job_template です（指定値: {args.resource}）")
    ctx = Context(client)
    ctx.prefetch(PREFETCH)
    target = resolve(ctx, resource, args.target)
    g = Graph(ctx)
    g.root = target["name"]
    if resource == "job_templates":
        tree, root_kind = g.job_template(target["id"]), "job_template"
    else:
        tree, root_kind = g.workflow(target["id"]), "workflow_job_template"
    rows = flat_rows(g, root_kind, target["id"])

    if renderer.fmt == "json":
        import json

        json.dump({"tree": tree, "resources": rows}, renderer.out, ensure_ascii=False, indent=2, default=str)
        renderer.out.write("\n")
    elif renderer.fmt == "csv":
        renderer.rows(rows, FLAT_COLUMNS)
    else:
        render_tree(tree, renderer.out)
        renderer.out.write("\n")
        renderer.rows(rows, FLAT_COLUMNS, title="■ 一緒に移行が必要なリソース")
    log.info("一緒に移行が必要なリソース: %d 件", len(rows))
    return 0
