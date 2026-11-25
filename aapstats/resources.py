"""list で出すリソースごとの列の定義と、行の組み立て（R2-1、R2-6、R4-3）。

- 各リソースの行は、API の応答を平らな dict にしたもの。参照先は ID ではなく名前で出す
- 参照先の ID は *_id の列に入れる（--wide か json で見える）
- 「使っている数」は、関連する一覧を一度ずつ取得して数える。リソースごとに問い合わせない
- 認証情報の inputs は出力しない（R2-6）
"""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List

log = logging.getLogger("aapstats")

# どの列にも出さない項目（R2-6）。行を組み立てるときに参照しないが、念のため最後に取り除く
FORBIDDEN_KEYS = {"inputs", "password", "ssh_key_data", "become_password", "vault_password", "secret", "token"}

INVENTORY_KIND = {"": "normal", "smart": "smart", "constructed": "constructed"}


def _sf(item, *path):
    """summary_fields の入れ子をたどる。途中で欠けていれば None。"""
    cur = item.get("summary_fields") or {}
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
        if cur is None:
            return None
    return cur


def _user(item, key):
    return _sf(item, key, "username")


class Context:
    """関連する一覧をまとめて取得し、名前の解決と参照の数え上げに使う。"""

    def __init__(self, client):
        self.client = client
        self._cache: Dict[str, List[dict]] = {}

    def prefetch(self, names):
        names = [n for n in dict.fromkeys(names) if n not in self._cache]
        if not names:
            return
        log.info("取得しています: %s", ", ".join(names))
        results = self.client.map_parallel(self.client.list_resource, names)
        self._cache.update(zip(names, results))

    def items(self, name):
        if name not in self._cache:
            self.prefetch([name])
        return self._cache[name]

    # ---------------------------------------------------------------- 名前

    def org_names(self):
        return {o["id"]: o["name"] for o in self.items("organizations")}

    # -------------------------------------------------------------- 数え上げ

    def schedules_by_template(self):
        c = Counter()
        for s in self.items("schedules"):
            if s.get("unified_job_template"):
                c[s["unified_job_template"]] += 1
        return c

    def workflows_by_node_template(self):
        """ノードが呼び出すテンプレートの ID → 呼び出し元ワークフロー名の集合。"""
        d = defaultdict(set)
        for n in self.items("workflow_job_template_nodes"):
            ujt = n.get("unified_job_template")
            wf = _sf(n, "workflow_job_template", "name")
            if ujt and wf:
                d[ujt].add(wf)
        return d

    def nodes_by_workflow(self):
        d = defaultdict(list)
        for n in self.items("workflow_job_template_nodes"):
            d[n.get("workflow_job_template")].append(n)
        return d

    def galaxy_credentials_usage(self):
        """組織の Galaxy 認証情報として使われている数（認証情報 ID → 組織数）。

        組織の一覧には含まれないため、組織ごとに問い合わせる。組織の数は少ない前提。
        """
        orgs = self.items("organizations")

        def one(org):
            url = (org.get("related") or {}).get("galaxy_credentials")
            return [c["id"] for c in self.client.paginate(url)] if url else []

        c = Counter()
        for ids in self.client.map_parallel(one, orgs):
            c.update(ids)
        return c

    def template_refs(self):
        """テンプレート（ジョブテンプレートとワークフロー）が使う各リソースの数。"""
        refs = {k: Counter() for k in ("project", "inventory", "execution_environment", "credential")}
        for t in self.items("job_templates"):
            for key in ("project", "inventory", "execution_environment"):
                if t.get(key):
                    refs[key][t[key]] += 1
            for cred in _sf(t, "credentials") or []:
                refs["credential"][cred["id"]] += 1
            if t.get("webhook_credential"):
                refs["credential"][t["webhook_credential"]] += 1
        for w in self.items("workflow_job_templates"):
            if w.get("inventory"):
                refs["inventory"][w["inventory"]] += 1
            if w.get("webhook_credential"):
                refs["credential"][w["webhook_credential"]] += 1
        return refs


@dataclass
class ResourceSpec:
    name: str
    default_columns: List[str]
    extra_columns: List[str]
    prefetch: List[str]
    build: Callable[[dict, Context], dict]
    # build の前に一度だけ計算する値（行ごとに数え直さないため）
    prepare: Callable[[Context], dict] = field(default=lambda ctx: {})

    @property
    def all_columns(self):
        return self.default_columns + [c for c in self.extra_columns if c not in self.default_columns]


COMMON_META = ["description", "created", "created_by", "modified", "modified_by"]


# ------------------------------------------------------------------ ジョブテンプレート


