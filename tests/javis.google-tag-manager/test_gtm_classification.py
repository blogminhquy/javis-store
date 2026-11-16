"""How Javis classifies each Google Tag Manager command, and how the pack hands over the Google sign-in.

    JAVIS_OS_DIR=../javis-os python tests/javis.google-tag-manager/test_gtm_classification.py   (no network)

Every gtm_* tool of Stape's server is a combined tool: the command sits in its `action` argument. So the danger
lives in `call_rules`, not in tool names, and `tools/soi-ban-moi.py` (which checks names) cannot see a command
slipping from danger to write. This test pins each command of google-tag-manager-mcp-server 6.0.1 to its group,
using Javis's own `classify`, so an edit of the template that loosens publishing or deleting turns the PR red.
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STORE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("JAVIS_OS_DIR") or (STORE.parent / "javis-os")).resolve()
if not (ROOT / "server" / "mcp_catalog.py").is_file():
    raise SystemExit(f"Javis OS checkout not found at {ROOT} (set JAVIS_OS_DIR)")
os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-test-gtm-"))
sys.path.insert(0, str(ROOT / "server"))

import mcp_catalog  # noqa: E402

PACK = STORE / "packs" / "javis.google-tag-manager"
CON = json.loads((PACK / "connectors" / "google-tag-manager.json").read_text(encoding="utf-8"))

fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def group(tool, action=None):
    return mcp_catalog.classify(CON, tool, {} if action is None else {"action": action})


# Every command of 6.0.1, read from the server's own zod enums (google-tag-manager-mcp-core 3.0.1).
COMMANDS = {
    "gtm_account": ["get", "list", "update"],
    "gtm_built_in_variable": ["create", "list", "remove", "revert"],
    "gtm_client": ["create", "get", "list", "update", "remove", "revert"],
    "gtm_container": ["create", "get", "list", "update", "remove", "combine", "lookup", "moveTagId", "snippet"],
    "gtm_environment": ["create", "get", "list", "update", "remove", "reauthorize"],
    "gtm_folder": ["create", "get", "list", "update", "remove", "revert", "entities", "moveEntitiesToFolder"],
    "gtm_gtag_config": ["create", "get", "list", "update", "remove"],
    "gtm_tag": ["create", "get", "list", "update", "remove", "revert"],
    "gtm_template": ["create", "get", "list", "update", "remove", "revert", "importFromGallery"],
    "gtm_transformation": ["create", "get", "list", "update", "remove", "revert"],
    "gtm_trigger": ["create", "get", "list", "update", "remove", "revert"],
    "gtm_user_permission": ["create", "get", "list", "update", "remove"],
    "gtm_variable": ["create", "get", "list", "update", "remove", "revert"],
    "gtm_version": ["get", "live", "publish", "remove", "setLatest", "undelete", "update"],
    "gtm_version_header": ["list", "latest"],
    "gtm_workspace": ["create", "get", "list", "update", "remove", "createVersion", "getStatus", "sync",
                      "quickPreview", "resolveConflict", "bulkUpdate"],
    "gtm_zone": ["create", "get", "list", "update", "remove", "revert"],
}
READ = {"get", "list", "lookup", "snippet", "entities", "live", "latest", "getStatus"}
# Live site, lost work or other people's access: only at Full access.
DANGER = {"remove", "publish", "revert", "bulkUpdate", "combine", "moveTagId", "reauthorize"}
# Outward even when the verb sounds ordinary: who can use the GTM account, and the account's own settings.
DANGER_TOOLS = {"gtm_user_permission", "gtm_account"}

for tool, actions in COMMANDS.items():
    for a in actions:
        want = ("read" if a in READ else "danger" if (a in DANGER or tool in DANGER_TOOLS) else "write")
        check(f"{tool} {a} -> {want}", group(tool, a) == want)

for tool in ("gtm_guide", "gtm_auth_status", "gtag_destination"):
    check(f"{tool} (no command) is a read", group(tool) == "read")

check("a command Stape adds later falls to danger", group("gtm_tag", "purgeEverything") == "danger")
check("a call without a command falls to danger", group("gtm_tag") == "danger")
check("a whole new gtm_* tool falls to danger until it is classified", group("gtm_new_thing", "frobnicate") == "danger")

# Listing (no arguments yet): a tool with read commands shows at Read only, so the model can browse.
check("gtm_tag is listed at Read only", mcp_catalog.classify(CON, "gtm_tag", None) == "read")

for perm, ok_publish, ok_edit in (("readonly", False, False), ("safe", False, True), ("full", True, True)):
    check(f"{perm}: publish {'allowed' if ok_publish else 'blocked'}",
          mcp_catalog.allowed(CON, perm, "full", "gtm_version", {"action": "publish"})[0] is ok_publish)
    check(f"{perm}: editing a tag {'allowed' if ok_edit else 'blocked'}",
          mcp_catalog.allowed(CON, perm, "full", "gtm_tag", {"action": "update"})[0] is ok_edit)
    check(f"{perm}: listing tags allowed", mcp_catalog.allowed(CON, perm, "full", "gtm_tag", {"action": "list"})[0])

# ---- sign-in: the three variables Stape's server reads, and nothing else ----
check("oauth_env hands over exactly the client pair and the refresh token",
      CON.get("oauth_env") == {"client_id": "GOOGLE_CLIENT_ID", "client_secret": "GOOGLE_CLIENT_SECRET",
                               "refresh_token": "GOOGLE_REFRESH_TOKEN"})
auth = CON.get("auth") or {}
check("Google OAuth with a refresh token", auth.get("type") == "oauth" and auth.get("provider") == "google"
      and (auth.get("authorize_params") or {}).get("access_type") == "offline")
check("in the Google key family, so a saved Google key can be reused", CON.get("key_family") == "google")
check("no scope to add GTM users or change account settings",
      not any(s.endswith(("tagmanager.manage.users", "tagmanager.manage.accounts")) for s in auth.get("scopes", [])))
check("one step with the redirect copy box", sum(1 for s in auth.get("steps", []) if s.get("copy") == "redirect") == 1)
check("the post-sign-in check is a read", group(CON["validate"]["tool"], CON["validate"]["args"].get("action")) == "read")

manifest = (PACK / "javis-pack.yaml").read_text(encoding="utf-8")
check("the pack asks for the Javis that knows oauth_env (0.573.0)", 'compat: {app: ">=0.573.0"}' in manifest)

# soi-ban-moi fills the oauth_env variables, or the server exits at once and is never checked.
spec = importlib.util.spec_from_file_location("soi_ban_moi", STORE / "tools" / "soi-ban-moi.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)
env = S._env_gia(CON, tempfile.mkdtemp(prefix="javis-test-gtm-env-"))
check("the daily check gives the server fake sign-in variables",
      all(env.get(v) == "javis-soi-gia" for v in CON["oauth_env"].values()))

for text in [CON.get("description", ""), CON.get("risk", ""), auth.get("guide", "")] + [
        s.get("text", "") for s in auth.get("steps", [])]:
    if "\u2014" in text or "\u2013" in text:
        check("no long dash in the texts", False)
        break

if fails:
    print(f"\nFAIL - test_gtm_classification: {len(fails)}")
    sys.exit(1)
print("\nOK - test_gtm_classification: all pass")
