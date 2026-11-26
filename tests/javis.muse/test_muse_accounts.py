# -*- coding: utf-8 -*-
"""Plugin `muse-accounts` of the pack javis.muse, against a fake Muse Gateway (no network, never the real gateway).

    JAVIS_OS_DIR=../javis-os python tests/javis.muse/test_muse_accounts.py

Came over from the hand-installed plugin 1.2.0 (`test_muse_accounts.py` next to it) and covers the same ground:
pasting cookies (names, duplicates, no cookie kept on disk), Pool and Rotate, removing an account, batch video with
retries and files in scene order, cookie renewal from a login profile. Added for the store (1.3.0):
- a pasted cookie with the name of exactly one account replaces that account's cookie;
- paths a tool call names stay inside the brain or the plugin's data folder (import_file reads then DELETES);
- the admin key goes to the gateway only: videos come from /v1/jobs/<id>/content, never from output.url;
- gateway address and key saved on the page win over environment and config.json, and never come back out;
- a gateway that is down is a status on the page, not an exception;
- muse-login.exe runs only when MUSE_LOGIN_EXE names it; no owner's name in what the owner reads.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True   # loading plugin.py must not leave __pycache__ inside the pack (the zip is compared)
import _paths  # noqa: F401,E402  - Javis OS on sys.path, like every code-pack test here
from _paths import PLUGIN  # noqa: E402

import asyncio
import importlib.util
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path

from _gia_gateway import KEY, Fake, Ngoai, start


class _Ctx:
    def __init__(self, data_dir, vault_root=None):
        self.data_dir, self.vault_root = Path(data_dir), vault_root


class PluginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="muse-plugin-test-"))
        cls.url, cls.servers = start()
        (cls.tmp / "config.json").write_text(json.dumps({"admin_key": KEY}), encoding="utf-8")
        cls.saved_env = {k: os.environ.get(k) for k in ("MUSE_GATEWAY_URL", "MUSE_GATEWAY_CONFIG", "MUSE_ADMIN_KEY",
                                                        "MUSE_PLUGIN_DATA", "MUSE_LOGIN_EXE")}
        for k in ("MUSE_ADMIN_KEY", "MUSE_PLUGIN_DATA", "MUSE_LOGIN_EXE"):
            os.environ.pop(k, None)
        os.environ["MUSE_GATEWAY_URL"] = cls.url
        os.environ["MUSE_GATEWAY_CONFIG"] = str(cls.tmp / "config.json")
        spec = importlib.util.spec_from_file_location("muse_plugin_under_test", PLUGIN / "plugin.py")
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers:
            s.shutdown()
        for k, v in cls.saved_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        Fake.reset()
        Ngoai.hits = []
        self.data = Path(tempfile.mkdtemp(dir=self.tmp))
        self.store = self.m.Store(self.data)
        self.gw = self.m.Gateway(store=self.store)

    def env(self, **kv):
        saved = {k: os.environ.get(k) for k in kv}
        for k, v in kv.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])

    PASTE = ("# danh sách tài khoản\n"
             "Tài khoản Ánh | datr=a1; hatch_sess=SESS_ONE; wd=1\n"
             "Tài khoản Đức\n"
             "datr=b2; hatch_sess=SESS_TWO_BAD; theme=dark\n"
             "datr=c3; hatch_sess=SESS_THREE\n"
             "datr=a1; hatch_sess=SESS_ONE; wd=2\n")

    def dump(self):
        s = "".join(p.read_text(encoding="utf-8") for p in self.data.rglob("*.json"))
        return s + json.dumps(self.m.overview(self.gw, self.store), ensure_ascii=False)

    # ------------------------------------------------------------ cookies and accounts (from 1.2.0)
    def test_parse_names_dedupe_vietnamese(self):
        items = self.m.parse_cookies(self.PASTE)
        self.assertEqual([i["name"] for i in items], ["Tài khoản Ánh", "Tài khoản Đức", ""])
        self.assertEqual(len({i["fingerprint"] for i in items}), 3)

    def test_import_sets_limit_and_skips_duplicates_without_storing_cookie(self):
        r = self.m.import_cookies(self.gw, self.store, self.PASTE)
        self.assertEqual(len(r["added"]), 3)
        self.assertEqual([a["ok"] for a in r["added"]], [True, False, True])
        self.assertEqual(r["added"][2]["name"], "Muse 3")
        self.assertTrue(all(a["limits"].get("video") == 2 for a in Fake.accounts))
        r2 = self.m.import_cookies(self.gw, self.store, self.PASTE)
        self.assertEqual((len(r2["added"]), len(r2["updated"]), len(r2["skipped"])), (0, 0, 3))
        for secret in ("SESS_ONE", "SESS_TWO_BAD", "SESS_THREE"):
            self.assertNotIn(secret, self.dump())

    def test_pool_check_switches_off_broken_account(self):
        self.m.import_cookies(self.gw, self.store, self.PASTE)
        rep = self.m.apply_mode(self.gw, self.store, check=True)
        self.assertEqual([d["name"] for d in rep["disabled"]], ["Tài khoản Đức"])
        self.assertEqual([a["enabled"] for a in Fake.accounts], [True, False, True])
        self.m.set_mode(self.gw, self.store, "pool")     # re-enable then re-test: broken one stays off
        self.assertEqual([a["enabled"] for a in Fake.accounts], [True, False, True])

    def test_rotate_keeps_one_and_skips_broken(self):
        self.m.import_cookies(self.gw, self.store, self.PASTE)
        rep = self.m.set_mode(self.gw, self.store, "rotate")
        self.assertEqual(rep["active"], "Tài khoản Ánh")
        self.assertEqual(sum(a["enabled"] for a in Fake.accounts), 1)
        rep = self.m.apply_mode(self.gw, self.store, rotate_now=True)
        self.assertEqual(rep["active"], "Muse 3")             # Đức fails its test and is skipped
        self.assertEqual([a["enabled"] for a in Fake.accounts], [False, False, True])
        rep = self.m.apply_mode(self.gw, self.store, rotate_now=True)
        self.assertEqual(rep["active"], "Tài khoản Ánh")
        self.assertEqual(sum(a["enabled"] for a in Fake.accounts), 1)

    def test_remove_drops_account_and_lets_cookie_be_imported_again(self):
        self.m.import_cookies(self.gw, self.store, self.PASTE)
        aid = Fake.accounts[0]["id"]
        r = self.m.remove_account(self.gw, self.store, aid)
        self.assertEqual(r["removed"], "Tài khoản Ánh")
        self.assertNotIn(aid, [a["id"] for a in Fake.accounts])
        self.assertNotIn(aid, self.store.state()["fingerprints"].values())
        self.assertIn("gỡ 'Tài khoản Ánh'", self.store.state()["events"][-1]["text"])
        r2 = self.m.import_cookies(self.gw, self.store, "Tài khoản Ánh | datr=a1; hatch_sess=SESS_ONE")
        self.assertEqual((len(r2["added"]), len(r2["skipped"])), (1, 0))
        with self.assertRaises(self.m.GatewayError):
            self.m.remove_account(self.gw, self.store, "acc-khong-co")

    def test_remove_active_account_in_rotate_picks_next(self):
        self.m.import_cookies(self.gw, self.store, self.PASTE)
        self.m.set_mode(self.gw, self.store, "rotate")
        active = self.store.state()["active"]
        self.m.remove_account(self.gw, self.store, active)
        self.assertNotEqual(self.store.state()["active"], active)
        self.assertEqual(sum(a["enabled"] for a in Fake.accounts), 1)
        self.assertEqual(next(a["name"] for a in Fake.accounts if a["enabled"]), "Muse 3")

    # ------------------------------------------------------------ same name replaces the cookie (1.3.0)
    def test_same_name_replaces_cookie_of_that_account(self):
        self.m.import_cookies(self.gw, self.store, "Tài khoản Ánh | hatch_sess=SESS_BAD_OLD\nBình | hatch_sess=B1")
        self.m.apply_mode(self.gw, self.store, check=True)
        self.assertEqual([a["enabled"] for a in Fake.accounts], [False, True])
        r = self.m.import_cookies(self.gw, self.store, "Tài khoản Ánh | datr=x; hatch_sess=SESS_FRESH")
        self.assertEqual((len(r["added"]), len(r["updated"])), (0, 1))
        self.assertTrue(r["updated"][0]["ok"])
        self.assertEqual(len(Fake.accounts), 2)
        self.assertEqual(Fake.accounts[0]["credential"], "datr=x; hatch_sess=SESS_FRESH")
        self.assertTrue(Fake.accounts[0]["enabled"], "a working cookie brings a switched-off account back in Pool")
        fps = self.store.state()["fingerprints"]
        self.assertEqual(list(fps.values()).count(Fake.accounts[0]["id"]), 1, "old fingerprint forgotten")
        self.assertNotIn("SESS_FRESH", self.dump())
        r = self.m.import_cookies(self.gw, self.store, "Tài khoản Ánh | datr=x; hatch_sess=SESS_FRESH")
        self.assertEqual(len(r["skipped"]), 1)

    def test_unnamed_or_ambiguous_name_still_adds(self):
        self.m.import_cookies(self.gw, self.store, "Trùng | hatch_sess=T1\nTrùng | hatch_sess=T2")
        self.assertEqual(len(Fake.accounts), 2)
        r = self.m.import_cookies(self.gw, self.store, "Trùng | hatch_sess=T3\nhatch_sess=T4")
        self.assertEqual((len(r["added"]), len(r["updated"])), (2, 0))

    # ------------------------------------------------------------ batch video
    def test_batch_runs_retries_and_names_files_in_order(self):
        self.m.import_cookies(self.gw, self.store, "hatch_sess=OK1\nhatch_sess=OK2\n")
        out = self.data / "out"
        b = self.m.new_batch(self.store, [{"prompt": "Cảnh mở: cô gái đi dưới mưa"}, {"prompt": "flaky cảnh hai"},
                                          "cảnh ba, cận mặt"], "Phong cách phim Việt, tông ấm", "9:16", 5, str(out))
        for _ in range(12):
            b = self.m.refresh_batch(self.gw, self.store, b["id"])
            if b["done"]:
                break
        self.assertTrue(b["done"])
        self.assertEqual([s["status"] for s in b["scenes"]], ["done"] * 3)
        self.assertEqual(b["scenes"][1]["attempts"], 2)
        files = sorted(Path(b["out_dir"]).iterdir())
        self.assertEqual([p.name for p in files], ["canh-01.mp4", "canh-02.mp4", "canh-03.mp4"])
        self.assertTrue(all(p.stat().st_size == 12000 for p in files), "whole file streamed, no .part left")
        first = next(j for j in Fake.jobs.values() if "cô gái" in j["body"]["prompt"])["body"]
        self.assertTrue(first["prompt"].startswith("Phong cách phim Việt"))
        self.assertEqual((first["aspect_ratio"], first["duration"]), ("9:16", 5))
        self.assertIn("3/3 cảnh xong", self.m.batch_summary(b))

    def test_admin_key_never_follows_output_url(self):
        self.m.import_cookies(self.gw, self.store, "hatch_sess=OK1")
        b = self.m.new_batch(self.store, ["một cảnh"], "", "", None, str(self.data / "o"))
        for _ in range(4):
            b = self.m.refresh_batch(self.gw, self.store, b["id"])
        self.assertTrue(b["done"])
        self.assertEqual(Ngoai.hits, [], "the absolute output.url was fetched")
        with self.assertRaises(self.m.GatewayError):
            self.gw.request("GET", Fake.ngoai + "/x")
        with self.assertRaises(self.m.GatewayError):
            self.gw.request("GET", "//127.0.0.1:1/x")
        self.assertEqual(Ngoai.hits, [])

    def test_batch_waits_when_no_account_without_burning_attempts(self):
        b = self.m.new_batch(self.store, ["một cảnh"], "", "", None, str(self.data / "o"))
        b = self.m.refresh_batch(self.gw, self.store, b["id"])
        self.assertEqual((b["scenes"][0]["status"], b["scenes"][0]["attempts"]), ("pending", 0))

    # ------------------------------------------------------------ paths a model names
    def test_safe_path_keeps_inside_brain_or_plugin_folder(self):
        vault = self.tmp / "brain"
        (vault / "attachments").mkdir(parents=True, exist_ok=True)
        ok = self.m.safe_path("attachments/x", self.data / "d", self.store, str(vault), "thư mục")
        self.assertEqual(ok, vault.resolve() / "attachments" / "x")
        self.assertEqual(self.m.safe_path("", self.data / "d", self.store, None, "x"), self.data / "d")
        outside = self.tmp / "ngoai"
        outside.mkdir(exist_ok=True)
        (vault / "lien-ket").symlink_to(outside)
        for bad in (str(outside), "../ngoai", "attachments/../../ngoai", "lien-ket/x", "/etc/passwd"):
            with self.assertRaises(self.m.GatewayError, msg=bad):
                self.m.safe_path(bad, self.data / "d", self.store, str(vault), "thư mục")

    def test_import_file_tool_never_reads_or_deletes_outside(self):
        victim = self.tmp / "khong-duoc-xoa.txt"
        victim.write_text("hatch_sess=SHOULD_NOT_LOAD", encoding="utf-8")
        ctx = _Ctx(self.data, str(self.tmp / "brain2"))
        out = asyncio.run(self.m._t_accounts({"op": "import_file", "path": str(victim)}, ctx))
        self.assertTrue(out.startswith("ERROR:"), out)
        self.assertTrue(victim.is_file())
        self.assertEqual(Fake.accounts, [])
        own = self.data / "muse-cookies.txt"
        own.write_text("Ánh | hatch_sess=FROM_FILE", encoding="utf-8")
        out = json.loads(asyncio.run(self.m._t_accounts({"op": "import_file"}, ctx)))
        self.assertEqual(len(out["added"]), 1)
        self.assertFalse(own.exists(), "the cookie file is deleted after import")

    def test_batch_tool_refuses_out_dir_outside_brain(self):
        self.m.import_cookies(self.gw, self.store, "hatch_sess=OK1")
        vault = self.tmp / "brain3"
        vault.mkdir(exist_ok=True)
        out = asyncio.run(self.m._t_batch({"scenes": ["cảnh"], "out_dir": str(self.tmp / "ngoai2")},
                                          _Ctx(self.data, str(vault))))
        self.assertTrue(out.startswith("ERROR:"), out)
        self.assertEqual(Fake.jobs, {})

    # ------------------------------------------------------------ gateway address and key
    def test_key_order_page_then_env_then_config(self):
        self.assertEqual((self.gw._key(), self.gw.key_source()), (KEY, "may"))
        self.env(MUSE_ADMIN_KEY="key-from-env")
        self.assertEqual(self.m.Gateway(store=self.store)._key(), "key-from-env")
        self.assertEqual(self.m.Gateway(store=self.store).key_source(), "bien")
        self.store.save_gateway_settings({"key": "key-from-page"})
        g = self.m.Gateway(store=self.store)
        self.assertEqual((g._key(), g.key_source()), ("key-from-page", "trang"))
        self.env(MUSE_ADMIN_KEY=None, MUSE_GATEWAY_CONFIG=str(self.tmp / "khong-co.json"))
        self.store.save_gateway_settings({})
        g = self.m.Gateway(store=self.store)
        self.assertEqual(g.key_source(), "")
        with self.assertRaises(self.m.GatewayError) as cm:
            g._key()
        self.assertIn("muse info", str(cm.exception))

    def test_save_gateway_on_page(self):
        for bad in ("127.0.0.1:8080", "http://a:b@127.0.0.1:8080", "http://127.0.0.1:8080/v1", "javascript:alert(1)",
                    "ftp://127.0.0.1"):
            with self.assertRaises(self.m.GatewayError, msg=bad):
                self.m.save_gateway(self.store, bad, "k")
        self.env(MUSE_GATEWAY_URL="http://127.0.0.1:1", MUSE_GATEWAY_CONFIG=str(self.tmp / "khong-co.json"))
        self.assertFalse(self.m.save_gateway(self.store, "", "")["ok"])
        r = self.m.save_gateway(self.store, self.url + "/", KEY)
        self.assertTrue(r["ok"], r)
        f = self.data / "gateway.json"
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertTrue(self.m.save_gateway(self.store, self.url, "")["ok"], "an empty key keeps the saved one")
        ov = self.m.overview(self.m.Gateway(store=self.store), self.store)
        self.assertTrue(ov["gateway"]["ok"])
        self.assertEqual((ov["gateway"]["url"], ov["gateway"]["key_from"], ov["gateway"]["saved"]),
                         (self.url, "trang", True))
        self.assertNotIn(KEY, json.dumps(ov) + json.dumps(self.store.state()))
        self.assertFalse(self.m.save_gateway(self.store, reset=True)["ok"])
        self.assertFalse(f.exists())

    def test_overview_when_gateway_is_down(self):
        ov = self.m.overview(self.m.Gateway(base="http://127.0.0.1:1", store=self.store), self.store)
        self.assertFalse(ov["gateway"]["ok"])
        self.assertTrue(ov["gateway"]["error"].startswith("Không nối được Muse Gateway"), ov["gateway"]["error"])
        self.assertEqual(ov["accounts"], [])

    def test_video_limit_on_every_account(self):
        self.m.import_cookies(self.gw, self.store, "hatch_sess=A1\nhatch_sess=A2")
        self.assertEqual(self.m.set_video_limit(self.gw, self.store, 99), {"video_limit": 10})
        self.assertTrue(all(a["limits"]["video"] == 10 for a in Fake.accounts))
        self.m.set_video_limit(self.gw, self.store, 1)
        self.assertEqual(self.store.state()["video_limit"], 1)
        with self.assertRaises(self.m.GatewayError):
            self.m.set_video_limit(self.gw, self.store, "nhiều")

    def test_overview_gives_the_page_what_it_draws(self):
        self.m.import_cookies(self.gw, self.store, "Ánh | hatch_sess=A1")
        aid = Fake.accounts[0]["id"]
        self._profile(aid, account_id=aid, expires=int(time.time()) + 5 * 86400)
        self.m.new_batch(self.store, ["a", "b"], "", "", None, str(self.data / "o"))
        ov = self.m.overview(self.gw, self.store)
        a = ov["accounts"][0]
        self.assertEqual((a["name"], a["enabled"], a["online"]), ("Ánh", True, True))
        self.assertGreater(a["profile"]["expires"], time.time())
        self.assertEqual((ov["batches"][0]["n"], ov["batches"][0]["ok"], ov["batches"][0]["done"]), (2, 0, False))
        self.assertIn("can_window", ov)

    # ------------------------------------------------------------ cookie renewal (from 1.2.0)
    COOKIES = [{"name": "wd", "value": "1", "domain": ".meta.ai"},
               {"name": "hatch_sess", "value": "SESS_NEW", "domain": ".meta.ai", "expires": 0},
               {"name": "datr", "value": "D1", "domain": ".meta.ai"},
               {"name": "other", "value": "X", "domain": ".facebook.com"}]

    def _profile(self, key, **extra):
        st = self.store.state()
        st.setdefault("profiles", {})[key] = {"created_at": 1, **extra}
        self.store.save_state(st)

    def test_cookies_to_header_takes_the_hatch_sess_site_only(self):
        header, exp = self.m.cookies_to_header(self.COOKIES)
        self.assertEqual(header, "datr=D1; hatch_sess=SESS_NEW; wd=1")
        self.assertEqual(exp, 0.0)
        self.assertEqual(self.m.cookies_to_header([{"name": "datr", "value": "D1", "domain": ".meta.ai"}]), ("", 0.0))

    def test_renew_patches_existing_account_and_keeps_cookie_out_of_state(self):
        self.m.import_cookies(self.gw, self.store, "Ánh | datr=a1; hatch_sess=SESS_BAD_OLD")
        aid = Fake.accounts[0]["id"]
        self._profile(aid)
        exp = time.time() + 30 * 86400
        r = self.m.renew_account(self.gw, self.store, aid,
                                 capture=lambda udd, url: ("datr=D1; hatch_sess=SESS_NEW", exp))
        self.assertTrue(r["ok"] and r["changed"] and not r["warn"])
        self.assertEqual((Fake.cred_patches, len(Fake.accounts)), (1, 1))
        self.assertEqual(Fake.accounts[0]["credential"], "datr=D1; hatch_sess=SESS_NEW")
        r2 = self.m.renew_account(self.gw, self.store, aid,
                                  capture=lambda udd, url: ("datr=D1; hatch_sess=SESS_NEW", exp))
        self.assertFalse(r2["changed"])
        self.assertEqual(Fake.cred_patches, 1)                 # same cookie: no second PATCH
        self.assertNotIn("SESS_NEW", self.dump())

    def test_renew_warns_when_logged_out_or_close_to_expiry(self):
        self.m.import_cookies(self.gw, self.store, "Ánh | hatch_sess=SESS_A")
        aid = Fake.accounts[0]["id"]
        self._profile(aid)
        r = self.m.renew_all(self.gw, self.store, capture=lambda udd, url: ("", 0.0))
        self.assertTrue(r["warn"])
        self.assertTrue(r["note"].startswith("Cần đăng nhập lại: "), r["note"])
        r = self.m.renew_all(self.gw, self.store,
                             capture=lambda udd, url: ("hatch_sess=SESS_B", time.time() + 86400))
        self.assertTrue(r["warn"] and r["renewed"][0]["ok"])
        self.assertLess(r["renewed"][0]["days_left"], 3)

    def test_renew_without_profile_asks_for_login(self):
        with self.assertRaises(self.m.GatewayError):
            self.m.renew_account(self.gw, self.store, "acc-khong-co", capture=lambda u, x: ("", 0.0))

    def test_failed_check_renews_from_profile_before_switching_off(self):
        self.m.import_cookies(self.gw, self.store, "Ánh | hatch_sess=SESS_BAD")
        aid = Fake.accounts[0]["id"]
        self._profile(aid, account_id=aid)
        orig = self.m.capture_cookies
        self.m.capture_cookies = lambda udd, url="": ("hatch_sess=SESS_FRESH", 0.0)
        try:
            rep = self.m.apply_mode(self.gw, self.store, check=True)
        finally:
            self.m.capture_cookies = orig
        self.assertEqual(rep["disabled"], [])
        self.assertTrue(Fake.accounts[0]["enabled"])
        self.assertEqual(Fake.accounts[0]["credential"], "hatch_sess=SESS_FRESH")

    def test_open_login_creates_profile_and_records_event(self):
        import subprocess
        orig_popen = subprocess.Popen
        called = []

        class DummyProc:
            returncode = 0

            def communicate(self, timeout=None):
                return json.dumps({"ok": True, "cookie": "hatch_sess=DUMMY_SESS", "expires": 0}), ""
        subprocess.Popen = lambda *a, **k: called.append(a) or DummyProc()
        fake_chrome = self.data / "chrome"
        fake_chrome.write_text("", encoding="utf-8")
        self.env(MUSE_CHROME=str(fake_chrome), DISPLAY=os.environ.get("DISPLAY") or ":0")
        try:
            res = self.m.open_login(self.store, key="acc_wv2", name="Tài khoản Test")
            self.assertTrue(res["opened"])
            self.assertEqual(res["key"], "acc_wv2")
            self.assertEqual(called[0][0][0], str(fake_chrome))
            st = self.store.state()
            self.assertEqual(st["profiles"]["acc_wv2"]["name"], "Tài khoản Test")
            self.assertTrue(any("Tài khoản Test" in e["text"] for e in st["events"]))
        finally:
            subprocess.Popen = orig_popen

    @unittest.skipIf(os.name == "nt", "Linux/macOS only")
    def test_open_login_without_display_points_to_paste_page(self):
        self.env(DISPLAY=None, WAYLAND_DISPLAY=None)
        self.assertFalse(self.m.can_open_window())
        with self.assertRaises(self.m.GatewayError) as cm:
            self.m.open_login(self.store, key="acc_headless")
        self.assertIn("/ext/muse-accounts/", str(cm.exception))

    @unittest.skipIf(os.name == "nt", "Linux/macOS only")
    def test_profile_busy_follows_singleton_lock_process(self):
        udd = self.data / "prof"
        udd.mkdir()
        self.assertFalse(self.m._profile_busy(udd))
        os.symlink(f"host-{os.getpid()}", udd / "SingletonLock")       # live process: busy
        self.assertTrue(self.m._profile_busy(udd))
        (udd / "SingletonLock").unlink()
        os.symlink("host-2147483600", udd / "SingletonLock")           # dead process: stale lock
        self.assertFalse(self.m._profile_busy(udd))
        self.assertFalse((udd / "SingletonLock").is_symlink())

    def test_login_helper_exe_only_when_named(self):
        self.assertIsNone(self.m.wv2_login_exe())
        exe = self.data / "muse-login.exe"
        exe.write_text("", encoding="utf-8")
        self.env(MUSE_LOGIN_EXE=str(exe))
        self.assertEqual(self.m.wv2_login_exe(), exe if os.name == "nt" else None)

    def test_nothing_addressed_to_one_person(self):
        src = (PLUGIN / "plugin.py").read_text(encoding="utf-8")
        for word in ("ANH ADAM", "AdamDeck-muse", "Anh dán"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
