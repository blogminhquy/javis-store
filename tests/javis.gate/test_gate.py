"""javis.gate 2.0.0: ChatGPT and Claude connect through OAuth and post into a Javis chat.

    JAVIS_OS_DIR=/path/to/javis-os python tests/javis.gate/test_gate.py      (no network)

Runs the real Javis OS app with the pack installed through the real installer, then walks the whole path an outside
AI takes: dynamic client registration with Claude's callback, the owner's consent page, the token exchange, then MCP
calls on /ext/javis-gate/mcp. The model behind Javis is a fake OpenRouter (HTTP mocked), so the chat turn is real.

Locks:
1. Redirects: ChatGPT, Claude and localhost (Claude Code / Desktop) yes; any other host no.
2. The four chat tools are listed on the Gate; javis_chat_send only at Full access.
3. javis_chat_send posts into the chat, Javis answers, and the stored message carries the sender "Claude".
4. Upgrade from javis.javis-trong-chatgpt 1.x: the token issued before the update still works, on the OLD address.
5. Notes (Javis 0.441.0+): javis_chat_note saves the owner's decision into a "Từ Claude: <title>" chat.
6. Model and effort (gate 2.2.0, on a Javis OS that has javis_chat_models): through this pack's own OAuth door, the
   outside AI pins a chat's model and sets one message's effort, the consent page says so, and javis_chat_updates
   hands over a result it did not wait for.
7. Other AI tools (gate 2.2.0, any Javis OS): an unknown redirect address is refused but listed for the owner, who
   alone can allow it (https or an app scheme, never http); a personal key, made by the owner only, connects a tool
   that only sends a header, labels its messages with the key's name, and dies on revoke or "disconnect all".
"""
import _paths  # noqa: F401
from _paths import PACK, STORE

import asyncio
import base64
import hashlib
import io
import json
import os
import secrets
import tempfile
import zipfile
from pathlib import Path

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-gate-store-")
os.environ["JAVIS_REQUIRE_LOGIN"] = "1"
os.environ["JAVIS_API_STREAM_TOOLS"] = "0"

from fastapi.testclient import TestClient   # noqa: E402
import config as cfg   # noqa: E402
import main            # noqa: E402
import engine          # noqa: E402
import pack_install    # noqa: E402
import plugins_host    # noqa: E402
from config import STATE_DIR  # noqa: E402

_fails = []


def check(name, cond, more=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{more}]" if more and not cond else ""))
    if not cond:
        _fails.append(name)


