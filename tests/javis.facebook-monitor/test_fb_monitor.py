"""Plugin `fb-monitor-apify` of the pack javis.facebook-monitor (tool `fb_monitor`).

    JAVIS_OS_DIR=../javis-os python tests/javis.facebook-monitor/test_fb_monitor.py

No network (`_token` and `_run` are faked). This test came over from Javis OS (`tests/python/test_fb_monitor.py`)
together with the plugin, when the plugin left the app for this pack (owner decision, 2026-11-12). It covers: the
pack declares the connector AND the plugin, the plugin registers one read-only tool, the gate without a token,
Page and Group URLs going to the right actor, the share count under its several field names, post normalising,
the min_shares filter, the order by shares, and errors folded into the answer.
"""
from _paths import PACK, ROOT, SERVER  # noqa: E402,F401  - puts server/ of Javis OS on sys.path
import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-fbmon-"))

_fails = []
def check(n, c):
    print(("ok  " if c else "FAIL ") + n)
    if not c: _fails.append(n)


# ---- 1. The pack: connector + plugin, one version, and never next to the bundled copy ----
manifest = (PACK / "javis-pack.yaml").read_text(encoding="utf-8")
plugin_yaml = (PACK / "plugins" / "fb-monitor-apify" / "plugin.yaml").read_text(encoding="utf-8")
pack_version = re.search(r"^version:\s*(\S+)", manifest, re.M).group(1)
check("pack: provides the facebook-monitor connector and the fb-monitor-apify plugin",
      "connectors/facebook-monitor.json" in manifest and "plugins/fb-monitor-apify" in manifest)
check("pack: only one plugin folder", sorted(p.name for p in (PACK / "plugins").iterdir()) == ["fb-monitor-apify"])
check("pack: plugin.yaml carries the pack's version",
      re.search(r"^version:\s*(\S+)", plugin_yaml, re.M).group(1) == pack_version)
compat = re.search(r'app:\s*">=(\d+)\.(\d+)\.(\d+)"', manifest)
# Javis before 0.548.0 still ships this plugin inside the app; installing the pack there would register fb_monitor twice.
check("pack: compat keeps it off app versions that still bundle the plugin",
      bool(compat) and tuple(int(x) for x in compat.groups()) >= (0, 548, 0))
check("plugin.yaml: slug, tool and level unchanged",
      "slug: fb-monitor-apify" in plugin_yaml and "- fb_monitor" in plugin_yaml and "min_mode: readonly" in plugin_yaml)
check("Javis OS no longer bundles the plugin (the checkout under test is new enough)",
      not (ROOT / "system" / "plugins" / "fb-monitor-apify").exists())


# ---- 2. Plugin nạp ----
spec = importlib.util.spec_from_file_location(
    "fb_monitor_test", str(PACK / "plugins" / "fb-monitor-apify" / "plugin.py"))
plug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plug)


class _Ctx:
    def __init__(self): self.tools = []
    def register_tool(self, name, description, handler, schema=None, min_mode="readonly", check_fn=None, **k):
        self.tools.append({"name": name, "min_mode": min_mode})


ctx = _Ctx()
plug.register(ctx)
check("plugin: có tool fb_monitor readonly",
      len(ctx.tools) == 1 and ctx.tools[0]["name"] == "fb_monitor" and ctx.tools[0]["min_mode"] == "readonly")

plug._connected_id = lambda: None
check("plugin: _check chặn khi chưa có token", "Chưa kết nối" in (plug._check() or ""))


# ---- 3. Helper thuần: split URL, bóc share, chuẩn hoá ----
check("_split_urls: chuỗi nhiều dòng/phẩy → list",
      plug._split_urls("https://fb.com/a,\n https://fb.com/groups/b") == ["https://fb.com/a", "https://fb.com/groups/b"])