def _prep_jt(ctx):
    return {"schedules": ctx.schedules_by_template(), "workflows": ctx.workflows_by_node_template()}


def _build_jt(t, ctx, pre):
    creds = _sf(t, "credentials") or []
    return {
        "id": t["id"],
        "name": t["name"],
        "organization": _sf(t, "organization", "name"),
        "project": _sf(t, "project", "name"),
        "playbook": t.get("playbook"),
        "inventory": _sf(t, "inventory", "name"),
        "credentials": [c["name"] for c in creds],
        "execution_environment": _sf(t, "execution_environment", "name"),
        "last_job_run": t.get("last_job_run"),
        "last_job_status": t.get("status"),
        "schedule_count": pre["schedules"].get(t["id"], 0),
        "used_in_workflows": sorted(pre["workflows"].get(t["id"], ())),
        "job_type": t.get("job_type"),
        "survey_enabled": t.get("survey_enabled"),
        "ask_inventory_on_launch": t.get("ask_inventory_on_launch"),
        "ask_credential_on_launch": t.get("ask_credential_on_launch"),
        "webhook_service": t.get("webhook_service") or None,
        "next_job_run": t.get("next_job_run"),
        "description": t.get("description"),
        "created": t.get("created"),
        "created_by": _user(t, "created_by"),
        "modified": t.get("modified"),
        "modified_by": _user(t, "modified_by"),
        "organization_id": t.get("organization"),
        "project_id": t.get("project"),
        "inventory_id": t.get("inventory"),
        "credential_ids": [c["id"] for c in creds],
        "execution_environment_id": t.get("execution_environment"),
    }


# ------------------------------------------------------------- ワークフロー


def _prep_wfjt(ctx):
    return {
        "schedules": ctx.schedules_by_template(),
        "workflows": ctx.workflows_by_node_template(),
        "nodes": ctx.nodes_by_workflow(),
    }


def _build_wfjt(w, ctx, pre):
    nodes = pre["nodes"].get(w["id"], [])
    called = sorted({_sf(n, "unified_job_template", "name") for n in nodes if _sf(n, "unified_job_template", "name")})
    return {
        "id": w["id"],
        "name": w["name"],
        "organization": _sf(w, "organization", "name"),
        "inventory": _sf(w, "inventory", "name"),
        "node_count": len(nodes),
        "node_templates": called,
        "last_job_run": w.get("last_job_run"),
        "last_job_status": w.get("status"),
        "schedule_count": pre["schedules"].get(w["id"], 0),
        "used_in_workflows": sorted(pre["workflows"].get(w["id"], ())),
        "survey_enabled": w.get("survey_enabled"),
        "webhook_service": w.get("webhook_service") or None,
        "next_job_run": w.get("next_job_run"),
        "description": w.get("description"),
        "created": w.get("created"),
        "created_by": _user(w, "created_by"),
        "modified": w.get("modified"),
        "modified_by": _user(w, "modified_by"),
        "organization_id": w.get("organization"),
        "inventory_id": w.get("inventory"),
        "node_template_ids": sorted({n["unified_job_template"] for n in nodes if n.get("unified_job_template")}),
    }


# ---------------------------------------------------------------- スケジュール


def _build_schedule(s, ctx, pre):
    return {
        "id": s["id"],
        "name": s["name"],
        "template": _sf(s, "unified_job_template", "name"),
        "template_type": _sf(s, "unified_job_template", "unified_job_type"),
        "enabled": s.get("enabled"),
        "next_run": s.get("next_run"),
        "rrule": s.get("rrule"),
        "timezone": s.get("timezone"),
        "dtstart": s.get("dtstart"),
        "dtend": s.get("dtend"),
        "description": s.get("description"),
        "created": s.get("created"),
        "modified": s.get("modified"),
        "template_id": s.get("unified_job_template"),
    }


# ---------------------------------------------------------------- プロジェクト


def _prep_project(ctx):
    return {"refs": ctx.template_refs(), "schedules": ctx.schedules_by_template()}


def _build_project(p, ctx, pre):
    return {
        "id": p["id"],
        "name": p["name"],
        "organization": _sf(p, "organization", "name"),
        "scm_type": p.get("scm_type") or "manual",
        "scm_url": p.get("scm_url"),
        "scm_branch": p.get("scm_branch"),
        "last_updated": p.get("last_updated"),
        "status": p.get("status"),
        "used_by_templates": pre["refs"]["project"].get(p["id"], 0),
        "scm_credential": _sf(p, "credential", "name"),
        "default_environment": _sf(p, "default_environment", "name"),
        "scm_update_on_launch": p.get("scm_update_on_launch"),
        "schedule_count": pre["schedules"].get(p["id"], 0),
        "description": p.get("description"),
        "created": p.get("created"),
        "created_by": _user(p, "created_by"),
        "modified": p.get("modified"),
        "modified_by": _user(p, "modified_by"),
        "organization_id": p.get("organization"),
        "scm_credential_id": p.get("credential"),
        "default_environment_id": p.get("default_environment"),
    }


