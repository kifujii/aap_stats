"""追加リソース（R2-2）: チーム、カスタム認証情報タイプ、Authentication Method、認証のマッピング。

バージョンによる取得元の違い:
- チームのロール: 2.5 以降は Controller の role_team_assignments を一括で取得する。
  2.4 以前はチームごとに teams/<id>/roles/ を取得する
- チームのメンバー数: 2.5 以降は Gateway のチーム（ansible_id で突き合わせる）、2.4 以前は Controller
- Authentication Method とマッピング: 2.5 以降は Gateway の authenticators / authenticator_maps。
  2.4 以前は Controller の LDAP 設定（settings/ldap/）。追加の LDAP サーバー（1〜5）も含める

秘密の値（R2-6）:
- 設定の中の項目は、名前（PASSWORD、SECRET、TOKEN、KEY、PRIVATE を含む）か値（$encrypted$）で判断して伏せる。
  Gateway の LDAP プラグインは BIND_PASSWORD を秘密の型として定義していないため、型の情報には頼らない
- 伏せた項目の名前は masked_keys 列に出す。移行先で入力し直す必要がある項目だと分かるようにするため
"""

import logging
import re
from collections import Counter, defaultdict

from .endpoints import CONTROLLER, GATEWAY, GATEWAY_SVC
from .errors import PermissionDenied

log = logging.getLogger("aapstats")

SECRET_KEY_RE = re.compile(r"PASSWORD|SECRET|TOKEN|PRIVATE|(^|_)KEY$", re.IGNORECASE)
ENCRYPTED = "$encrypted$"


def sanitize(value, path=""):
    """秘密の項目を取り除いた値と、取り除いた項目の名前の一覧を返す。"""
    masked = []
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key_path = f"{path}.{k}" if path else str(k)
            if SECRET_KEY_RE.search(str(k)) or v == ENCRYPTED:
                masked.append(key_path)
                continue
            out[k], sub = sanitize(v, key_path)
            masked += sub
        return out, masked
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            if v == ENCRYPTED:
                masked.append(f"{path}[{i}]")
                continue
            sv, sub = sanitize(v, f"{path}[{i}]")
            out.append(sv)
            masked += sub
        return out, masked
    return value, masked


def _sf(item, *path):
    cur = item.get("summary_fields") or {}
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _user(item, key):
    return _sf(item, key, "username")


def _search_base(search):
    """LDAP の検索条件 [base_dn, scope, filter] を文字列にする。"""
    if isinstance(search, (list, tuple)) and search:
        return " ".join(str(x) for x in search)
    return search or None


# ======================================================================= チーム


def fetch_teams(ctx):
    return ctx.items("teams")


def _prep_team(ctx):
    client = ctx.client
    teams = ctx.items("teams")
    roles = defaultdict(list)  # チーム ID → [{"role", "type", "id", "name"}]
    members = {}

    if client.layout.flavor == GATEWAY:
        for a in ctx.items("role_team_assignments"):
            obj = _sf(a, "content_object") or {}
            roles[a.get("team")].append(
                {
                    "role": _sf(a, "role_definition", "name"),
                    "type": (a.get("content_type") or "").split(".")[-1] or None,
                    "id": int(a["object_id"]) if str(a.get("object_id") or "").isdigit() else a.get("object_id"),
                    "name": obj.get("name"),
                }
            )
        # メンバーは Gateway で管理している。Controller と Gateway のチームは ansible_id で突き合わせる
        gw_by_ansible = {}
        try:
            gw_by_ansible = {_sf(t, "resource", "ansible_id"): t for t in ctx.items("gateway_teams")}
        except PermissionDenied:
            log.warning("Gateway のチームを読む権限がないため、メンバー数は空欄になります")

        def count_members(team):
            gw = gw_by_ansible.get(_sf(team, "resource", "ansible_id"))
            if not gw:
                return team["id"], None
            return team["id"], client.count(client.layout.path(GATEWAY_SVC, f"teams/{gw['id']}/users/"))

    else:

        def team_roles(team):
            url = (team.get("related") or {}).get("roles") or client.layout.path(CONTROLLER, f"teams/{team['id']}/roles/")
            out = []
            for r in client.paginate(url):
                out.append(
                    {
                        "role": r.get("name"),
                        "type": _sf(r, "resource_type"),
                        "id": _sf(r, "resource_id"),
                        "name": _sf(r, "resource_name"),
                    }
                )
            return team["id"], out

        for tid, rs in client.map_parallel(team_roles, teams):
            roles[tid] = rs

        def count_members(team):
            return team["id"], client.count(client.layout.path(CONTROLLER, f"teams/{team['id']}/users/"))

    members = dict(client.map_parallel(count_members, teams))
    return {"roles": roles, "members": members}


