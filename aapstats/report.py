"""移行判定（R5-5、R5-6）。

テンプレート（ジョブテンプレートとワークフロー）を 3 つに分類し、その結果を関連リソースに広げる。

テンプレート:
  移行対象      直近 N 日以内に実行されている（ただし、その期間の実行がすべて失敗なら要確認）
  要確認        N 日以上実行されていないが、有効なスケジュールか、移行不要候補でないワークフローから呼ばれている
  移行不要候補  N 日以上実行されておらず、どこからも呼ばれていない
  呼び出し元のワークフローが移行不要候補なら、呼ばれていないものとして扱う（使われていないワークフローの部品が
  要確認に残り続けないように）。ワークフローの入れ子に対応するため、判定が変わらなくなるまで繰り返す。

関連リソース:
  使っているもののうち、いちばん強い判定（移行対象 > 要確認 > 移行不要候補）を引き継ぐ。
  どこからも使われていなければ移行不要候補。テンプレート以外からの参照も数える:
    プロジェクト   … インベントリソース（インベントリの判定）、ワークフローの同期ノード
    インベントリ   … ワークフローのインベントリ同期ノード
    認証情報       … プロジェクトの SCM 認証情報、インベントリソース、実行環境のレジストリ認証情報、組織の Galaxy 認証情報
    実行環境       … プロジェクトと組織の既定の実行環境、インベントリソース
    認証情報タイプ … 認証情報（カスタムのタイプだけ）
    チーム         … ロールの対象。認証のマッピング（LDAP など）で使われているチームは移行対象
  組織から参照されているもの（Galaxy 認証情報、既定の実行環境）は移行対象とする。組織は移行する前提のため。
  AAP の組み込みのリソース（managed）は判定しない。
"""

import logging
from collections import defaultdict
from datetime import timedelta

from .endpoints import CONTROLLER, GATEWAY
from .errors import ApiError, PermissionDenied
from .output import parse_dt

log = logging.getLogger("aapstats")

MIGRATE, REVIEW, UNNEEDED = 3, 2, 1
LABEL = {MIGRATE: "移行対象", REVIEW: "要確認", UNNEEDED: "移行不要候補"}
CODE = {MIGRATE: "migrate", REVIEW: "review", UNNEEDED: "unneeded"}
CODES = {v: k for k, v in CODE.items()}

TYPE_LABEL = {
    "job_template": "ジョブテンプレート",
    "workflow_job_template": "ワークフロー",
    "project": "プロジェクト",
    "inventory": "インベントリ",
    "credential": "認証情報",
    "credential_type": "認証情報タイプ",
    "execution_environment": "実行環境",
    "team": "チーム",
}
TYPE_ORDER = list(TYPE_LABEL)

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
    "teams",
]

# チームのロールの対象の種類（2.5 以降の content_type と 2.4 以前の resource_type）を、判定の種類にそろえる
ROLE_TYPE = {
    "jobtemplate": "job_template",
    "job_template": "job_template",
    "workflowjobtemplate": "workflow_job_template",
    "workflow_job_template": "workflow_job_template",
    "project": "project",
    "inventory": "inventory",
    "credential": "credential",
    "executionenvironment": "execution_environment",
    "execution_environment": "execution_environment",
    "organization": "organization",
}

MAX_NAMES = 3


def _sf(item, *path):
    cur = item.get("summary_fields") or {}
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _names(refs, status):
    names = sorted({n for s, _, n in refs if s == status and n})
    shown = "、".join(names[:MAX_NAMES])
    return shown + (f" ほか {len(names) - MAX_NAMES} 件" if len(names) > MAX_NAMES else "")