def zip_dir(d: Path) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for f in sorted(d.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.writestr(f.relative_to(d).as_posix(), f.read_bytes())
    return b.getvalue()


def install(data: bytes):
    s = pack_install.soi(data, "pack.zip")
    assert s.get("ok"), s
    r = pack_install.cai(s["staging_id"], s["sha256"], enable=True)
    assert r.get("ok"), r
    plugins_host.invalidate()
    return s, r


# ---- fake model behind Javis: answers "DONE: <last line of the owner's message>" ----
class _Resp:
    def __init__(self, data):
        self.status_code, self._data, self.text, self.headers = 200, data, json.dumps(data), {}

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        msgs = (json or {}).get("messages") or []
        last = next((m.get("content") for m in reversed(msgs) if m.get("role") == "user"), "")
        line = str(last or "").strip().splitlines()[-1] if str(last or "").strip() else ""
        return _Resp({"choices": [{"message": {"content": "DONE: " + line[:120]}}],
                      "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


main._chat_provider = lambda _cfg: ("openrouter", "api", "test-key", "test-model")
main._reasoning_level = lambda _cfg: "off"
main.usage_store.record = lambda *a, **k: None
engine.httpx.AsyncClient = _FakeClient

_s = cfg.read_settings()
_s["auth"] = {"username": "admin", **dict(zip(("password_hash", "salt"), cfg.hash_password("matkhau123")))}
_s.setdefault("domain", {})["custom"] = "javis.example.com"
cfg.write_settings(_s)

# ============================================================
# 4a. Old pack first (1.x, as a user has it today), with a token issued before the update
# ============================================================
OLD = Path(tempfile.mkdtemp(prefix="old-gate-"))
(OLD / "plugins" / "javis-trong-chatgpt").mkdir(parents=True)
(OLD / "javis-pack.yaml").write_text(
    "format: javis-pack\nspec: 1\nid: javis.javis-trong-chatgpt\nversion: 1.0.0\nname: Javis trong ChatGPT\n"
    "provides:\n  plugins:\n    - plugins/javis-trong-chatgpt\n", encoding="utf-8")
src = (PACK / "plugins" / "javis-gate" / "plugin.py").read_text(encoding="utf-8")
(OLD / "plugins" / "javis-trong-chatgpt" / "plugin.py").write_text(src, encoding="utf-8")
(OLD / "plugins" / "javis-trong-chatgpt" / "plugin.yaml").write_text(
    "name: Javis trong ChatGPT\nslug: javis-trong-chatgpt\nversion: 1.0.0\nenabled: true\nmin_mode: readonly\n",
    encoding="utf-8")
install(zip_dir(OLD))
old_data = STATE_DIR / "plugin-data" / "javis-trong-chatgpt"
old_data.mkdir(parents=True, exist_ok=True)
old_token = secrets.token_urlsafe(32)
h = hashlib.sha256(old_token.encode()).hexdigest()
import time  # noqa: E402
(old_data / "kho.json").write_text(json.dumps({
    "clients": {"chatgpt-x": {"id": "chatgpt-x", "ten": "ChatGPT", "redirect_uris": ["https://chatgpt.com/cb"],
                              "tao": time.time()}},
    "tokens": {h: {"loai": "access", "client": "chatgpt-x", "ho": "f1", "het": time.time() + 3600}},
    "yeu_cau": {}, "ma": {}}), encoding="utf-8")

# ---- 4b. update to javis.gate from this repo ----
s, r = install(zip_dir(PACK))
check("installing javis.gate replaces the old pack", r.get("replaced") == ["javis.javis-trong-chatgpt"], r)
check("old plugin data (tokens) moved to javis-gate", (STATE_DIR / "plugin-data" / "javis-gate" / "kho.json").is_file())

stranger = TestClient(main.app)
owner = TestClient(main.app)
check("owner logs in", owner.post("/auth/login", data={"username": "admin", "password": "matkhau123"}).json().get("ok"))
HDR = {"Origin": "https://chatgpt.com"}


def rpc(client, path, method, params=None, token="", n=1):
    r = client.post(path, json={"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}},
                    headers={"Authorization": f"Bearer {token}", **HDR})
    return r


r = rpc(stranger, "/ext/javis-trong-chatgpt/mcp", "tools/list", token=old_token)
check("token from before the update still works on the OLD address", r.status_code == 200
      and "javis_chat_find" in r.text, r.status_code)
r = stranger.get("/.well-known/oauth-protected-resource/ext/javis-trong-chatgpt/mcp")
check("discovery on the old address answers with the old resource",
      r.status_code == 200 and r.json().get("resource", "").endswith("/ext/javis-trong-chatgpt/mcp"), r.text[:200])
r = stranger.get("/.well-known/oauth-protected-resource/ext/javis-gate/mcp")
check("discovery on the new address answers with the new resource",
      r.status_code == 200 and r.json().get("resource", "").endswith("/ext/javis-gate/mcp"), r.text[:200])

# ============================================================
# 1. Redirects
# ============================================================
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("gate_plugin", PACK / "plugins" / "javis-gate" / "plugin.py")
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
for uri, ok in (("https://claude.ai/api/mcp/auth_callback", True), ("https://claude.com/api/mcp/auth_callback", True),
                ("https://chatgpt.com/connector_platform_oauth_redirect", True), ("http://localhost:33418/callback", True),
                ("http://127.0.0.1:5000/cb", True), ("http://claude.ai/cb", False), ("https://evil.example/cb", False),
                ("https://claude.ai.evil.example/cb", False), ("javascript:alert(1)", False)):
    check(f"redirect {uri} -> {'allowed' if ok else 'refused'}", G.redirect_hop_le(uri) is ok)
check("AI name from client_name", G.ten_ai("Claude") == "Claude" and G.ten_ai("ChatGPT Connector") == "ChatGPT"
      and G.ten_ai("claude-code") == "Claude")

# ============================================================
# Full OAuth with Claude's callback
# ============================================================
CB = "https://claude.ai/api/mcp/auth_callback"
r = stranger.post("/ext/javis-gate/oauth/register", json={"client_name": "Claude", "redirect_uris": [CB]}, headers=HDR)
check("Claude registers itself (RFC 7591)", r.status_code == 201, r.text[:200])
cid = r.json().get("client_id", "")
ver = secrets.token_urlsafe(48)
ch = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).decode().rstrip("=")
q = {"client_id": cid, "redirect_uri": CB, "response_type": "code", "state": "st1",
     "code_challenge": ch, "code_challenge_method": "S256"}
r = owner.get("/ext/javis-gate/oauth/authorize", params=q)
check("consent page names Claude", r.status_code == 200 and "Cho phép Claude dùng Javis?" in r.text, r.text[:300])
yid = r.text.split('name="yeu_cau" value="', 1)[1].split('"', 1)[0]
r = owner.post("/ext/javis-gate/oauth/authorize", data={"yeu_cau": yid, "hanh_dong": "cho_phep"},
               headers={"Origin": "http://testserver"}, follow_redirects=False)
check("allow sends the code back to Claude", r.status_code == 302 and r.headers["location"].startswith(CB), r.text[:200])
code = r.headers["location"].split("code=", 1)[1].split("&", 1)[0]
r = stranger.post("/ext/javis-gate/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                       "code_verifier": ver, "client_id": cid, "redirect_uri": CB},
                  headers=HDR)
check("token exchange", r.status_code == 200 and r.json().get("access_token"), r.text[:200])
tok = r.json()["access_token"]

# ============================================================
# 2. Tools on the Gate
# ============================================================
names = [t["name"] for t in rpc(stranger, "/ext/javis-gate/mcp", "tools/list", token=tok).json()["result"]["tools"]]
check("Full access lists the four chat tools",
      all(n in names for n in ("javis_chat_find", "javis_chat_read", "javis_chat_send", "javis_chat_result")))
plugin_ctx = next(lp.ctx for lp in plugins_host._plugin_http() if lp.slug == "javis-gate")
G.dat_muc_quyen(plugin_ctx, "suggest")
names = [t["name"] for t in rpc(stranger, "/ext/javis-gate/mcp", "tools/list", token=tok).json()["result"]["tools"]]
check("Read-only level: no javis_chat_send, find and read stay", "javis_chat_send" not in names
      and "javis_chat_find" in names)
G.dat_muc_quyen(plugin_ctx, "full")

# ============================================================
# 3. Post into a chat
# ============================================================
ss = main.get_store()
sid = ss.create_session(brain="brain", engine="test", model="test", title="Viết Web")
ss.append_message(sid, "user", "Làm trang web giúp anh")
ss.append_message(sid, "assistant", "Đã dựng bản nháp.")
r = rpc(stranger, "/ext/javis-gate/mcp", "tools/call", {"name": "javis_chat_find", "arguments": {"query": "viet web"}},
        token=tok)
check("find the 'Viết Web' chat", sid in r.json()["result"]["content"][0]["text"])
r = rpc(stranger, "/ext/javis-gate/mcp", "tools/call",
        {"name": "javis_chat_send", "arguments": {"id": sid, "message": "Anh duyệt phương án 2. Làm trang giỏ hàng.",
                                                  "wait_seconds": 15}}, token=tok)
txt = r.json()["result"]["content"][0]["text"]
out = json.loads(txt) if not txt.startswith("ERROR") else {}
check("send: Javis answers in the same call", (out.get("result") or {}).get("status") == "done"
      and "trang giỏ hàng" in (out.get("result") or {}).get("answer", ""), txt[:300])
last_user = [m for m in ss.get_messages(sid) if m["role"] == "user"][-1]["content"]
check("the stored message says it came from Claude", last_user.startswith("[GỬI QUA JAVIS GATE: Claude."),
      last_user[:80])

# ============================================================
# 5. Notes (Javis 0.441.0): the outside AI saves what the owner decided, Javis runs nothing
# ============================================================
import chat_bridge  # noqa: E402
if "javis_chat_note" in chat_bridge.TOOLS:
    r = rpc(stranger, "/ext/javis-gate/mcp", "tools/call",
            {"name": "javis_chat_note", "arguments": {"title": "Tet plan", "asked": "Run ads for Tet?",
                                                      "decided": "Plan 2, 5M a week"}}, token=tok)
    txt = r.json()["result"]["content"][0]["text"]
    out = json.loads(txt) if not txt.startswith("ERROR") else {}
    check("note: saved into its own 'Từ Claude: <title>' chat", out.get("title") == "Từ Claude: Tet plan", txt[:200])
    msgs = ss.get_messages(out.get("id", "")) if out.get("id") else []
    check("note: the owner's decision is in Javis", any("Plan 2" in (m.get("content") or "") for m in msgs))
else:
    print("skip notes: this Javis OS is older than 0.441.0")

# ============================================================
# 6. Model and effort, and unread results (javis.gate 2.2.0 on a Javis OS that has them)
# ============================================================
if "javis_chat_models" in chat_bridge.TOOLS:
    SEEN = []
    _post = _FakeClient.post

    async def _post_seen(self, url, headers=None, json=None):
        SEEN.append({"model": (json or {}).get("model"), "reasoning": (json or {}).get("reasoning")})
        return await _post(self, url, headers=headers, json=json)
    _FakeClient.post = _post_seen
    cfg.update_settings({"model": {"openrouter_key": "test-key", "catalog": {"openrouter": ["test-model", "model-b"]}}})

    def call(name, args):
        r = rpc(stranger, "/ext/javis-gate/mcp", "tools/call", {"name": name, "arguments": args}, token=tok)
        return r.json()["result"]["content"][0]["text"]

    txt = call("javis_chat_models", {"id": sid})
    check("javis_chat_models through the gate lists the connected models", "model-b" in txt, txt[:200])
    txt = call("javis_chat_send", {"id": sid, "message": "Viết lại trang chủ.", "model": "openrouter/model-b",
                                   "effort": "high", "wait_seconds": 15})
    out = json.loads(txt) if not txt.startswith("ERROR") else {}
    check("send with model + effort through the gate: answered", (out.get("result") or {}).get("status") == "done",
          txt[:300])
    row = ss.get_session(sid)
    check("the chat is pinned to the model the outside AI picked",
          row.get("pinned_provider") == "openrouter" and row.get("pinned_model") == "model-b", row)
    check("that turn ran on it, at that effort", SEEN and SEEN[-1] == {"model": "model-b", "reasoning": {"effort": "high"}},
          SEEN[-1:])
    # A turn left running after the request ends does not go on under TestClient (its event loop closes with the
    # request), so the unread-result path itself is locked in Javis OS (tests/python/test_javis_gate_model_notice.py).
    # Here: the tool answers through this pack's door, and a result the AI already waited for is not handed over again.
    txt = call("javis_chat_updates", {})
    check("javis_chat_updates answers through the gate, nothing unread after a send it waited for",
          not txt.startswith("ERROR") and json.loads(txt).get("items") == [], txt[:300])
    _FakeClient.post = _post
    r = owner.get("/ext/javis-gate/oauth/authorize", params={**q, "state": "st2"})
    check("consent page says the AI may pick the model and effort", "chọn cả model" in r.text, r.text[:300])
else:
    print("skip model/effort: this Javis OS has no javis_chat_models yet")

# ============================================================
# 7. Other AI tools: owner-allowed addresses and personal keys
# ============================================================
check("names of other AIs", G.ten_ai("Gemini CLI") == "Gemini" and G.ten_ai("Mistral Le Chat") == "Le Chat"
      and G.ten_ai("Codex (OpenAI)") == "Codex" and G.ten_ai("GitHub Copilot") == "Copilot"
      and G.ten_ai("Máy  của tôi") == "Máy của tôi")
for uri, want in (("https://chat.mistral.ai/oauth/cb", "https://chat.mistral.ai"),
                  ("cursor://anysphere.cursor-mcp/oauth/callback", "cursor://anysphere.cursor-mcp"),
                  ("http://chat.mistral.ai/cb", ""), ("javascript://x.com/%0aalert(1)", ""),
                  ("http://localhost:3000/cb", ""), ("data://x/y", "")):
    check(f"origin of {uri} -> {want or 'refused'}", G.goc_uri(uri) == want, G.goc_uri(uri))
OWN = {"Origin": "http://testserver"}
LC = "https://chat.mistral.ai/connectors/oauth/callback"
r = stranger.post("/ext/javis-gate/oauth/register", json={"client_name": "Le Chat", "redirect_uris": [LC]}, headers=HDR)
check("unknown web tool: refused at registration", r.status_code == 400 and "Javis Gate page" in r.text, r.text[:200])
page = owner.get("/ext/javis-gate/").text
check("the owner's page lists it as asking to connect", "https://chat.mistral.ai" in page and "Le Chat" in page)
r = stranger.post("/ext/javis-gate/nguon", json={"goc": "https://chat.mistral.ai", "cho": True}, headers=OWN)
check("a stranger cannot allow an address", r.status_code in (401, 403), r.status_code)
r = owner.post("/ext/javis-gate/nguon", json={"goc": "http://chat.mistral.ai", "cho": True}, headers=OWN)
check("the owner cannot allow plain http", r.status_code == 400, r.text[:200])
r = owner.post("/ext/javis-gate/nguon", json={"goc": "https://chat.mistral.ai", "cho": True}, headers=OWN)
check("the owner allows it", r.status_code == 200 and r.json().get("ok"), r.text[:200])
check("it leaves the asking list", "data-cho=\"1\"" not in owner.get("/ext/javis-gate/").text)
r = stranger.post("/ext/javis-gate/oauth/register", json={"client_name": "Le Chat", "redirect_uris": [LC]}, headers=HDR)
check("then the tool registers", r.status_code == 201, r.text[:200])
lc_id = r.json().get("client_id", "")
ver2 = secrets.token_urlsafe(48)
ch2 = base64.urlsafe_b64encode(hashlib.sha256(ver2.encode()).digest()).decode().rstrip("=")
q2 = {"client_id": lc_id, "redirect_uri": LC, "response_type": "code", "state": "lc",
      "code_challenge": ch2, "code_challenge_method": "S256"}
r = owner.get("/ext/javis-gate/oauth/authorize", params=q2)
check("consent page names Le Chat and says the owner added this address",
      "Cho phép Le Chat dùng Javis?" in r.text and "tự cho phép thêm" in r.text, r.text[:300])
yid = r.text.split('name="yeu_cau" value="', 1)[1].split('"', 1)[0]
r = owner.post("/ext/javis-gate/oauth/authorize", data={"yeu_cau": yid, "hanh_dong": "cho_phep"}, headers=OWN,
               follow_redirects=False)
code2 = r.headers.get("location", "").split("code=", 1)[-1].split("&", 1)[0]
r = stranger.post("/ext/javis-gate/oauth/token", data={"grant_type": "authorization_code", "code": code2,
                                                       "code_verifier": ver2, "client_id": lc_id, "redirect_uri": LC},
                  headers=HDR)
check("Le Chat gets its token", r.status_code == 200 and r.json().get("access_token"), r.text[:200])
owner.post("/ext/javis-gate/nguon", json={"goc": "https://chat.mistral.ai", "cho": False}, headers=OWN)
r = owner.get("/ext/javis-gate/oauth/authorize", params=q2)
check("after the owner removes the address, a new consent is refused", "không còn được cho phép" in r.text, r.text[:300])
CUR = "cursor://anysphere.cursor-mcp/oauth/callback"
r = stranger.post("/ext/javis-gate/oauth/register", json={"client_name": "Cursor", "redirect_uris": [CUR]}, headers=HDR)
check("an app scheme (cursor://) waits for the owner too", r.status_code == 400
      and "cursor://anysphere.cursor-mcp" in owner.get("/ext/javis-gate/").text)
owner.post("/ext/javis-gate/nguon", json={"goc": "cursor://anysphere.cursor-mcp", "cho": True}, headers=OWN)
r = stranger.post("/ext/javis-gate/oauth/register", json={"client_name": "Cursor", "redirect_uris": [CUR]}, headers=HDR)
check("and registers once allowed", r.status_code == 201, r.text[:200])

r = stranger.post("/ext/javis-gate/khoa", json={"ten": "n8n"}, headers=OWN)
check("a stranger cannot make a key", r.status_code in (401, 403), r.status_code)
check("a key needs a name", owner.post("/ext/javis-gate/khoa", json={"ten": "  "}, headers=OWN).status_code == 400)
r = owner.post("/ext/javis-gate/khoa", json={"ten": "n8n"}, headers=OWN)
key = r.json().get("khoa", "")
check("the owner makes a key for n8n", r.status_code == 200 and key.startswith("jg_"), r.text[:200])
page = owner.get("/ext/javis-gate/").text
check("the page lists the key by name, never the key itself", "n8n" in page and key not in page)
kho = (STATE_DIR / "plugin-data" / "javis-gate" / "kho.json").read_text(encoding="utf-8")
check("the key is stored hashed only", key not in kho and hashlib.sha256(key.encode()).hexdigest() in kho)
r = rpc(stranger, "/ext/javis-gate/mcp", "tools/list", token=key)
check("the key opens the gate", r.status_code == 200 and "javis_chat_find" in r.text, r.status_code)
r = rpc(stranger, "/ext/javis-gate/mcp", "tools/call",
        {"name": "javis_chat_send", "arguments": {"id": sid, "message": "Gửi từ n8n.", "wait_seconds": 15}}, token=key)
last_user = [m for m in ss.get_messages(sid) if m["role"] == "user"][-1]["content"]
check("its messages carry the key's name as the source", last_user.startswith("[GỬI QUA JAVIS GATE: n8n."),
      last_user[:80])
check("a made-up jg_ key is refused", rpc(stranger, "/ext/javis-gate/mcp", "tools/list",
                                          token="jg_" + secrets.token_urlsafe(32)).status_code == 401)
kid = next(k["id"] for k in G.ds_khoa_rieng(plugin_ctx) if k["ten"] == "n8n")
check("revoke", owner.post("/ext/javis-gate/khoa-xoa", json={"id": kid}, headers=OWN).json().get("ok"))
check("a revoked key is refused at once", rpc(stranger, "/ext/javis-gate/mcp", "tools/list", token=key).status_code == 401)
key2 = owner.post("/ext/javis-gate/khoa", json={"ten": "Script"}, headers=OWN).json().get("khoa", "")
owner.post("/ext/javis-gate/ngat", json={}, headers=OWN)
check("'disconnect all' also kills personal keys",
      rpc(stranger, "/ext/javis-gate/mcp", "tools/list", token=key2).status_code == 401)

print()
print("ALL PASS" if not _fails else f"{len(_fails)} FAIL: {_fails}")
raise SystemExit(1 if _fails else 0)
