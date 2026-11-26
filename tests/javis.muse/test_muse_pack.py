# -*- coding: utf-8 -*-
"""javis.muse 1.3.0 installed into a real Javis OS: the installer, the plugin loader, the tool levels and the page
routes, against a fake Muse Gateway (no network).

    JAVIS_OS_DIR=../javis-os python tests/javis.muse/test_muse_pack.py

Locks:
1. The pack: one plugin folder, its version is the pack's, no binary of any kind (nobody can review a compiled .exe,
   and the 1.2.0 bundle carried muse-login.exe and its DLLs), compat at least the Javis that knows `op_modes`.
2. The real installer accepts the zip, and the plugin loads with no error and registers its three tools.
3. Levels, with the run modes Javis really uses (suggest = Chỉ đọc, auto = Ghi nháp, full): op=list reads under
   suggest; op=check, op=import_file and muse_batch_video are refused there and run under auto. The levels are the
   ones 1.2.0 had.
4. Page routes: a browser without the owner's session gets nothing; the owner gets the status even with no gateway
   (a status, not a crash), saves the gateway address and key, pastes cookies, sets the video limit. The admin key
   and the cookies never come back in any answer or land in the plugin's state.
5. The hand-installed copy (`_state/plugins/muse-accounts`, how 1.2.0 was installed) shadows the pack's plugin
   while it is still there: one set of tools, never two, and the same data folder.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True   # the installed copy is loaded by Javis; nothing may appear inside packs/
import _paths  # noqa: F401,E402
from _paths import PACK, PLUGIN  # noqa: E402

import asyncio
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-muse-store-")
os.environ["JAVIS_REQUIRE_LOGIN"] = "1"
for _k in ("MUSE_GATEWAY_URL", "MUSE_ADMIN_KEY", "MUSE_PLUGIN_DATA", "MUSE_LOGIN_EXE"):
    os.environ.pop(_k, None)
os.environ["MUSE_GATEWAY_CONFIG"] = str(Path(os.environ["JAVIS_STATE_DIR"]) / "khong-co-gateway.json")

from fastapi.testclient import TestClient   # noqa: E402
import config as cfg   # noqa: E402
import main            # noqa: E402
import pack_install    # noqa: E402
import plugins_host    # noqa: E402
from config import STATE_DIR  # noqa: E402

from _gia_gateway import KEY, Fake, start  # noqa: E402

_fails = []


def check(name, cond, more=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{more}]" if more and not cond else ""))
    if not cond:
        _fails.append(name)


# ============================================================
# 1. The pack as it sits in the repo
# ============================================================
manifest = (PACK / "javis-pack.yaml").read_text(encoding="utf-8")
plugin_yaml = (PLUGIN / "plugin.yaml").read_text(encoding="utf-8")
ver = re.search(r"^version:\s*(\S+)", manifest, re.M).group(1)
check("pack: one plugin folder", sorted(p.name for p in (PACK / "plugins").iterdir()) == ["muse-accounts"])
check("pack: plugin.yaml carries the pack's version", re.search(r"^version:\s*(\S+)", plugin_yaml, re.M).group(1) == ver)
check("pack: slug muse-accounts kept (data of the hand-installed copy carries over)", "slug: muse-accounts" in plugin_yaml)
files = [p for p in PACK.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
binaries = [p.name for p in files if p.suffix.lower() in (".exe", ".dll", ".pdb", ".so", ".dylib", ".bin")
            or (p.suffix not in (".png",) and b"\0" in p.read_bytes()[:4096])]
check("pack: no binary file (only reviewable text, plus the logo)", not binaries, binaries)
compat = re.search(r'app:\s*">=(\d+)\.(\d+)\.(\d+)"', manifest)
check("pack: compat needs a Javis that knows op_modes (0.491.0+)",
      bool(compat) and tuple(int(x) for x in compat.groups()) >= (0, 491, 0))
check("pack: no em dash or en dash in any file",
      not [p.name for p in files if p.suffix != ".png" and (chr(0x2014) in p.read_text(encoding="utf-8")
                                                            or chr(0x2013) in p.read_text(encoding="utf-8"))])


# ============================================================
# 2. Real installer, real loader
# ============================================================
def zip_dir(d: Path) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for f in sorted(d.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.writestr(f.relative_to(d).as_posix(), f.read_bytes())
    return b.getvalue()


s = pack_install.soi(zip_dir(PACK), "javis-muse.zip")
check("installer reads the pack", s.get("ok"), s)
r = pack_install.cai(s["staging_id"], s["sha256"], enable=True)
check("installer installs it", r.get("ok"), r)
plugins_host.invalidate()

info = next((p for p in plugins_host.describe() if p["slug"] == "muse-accounts"), None)
check("plugin listed from the pack", bool(info) and info["source"] == "pack", info)
tools, route = plugins_host.plugin_tools("readonly")
names = sorted(t["name"] for t in tools if t.get("namespace") == "muse-accounts")
check("three tools registered", names == ["muse_accounts", "muse_batch_status", "muse_batch_video"], names)
check("plugin loaded without error", not (plugins_host.describe() and next(
    p for p in plugins_host.describe() if p["slug"] == "muse-accounts")["error"]))


# ============================================================
# 3. Levels at call time
# ============================================================
gw_url, servers = start()


def call(mode, tool, args):
    _, rt = plugins_host.plugin_tools(mode)
    return asyncio.run(rt[tool]["call"](args))


def refused(out):
    return isinstance(out, str) and out.startswith("ERROR: tool") and "cần mức quyền cao hơn" in out


out = call("suggest", "muse_accounts", {"op": "list"})
check("op=list runs under suggest (gateway down is a status)", '"ok": false' in out and "gateway" in out, out[:200])
for args in ({"op": "check"}, {"op": "import_file"}, {"op": "mode", "mode": "rotate"}, {"op": "renew"},
             {"op": "login"}, {"op": "khong-co"}):
    check(f"muse_accounts op={args['op']} refused under suggest", refused(call("suggest", "muse_accounts", args)))
check("muse_batch_video refused under suggest",
      refused(call("suggest", "muse_batch_video", {"scenes": ["x"]})))
check("muse_batch_status runs under suggest", call("suggest", "muse_batch_status", {}) == "Chưa có lô video nào.")
out = call("auto", "muse_accounts", {"op": "check"})
check("op=check runs under auto (reaches the gateway step)", not refused(out), out[:200])
check("nothing refused under suggest changed the mode", '"mode": "pool"' in call("suggest", "muse_accounts", {"op": "list"}))
check("muse_batch_video with out_dir outside the brain refused even at full",
      call("full", "muse_batch_video", {"scenes": ["x"], "out_dir": "/etc"}).startswith("ERROR:"))


# ============================================================
# 4. Page routes
# ============================================================
_s = cfg.read_settings()
_s["auth"] = {"username": "admin", **dict(zip(("password_hash", "salt"), cfg.hash_password("matkhau123")))}
cfg.write_settings(_s)
stranger = TestClient(main.app)
owner = TestClient(main.app)
check("owner logs in", owner.post("/auth/login", data={"username": "admin", "password": "matkhau123"}).json().get("ok"))
O = {"Origin": "http://testserver"}

r = stranger.get("/ext/muse-accounts/state")
check("no session: state refused", r.status_code in (401, 403) or "accounts" not in r.text, r.status_code)
r = stranger.post("/ext/muse-accounts/gateway", json={"url": gw_url, "key": "x"}, headers=O)
check("no session: cannot change the gateway", r.status_code in (401, 403), r.status_code)

st = owner.get("/ext/muse-accounts/state").json()
check("owner: state with no gateway is a readable status that says what to do",
      st.get("gateway", {}).get("ok") is False and "Nối gateway" in st["gateway"]["error"], st)
r = owner.post("/ext/muse-accounts/gateway", json={"url": "http://user:pw@127.0.0.1:1", "key": "x"}, headers=O).json()
check("owner: an address with a password in it is refused", r.get("error", "").startswith("Địa chỉ gateway"), r)
r = owner.post("/ext/muse-accounts/gateway", json={"url": gw_url, "key": KEY}, headers=O).json()
check("owner: saves the gateway and it answers", r == {"ok": True, "loi": ""}, r)
st = owner.get("/ext/muse-accounts/state").json()
check("state: connected, key saved on the page", st["gateway"]["ok"] and st["gateway"]["key_from"] == "trang", st)

PASTE = "Ánh | datr=a; hatch_sess=SESS_SECRET_1\nBình | hatch_sess=SESS_SECRET_2_BAD"
r = owner.post("/ext/muse-accounts/import", json={"text": PASTE}, headers=O).json()
check("owner: paste two accounts", [a["ok"] for a in r.get("added", [])] == [True, False], r)
r = owner.post("/ext/muse-accounts/limit", json={"video_limit": 3}, headers=O).json()
check("owner: video limit on every account", r == {"video_limit": 3}
      and all(a["limits"]["video"] == 3 for a in Fake.accounts), r)
r = owner.post("/ext/muse-accounts/check", json={}, headers=O).json()
check("owner: check switches off the broken one", [d["name"] for d in r.get("disabled", [])] == ["Bình"], r)
r = owner.post("/ext/muse-accounts/remove", json={"id": "khong-co"}, headers=O).json()
check("owner: removing a missing account is a sentence, not a crash", r.get("error", "").startswith("Không thấy"), r)
st_text = owner.get("/ext/muse-accounts/state").text
data_dir = STATE_DIR / "plugin-data" / "muse-accounts"
on_disk = "".join(p.read_text(encoding="utf-8") for p in data_dir.rglob("state.json"))
check("no cookie and no admin key in the page's answers", not any(x in st_text for x in ("SESS_SECRET", KEY)))
check("no cookie in the plugin's state file", "SESS_SECRET" not in on_disk and KEY not in on_disk)
check("the key sits only in gateway.json of the plugin's data folder",
      KEY in (data_dir / "gateway.json").read_text(encoding="utf-8"))
r = owner.get("/ext/muse-accounts/khong-co")
check("unknown route under the plugin: not found", r.status_code == 404, r.status_code)


# ============================================================
# 5. Hand-installed copy shadows the pack's plugin
# ============================================================
hand = STATE_DIR / "plugins" / "muse-accounts"
shutil.copytree(PLUGIN, hand)
(hand / "plugin.yaml").write_text(plugin_yaml.replace(f"version: {ver}", "version: 1.2.0"), encoding="utf-8")
plugins_host.invalidate()
mine = [p for p in plugins_host.describe() if p["slug"] == "muse-accounts"]
check("hand-installed copy wins while it is there (one plugin listed, source user)",
      len(mine) == 1 and mine[0]["source"] == "user" and mine[0]["version"] == "1.2.0", mine)
tools, _ = plugins_host.plugin_tools("full")
check("tools registered once", sorted(t["name"] for t in tools if t["name"].startswith("muse_"))
      == ["muse_accounts", "muse_batch_status", "muse_batch_video"])
shutil.rmtree(hand)
plugins_host.invalidate()
check("removing the hand-installed copy brings the pack's plugin back",
      next(p for p in plugins_host.describe() if p["slug"] == "muse-accounts")["source"] == "pack")
st = owner.get("/ext/muse-accounts/state").json()
check("same data folder before and after (accounts, saved gateway)", st["gateway"]["key_from"] == "trang"
      and len(st["accounts"]) == 2, st.get("gateway"))

for srv in servers:
    srv.shutdown()
print()
if _fails:
    print(f"FAIL {len(_fails)}: " + "; ".join(_fails))
    raise SystemExit(1)
print("ALL PASS")
