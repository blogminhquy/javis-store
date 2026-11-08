"""The javis.zalo pack ships ONE plugin, zalo-extras, holding every Zalo tool (pack 2.0.0).

    JAVIS_OS_DIR=../javis-os python tests/javis.zalo/test_zalo_extras.py

Owner, 2026-11-08: every Zalo feature used to add a plugin of its own (four by pack 1.5.1), so the Plugins page was
fragmented. They were merged into one plugin whose modules keep their own code. What this file guards:

1. ONE PLUGIN. The pack declares exactly one plugin folder and no other folder sits under plugins/: a fifth folder
   is the fragmentation coming back. A new Zalo feature is a module of zalo-extras.
2. NOTHING LOST. The merged plugin registers the 22 tools the four plugins had, each exactly once, and plugin.yaml
   lists exactly those.
3. NO LEVEL CHANGED. Every tool keeps the permission level it had before the merge: a merge that quietly lowered
   "send a friend request" to readonly would let a read-only loop act on Zalo.
4. IT LOADS IN JAVIS. Through the real loader of Javis OS (`plugins_host._import_entry`), and through a bare
   `spec_from_file_location` like the oldest loaders, without relying on package-relative imports.
"""
from _paths import PACK, SERVER  # noqa: F401
import importlib.util
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import yaml  # noqa: E402

import plugins_host  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(extra) + "]") if extra and not cond else ""))
    if not cond:
        fails.append(name)


PLUGIN = PACK / "plugins" / "zalo-extras"

# The level each tool had in its own plugin before the merge (pack 1.5.1).
LEVELS = {
    "zalo_send_image": "full",
    "zalo_group_members": "readonly", "zalo_send_mention": "full", "zalo_create_note": "full",
    "zalo_list_notes": "readonly", "zalo_edit_note": "full", "zalo_create_reminder": "full", "zalo_create_poll": "full",
    "zalo_read_images": "safe",
    "zalo_find_user": "readonly", "zalo_find_phones": "readonly", "zalo_search_friends": "readonly",
    "zalo_friend_status": "readonly", "zalo_friend_requests": "readonly", "zalo_sent_friend_requests": "readonly",
    "zalo_add_friend": "full", "zalo_accept_friend": "full", "zalo_reject_friend": "full",
    "zalo_undo_friend_request": "full", "zalo_friend_aliases": "readonly", "zalo_set_friend_alias": "safe",
    "zalo_remove_friend_alias": "safe",
}


class Ctx:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kw):
        self.tools.append(kw)


# ---- 1. One plugin
pack = yaml.safe_load((PACK / "javis-pack.yaml").read_text(encoding="utf-8"))
check("the pack declares exactly one plugin", (pack.get("provides") or {}).get("plugins") == ["plugins/zalo-extras"],
      pack.get("provides"))
folders = sorted(d.name for d in (PACK / "plugins").iterdir() if d.is_dir() and not d.name.startswith("__"))
check("no other plugin folder in the pack (a new Zalo feature is a module of zalo-extras)", folders == ["zalo-extras"],
      folders)
manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))
check("plugin.yaml: slug zalo-extras, on inside the pack, strongest level shown", manifest.get("slug") == "zalo-extras"
      and manifest.get("enabled") is True and manifest.get("min_mode") == "full" and manifest.get("hooks") == [])
check("plugin version follows the pack", manifest.get("version") == pack.get("version"),
      (manifest.get("version"), pack.get("version")))

# ---- 2 and 3. Through the real Javis loader
module = plugins_host._import_entry("zalo-extras", "pack", PLUGIN / "plugin.py")
c = Ctx()
module.register(c)
names = [t["name"] for t in c.tools]
check("every tool registered exactly once", len(names) == len(set(names)), names)
check("the 22 tools of the four former plugins, nothing lost, nothing extra", sorted(names) == sorted(LEVELS),
      sorted(set(LEVELS) ^ set(names)))
check("plugin.yaml lists exactly the registered tools", sorted(manifest.get("tools") or []) == sorted(names),
      sorted(set(manifest.get("tools") or []) ^ set(names)))
changed = {t["name"]: t["min_mode"] for t in c.tools if t["min_mode"] != LEVELS.get(t["name"])}
check("CANARY: no tool's permission level changed in the merge", not changed, changed)
check("every tool has a handler and a readiness check", all(callable(t["handler"]) and callable(t.get("check_fn"))
                                                            for t in c.tools))

# ---- 4. Like the oldest loaders: a bare module, no package semantics
spec = importlib.util.spec_from_file_location("zalo_extras_bare", PLUGIN / "plugin.py")
bare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bare)
c2 = Ctx()
bare.register(c2)
check("loads without package-relative imports", sorted(t["name"] for t in c2.tools) == sorted(LEVELS))

for f in [PLUGIN / "plugin.py", PLUGIN / "plugin.yaml", PACK / "javis-pack.yaml", __import__("pathlib").Path(__file__)]:
    check(f"no em dash in {f.name}", chr(0x2014) not in f.read_text(encoding="utf-8"))

if fails:
    print("\nFAIL - test_zalo_extras: " + str(len(fails)) + " lỗi: " + ", ".join(fails))
    sys.exit(1)
print("\nOK - test_zalo_extras: tất cả pass")