# ---------------------------------------------------------------- インベントリ


def _prep_inventory(ctx):
    sources = defaultdict(list)
    for src in ctx.items("inventory_sources"):
        sources[src.get("inventory")].append(src)
    return {"refs": ctx.template_refs(), "sources": sources}


def _build_inventory(i, ctx, pre):
    srcs = pre["sources"].get(i["id"], [])
    return {
        "id": i["id"],
        "name": i["name"],
        "organization": _sf(i, "organization", "name"),
        "kind": INVENTORY_KIND.get(i.get("kind") or "", i.get("kind")),
        "total_hosts": i.get("total_hosts"),
        "inventory_sources": [f"{s['name']} ({s.get('source')})" for s in srcs],
        "used_by_templates": pre["refs"]["inventory"].get(i["id"], 0),
        "total_groups": i.get("total_groups"),
        "host_filter": i.get("host_filter") or None,
        "description": i.get("description"),
        "created": i.get("created"),
        "created_by": _user(i, "created_by"),
        "modified": i.get("modified"),
        "modified_by": _user(i, "modified_by"),
        "organization_id": i.get("organization"),
    }


# ---------------------------------------------------------------- 認証情報


def _prep_credential(ctx):
    by_project = Counter(p["credential"] for p in ctx.items("projects") if p.get("credential"))
    by_source = Counter(s["credential"] for s in ctx.items("inventory_sources") if s.get("credential"))
    return {
        "refs": ctx.template_refs(),
        "orgs": ctx.org_names(),
        "projects": by_project,
        "sources": by_source,
        "galaxy": ctx.galaxy_credentials_usage(),
    }


def _build_credential(c, ctx, pre):
    # inputs（接続先やユーザー名を含む）は参照しない（R2-6）
    return {
        "id": c["id"],
        "name": c["name"],
        "credential_type": _sf(c, "credential_type", "name"),
        "organization": pre["orgs"].get(c.get("organization")),
        "used_by_templates": pre["refs"]["credential"].get(c["id"], 0),
        "used_by_projects": pre["projects"].get(c["id"], 0),
        "used_by_inventory_sources": pre["sources"].get(c["id"], 0),
        "used_by_organizations": pre["galaxy"].get(c["id"], 0),
        "kind": c.get("kind"),
        "managed": c.get("managed"),
        "description": c.get("description"),
        "created": c.get("created"),
        "created_by": _user(c, "created_by"),
        "modified": c.get("modified"),
        "modified_by": _user(c, "modified_by"),
        "organization_id": c.get("organization"),
        "credential_type_id": c.get("credential_type"),
    }


# ---------------------------------------------------------------- 実行環境


def _prep_ee(ctx):
    by_project = Counter(p["default_environment"] for p in ctx.items("projects") if p.get("default_environment"))
    by_org = Counter(o["default_environment"] for o in ctx.items("organizations") if o.get("default_environment"))
    by_source = Counter(s["execution_environment"] for s in ctx.items("inventory_sources") if s.get("execution_environment"))
    return {"refs": ctx.template_refs(), "orgs": ctx.org_names(), "projects": by_project, "org_default": by_org, "sources": by_source}


def _build_ee(e, ctx, pre):
    return {
        "id": e["id"],
        "name": e["name"],
        "image": e.get("image"),
        "organization": pre["orgs"].get(e.get("organization")),
        "used_by_templates": pre["refs"]["execution_environment"].get(e["id"], 0),
        "used_by_projects": pre["projects"].get(e["id"], 0),
        "used_by_organizations": pre["org_default"].get(e["id"], 0),
        "used_by_inventory_sources": pre["sources"].get(e["id"], 0),
        "pull": e.get("pull") or None,
        "managed": e.get("managed"),
        "description": e.get("description"),
        "created": e.get("created"),
        "created_by": _user(e, "created_by"),
        "modified": e.get("modified"),
        "modified_by": _user(e, "modified_by"),
        "organization_id": e.get("organization"),
    }


# ---------------------------------------------------------------- 組織


def _prep_org(ctx):
    counts = {}
    for name in ("job_templates", "workflow_job_templates", "projects", "inventories", "credentials", "execution_environments", "teams"):
        counts[name] = Counter(x.get("organization") for x in ctx.items(name))
    return {"counts": counts}