def _build_team(t, ctx, pre):
    rs = sorted(pre["roles"].get(t["id"], []), key=lambda r: (r["type"] or "", r["name"] or "", r["role"] or ""))
    return {
        "id": t["id"],
        "name": t["name"],
        "organization": _sf(t, "organization", "name"),
        "member_count": pre["members"].get(t["id"]),
        "role_count": len(rs),
        "roles": [f"{r['role']}: {r['name'] or r['id']}" for r in rs],
        "role_targets": rs,
        "description": t.get("description"),
        "created": t.get("created"),
        "created_by": _user(t, "created_by"),
        "modified": t.get("modified"),
        "modified_by": _user(t, "modified_by"),
        "organization_id": t.get("organization"),
    }


# ======================================================= カスタム認証情報タイプ


def _prep_credential_type(ctx):
    creds = defaultdict(list)
    for c in ctx.items("credentials"):
        creds[c.get("credential_type")].append(c["name"])
    return {"creds": creds}


def _build_credential_type(t, ctx, pre):
    fields = (t.get("inputs") or {}).get("fields") or []
    injectors = t.get("injectors") or {}
    used = sorted(pre["creds"].get(t["id"], []))
    return {
        "id": t["id"],
        "name": t["name"],
        "kind": t.get("kind"),
        # 入力項目は名前だけを出す（既定値などは出さない）
        "input_fields": [f.get("id") for f in fields],
        "secret_fields": [f.get("id") for f in fields if f.get("secret")],
        "injector_env": sorted((injectors.get("env") or {}).keys()),
        "injector_extra_vars": sorted((injectors.get("extra_vars") or {}).keys()),
        "injector_files": sorted((injectors.get("file") or {}).keys()),
        "credential_count": len(used),
        "credentials": used,
        "injectors": injectors,
        "description": t.get("description"),
        "created": t.get("created"),
        "created_by": _user(t, "created_by"),
        "modified": t.get("modified"),
        "modified_by": _user(t, "modified_by"),
    }


# ================================================== Authentication Method


def _short_type(t):
    return (t or "").rsplit(".", 1)[-1] or None


LEGACY_LDAP_PREFIXES = ["AUTH_LDAP_"] + [f"AUTH_LDAP_{i}_" for i in range(1, 6)]


def _legacy_ldap_settings(ctx):
    cache = getattr(ctx, "_legacy_ldap", None)
    if cache is None:
        client = ctx.client
        try:
            cache = client.get(client.layout.path(CONTROLLER, "settings/ldap/"))
        except PermissionDenied:
            raise PermissionDenied(
                "LDAP の設定を読む権限がありません。システム管理者のユーザーで実行してください", status=403
            ) from None
        ctx._legacy_ldap = cache
    return cache


def fetch_authenticators(ctx):
    """2.5 以降は Gateway の一覧。2.4 以前は LDAP 設定を、サーバーごとの擬似的な行にする。"""
    if ctx.client.layout.flavor == GATEWAY:
        try:
            return ctx.items("authenticators")
        except PermissionDenied:
            raise PermissionDenied(
                "Authentication Method を読む権限がありません。システム管理者のユーザーで実行してください", status=403
            ) from None
    settings = _legacy_ldap_settings(ctx)
    rows = []
    for i, prefix in enumerate(LEGACY_LDAP_PREFIXES):
        conf = {k[len(prefix):]: v for k, v in settings.items() if k.startswith(prefix) and not k[len(prefix):][:2].rstrip("_").isdigit()}
        if not conf.get("SERVER_URI"):
            continue
        rows.append(
            {
                "id": i,
                "name": "LDAP（既定）" if i == 0 else f"LDAP {i}",
                "type": "legacy.ldap",
                "enabled": True,
                "order": i,
                "configuration": conf,
                "_legacy_prefix": prefix,
            }
        )
    return rows


