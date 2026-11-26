# -*- coding: utf-8 -*-
"""Plugin `muse-accounts` of the pack javis.muse: several Muse accounts in Muse Gateway, and many video scenes
rendered in parallel.

Muse is Meta's app at muse.ai. It has no public API. Muse Gateway (a separate program, `muse serve`, by default on
127.0.0.1:8080) logs in with each account's session cookie and serves an OpenAI-style API; it already pools accounts:
every request goes to the least busy enabled account, within per-account limits, and queues when all are busy. This
plugin does not reimplement that and does not ship the gateway. It adds:

- a page (`/ext/muse-accounts/`) that takes many cookies at once, so cookies go browser -> Javis -> gateway and never
  through chat;
- two modes: "pool" (every account enabled; accounts that fail a live test are switched off) and "rotate" (exactly one
  account enabled; when it fails, switch it off and enable the next one);
- batch video: submit every scene at once, let the gateway queue them by account limits, poll, retry failed scenes,
  and download the files named in scene order into the brain.

History: written by AdamDeck as a hand-installed plugin (`_state/plugins/muse-accounts`, 1.0.0 to 1.2.0). 1.3.0 is
the same plugin moving into the store, so the slug is kept: its data folder (`plugin-data/muse-accounts`: mode, login
profiles, batches) carries over, and while the hand-installed copy is still there it shadows this one (Javis loads
user plugins after pack plugins), so tools are never registered twice. Changes for the store:
- no binaries in the pack. The Edge WebView2 helper (muse-login.exe) is used only when MUSE_LOGIN_EXE points to it;
  otherwise the login window is Chrome, or Edge on Windows (same DevTools protocol).
- files: `import_file` reads and deletes only inside this plugin's data folder or the brain, and batch videos are
  written only there. A model-chosen path anywhere on the disk is refused.
- the admin key goes to the gateway only: results are downloaded from `/v1/jobs/<id>/content` on the gateway itself,
  never from an absolute URL found in a response.
- the gateway address and admin key can be saved on the page, for Javis and the gateway in different containers.
- a pasted cookie whose name matches one existing account replaces that account's cookie instead of adding a twin.

Secrets: the admin key comes from the page (saved in this plugin's data folder, mode 600), MUSE_ADMIN_KEY, or the
gateway's own config.json, in that order, and is never printed or returned. Only a SHA-256 fingerprint of each
imported session cookie is stored (to skip duplicates), never the cookie. Tests point MUSE_GATEWAY_URL /
MUSE_GATEWAY_CONFIG / MUSE_PLUGIN_DATA at a fake gateway in a temp folder.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8080"
DEFAULT_VIDEO_LIMIT = 2          # owner's choice 2026-10-31: two videos at once per account
MAX_ATTEMPTS = 3                 # one scene is submitted at most this many times
POLL_EVERY_S = 6
BATCH_DEADLINE_S = 3 * 3600      # a background poller gives up after this; status calls resume it
MAX_SCENES = 60
USER_AGENT = "Javis-muse-accounts/1.3"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SESS_RE = re.compile(r"hatch_sess=([^;\s]+)")
_URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+$")

_lock = threading.RLock()
_pollers: dict[str, asyncio.Task] = {}


def gateway_config() -> Path:
    """The gateway's own config.json (it holds admin_key). Same folder the gateway picks: %APPDATA% on Windows,
    $XDG_CONFIG_HOME or ~/.config elsewhere; MUSE_GATEWAY_CONFIG overrides."""
    base = os.getenv("APPDATA") or os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(os.getenv("MUSE_GATEWAY_CONFIG") or Path(base) / "MuseGateway" / "config.json")


# ---------------------------------------------------------------- storage
class Store:
    """Plugin state on disk: mode, cookie fingerprints, batches. One JSON file per batch."""

    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "batches").mkdir(parents=True, exist_ok=True)

    def _read(self, p: Path, default):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return default

    def _write(self, p: Path, data, private: bool = False) -> None:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, p)

    def state(self) -> dict:
        with _lock:
            st = self._read(self.root / "state.json", {})
        st.setdefault("mode", "pool")
        st.setdefault("video_limit", DEFAULT_VIDEO_LIMIT)
        st.setdefault("fingerprints", {})
        st.setdefault("events", [])
        return st

    def save_state(self, st: dict) -> None:
        st["events"] = st.get("events", [])[-30:]
        with _lock:
            self._write(self.root / "state.json", st)

    def event(self, text: str) -> None:
        st = self.state()
        st["events"].append({"at": int(time.time()), "text": text})
        self.save_state(st)

    def gateway_settings(self) -> dict:
        """Address and admin key saved on the page. Its own file (mode 600), apart from state.json."""
        with _lock:
            d = self._read(self.root / "gateway.json", {})
        return d if isinstance(d, dict) else {}

    def save_gateway_settings(self, d: dict) -> None:
        with _lock:
            if d:
                self._write(self.root / "gateway.json", d, private=True)
            else:
                (self.root / "gateway.json").unlink(missing_ok=True)

    def batch(self, bid: str) -> dict | None:
        if not _ID_RE.match(bid or ""):
            return None
        with _lock:
            return self._read(self.root / "batches" / f"{bid}.json", None)

    def save_batch(self, b: dict) -> None:
        with _lock:
            self._write(self.root / "batches" / f"{b['id']}.json", b)

    def batches(self) -> list[dict]:
        out = []
        for p in sorted((self.root / "batches").glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            b = self._read(p, None)
            if b:
                out.append(b)
        return out


# ---------------------------------------------------------------- gateway client
class GatewayError(Exception):
    pass


def clean_url(u: str) -> str:
    """A gateway address the owner typed: scheme and host[:port] only, no path, no user:password@."""
    u = str(u or "").strip().rstrip("/")
    if not _URL_RE.match(u):
        raise GatewayError("địa chỉ gateway phải có dạng http://máy:cổng, ví dụ http://127.0.0.1:8080")
    return u


class Gateway:
    """Talks to Muse Gateway with the admin key. Address: `base`, else saved on the page, else MUSE_GATEWAY_URL,
    else 127.0.0.1:8080. Key: saved on the page, else MUSE_ADMIN_KEY, else the gateway's config.json."""

    def __init__(self, base: str = "", config: Path | None = None, store: "Store | None" = None):
        saved = store.gateway_settings() if store else {}
        self.saved = saved
        self.base = (base or saved.get("url") or os.getenv("MUSE_GATEWAY_URL") or DEFAULT_URL).rstrip("/")
        self.config = Path(config or gateway_config())

    def key_source(self) -> str:
        """Where the key comes from: "trang" (page), "bien" (environment), "may" (config.json), "" (none)."""
        if str(self.saved.get("key") or "").strip():
            return "trang"
        if os.getenv("MUSE_ADMIN_KEY", "").strip():
            return "bien"
        try:
            return "may" if json.loads(self.config.read_text(encoding="utf-8")).get("admin_key") else ""
        except Exception:  # noqa: BLE001
            return ""

    def _key(self) -> str:
        key = str(self.saved.get("key") or "").strip() or os.getenv("MUSE_ADMIN_KEY", "").strip()
        if key:
            return key
        try:
            key = json.loads(self.config.read_text(encoding="utf-8")).get("admin_key", "")
        except FileNotFoundError:
            key = ""
        except Exception as ex:  # noqa: BLE001
            raise GatewayError(f"không đọc được cấu hình Muse Gateway ({self.config}): {type(ex).__name__}")
        if not key:
            raise GatewayError("chưa có khoá quản trị của Muse Gateway. Điền khoá ở mục Nối gateway trên trang Muse "
                               "(lấy bằng lệnh muse info trên máy chạy gateway).")
        return key

    def _open(self, method: str, path: str, body=None, timeout: float = 90):
        # Paths only: the admin key never leaves for a host the gateway (or anything it returns) names.
        if not path.startswith("/") or path.startswith("//"):
            raise GatewayError("đường dẫn gateway không hợp lệ")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self._key(), "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}), "User-Agent": USER_AGENT})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as ex:
            text = ex.read().decode("utf-8", "replace")
            try:
                msg = (json.loads(text).get("error") or {}).get("message") or text
            except Exception:  # noqa: BLE001
                msg = text
            raise GatewayError(f"gateway trả {ex.code}: {str(msg)[:300]}")
        except Exception as ex:  # noqa: BLE001
            raise GatewayError(f"không nối được Muse Gateway ở {self.base} ({type(ex).__name__}). Bật gateway lên, "
                               "hoặc điền địa chỉ đúng ở mục Nối gateway trên trang Muse.")

    def request(self, method: str, path: str, body=None, timeout: float = 90):
        with self._open(method, path, body, timeout) as r:
            payload = r.read()
        return json.loads(payload.decode("utf-8") or "{}")

    def download(self, path: str, dest: Path, timeout: float = 300) -> int:
        """Stream a file from the gateway into `dest` (written to a .part first). Returns its size."""
        dest = Path(dest)
        part = dest.with_name(dest.name + ".part")
        n = 0
        with self._open("GET", path, None, timeout) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                n += len(chunk)
        os.replace(part, dest)
        return n

    def health(self) -> tuple[bool, str]:
        """(ok, readable error) for the page's status line: the accounts list needs both the address and the key."""
        try:
            self.accounts()
            return True, ""
        except GatewayError as ex:
            msg = str(ex)
            return False, msg[:1].upper() + msg[1:]

    def accounts(self) -> list[dict]:
        return list(self.request("GET", "/api/admin/accounts").get("data") or [])

    def add(self, name: str, credential: str) -> dict:
        return self.request("POST", "/api/admin/accounts", {"name": name, "credential": credential, "proxy": ""})

    def patch(self, aid: str, body: dict) -> dict:
        return self.request("PATCH", f"/api/admin/accounts/{urllib.parse.quote(aid, safe='')}", body)

    def test(self, aid: str) -> dict:
        return self.request("POST", f"/api/admin/accounts/{urllib.parse.quote(aid, safe='')}/test", {})

    def delete(self, aid: str) -> dict:
        return self.request("DELETE", f"/api/admin/accounts/{urllib.parse.quote(aid, safe='')}")