def _build_org(o, ctx, pre):
    c = pre["counts"]
    oid = o["id"]
    return {
        "id": oid,
        "name": o["name"],
        "job_template_count": c["job_templates"].get(oid, 0),
        "workflow_job_template_count": c["workflow_job_templates"].get(oid, 0),
        "project_count": c["projects"].get(oid, 0),
        "inventory_count": c["inventories"].get(oid, 0),
        "credential_count": c["credentials"].get(oid, 0),
        "execution_environment_count": c["execution_environments"].get(oid, 0),
        "team_count": c["teams"].get(oid, 0),
        "default_environment": _sf(o, "default_environment", "name"),
        "description": o.get("description"),
        "created": o.get("created"),
        "created_by": _user(o, "created_by"),
        "modified": o.get("modified"),
        "modified_by": _user(o, "modified_by"),
    }


SPECS = {
    s.name: s
    for s in [
        ResourceSpec(
            "job_templates",
            ["id", "name", "organization", "project", "playbook", "inventory", "credentials", "execution_environment", "last_job_run", "last_job_status"],
            ["schedule_count", "used_in_workflows", "job_type", "survey_enabled", "ask_inventory_on_launch", "ask_credential_on_launch", "webhook_service", "next_job_run"]
            + COMMON_META
            + ["organization_id", "project_id", "inventory_id", "credential_ids", "execution_environment_id"],
            ["schedules", "workflow_job_template_nodes"],
            _build_jt,
            _prep_jt,
        ),
        ResourceSpec(
            "workflow_job_templates",
            ["id", "name", "organization", "inventory", "node_count", "node_templates", "last_job_run", "last_job_status"],
            ["schedule_count", "used_in_workflows", "survey_enabled", "webhook_service", "next_job_run"] + COMMON_META + ["organization_id", "inventory_id", "node_template_ids"],
            ["schedules", "workflow_job_template_nodes"],
            _build_wfjt,
            _prep_wfjt,
        ),
        ResourceSpec(
            "schedules",
            ["id", "name", "template", "template_type", "enabled", "next_run", "rrule"],
            ["timezone", "dtstart", "dtend", "description", "created", "modified", "template_id"],
            [],
            _build_schedule,
        ),
        ResourceSpec(
            "projects",
            ["id", "name", "organization", "scm_type", "scm_url", "scm_branch", "last_updated", "status", "used_by_templates"],
            ["scm_credential", "default_environment", "scm_update_on_launch", "schedule_count"] + COMMON_META + ["organization_id", "scm_credential_id", "default_environment_id"],
            ["job_templates", "workflow_job_templates", "schedules"],
            _build_project,
            _prep_project,
        ),
        ResourceSpec(
            "inventories",
            ["id", "name", "organization", "kind", "total_hosts", "inventory_sources", "used_by_templates"],
            ["total_groups", "host_filter"] + COMMON_META + ["organization_id"],
            ["job_templates", "workflow_job_templates", "inventory_sources"],
            _build_inventory,
            _prep_inventory,
        ),
        ResourceSpec(
            "credentials",
            ["id", "name", "credential_type", "organization", "used_by_templates", "used_by_projects", "used_by_inventory_sources", "used_by_organizations"],
            ["kind", "managed"] + COMMON_META + ["organization_id", "credential_type_id"],
            ["job_templates", "workflow_job_templates", "projects", "inventory_sources", "organizations"],
            _build_credential,
            _prep_credential,
        ),
        ResourceSpec(
            "execution_environments",
            ["id", "name", "image", "organization", "used_by_templates", "used_by_projects", "used_by_organizations", "used_by_inventory_sources"],
            ["pull", "managed"] + COMMON_META + ["organization_id"],
            ["job_templates", "workflow_job_templates", "projects", "inventory_sources", "organizations"],
            _build_ee,
            _prep_ee,
        ),
        ResourceSpec(
            "organizations",
            ["id", "name", "job_template_count", "workflow_job_template_count", "project_count", "inventory_count", "credential_count", "execution_environment_count", "team_count"],
            ["default_environment"] + COMMON_META,
            ["job_templates", "workflow_job_templates", "projects", "inventories", "credentials", "execution_environments", "teams"],
            _build_org,
            _prep_org,
        ),
    ]
}


def build_rows(client, resource_name):
    spec = SPECS[resource_name]
    ctx = Context(client)
    ctx.prefetch([resource_name] + spec.prefetch)
    pre = spec.prepare(ctx)
    rows = []
    for item in ctx.items(resource_name):
        row = spec.build(item, ctx, pre)
        for k in FORBIDDEN_KEYS & row.keys():
            del row[k]
        rows.append(row)
    return rows