class Judge:
    def __init__(self, ctx, unused_days, now, check_failures=True):
        self.ctx = ctx
        self.client = ctx.client
        self.days = unused_days
        self.border = now - timedelta(days=unused_days)
        self.check_failures = check_failures
        self.items = []  # 判定結果の行
        self.status = {}  # (type, id) → 判定

    # ---------------------------------------------------------------- 共通

    def _put(self, rtype, item, status, reason, org=None, **extra):
        self.status[(rtype, item["id"])] = status
        self.items.append(
            {
                "resource_type": rtype,
                "resource_label": TYPE_LABEL[rtype],
                "id": item["id"],
                "name": item.get("name"),
                "organization": org,
                "classification": LABEL[status],
                "classification_code": CODE[status],
                "reason": reason,
                "decision": "",
                **extra,
            }
        )

    def _from_refs(self, rtype, item, refs, org=None, **extra):
        """refs: [(判定, 参照元の種類の名前, 参照元の名前)] から判定と理由を決める。"""
        if not refs:
            self._put(rtype, item, UNNEEDED, "どこからも使われていない", org, **extra)
            return
        best = max(s for s, _, _ in refs)
        kinds = sorted({k for s, k, _ in refs if s == best})
        if best == UNNEEDED:
            reason = f"移行不要候補の{'・'.join(kinds)}（{_names(refs, best)}）からしか使われていない"
        else:
            reason = f"{LABEL[best]}の{'・'.join(kinds)}（{_names(refs, best)}）から使われている"
        self._put(rtype, item, best, reason, org, **extra)

    def st(self, rtype, oid):
        return self.status.get((rtype, oid))

    # ---------------------------------------------------------- テンプレート

    def _period_results(self, templates):
        """直近 N 日の実行回数と成功回数（実行されたテンプレートだけ API で数える）。"""
        if not self.check_failures:
            return {}
        since = self.border.strftime("%Y-%m-%dT%H:%M:%SZ")

        def one(t):
            rtype, item = t
            source, key = ("jobs", "job_template") if rtype == "job_template" else ("workflow_jobs", "workflow_job_template")
            q = {key: item["id"], "created__gte": since}
            runs = self.client.count_resource(source, q)
            ok = self.client.count_resource(source, dict(q, status="successful")) if runs else 0
            finished = self.client.count_resource(source, dict(q, status__in="successful,failed,error,canceled")) if runs else 0
            return (rtype, item["id"]), (runs, ok, finished)

        log.info("直近 %d 日に実行されたテンプレート %d 件の成功・失敗を数えています", self.days, len(templates))
        return dict(self.client.map_parallel(one, templates))

    def templates(self):
        jts = [("job_template", t) for t in self.ctx.items("job_templates")]
        wfs = [("workflow_job_template", t) for t in self.ctx.items("workflow_job_templates")]
        all_t = jts + wfs
        by_ujt = {item["id"]: (rtype, item) for rtype, item in all_t}

        enabled = defaultdict(list)
        for s in self.ctx.items("schedules"):
            if s.get("enabled") and s.get("unified_job_template"):
                enabled[s["unified_job_template"]].append(s["name"])
        parents = defaultdict(set)  # 呼ばれるテンプレートの ID → 呼び出し元ワークフローの ID
        for n in self.ctx.items("workflow_job_template_nodes"):
            if n.get("unified_job_template") and n.get("workflow_job_template"):
                parents[n["unified_job_template"]].add(n["workflow_job_template"])
        self.parents = parents
        self.enabled = enabled

        active = []
        for rtype, item in all_t:
            dt = parse_dt(item.get("last_job_run"))
            if dt and dt >= self.border:
                active.append((rtype, item))
        results = self._period_results(active)

        status, reason = {}, {}
        for rtype, item in all_t:
            dt = parse_dt(item.get("last_job_run"))
            key = (rtype, item["id"])
            if dt and dt >= self.border:
                runs, ok, finished = results.get(key, (None, None, None))
                if finished and ok == 0:
                    status[key] = REVIEW
                    reason[key] = f"直近 {self.days} 日の実行がすべて失敗している（{finished} 回）"
                else:
                    status[key] = MIGRATE
                    reason[key] = f"直近 {self.days} 日以内に実行されている（最終: {item['last_job_run'][:10]}）"
            else:
                status[key] = None  # 未定。参照関係で決める

        # 呼び出し元の判定に依存するため、変わらなくなるまで繰り返す
        changed = True
        for _ in range(50):  # ワークフローが循環して呼び合っていても止まるように上限を設ける
            if not changed:
                break
            changed = False
            for rtype, item in all_t:
                key = (rtype, item["id"])
                if status[key] in (MIGRATE,) or (status[key] == REVIEW and key in reason and "失敗" in reason[key]):
                    continue
                sched = enabled.get(item["id"], [])
                live_parents = [by_ujt[p][1]["name"] for p in parents.get(item["id"], ()) if p in by_ujt and status.get(("workflow_job_template", p)) in (MIGRATE, REVIEW)]
                new = REVIEW if (sched or live_parents) else UNNEEDED
                if new != status[key]:
                    status[key] = new
                    changed = True
                    when = "一度も実行されていない" if not item.get("last_job_run") else f"{self.days} 日以上実行されていない（最終: {item['last_job_run'][:10]}）"
                    if new == REVIEW:
                        via = []
                        if sched:
                            via.append(f"有効なスケジュール（{'、'.join(sorted(sched)[:MAX_NAMES])}）")
                        if live_parents:
                            via.append(f"ワークフロー（{'、'.join(sorted(live_parents)[:MAX_NAMES])}）")
                        reason[key] = f"{when}が、{'と'.join(via)}から呼ばれている"
                    else:
                        dead = [by_ujt[p][1]["name"] for p in parents.get(item["id"], ()) if p in by_ujt]
                        reason[key] = f"{when}。どこからも呼ばれていない" + (f"（呼び出し元のワークフロー {'、'.join(sorted(dead)[:MAX_NAMES])} も移行不要候補）" if dead else "")

        for rtype, item in all_t:
            key = (rtype, item["id"])
            self._put(rtype, item, status[key], reason[key], _sf(item, "organization", "name"), last_job_run=item.get("last_job_run"))

    # ------------------------------------------------------------ 関連リソース

    def _template_refs(self, field, multi=False):
        """テンプレートからの参照: リソース ID → [(判定, 種類, 名前)]"""
        refs = defaultdict(list)
        for rtype, plural in (("job_template", "job_templates"), ("workflow_job_template", "workflow_job_templates")):
            for t in self.ctx.items(plural):
                s = self.st(rtype, t["id"])
                ids = []
                if multi:
                    ids = [c["id"] for c in (_sf(t, "credentials") or [])]
                    if t.get("webhook_credential"):
                        ids.append(t["webhook_credential"])
                elif t.get(field):
                    ids = [t[field]]
                for i in ids:
                    refs[i].append((s, TYPE_LABEL[rtype], t["name"]))
        return refs

    def _node_refs(self, ujt_id):
        out = []
        for p in self.parents.get(ujt_id, ()):
            s = self.st("workflow_job_template", p)
            if s:
                wf = next((w for w in self.ctx.items("workflow_job_templates") if w["id"] == p), None)
                out.append((s, "ワークフロー", wf["name"] if wf else str(p)))
        return out

    def inventories(self):
        refs = self._template_refs("inventory")
        sources = defaultdict(list)
        for src in self.ctx.items("inventory_sources"):
            sources[src.get("inventory")].append(src)
        for inv in self.ctx.items("inventories"):
            r = list(refs.get(inv["id"], []))
            for src in sources.get(inv["id"], []):
                r += self._node_refs(src["id"])  # ワークフローのインベントリ同期ノード
            self._from_refs("inventory", inv, r, _sf(inv, "organization", "name"))

    def projects(self):
        refs = self._template_refs("project")
        for src in self.ctx.items("inventory_sources"):
            if src.get("source_project"):
                refs[src["source_project"]].append((self.st("inventory", src.get("inventory")) or UNNEEDED, "インベントリソース", src["name"]))
        for p in self.ctx.items("projects"):
            r = list(refs.get(p["id"], [])) + self._node_refs(p["id"])
            self._from_refs("project", p, r, _sf(p, "organization", "name"))

    def _global_default_ee(self):
        """全体の既定の実行環境（settings/system/ の DEFAULT_EXECUTION_ENVIRONMENT）。読めなければ None。"""
        try:
            return self.client.get(self.client.layout.path(CONTROLLER, "settings/system/")).get("DEFAULT_EXECUTION_ENVIRONMENT")
        except ApiError as e:
            log.warning("全体の既定の実行環境を読めないため、判定に含めません（%s）", e)
            return None

    def execution_environments(self):
        refs = self._template_refs("execution_environment")
        orgs = {o["id"]: o["name"] for o in self.ctx.items("organizations")}
        # ジョブテンプレートに実行環境を指定していないときは、プロジェクトの既定 → 組織の既定 → 全体の既定 の順に決まる
        projects = {p["id"]: p for p in self.ctx.items("projects")}
        org_default = {o["id"]: o.get("default_environment") for o in self.ctx.items("organizations")}
        global_default = self._global_default_ee()
        unresolved = []
        for t in self.ctx.items("job_templates"):
            if t.get("execution_environment"):
                continue
            proj = projects.get(t.get("project")) or {}
            implicit = proj.get("default_environment") or org_default.get(t.get("organization")) or global_default
            if implicit:
                refs[implicit].append((self.st("job_template", t["id"]), "ジョブテンプレート（実行環境の指定なし）", t["name"]))
            elif t.get("last_job_run"):
                unresolved.append(t)
        # 全体の既定も設定されていないときは、インストール時に登録した実行環境が使われる。
        # どれが使われるかは API から分からないため、最後に実行したジョブが実際に使った実行環境を参照先とする
        if unresolved:

            def last_ee(t):
                j = self.client.first_resource("jobs", {"job_template": t["id"], "order_by": "-id"})
                return t, (j or {}).get("execution_environment")

            for t, ee in self.client.map_parallel(last_ee, unresolved):
                if ee:
                    refs[ee].append((self.st("job_template", t["id"]), "ジョブテンプレート（最後のジョブが使った実行環境）", t["name"]))
        if global_default:
            refs[global_default].append((MIGRATE, "全体の既定", "DEFAULT_EXECUTION_ENVIRONMENT"))
        for p in self.ctx.items("projects"):
            if p.get("default_environment"):
                refs[p["default_environment"]].append((self.st("project", p["id"]) or UNNEEDED, "プロジェクト", p["name"]))
        for o in self.ctx.items("organizations"):
            if o.get("default_environment"):
                refs[o["default_environment"]].append((MIGRATE, "組織", o["name"]))
        for src in self.ctx.items("inventory_sources"):
            if src.get("execution_environment"):
                refs[src["execution_environment"]].append((self.st("inventory", src.get("inventory")) or UNNEEDED, "インベントリソース", src["name"]))
        for e in self.ctx.items("execution_environments"):
            if e.get("managed"):
                continue
            self._from_refs("execution_environment", e, refs.get(e["id"], []), orgs.get(e.get("organization")))

    def credentials(self):
        refs = self._template_refs(None, multi=True)
        orgs = {o["id"]: o["name"] for o in self.ctx.items("organizations")}
        for p in self.ctx.items("projects"):
            if p.get("credential"):
                refs[p["credential"]].append((self.st("project", p["id"]) or UNNEEDED, "プロジェクト", p["name"]))
        for src in self.ctx.items("inventory_sources"):
            if src.get("credential"):
                refs[src["credential"]].append((self.st("inventory", src.get("inventory")) or UNNEEDED, "インベントリソース", src["name"]))
        for e in self.ctx.items("execution_environments"):
            if e.get("credential"):
                refs[e["credential"]].append((self.st("execution_environment", e["id"]) or MIGRATE, "実行環境", e["name"]))
        for cid in self.ctx.galaxy_credentials_usage():
            refs[cid].append((MIGRATE, "組織", "Galaxy 認証情報"))
        for c in self.ctx.items("credentials"):
            if c.get("managed"):
                continue
            self._from_refs("credential", c, refs.get(c["id"], []), orgs.get(c.get("organization")))

    def credential_types(self):
        refs = defaultdict(list)
        for c in self.ctx.items("credentials"):
            s = self.st("credential", c["id"])
            if s:
                refs[c.get("credential_type")].append((s, "認証情報", c["name"]))
        for t in self.ctx.items("credential_types"):  # 既定の取得条件でカスタムのタイプだけ
            self._from_refs("credential_type", t, refs.get(t["id"], []))

    def teams(self):
        from .resources_extra import _prep_team

        try:
            roles = _prep_team(self.ctx)["roles"]
        except PermissionDenied:
            log.warning("チームのロールを読む権限がないため、チームは判定しません")
            return
        mapped = set()
        if self.client.layout.flavor == GATEWAY:
            try:
                mapped = {m.get("team") for m in self.ctx.items("authenticator_maps") if m.get("map_type") == "team" and m.get("team")}
            except PermissionDenied:
                log.warning("認証のマッピングを読む権限がないため、マッピングで使われているチームを判定に含めません")
        else:
            from .resources_extra import fetch_authenticator_maps

            try:
                mapped = {m.get("team") for m in fetch_authenticator_maps(self.ctx) if m.get("map_type") == "team"}
            except PermissionDenied:
                log.warning("LDAP の設定を読む権限がないため、マッピングで使われているチームを判定に含めません")

        for t in self.ctx.items("teams"):
            org = _sf(t, "organization", "name")
            if t["name"] in mapped:
                self._put("team", t, MIGRATE, "認証のマッピング（LDAP など）で使われている", org)
                continue
            refs = []
            for r in roles.get(t["id"], []):
                rtype = ROLE_TYPE.get(str(r.get("type") or "").replace(" ", "").lower())
                label = f"{r.get('role')}: {r.get('name') or r.get('id')}"
                if rtype == "organization":
                    refs.append((MIGRATE, "組織へのロール", label))
                elif rtype in TYPE_LABEL:
                    s = self.st(rtype, r.get("id"))
                    refs.append((s or REVIEW, f"{TYPE_LABEL[rtype]}へのロール", label))
                else:
                    refs.append((REVIEW, "その他のロール", label))
            if not refs:
                self._put("team", t, UNNEEDED, "ロールがなく、認証のマッピングにも使われていない", org)
            else:
                self._from_refs("team", t, refs, org)

    # ------------------------------------------------------------------ 全体

    def run(self):
        self.templates()
        self.inventories()
        self.projects()
        self.execution_environments()
        self.credentials()
        self.credential_types()
        self.teams()
        self.items.sort(key=lambda r: (TYPE_ORDER.index(r["resource_type"]), -CODES[r["classification_code"]], r["name"] or ""))
        return self.items


def summary(items):
    rows = []
    for rtype in TYPE_ORDER:
        sub = [i for i in items if i["resource_type"] == rtype]
        if not sub:
            continue
        rows.append(
            {
                "resource_label": TYPE_LABEL[rtype],
                "count": len(sub),
                "migrate": sum(1 for i in sub if i["classification_code"] == "migrate"),
                "review": sum(1 for i in sub if i["classification_code"] == "review"),
                "unneeded": sum(1 for i in sub if i["classification_code"] == "unneeded"),
            }
        )
    return rows