# ---------------------------------------------------------------- cookie parsing
def parse_cookies(text: str) -> list[dict]:
    """Split pasted text into accounts. One account per line that holds `hatch_sess=` or a
    `wss://` link. A name can sit before it as `Name | cookie`, or alone on the line above.
    Lines starting with # are comments. Returns [{name, credential, fingerprint}]."""
    out, pending, seen = [], "", set()
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        name = ""
        if "|" in s and ("hatch_sess=" in s.split("|", 1)[1] or "wss://" in s.split("|", 1)[1]):
            name, s = (x.strip() for x in s.split("|", 1))
        m = _SESS_RE.search(s)
        if m or s.startswith("wss://"):
            fp = hashlib.sha256((m.group(1) if m else s).encode("utf-8")).hexdigest()[:24]
            if fp in seen:
                continue
            seen.add(fp)
            out.append({"name": (name or pending)[:60], "credential": s, "fingerprint": fp})
            pending = ""
        elif "=" not in s:
            pending = s
    return out


def _masked_view(a: dict) -> dict:
    live = a.get("live") or {}
    return {"id": a.get("id"), "name": a.get("name"), "enabled": bool(a.get("enabled")),
            "online": bool(live.get("connected")), "status": a.get("status"),
            "error": (a.get("last_error") or "")[:200], "credential": a.get("credential_masked"),
            "requests": a.get("requests", 0), "failures": a.get("failures", 0),
            "active": live.get("active") or {}, "limits": live.get("limits") or a.get("limits") or {}}


# ---------------------------------------------------------------- account operations (sync)
def import_cookies(gw: Gateway, store: Store, text: str) -> dict:
    """Add each pasted account to the gateway. A cookie already imported is skipped (fingerprint). A named line whose
    name matches exactly one account in the gateway replaces that account's cookie: on a server without a screen
    that is how an expired cookie is renewed, and it keeps the account's id, limits and mode instead of a twin."""
    items = parse_cookies(text)
    if not items:
        return {"added": [], "updated": [], "skipped": [], "failed": [],
                "note": "Không thấy cookie nào có hatch_sess= hoặc link wss://."}
    st = store.state()
    accts = gw.accounts()
    existing = {a.get("id") for a in accts}
    by_name: dict[str, list[str]] = {}
    for a in accts:
        by_name.setdefault(str(a.get("name") or "").strip(), []).append(a.get("id"))
    added, updated, skipped, failed = [], [], [], []
    base_n = len(existing)
    for it in items:
        name = it["name"] or f"Muse {base_n + len(added) + 1}"
        known = st["fingerprints"].get(it["fingerprint"])
        if known and known in existing:
            skipped.append({"name": name, "reason": "cookie này đã nạp rồi"})
            continue
        same = by_name.get(it["name"].strip()) if it["name"] else None
        if same and len(same) == 1:
            aid = same[0]
            try:
                gw.patch(aid, {"credential": it["credential"]})
            except GatewayError as ex:
                failed.append({"name": name, "error": str(ex)})
                continue
            ok, err = _alive(gw, {"id": aid})
            st["fingerprints"] = {f: v for f, v in st["fingerprints"].items() if v != aid}
            st["fingerprints"][it["fingerprint"]] = aid
            if ok and aid in (st.get("auto_off") or []):
                if st["mode"] == "pool":
                    gw.patch(aid, {"enabled": True})
                st["auto_off"] = [x for x in st["auto_off"] if x != aid]
            updated.append({"name": name, "id": aid, "ok": ok, "error": err})
            continue
        try:
            res = gw.add(name, it["credential"])
        except GatewayError as ex:
            failed.append({"name": name, "error": str(ex)})
            continue
        acc = res.get("account") or res.get("data") or res
        aid = acc.get("id") if isinstance(acc, dict) else None
        if not aid:
            after = [a for a in gw.accounts() if a.get("id") not in existing and a.get("name") == name]
            aid = after[-1]["id"] if after else None
        test = res.get("test") or {}
        if aid:
            existing.add(aid)
            st["fingerprints"][it["fingerprint"]] = aid
            try:
                gw.patch(aid, {"limits": {"chat": 0, "image": 0, "video": int(st["video_limit"])}})
            except GatewayError:
                pass
        added.append({"name": name, "id": aid, "ok": bool(test.get("ok")),
                      "error": (test.get("error") or "")[:200]})
    store.save_state(st)
    if added:
        store.event(f"nạp {len(added)} tài khoản ({sum(1 for a in added if a['ok'])} kết nối được)")
    for u in updated:
        store.event(f"thay cookie cho '{u['name']}': " + ("kết nối được" if u["ok"] else "kiểm lỗi: " + u["error"]))
    apply_mode(gw, store)
    return {"added": added, "updated": updated, "skipped": skipped, "failed": failed}