def _prep_authenticator(ctx):
    counts = Counter()
    if ctx.client.layout.flavor == GATEWAY:
        try:
            counts = Counter(m.get("authenticator") for m in ctx.items("authenticator_maps"))
        except PermissionDenied:
            log.warning("認証のマッピングを読む権限がないため、map_count は空欄になります")
            counts = None
    else:
        for a in fetch_authenticators(ctx):
            conf = a["configuration"]
            counts[a["id"]] = len(conf.get("ORGANIZATION_MAP") or {}) + len(conf.get("TEAM_MAP") or {})
    return {"maps": counts}


def _build_authenticator(a, ctx, pre):
    conf, masked = sanitize(a.get("configuration") or {})
    server_uri = conf.get("SERVER_URI")
    return {
        "id": a["id"],
        "name": a["name"],
        "type": _short_type(a.get("type")),
        "enabled": a.get("enabled"),
        "order": a.get("order"),
        "server_uri": server_uri if not isinstance(server_uri, list) else list(server_uri),
        "bind_dn": conf.get("BIND_DN") or None,
        "user_search": _search_base(conf.get("USER_SEARCH")),
        "group_search": _search_base(conf.get("GROUP_SEARCH")),
        "map_count": None if pre["maps"] is None else pre["maps"].get(a["id"], 0),
        "user_dn_template": conf.get("USER_DN_TEMPLATE") or None,
        "group_type": conf.get("GROUP_TYPE") or None,
        "start_tls": conf.get("START_TLS"),
        "user_attr_map": conf.get("USER_ATTR_MAP") or None,
        "create_objects": a.get("create_objects"),
        "remove_users": a.get("remove_users"),
        "masked_keys": masked,
        "configuration": conf,
        "created": a.get("created"),
        "created_by": _user(a, "created_by"),
        "modified": a.get("modified"),
        "modified_by": _user(a, "modified_by"),
    }


# ============================================================ 認証のマッピング


def fetch_authenticator_maps(ctx):
    if ctx.client.layout.flavor == GATEWAY:
        return ctx.items("authenticator_maps")
    # 2.4 以前: LDAP 設定の ORGANIZATION_MAP / TEAM_MAP を、マッピングごとの行にする
    rows = []
    for a in fetch_authenticators(ctx):
        conf = a["configuration"]
        for org, rule in (conf.get("ORGANIZATION_MAP") or {}).items():
            rows.append({"id": f"{a['id']}-org-{org}", "name": org, "authenticator": a["id"], "_authenticator_name": a["name"], "map_type": "organization", "organization": org, "triggers": rule})
        for team, rule in (conf.get("TEAM_MAP") or {}).items():
            rule = dict(rule or {})
            rows.append({"id": f"{a['id']}-team-{team}", "name": team, "authenticator": a["id"], "_authenticator_name": a["name"], "map_type": "team", "team": team, "organization": rule.pop("organization", None), "triggers": rule})
    return rows


def _prep_authenticator_map(ctx):
    names = {}
    if ctx.client.layout.flavor == GATEWAY:
        try:
            names = {a["id"]: a["name"] for a in ctx.items("authenticators")}
        except PermissionDenied:
            pass
    return {"auth_names": names}


def _build_authenticator_map(m, ctx, pre):
    triggers, masked = sanitize(m.get("triggers") or {})
    return {
        "id": m["id"],
        "name": m.get("name"),
        "authenticator": m.get("_authenticator_name") or pre["auth_names"].get(m.get("authenticator")) or m.get("authenticator"),
        "map_type": m.get("map_type"),
        "organization": m.get("organization") if not isinstance(m.get("organization"), int) else _sf(m, "organization", "name") or m.get("organization"),
        "team": m.get("team") if not isinstance(m.get("team"), int) else _sf(m, "team", "name") or m.get("team"),
        "role": m.get("role"),
        "triggers": triggers,
        "revoke": m.get("revoke"),
        "order": m.get("order"),
        "masked_keys": masked,
        "created": m.get("created"),
        "modified": m.get("modified"),
    }