check("_shares: bóc nhiều tên trường", plug._shares({"shareCount": "1,234"}) == 1234 and plug._shares({"shares": 7}) == 7)
n = plug._norm({"text": "hello", "postUrl": "u1", "shares": 50, "likesCount": 10, "commentsCount": 3,
                "author": {"name": "Ann"}, "groupTitle": "Deal Hot"})
check("_norm: chuẩn hoá bài", n["shares"] == 50 and n["reactions"] == 10 and n["author"] == "Ann" and n["source"] == "Deal Hot")


# ---- 4. fb_monitor (giả _token + _run) ----
async def monitor_tests():
    plug._connected_id = lambda: "cfm"
    plug._token = lambda: "APIFYTOK"

    seen = {"actors": []}
    async def _fake_run(actor, token, input_obj):
        seen["actors"].append((actor, [s["url"] for s in input_obj["startUrls"]], input_obj["resultsLimit"], token))
        if actor == plug.PAGES_ACTOR:
            return [{"text": "page viral", "postUrl": "p1", "shares": 300, "likesCount": 20},
                    {"text": "page nhẹ", "postUrl": "p2", "shares": 5}]
        if actor == plug.GROUPS_ACTOR:
            return [{"text": "group hot", "url": "g1", "shares": 999, "groupTitle": "G"}]
        return []
    plug._run = _fake_run

    r = await plug._monitor({"urls": "https://fb.com/pageA, https://fb.com/groups/123", "min_shares": 10, "limit": 25}, None)
    d = json.loads(r)
    actors = {a[0] for a in seen["actors"]}
    check("fb_monitor: gọi CẢ actor Trang lẫn actor Nhóm", actors == {plug.PAGES_ACTOR, plug.GROUPS_ACTOR})
    # nhóm đi đúng actor nhóm
    grp_call = next(a for a in seen["actors"] if a[0] == plug.GROUPS_ACTOR)
    check("fb_monitor: URL /groups/ → actor nhóm", grp_call[1] == ["https://fb.com/groups/123"] and grp_call[2] == 25)
    check("fb_monitor: token truyền vào Apify", all(a[3] == "APIFYTOK" for a in seen["actors"]))
    # lọc min_shares (bỏ bài 5 share) + sắp giảm dần theo share
    shares = [p["shares"] for p in d["posts"]]
    check("fb_monitor: lọc min_shares + sắp theo share giảm dần", shares == [999, 300])
    check("fb_monitor: bài viral nhất lên đầu", d["posts"][0]["text"] == "group hot")

    async def _fake_mixed(actor, token, input_obj):
        return [{"error": "not_available", "errorDescription": "login required",
                 "url": "https://fb.com/blocked"},
                {"text": "real post", "postUrl": "p3", "shares": 20}]
    plug._run = _fake_mixed
    mixed = json.loads(await plug._monitor({"urls": "https://fb.com/pageA"}, None))
    check("fb_monitor: bản ghi lỗi không bị đếm thành bài viết",
          len(mixed["posts"]) == 1 and mixed["posts"][0]["text"] == "real post")
    check("fb_monitor: lỗi actor xuất hiện trong danh sách lỗi",
          any("not_available" in e for e in mixed["errors"]))

    # thiếu urls → ERROR
    r_no = await plug._monitor({}, None)
    check("fb_monitor: thiếu urls → ERROR", r_no.startswith("ERROR"))

    # Apify lỗi trên mọi actor → ERROR gộp
    async def _fake_err(actor, token, input_obj):
        return {"__error": "token sai"}
    plug._run = _fake_err
    r_err = await plug._monitor({"urls": "https://fb.com/pageA"}, None)
    check("fb_monitor: Apify lỗi → ERROR", r_err.startswith("ERROR") and "token sai" in r_err)

asyncio.run(monitor_tests())

if _fails:
    print(f"\nFAIL - {len(_fails)} test: {_fails}")
    sys.exit(1)
print("\nOK - test_fb_monitor: tất cả pass")