def _order(accts: list[dict]) -> list[dict]:
    return sorted(accts, key=lambda a: (str(a.get("created_at") or ""), str(a.get("id"))))


def _alive(gw: Gateway, a: dict) -> tuple[bool, str]:
    try:
        r = gw.test(a["id"])
    except GatewayError as ex:
        return False, str(ex)
    return bool(r.get("ok")), str(r.get("error") or "")[:200]


def apply_mode(gw: Gateway, store: Store, check: bool = False, rotate_now: bool = False,
               reenable: bool = False) -> dict:
    """Bring the gateway in line with the mode.

    pool:   with check=True every enabled account is tested and a failing one is switched off;
            reenable=True (switching into pool) turns every account this plugin imported back on.
            Accounts the owner switched off by hand are otherwise left alone.
    rotate: exactly one account enabled. It is replaced when it fails a test (check=True), when
            rotate_now=True, or when none is enabled yet."""
    st = store.state()
    accts = _order(gw.accounts())
    report = {"mode": st["mode"], "disabled": [], "enabled": [], "active": None}
    if not accts:
        return report
    off = set(st.get("auto_off") or [])
    mine = set(st["fingerprints"].values())
    active = st.get("active")
    if st["mode"] == "pool":
        for a in accts:
            if not a.get("enabled") and reenable and a["id"] in mine:
                gw.patch(a["id"], {"enabled": True})
                a["enabled"] = True
                report["enabled"].append(a["name"])
            if a.get("enabled") and check:
                ok, err = _alive_or_renew(gw, store, a)
                if not ok:
                    gw.patch(a["id"], {"enabled": False})
                    off.add(a["id"])
                    report["disabled"].append({"name": a["name"], "error": err})
                    store.event(f"tắt '{a['name']}' vì kiểm lỗi: {err}")
        active = None
    else:
        on = [a for a in accts if a.get("enabled")]
        current = next((a for a in on if a["id"] == active), on[0] if on else None)
        need_next = current is None or rotate_now
        if current and check and not rotate_now:
            ok, err = _alive_or_renew(gw, store, current)
            if not ok:
                off.add(current["id"])
                report["disabled"].append({"name": current["name"], "error": err})
                store.event(f"luân phiên: '{current['name']}' kiểm lỗi ({err}), chuyển tài khoản")
                need_next = True
        if need_next:
            start = accts.index(current) + 1 if current else 0
            pick = None
            for a in accts[start:] + accts[:start]:
                if (current and a["id"] == current["id"]) or (a["id"] in off and not rotate_now):
                    continue
                if not a.get("enabled"):
                    gw.patch(a["id"], {"enabled": True})
                    a["enabled"] = True
                ok, err = _alive_or_renew(gw, store, a)
                if ok:
                    pick = a
                    break
                gw.patch(a["id"], {"enabled": False})
                a["enabled"] = False
                off.add(a["id"])
                report["disabled"].append({"name": a["name"], "error": err})
            if pick is None and current and current["id"] not in off:
                pick = current          # nothing better: keep the one we had
            if pick and (not current or pick["id"] != current["id"]):
                store.event(f"luân phiên: dùng '{pick['name']}'")
            current = pick
        for a in accts:
            want = bool(current) and a["id"] == current["id"]
            if bool(a.get("enabled")) != want:
                gw.patch(a["id"], {"enabled": want})
                if want:
                    report["enabled"].append(a["name"])
        if current:
            off.discard(current["id"])
        active = current["id"] if current else None
        report["active"] = current["name"] if current else None
    st = store.state()                  # reload: store.event() above wrote the file
    st["auto_off"], st["active"] = sorted(off), active
    store.save_state(st)
    return report


def set_mode(gw: Gateway, store: Store, mode: str, video_limit: int | None = None) -> dict:
    if mode not in ("pool", "rotate"):
        raise GatewayError("mode chỉ nhận 'pool' (Gộp) hoặc 'rotate' (Luân phiên)")
    st = store.state()
    st["mode"] = mode
    if mode == "pool":
        st["auto_off"] = []          # switching into pool gives every account a fresh chance
    store.save_state(st)
    if video_limit:
        set_video_limit(gw, store, video_limit)
    store.event(f"đổi chế độ sang {'Gộp' if mode == 'pool' else 'Luân phiên'}")
    return apply_mode(gw, store, check=True, reenable=(mode == "pool"))


def set_video_limit(gw: Gateway, store: Store, video_limit) -> dict:
    """Videos at once per account (1-10), on every account in the gateway. No live test: nothing else changes."""
    try:
        n = max(1, min(10, int(video_limit)))
    except (TypeError, ValueError):
        raise GatewayError("số video cùng lúc phải là số từ 1 tới 10")
    st = store.state()
    st["video_limit"] = n
    store.save_state(st)
    for a in gw.accounts():
        gw.patch(a["id"], {"limits": {"chat": 0, "image": 0, "video": n}})
    store.event(f"mỗi tài khoản làm tối đa {n} video cùng lúc")
    return {"video_limit": n}


def remove_account(gw: Gateway, store: Store, aid: str) -> dict:
    """Delete one account from the gateway and forget its cookie fingerprint, so the same cookie
    can be imported again later. Removing the active account in Rotate mode picks the next one."""
    acct = next((a for a in gw.accounts() if a.get("id") == aid), None)
    if not acct:
        raise GatewayError("không thấy tài khoản này trong gateway (có thể đã gỡ rồi)")
    gw.delete(aid)
    st = store.state()
    st["fingerprints"] = {fp: v for fp, v in st["fingerprints"].items() if v != aid}
    st["auto_off"] = [x for x in (st.get("auto_off") or []) if x != aid]
    was_active = st.get("active") == aid
    if was_active:
        st["active"] = None
    store.save_state(st)
    store.event(f"gỡ '{acct.get('name')}'")
    report = {"removed": acct.get("name")}
    if was_active and st["mode"] == "rotate":
        report["mode"] = apply_mode(gw, store)
    return report


def can_open_window() -> bool:
    """Whether a login window can open on the machine that runs Javis (Windows, or a desktop session elsewhere)."""
    return os.name == "nt" or bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


def overview(gw: Gateway, store: Store) -> dict:
    """Everything the page and op=list show. Never raises for a gateway that is down: that is a status, not an
    error, so the page can still offer the fix (the Nối gateway section)."""
    st = store.state()
    ok, err = True, ""
    try:
        accts = _order(gw.accounts())
    except GatewayError as ex:
        accts, ok, err = [], False, str(ex)
        err = err[:1].upper() + err[1:]
    profiles = st.get("profiles") or {}

    def prof(aid):
        for k, v in profiles.items():
            if v.get("account_id") == aid or k == profile_key(aid):
                return {"expires": v.get("expires") or 0, "last_error": v.get("last_error") or "",
                        "renewed_at": v.get("renewed_at")}
        return None

    batches = []
    for b in store.batches()[:5]:
        sc = b.get("scenes") or []
        batches.append({"id": b["id"], "created": b.get("created"), "done": bool(b.get("done")), "n": len(sc),
                        "ok": sum(1 for x in sc if x.get("status") == "done"),
                        "failed": sum(1 for x in sc if x.get("status") == "failed")})
    saved = gw.saved
    return {"gateway": {"ok": ok, "error": err, "url": gw.base, "key_from": gw.key_source(),
                        "saved": bool(saved), "saved_url": str(saved.get("url") or "")},
            "can_window": can_open_window(),
            "mode": st["mode"], "video_limit": st["video_limit"], "active": st.get("active"),
            "accounts": [{**_masked_view(a), "profile": prof(a.get("id"))} for a in accts],
            "batches": batches,
            "events": st["events"][-10:]}


def save_gateway(store: Store, url: str = "", key: str = "", reset: bool = False) -> dict:
    """Address and admin key from the page. An empty key keeps the one saved before. Answers whether it now works."""
    if reset:
        store.save_gateway_settings({})
        store.event("nối gateway: về mặc định")
    else:
        d = dict(store.gateway_settings())
        if str(url or "").strip():
            d["url"] = clean_url(url)
        else:
            d.pop("url", None)
        if str(key or "").strip():
            d["key"] = str(key).strip()
        store.save_gateway_settings(d)
        store.event("nối gateway: lưu " + (d.get("url") or "địa chỉ mặc định")
                    + (", có khoá riêng" if d.get("key") else ""))
    ok, err = Gateway(store=store).health()
    return {"ok": ok, "error": err}


# ---------------------------------------------------------------- cookie renewal
# One Chrome profile per Muse account, logged in once by the owner. Renewal opens Muse in that
# profile without a window, reads the fresh cookies over DevTools and pushes them to the gateway,
# which then renews its own wss:// link from hatch_sess. Cookies are never printed or logged.
LOGIN_URL = os.getenv("MUSE_LOGIN_URL", "https://muse.ai")


def wv2_login_exe() -> Path | None:
    """The Edge WebView2 login helper (muse-login.exe), only where the owner installed it and named it in
    MUSE_LOGIN_EXE. The store pack carries no binaries: nobody can review a compiled .exe, and this plugin runs it."""
    if os.name != "nt":              # Edge WebView2 exists on Windows only
        return None
    exe = os.getenv("MUSE_LOGIN_EXE", "").strip()
    return Path(exe) if exe and Path(exe).is_file() else None


WARN_DAYS = 3                    # warn the owner when hatch_sess has less than this left
RENEW_SETTLE_S = 8.0             # time for the page to refresh its session before cookies are read
_CONTINUE_JS = r"""(() => {
  const re = /^(tiếp tục|continue)(\s+(với|as)\b.*)?$/i;
  for (const el of document.querySelectorAll('button,[role="button"],a')) {
    const t = (el.innerText || '').trim();
    if (t && t.length < 80 && re.test(t)) { el.click(); return t; }
  }
  return '';
})()"""


def chrome_exe() -> str:
    cands = [os.getenv("MUSE_CHROME") or ""]
    bases = [b for b in (os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)"), os.getenv("LOCALAPPDATA")) if b]
    cands += [str(Path(b) / "Google" / "Chrome" / "Application" / "chrome.exe") for b in bases]
    # Edge ships with Windows and speaks the same DevTools protocol, so the window and the renewal work without Chrome.
    cands += [str(Path(b) / "Microsoft" / "Edge" / "Application" / "msedge.exe") for b in bases]
    for c in cands:
        if c and Path(c).is_file():
            return c
    if os.name != "nt":
        import shutil
        for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(n)
            if found:
                return found
    raise GatewayError("không thấy Chrome hay Edge trên máy (đặt biến MUSE_CHROME nếu cài ở chỗ khác)")


def profile_key(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s or ""))[:60].strip("_") or "muse"


def profile_dir(store: Store, key: str) -> Path:
    return store.root / "profiles" / profile_key(key)


def cookies_to_header(cookies: list[dict]) -> tuple[str, float]:
    """Find the site that holds hatch_sess and join that site's cookies into one Cookie header.
    Returns (header, hatch_sess expiry as epoch seconds, 0 for a session cookie). Empty header
    means the profile is not logged in."""
    sess = next((c for c in cookies or [] if c.get("name") == "hatch_sess" and c.get("value")), None)
    if not sess:
        return "", 0.0
    site = ".".join(str(sess.get("domain") or "").lstrip(".").split(".")[-2:])
    picked = [c for c in cookies if c.get("name") and
              (str(c.get("domain") or "").lstrip(".") == site or str(c.get("domain") or "").endswith("." + site))]
    picked.sort(key=lambda c: (c["name"] != "datr", c["name"] != "hatch_sess", c["name"]))
    seen, parts = set(), []
    for c in picked:
        if c["name"] not in seen:
            seen.add(c["name"])
            parts.append(f"{c['name']}={c.get('value', '')}")
    exp = float(sess.get("expires") or 0)
    return "; ".join(parts), (exp if exp > 0 else 0.0)


def _chrome_flags() -> list[str]:
    """Extra Chrome flags per platform. Off Windows the cookie store must not depend on a desktop
    keyring, or a headless run cannot read cookies written by the visible login window; a server
    usually runs as root without user namespaces, where Chrome needs --no-sandbox to start."""
    if os.name == "nt":
        return []
    flags = ["--password-store=basic"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        flags.append("--no-sandbox")
    return flags


def _profile_busy(udd: Path) -> bool:
    if os.name != "nt":
        # Chrome on Linux/macOS marks an open profile with a SingletonLock symlink "host-pid".
        # Unlinking it always succeeds there, so ask whether that process is alive instead.
        lock = udd / "SingletonLock"
        if not lock.is_symlink():
            return False
        try:
            os.kill(int(os.readlink(lock).rsplit("-", 1)[-1]), 0)
            return True
        except PermissionError:
            return True                  # alive, owned by another user
        except (OSError, ValueError):
            lock.unlink(missing_ok=True)  # stale lock from a crashed process
            return False
    for lock in (udd / "lockfile", udd / "EBWebView" / "lockfile"):
        if not lock.exists():
            continue
        try:
            lock.unlink()                # a stale lock from a crashed process can be removed
        except OSError:
            return True                  # process holds it: the login window is still open
    return False


class _Cdp:
    def __init__(self, url: str):
        from websockets.sync.client import connect
        self.ws = connect(url, max_size=None, open_timeout=10)
        self.n = 0

    def call(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 20):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                raise GatewayError(f"Chrome không trả lời {method}")
            r = json.loads(self.ws.recv(timeout=left))
            if r.get("id") == self.n:
                if "error" in r:
                    raise GatewayError(f"Chrome báo lỗi ở {method}: {(r['error'] or {}).get('message')}")
                return r.get("result") or {}

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def _devtools_url(udd: Path, proc, timeout: float = 25) -> str:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise GatewayError("Trình duyệt đóng ngay khi mở hồ sơ này. Đóng cửa sổ đăng nhập Muse đang mở rồi thử lại.")
        try:
            port, path = (udd / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()[:2]
            return f"ws://127.0.0.1:{port.strip()}{path.strip()}"
        except (OSError, ValueError):
            time.sleep(0.3)
    raise GatewayError("Chrome không mở cổng DevTools")


def capture_cookies(udd: Path, url: str = "", headless: bool = True,
                    settle: float = RENEW_SETTLE_S) -> tuple[str, float]:
    """Open `url` in the profile at `udd`. Uses muse-login.exe (WebView2) when available,
    falling back to Chrome DevTools CDP."""
    import subprocess
    if _profile_busy(udd):
        raise GatewayError("hồ sơ này đang mở (cửa sổ đăng nhập). Đóng cửa sổ đó rồi gia hạn lại.")
    udd.mkdir(parents=True, exist_ok=True)
    exe = wv2_login_exe()
    if exe:
        try:
            cmd = [str(exe), "--profile-dir", str(udd), "--url", url or LOGIN_URL, "--timeout", "15"]
            if headless:
                cmd.append("--headless")
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if p.returncode == 0 and p.stdout.strip():
                data = json.loads(p.stdout.strip())
                if data.get("ok") and data.get("cookie"):
                    return data["cookie"], float(data.get("expires") or 0.0)
        except Exception:
            pass
    (udd / "DevToolsActivePort").unlink(missing_ok=True)
    args = [chrome_exe(), f"--user-data-dir={udd}", "--remote-debugging-port=0", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", *_chrome_flags(), "about:blank"]
    args[1:1] = ["--headless=new"] if headless else ["--window-position=-32000,-32000"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    cdp = None
    try:
        cdp = _Cdp(_devtools_url(udd, proc))
        tid = cdp.call("Target.createTarget", {"url": url or LOGIN_URL})["targetId"]
        sid = cdp.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        time.sleep(settle)
        header, exp = cookies_to_header(cdp.call("Storage.getCookies").get("cookies") or [])
        if not header or (exp and exp - time.time() < WARN_DAYS * 86400):
            r = cdp.call("Runtime.evaluate", {"expression": _CONTINUE_JS, "returnByValue": True}, session=sid)
            if (r.get("result") or {}).get("value"):
                time.sleep(settle)
                header, exp = cookies_to_header(cdp.call("Storage.getCookies").get("cookies") or [])
        return header, exp
    finally:
        if cdp:
            try:
                cdp.call("Browser.close", timeout=5)
            except Exception:  # noqa: BLE001
                pass
            cdp.close()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


def _bg_auto_renew_wv2(store: Store, key: str, proc) -> None:
    try:
        out, _ = proc.communicate(timeout=600)
        if proc.returncode == 0 and out and out.strip():
            data = json.loads(out.strip())
            if data.get("ok") and data.get("cookie"):
                renew_account(Gateway(store=store), store, key,
                              capture=lambda *_, **__: (data["cookie"], float(data.get("expires") or 0.0)))
    except Exception:
        pass


def open_login(store: Store, key: str = "", name: str = "", url: str = "") -> dict:
    """Open a visible window on the profile for one account so the owner can log in once.
    Uses Edge WebView2 (muse-login.exe) when available, falling back to visible Chrome."""
    import subprocess
    key = profile_key(key or name or f"new_{int(time.time())}")
    udd = profile_dir(store, key)
    if _profile_busy(udd):
        return {"key": key, "opened": False, "note": "cửa sổ đăng nhập của hồ sơ này đang mở sẵn"}
    udd.mkdir(parents=True, exist_ok=True)
    exe = wv2_login_exe()
    label = name or key
    if exe:
        proc = subprocess.Popen([str(exe), "--profile-dir", str(udd), "--url", url or LOGIN_URL,
                                 "--title", f"Đăng nhập Muse - {label}"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        threading.Thread(target=_bg_auto_renew_wv2, args=(store, key, proc), daemon=True).start()
        note = "Đăng nhập Muse trong cửa sổ vừa mở (cửa sổ sẽ tự đóng khi nhận diện phiên), rồi bấm Gia hạn."
        engine_name = "Edge WebView2"
    else:
        if os.name != "nt" and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
            raise GatewayError("máy này không có màn hình nên không mở được cửa sổ đăng nhập. "
                               "Dán cookie mới ở trang Muse (/ext/muse-accounts/) thay cho bước này.")
        subprocess.Popen([chrome_exe(), f"--user-data-dir={udd}", "--no-first-run", "--no-default-browser-check",
                          *_chrome_flags(), url or LOGIN_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        note = "Đăng nhập Muse trong cửa sổ vừa mở, đóng cửa sổ, rồi bấm Gia hạn."
        engine_name = "trình duyệt"
    st = store.state()
    prof = st.setdefault("profiles", {}).setdefault(key, {"created_at": int(time.time())})
    prof["url"] = url or prof.get("url") or LOGIN_URL
    if name:
        prof["name"] = name[:60]
    store.save_state(st)
    store.event(f"mở cửa sổ {engine_name} '{prof.get('name') or key}' để đăng nhập")
    return {"key": key, "opened": True, "note": note}


def _days_left(exp: float) -> float | None:
    return round((exp - time.time()) / 86400, 1) if exp else None


def renew_account(gw: Gateway, store: Store, key: str, capture=None) -> dict:
    """Read fresh cookies from the account's Chrome profile and push them to the gateway.
    The cookie itself never leaves this function; only a fingerprint is kept."""
    capture = capture or capture_cookies
    key = profile_key(key)
    prof = (store.state().get("profiles") or {}).get(key)
    if not prof:
        raise GatewayError("tài khoản này chưa có cửa sổ đăng nhập riêng. Bấm nút đăng nhập (op=login) một lần trước.")
    header, exp = capture(profile_dir(store, key), prof.get("url") or LOGIN_URL)
    st = store.state()
    prof = st.setdefault("profiles", {}).setdefault(key, prof)
    prof["checked_at"] = int(time.time())
    name = prof.get("name") or key
    if not header:
        prof["last_error"] = "hồ sơ chưa đăng nhập hoặc phiên đã hết"
        store.save_state(st)
        store.event(f"gia hạn '{name}' không được: cần đăng nhập lại")
        return {"key": key, "name": name, "ok": False, "warn": True, "error": prof["last_error"]}
    m = _SESS_RE.search(header)
    fp = hashlib.sha256(m.group(1).encode("utf-8")).hexdigest()[:24]
    accts = {a.get("id"): a for a in gw.accounts()}
    aid = next((x for x in (prof.get("account_id"), key) if x in accts), None)
    changed = True
    if aid:
        name = prof.get("name") or accts[aid].get("name") or key
        changed = st["fingerprints"].get(fp) != aid
        if changed:
            gw.patch(aid, {"credential": header})
    else:
        res = gw.add(name, header)
        acc = res.get("account") or res.get("data") or res
        aid = acc.get("id") if isinstance(acc, dict) else None
        if not aid:
            raise GatewayError("gateway không trả mã tài khoản sau khi thêm")
        try:
            gw.patch(aid, {"limits": {"chat": 0, "image": 0, "video": int(st["video_limit"])}})
        except GatewayError:
            pass
    ok, err = _alive(gw, {"id": aid})
    st = store.state()                  # reload: gateway calls above may have taken a while
    prof = st.setdefault("profiles", {}).setdefault(key, prof)
    st["fingerprints"] = {f: v for f, v in st["fingerprints"].items() if v != aid}
    st["fingerprints"][fp] = aid
    prof.update({"account_id": aid, "name": name, "expires": int(exp) if exp else 0,
                 "checked_at": int(time.time()), "last_error": "" if ok else err})
    if changed:
        prof["renewed_at"] = int(time.time())
    if ok and st["mode"] == "pool" and aid in (st.get("auto_off") or []):
        gw.patch(aid, {"enabled": True})
        st["auto_off"] = [x for x in st["auto_off"] if x != aid]
    store.save_state(st)
    left = _days_left(exp)
    warn = (not ok) or (left is not None and left < WARN_DAYS)
    store.event(f"gia hạn '{name}': {'cookie mới' if changed else 'cookie không đổi'}, "
                f"{'kết nối được' if ok else 'kiểm lỗi: ' + err}"
                + (f", còn {left} ngày" if left is not None else ""))
    return {"key": key, "name": name, "id": aid, "ok": ok, "changed": changed, "days_left": left,
            "warn": warn, "error": "" if ok else err}


def renew_all(gw: Gateway, store: Store, capture=None) -> dict:
    out = []
    for key in sorted((store.state().get("profiles") or {}).keys()):
        try:
            out.append(renew_account(gw, store, key, capture))
        except GatewayError as ex:
            out.append({"key": key, "ok": False, "warn": True, "error": str(ex)})
    warns = [r for r in out if r.get("warn")]
    note = ""
    if warns:
        note = ("Cần đăng nhập lại: " + "; ".join(
            f"{r.get('name') or r['key']}: " + (r.get("error") or f"cookie còn {r.get('days_left')} ngày")
            for r in warns) + ". Mở trang Muse (/ext/muse-accounts/) và bấm nút đăng nhập ở tài khoản đó, "
                "hoặc dán cookie mới cùng tên.")
    return {"renewed": out, "warn": bool(warns), "note": note}


def _profile_for(store: Store, aid: str) -> str | None:
    for key, p in (store.state().get("profiles") or {}).items():
        if p.get("account_id") == aid or key == profile_key(aid):
            return key
    return None


def _alive_or_renew(gw: Gateway, store: Store, a: dict) -> tuple[bool, str]:
    """Live test; when it fails and the account has a Chrome profile, renew once and test again."""
    ok, err = _alive(gw, a)
    if ok:
        return ok, err
    key = _profile_for(store, a["id"])
    if not key:
        return ok, err
    try:
        r = renew_account(gw, store, key)
    except GatewayError as ex:
        return False, f"{err}; gia hạn lỗi: {ex}"
    return (True, "") if r.get("ok") else (False, r.get("error") or err)


# ---------------------------------------------------------------- batch video (sync core)
def new_batch(store: Store, scenes: list, style: str, aspect_ratio: str, duration, out_dir: str) -> dict:
    clean = []
    for s in scenes or []:
        p = (s.get("prompt") if isinstance(s, dict) else str(s or "")).strip()
        if not p:
            continue
        clean.append({"n": len(clean) + 1, "prompt": p,
                      "duration": (s.get("duration") if isinstance(s, dict) else None) or duration,
                      "aspect_ratio": (s.get("aspect_ratio") if isinstance(s, dict) else None) or aspect_ratio,
                      "status": "pending", "job": None, "attempts": 0, "file": None, "error": ""})
    if not clean:
        raise GatewayError("cần ít nhất một cảnh có prompt")
    if len(clean) > MAX_SCENES:
        raise GatewayError(f"tối đa {MAX_SCENES} cảnh một lô")
    bid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    b = {"id": bid, "created": int(time.time()), "style": (style or "").strip(),
         "out_dir": str(Path(out_dir) / bid), "scenes": clean, "done": False}
    store.save_batch(b)
    return b


def _body(b: dict, sc: dict) -> dict:
    prompt = (b["style"] + "\n\n" + sc["prompt"]) if b.get("style") else sc["prompt"]
    body = {"prompt": prompt}
    if sc.get("aspect_ratio"):
        body["aspect_ratio"] = sc["aspect_ratio"]
    if sc.get("duration"):
        body["duration"] = max(3, min(10, int(sc["duration"])))
    return body


def _ext(mime: str) -> str:
    return {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}.get((mime or "").lower(), ".mp4")


def refresh_batch(gw: Gateway, store: Store, bid: str) -> dict:
    """One pass over a batch: submit pending scenes, poll running ones, download finished ones,
    resubmit failed ones up to MAX_ATTEMPTS. Safe to call any number of times."""
    b = store.batch(bid)
    if not b:
        raise GatewayError("không thấy lô này")
    for sc in b["scenes"]:
        if sc["status"] in ("done", "failed"):
            continue
        if sc["status"] == "pending":
            if sc["attempts"] >= MAX_ATTEMPTS:
                sc["status"] = "failed"
                continue
            try:
                job = gw.request("POST", "/v1/videos/generations", _body(b, sc))
                sc.update(job=job.get("id"), status="running", error="")
                sc["attempts"] += 1
            except GatewayError as ex:
                sc["error"] = str(ex)
                if "503" in str(ex) or "không nối được" in str(ex):
                    continue        # no account free or gateway down: try again next pass, no attempt used
                sc["attempts"] += 1
            continue
        try:
            j = gw.request("GET", f"/v1/jobs/{urllib.parse.quote(str(sc['job']), safe='')}")
        except GatewayError as ex:
            sc["error"] = str(ex)
            continue
        if j.get("status") == "succeeded":
            o = j.get("output") or {}
            try:
                d = Path(b["out_dir"])
                d.mkdir(parents=True, exist_ok=True)
                f = d / f"canh-{sc['n']:02d}{_ext(o.get('mime_type'))}"
                # The job's own content path on the gateway, not output.url: that is an absolute link built from
                # whatever Host the gateway saw, and the admin key must never follow a link to another host.
                gw.download(f"/v1/jobs/{urllib.parse.quote(str(sc['job']), safe='')}/content", f)
                sc.update(status="done", file=str(f), error="")
            except (GatewayError, OSError) as ex:
                sc["error"] = f"tải file lỗi: {ex}"
        elif j.get("status") == "failed":
            sc["error"] = str((j.get("error") or {}).get("message") or "job thất bại")[:300]
            sc["status"] = "pending" if sc["attempts"] < MAX_ATTEMPTS else "failed"
        else:
            sc["progress"] = str(j.get("progress_text") or j.get("status") or "")[:120]
    b["done"] = all(sc["status"] in ("done", "failed") for sc in b["scenes"])
    store.save_batch(b)
    return b


def batch_summary(b: dict) -> str:
    n = len(b["scenes"])
    done = [s for s in b["scenes"] if s["status"] == "done"]
    failed = [s for s in b["scenes"] if s["status"] == "failed"]
    lines = [f"Lô {b['id']}: {len(done)}/{n} cảnh xong, {len(failed)} hỏng"
             + (", đã kết thúc." if b["done"] else ", đang chạy."),
             f"Thư mục: {b['out_dir']}"]
    for s in b["scenes"]:
        tail = s.get("file") or s.get("error") or s.get("progress") or ""
        lines.append(f"- Cảnh {s['n']:02d}: {s['status']} (lần {s['attempts']}) {tail}".rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------- async wrappers
async def _poll_until_done(gw: Gateway, store: Store, bid: str) -> None:
    t0 = time.time()
    try:
        while time.time() - t0 < BATCH_DEADLINE_S:
            b = await asyncio.to_thread(refresh_batch, gw, store, bid)
            if b["done"]:
                store.event(f"lô video {bid} xong")
                return
            await asyncio.sleep(POLL_EVERY_S)
    except Exception as ex:  # noqa: BLE001
        store.event(f"lô video {bid} dừng theo dõi: {ex}")
    finally:
        _pollers.pop(bid, None)


def _ensure_poller(gw: Gateway, store: Store, bid: str) -> None:
    t = _pollers.get(bid)
    if t and not t.done():
        return
    _pollers[bid] = asyncio.get_running_loop().create_task(_poll_until_done(gw, store, bid))


def _say(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def _ctx_store(ctx) -> Store:
    return Store(Path(os.getenv("MUSE_PLUGIN_DATA") or ctx.data_dir))


def _gw(store: Store) -> Gateway:
    return Gateway(store=store)


def _out_root(ctx) -> Path:
    if ctx.vault_root:
        return Path(ctx.vault_root) / "attachments" / "muse-video"
    return Path(ctx.data_dir) / "videos"


def _inside(p: Path, roots: list[Path]) -> bool:
    try:
        rp = p.resolve()
    except OSError:
        return False
    return any(rp == r or r in rp.parents for r in roots)


def safe_path(raw: str, default: Path, store: Store, vault_root, what: str) -> Path:
    """A path a tool call names (so a model chose it): relative paths start at the brain, and the result must stay
    inside the brain or this plugin's data folder, symlinks followed. Writing videos or deleting a cookie file
    anywhere else on the disk is refused at every access level."""
    roots = [store.root.resolve()]
    if vault_root:
        roots.append(Path(vault_root).resolve())
    p = Path(str(raw)) if str(raw or "").strip() else Path(default)
    if not p.is_absolute():
        p = roots[-1] / p
    if not _inside(p, roots):
        raise GatewayError(f"{what} phải nằm trong brain đang mở hoặc thư mục dữ liệu của plugin")
    return p


# ---------------------------------------------------------------- tools
async def _t_accounts(args, ctx):
    args = args or {}
    op = str(args.get("op") or "list").lower()
    store = _ctx_store(ctx)
    gw = _gw(store)
    try:
        if op == "list":
            return _say(await asyncio.to_thread(overview, gw, store))
        if op == "check":
            return _say(await asyncio.to_thread(apply_mode, gw, store, True))
        if op == "rotate":
            return _say(await asyncio.to_thread(apply_mode, gw, store, False, True))
        if op == "mode":
            return _say(await asyncio.to_thread(set_mode, gw, store, str(args.get("mode") or ""),
                                                args.get("video_limit")))
        if op == "import_file":
            p = safe_path(str(args.get("path") or ""), store.root / "muse-cookies.txt", store, ctx.vault_root,
                          "file cookie")
            if not p.is_file():
                return f"ERROR: không thấy file {p}. Dán cookie ở trang Muse (/ext/muse-accounts/) cho nhanh."
            res = await asyncio.to_thread(import_cookies, gw, store, p.read_text(encoding="utf-8", errors="replace"))
            p.unlink(missing_ok=True)
            res["file_deleted"] = str(p)
            return _say(res)
        if op == "login":
            return _say(await asyncio.to_thread(open_login, store, str(args.get("id") or ""),
                                                str(args.get("name") or "")))
        if op == "renew":
            if args.get("id"):
                key = _profile_for(store, str(args["id"])) or str(args["id"])
                return _say(await asyncio.to_thread(renew_account, gw, store, key))
            return _say(await asyncio.to_thread(renew_all, gw, store))
    except GatewayError as ex:
        return f"ERROR: {ex}"
    return "ERROR: op chỉ nhận list | check | rotate | mode | import_file | login | renew"


async def _t_batch(args, ctx):
    args = args or {}
    store = _ctx_store(ctx)
    gw = _gw(store)
    try:
        out = safe_path(str(args.get("out_dir") or ""), _out_root(ctx), store, ctx.vault_root, "thư mục lưu video")
        accts = await asyncio.to_thread(gw.accounts)
        if not any(a.get("enabled") for a in accts):
            return ("ERROR: Muse Gateway chưa có tài khoản nào đang bật. Thêm tài khoản ở trang Muse "
                    "(/ext/muse-accounts/) trước.")
        if store.state()["mode"] == "rotate":
            await asyncio.to_thread(apply_mode, gw, store)
        b = await asyncio.to_thread(new_batch, store, args.get("scenes") or [], str(args.get("style") or ""),
                                    str(args.get("aspect_ratio") or ""), args.get("duration"), str(out))
        store.event(f"lô video {b['id']}: gửi {len(b['scenes'])} cảnh")
        b = await asyncio.to_thread(refresh_batch, gw, store, b["id"])
    except GatewayError as ex:
        return f"ERROR: {ex}"
    _ensure_poller(gw, store, b["id"])
    on = sum(1 for a in accts if a.get("enabled"))
    return (f"Đã gửi {len(b['scenes'])} cảnh, mã lô {b['id']}. Gateway chạy song song theo số tài khoản đang bật "
            f"({on}) nhân giới hạn video mỗi tài khoản, phần còn lại xếp hàng. Plugin tự theo dõi ở nền, cảnh lỗi "
            f"làm lại tối đa {MAX_ATTEMPTS} lần. Video chưa có cho tới khi muse_batch_status với mã lô này báo xong."
            "\n\n" + batch_summary(b))


async def _t_status(args, ctx):
    bid = str((args or {}).get("id") or "").strip()
    store = _ctx_store(ctx)
    gw = _gw(store)
    if not bid:
        bs = store.batches()[:10]
        if not bs:
            return "Chưa có lô video nào."
        return "\n".join(f"- {b['id']}: {sum(s['status'] == 'done' for s in b['scenes'])}/{len(b['scenes'])} cảnh"
                         f"{' (xong)' if b['done'] else ''}" for b in bs)
    try:
        b = await asyncio.to_thread(refresh_batch, gw, store, bid)
    except GatewayError as ex:
        return f"ERROR: {ex}"
    if not b["done"]:
        _ensure_poller(gw, store, bid)
    return batch_summary(b)


# ---------------------------------------------------------------- page routes
# Every route sits under /ext/muse-accounts/ and needs the owner's dashboard session (Javis enforces it; none of
# them is public). Errors come back as {"error": "..."} with status 200, so the page shows the sentence.
def _err(ex) -> dict:
    msg = str(ex)
    return {"error": msg[:1].upper() + msg[1:]}


async def _request_json(request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _route(fn):
    """Wrap a sync `fn(body, store)` into an async page route that runs it off the event loop."""
    async def h(request, c):
        b = await _request_json(request) if request.method == "POST" else {}
        try:
            return await asyncio.to_thread(fn, b, _ctx_store(c))
        except (GatewayError, ValueError) as ex:
            return _err(ex)
    return h


def _r_renew(b, store):
    aid = str(b.get("id") or "")
    if aid:
        r = renew_account(_gw(store), store, _profile_for(store, aid) or aid)
        r["loi"] = r.pop("error", "")    # a failed renewal is an answer, not a broken request
        return r
    return renew_all(_gw(store), store)


def _r_gateway(b, store):
    r = save_gateway(store, str(b.get("url") or ""), str(b.get("key") or ""), bool(b.get("reset")))
    return {"ok": r["ok"], "loi": r["error"]}


ROUTES = {
    "state": (lambda b, st: overview(_gw(st), st), ("GET",)),
    "import": (lambda b, st: import_cookies(_gw(st), st, str(b.get("text") or "")), ("POST",)),
    "mode": (lambda b, st: set_mode(_gw(st), st, str(b.get("mode") or "")), ("POST",)),
    "limit": (lambda b, st: set_video_limit(_gw(st), st, b.get("video_limit")), ("POST",)),
    "check": (lambda b, st: apply_mode(_gw(st), st, True), ("POST",)),
    "rotate": (lambda b, st: apply_mode(_gw(st), st, False, True), ("POST",)),
    "remove": (lambda b, st: remove_account(_gw(st), st, str(b.get("id") or "")), ("POST",)),
    "login": (lambda b, st: open_login(st, str(b.get("id") or ""), str(b.get("name") or "")), ("POST",)),
    "renew": (_r_renew, ("POST",)),
    "gateway": (_r_gateway, ("POST",)),
}


def register(ctx):
    for path, (fn, methods) in ROUTES.items():
        ctx.register_http(path, _route(fn), methods=methods)

    ctx.register_tool(
        name="muse_accounts",
        description=("Quản lý tài khoản Muse trong Muse Gateway. op=list: danh sách (cookie đã che), chế độ, nhật ký, "
                     "tình trạng gateway. op=check: kiểm từng tài khoản, tắt cái hỏng (Luân phiên thì chuyển tài "
                     "khoản). op=rotate: chuyển sang tài khoản kế (chế độ Luân phiên). op=mode: mode='pool' (Gộp) hoặc "
                     "'rotate' (Luân phiên), video_limit = số video cùng lúc mỗi tài khoản. op=import_file: nạp cookie "
                     "từ một file trong brain hoặc thư mục plugin rồi xoá file đó. op=login: mở cửa sổ đăng nhập riêng "
                     "của tài khoản (id) trên máy chạy Javis để chủ đăng nhập một lần. op=renew: lấy cookie mới từ cửa "
                     "sổ đăng nhập đã lưu và đẩy sang gateway (id = một tài khoản, bỏ trống = tất cả); kết quả có note "
                     "'Cần đăng nhập lại' khi chủ phải đăng nhập lại. Cookie chỉ nạp ở trang Muse "
                     "(/ext/muse-accounts/) hoặc qua file, không nhận qua chat."),
        handler=_t_accounts, min_mode="readonly", emoji="🎭", op_modes={"list": "readonly", "check": "safe",
                                                                       "rotate": "safe", "mode": "safe",
                                                                       "import_file": "safe", "login": "safe",
                                                                       "renew": "safe"},
        schema={"type": "object", "properties": {
            "op": {"type": "string", "enum": ["list", "check", "rotate", "mode", "import_file", "login", "renew"]},
            "id": {"type": "string", "description": "op=login/renew: mã tài khoản trong gateway"},
            "name": {"type": "string", "description": "op=login: tên cho tài khoản mới"},
            "mode": {"type": "string", "enum": ["pool", "rotate"]},
            "video_limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "path": {"type": "string", "description": "op=import_file: file cookie trong brain hoặc thư mục plugin "
                                                      "(mặc định muse-cookies.txt trong thư mục plugin)"}},
            "required": ["op"]})
    ctx.register_tool(
        name="muse_batch_video",
        description=("Tạo nhiều cảnh video Muse SONG SONG trên mọi tài khoản đang bật. Trả mã lô ngay, video chưa có; "
                     "plugin tự theo dõi ở nền, làm lại cảnh lỗi, tải file canh-01.mp4, canh-02.mp4... theo thứ tự "
                     "vào brain. Đặt mô tả nhân vật và phong cách chung vào 'style' để các cảnh đồng nhất. Đọc tiến độ "
                     "bằng muse_batch_status."),
        handler=_t_batch, min_mode="safe", emoji="🎬",
        schema={"type": "object", "properties": {
            "scenes": {"type": "array", "description": "Danh sách cảnh theo thứ tự", "items": {
                "type": "object", "properties": {
                    "prompt": {"type": "string"},
                    "duration": {"type": "integer", "minimum": 3, "maximum": 10},
                    "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]}},
                "required": ["prompt"]}},
            "style": {"type": "string", "description": "Mô tả nhân vật, bối cảnh, phong cách dùng chung, ghép trước mỗi prompt"},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
            "duration": {"type": "integer", "minimum": 3, "maximum": 10, "description": "Giây mỗi cảnh"},
            "out_dir": {"type": "string", "description": "Thư mục lưu trong brain (mặc định attachments/muse-video)"}},
            "required": ["scenes"]})
    ctx.register_tool(
        name="muse_batch_status",
        description="Tiến độ một lô video Muse theo mã lô (đường file từng cảnh khi xong). Bỏ trống mã để liệt kê 10 lô gần nhất.",
        handler=_t_status, min_mode="readonly", emoji="📼",
        schema={"type": "object", "properties": {"id": {"type": "string", "description": "Mã lô từ muse_batch_video"}}})

    def _stop():
        for t in list(_pollers.values()):
            t.cancel()
        _pollers.clear()

    ctx.on_unload(_stop)
