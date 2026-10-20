"""Real Javis pack loader and native HTTP/hub, against a loopback-only fake MCP.

JAVIS_OS_DIR must point at a Javis checkout. No real Baizhi key, service or model.
"""
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
HOST = Path(os.environ.get("JAVIS_OS_DIR", "")).resolve()
if not (HOST / "server/packs.py").is_file():
    raise SystemExit("Set JAVIS_OS_DIR to a real Javis OS checkout")
TEMP = tempfile.TemporaryDirectory(prefix="javis-baizhi-test-")
STATE = Path(TEMP.name) / "state"
BRAIN = Path(TEMP.name) / "brain"
BRAIN.mkdir()
os.environ["JAVIS_STATE_DIR"] = str(STATE)
os.environ["BRAINS_DIR"] = str(BRAIN.parent)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, str(HOST / "server"))

# Catch any accidental external socket before importing the real host modules.
original_connect = socket.socket.connect

def loopback_only_connect(sock, address):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError("Non-loopback networking forbidden in this test")
    return original_connect(sock, address)

socket.socket.connect = loopback_only_connect
import config
import packs
import pack_install
import mcp_catalog
import mcp_store
import mcp_client
import mcp_hub
import secrets_store
import team_gate
import team_approval
import turn_context
import nen_phan_hoi
import httpx
from fastapi import FastAPI, Request

# Register the exact current main.py wrapper without importing its full application lifecycle.
# The fixture uses real HTTP auth, turn binding, middleware, discovery and guarded dispatch;
# it does not exercise the full dashboard, startup services or a real model.
app = FastAPI()
app.add_middleware(nen_phan_hoi.NenPhanHoi)
main_tree = ast.parse((HOST / "server/main.py").read_text(encoding="utf-8"))
hub_wrapper = next(n for n in main_tree.body
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "hub_mcp")
exec(compile(ast.Module(body=[hub_wrapper], type_ignores=[]), str(HOST / "server/main.py"), "exec"),
     {"app": app, "Request": Request, "mcp_hub": mcp_hub})

CHECKS = []
REQUESTS = []
TOOLS = ["websearch_search", "web_scrape", "web_extract", "synthetic_new_tool",
         "synthetic_send_message", "synthetic_payment"]
SYNTHETIC_KEY = "synthetic-baizhi-not-a-real-key"


def check(name, condition):
    CHECKS.append({"name": name, "pass": bool(condition)})
    assert condition, name
    print("PASS", name)


class FakeMCP(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        method = body.get("method")
        auth_ok = self.headers.get("Authorization") == "Bearer " + SYNTHETIC_KEY
        REQUESTS.append({"method": method, "authMatchedSynthetic": auth_ok,
                         "hasSession": bool(self.headers.get("Mcp-Session-Id"))})
        if not auth_ok:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"synthetic invalid key"}}')
            return
        if method.startswith("notifications/"):
            self.send_response(202)
            self.end_headers()
            return
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "synthetic-baizhi", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": "Synthetic fixture only",
                                  "inputSchema": {"type": "object", "properties": {}}}
                                 for name in TOOLS]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "synthetic-result:" + body["params"]["name"]}]}
        else:
            result = {}
        payload = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "synthetic-session")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


async def run():
    archive = ROOT / "dist/community-baizhi-0.1.0.zip"
    con = json.loads((ROOT / "packs/community.baizhi/connectors/baizhi.json").read_bytes())
    check("published artifact has only fixed Baizhi URL", con["url"] == "https://agent-toolkit.app.baizhi.cloud/mcp" and not con.get("url_template"))
    check("all tools conservatively danger", con["tool_meta"] == {"read": [], "write": [], "danger": ["*"]})
    check("no secret value/default in package", not con["auth"]["fields"][0].get("default") and SYNTHETIC_KEY not in archive.read_bytes().decode("latin1"))
    config.update_settings({"mcp": {"hub": True, "lazy_tools": False}})
    before = mcp_store.list_connections()
    report = pack_install.soi(archive.read_bytes(), archive.name)
    check("actual host ZIP inspector", report["ok"] and report["tier"] == "data" and report["connectors"] == ["baizhi"])
    check("preview makes no network or account", not REQUESTS and mcp_store.list_connections() == before)
    check("wrong consent SHA rejected", not pack_install.cai(report["staging_id"], "wrong")["ok"])
    installed = pack_install.cai(report["staging_id"], report["sha256"], enable=True)
    check("actual host installs data pack", installed["ok"] and installed["enabled"] and installed["tier"] == "data")
    check("install makes no network or account", not REQUESTS and mcp_store.list_connections() == before)
    loaded = mcp_catalog.get("baizhi")
    check("actual loader preserves pack ownership", loaded["_pack"] == "community.baizhi" and loaded["url"] == con["url"])
    check("loader enforces readonly default", loaded["default_perm"] == "readonly")
    public = next(c for c in mcp_catalog.public_catalog() if c["id"] == "baizhi")
    check("actual named public catalog card", "Baizhi" in public["name"] and public["pack"] == "community.baizhi")
    check("local installed logo route", public["icon"] == "/packs/community.baizhi/asset/baizhi.png")
    check("required key projection has no default", not public["fields"][0]["optional"] and not public["fields"][0]["default"])
    for name in TOOLS + ["brand_new_future_action"]:
        check("danger classification " + name, mcp_catalog.classify(loaded, name) == "danger")
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMCP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%d/mcp" % server.server_port
    try:
        cid, error = mcp_store.add_connection("baizhi", {"fields": {"api_key": SYNTHETIC_KEY}, "url": url})
        check("actual account save has enabledTrue and readonly", not error and mcp_store.get_connection(cid)["enabled"] and mcp_store.get_connection(cid)["perm"] == "readonly")
        check("saving alone does not call remote", not REQUESTS)
        check("saved public projection masks synthetic key", SYNTHETIC_KEY not in json.dumps(mcp_store.get_connection(cid)))
        raw_state = mcp_store.STORE.read_text()
        check("normal encrypted host storage", secrets_store.encryption_available() and SYNTHETIC_KEY not in raw_state and "enc:" in raw_state)
        resolved = next(c for c in mcp_store.resolved() if c["id"] == cid)
        check("actual secret to Bearer reconstruction", resolved["headers"] == {"Authorization": "Bearer " + SYNTHETIC_KEY})
        health = await mcp_hub.validate_connection(cid)
        check("native HTTP initialize and tools list", health["ok"] and health["tools"] == len(TOOLS))
        check("Connect verification makes no tools call", not any(r["method"] == "tools/call" for r in REQUESTS))
        check("native session and synthetic auth transmitted", all(r["authMatchedSynthetic"] for r in REQUESTS) and any(r["hasSession"] for r in REQUESTS if r["method"] == "tools/list"))
        def baizhi_names(response):
            return [t["name"] for t in response["result"]["tools"] if t["name"].startswith("baizhi__")]
        async def listing(mode):
            mcp_hub.invalidate_cache()
            return await mcp_hub._handle_one({"id": 1, "method": "tools/list"}, mode, include_plugins=False, vault_root=str(BRAIN))
        check("default readonly account has no callable Baizhi tool", not baizhi_names(await listing("full")))
        mcp_store.update_connection(cid, {"perm": "safe"})
        check("Draft account has no callable Baizhi tool", not baizhi_names(await listing("full")))
        mcp_store.update_connection(cid, {"perm": "full"})
        check("Full account suggest mode still no calls", not baizhi_names(await listing("suggest")))
        check("Full account auto mode still no calls", not baizhi_names(await listing("auto")))
        names = baizhi_names(await listing("full"))
        check("Full plus Full lists exact six synthetic tools", set(names) == {"baizhi__" + t for t in TOOLS})
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://javis-test", trust_env=False) as client:
            async def rpc(method, params=None, mode="full", turn_key="", authenticated=True):
                headers = {"X-Javis-Mode": mode, "X-Javis-No-Plugins": "1",
                           "X-Javis-Vault": str(BRAIN), "Accept-Encoding": "gzip"}
                if authenticated:
                    headers["Authorization"] = "Bearer " + mcp_hub.hub_token()
                if turn_key:
                    headers[turn_context.HEADER] = turn_key
                return await client.post("/hub/mcp", headers=headers,
                                         json={"jsonrpc": "2.0", "id": 3, "method": method,
                                               "params": params or {}})

            def call_count():
                return sum(r["method"] == "tools/call" for r in REQUESTS)

            no_auth = await rpc("tools/list", authenticated=False)
            check("registered HTTP wrapper requires hub authentication", no_auth.status_code == 401)
            initialized = await rpc("initialize")
            check("registered HTTP wrapper initializes hub", initialized.json()["result"]["serverInfo"]["name"] == "javis-hub")
            listed = await rpc("tools/list", mode="gated")
            check("current gated Full account lists six synthetic tools", set(baizhi_names(listed.json())) == {"baizhi__" + t for t in TOOLS})
            check("current response middleware preserves tools list", listed.headers.get("content-encoding") == "gzip")
            params = {"name": "baizhi__websearch_search", "arguments": {}}
            before_gate = call_count()
            absent = await rpc("tools/call", params, mode="gated")
            check("gated missing turn key rejects without remote call", absent.json()["result"]["isError"] and call_count() == before_gate)
            forged = await rpc("tools/call", params, mode="gated", turn_key="synthetic-forged-turn")
            check("gated forged turn key rejects without remote call", forged.json()["result"]["isError"] and call_count() == before_gate)

            approvals = team_approval.ApprovalStore(STATE / "synthetic-team.sqlite3")
            gate = team_gate.open_gate(approvals, "synthetic-team", "synthetic-task", "synthetic-step")
            token = turn_context.bind({"team_gate": gate.gate_id})
            turn_key = turn_context.issue_key()
            try:
                pending = await rpc("tools/call", params, mode="gated", turn_key=turn_key)
                check("gated live unapproved call rejects without remote call", pending.json()["result"]["isError"] and call_count() == before_gate)
                fp = team_approval.fingerprint("hub", params["name"], params["arguments"])
                check("real gate records exact synthetic call", any(x["fp"] == fp for x in gate.blocked))
                approval = approvals.create(team_id="synthetic-team", task_id="synthetic-task",
                                            step_id="synthetic-step", team_rev=1,
                                            requested_by="synthetic-agent", kind="action",
                                            title="Synthetic Baizhi call", action="Loopback fixture only",
                                            fingerprints=[{"fp": fp}])
                approved = approvals.decide(approval["approval_id"], "approve", by="synthetic-owner",
                                            decision_key="synthetic-approval-click", current_rev=1)
                approvals.granted_step(approval["approval_id"], "synthetic-step")
                check("synthetic fixture grants real approval record", approved["ok"])
                changed = {"name": params["name"], "arguments": {"different": True}}
                mismatch = await rpc("tools/call", changed, mode="gated", turn_key=turn_key)
                check("gated changed arguments cannot reuse approval", mismatch.json()["result"]["isError"] and call_count() == before_gate)
                mcp_store.update_connection(cid, {"perm": "readonly"})
                denied = await rpc("tools/call", params, mode="gated", turn_key=turn_key)
                check("gated approval cannot override readonly connection", denied.json()["result"]["isError"] and call_count() == before_gate)
                mcp_store.update_connection(cid, {"perm": "full"})
                mcp_hub.invalidate_cache()
                allowed = await rpc("tools/call", params, mode="gated", turn_key=turn_key)
                check("gated exact approved call dispatches once via native HTTP", not allowed.json()["result"]["isError"] and call_count() == before_gate + 1)
                repeated = await rpc("tools/call", params, mode="gated", turn_key=turn_key)
                check("gated consumed approval rejects repeat", repeated.json()["result"]["isError"] and call_count() == before_gate + 1)
                check("real gate records one consumed approval", len(gate.passed) == 1)
            finally:
                team_gate.close_gate(gate.gate_id)
                turn_context.reset(token)
            stale = await rpc("tools/call", params, mode="gated", turn_key=turn_key)
            check("gated closed turn rejects stale key", stale.json()["result"]["isError"] and call_count() == before_gate + 1)
            ordinary = await rpc("tools/call", params, mode="full")
            check("ordinary Full dispatch has no Team approval gate", not ordinary.json()["result"]["isError"] and call_count() == before_gate + 2)
        for name in ("websearch_search", "synthetic_new_tool"):
            result = await mcp_hub._handle_one({"id": 2, "method": "tools/call", "params": {"name": "baizhi__" + name, "arguments": {}}}, "full", include_plugins=False, vault_root=str(BRAIN))
            check("native guarded dispatch " + name, "synthetic-result:" + name in str(result))
        tools, routes = await mcp_hub.discover_all("full", vault_root=str(BRAIN), include_plugins=False)
        raw = dict(routes["baizhi__websearch_search"])
        raw.pop("call")
        raw["spec"] = mcp_client._conn_spec(resolved)
        raw["conn"] = {**raw["conn"], "perm": "readonly"}
        calls_before = sum(r["method"] == "tools/call" for r in REQUESTS)
        rejected = await mcp_hub._guard(raw, "baizhi__websearch_search", "full")({})
        check("actual guard rejects readonly without network tools call", rejected.startswith("ERROR:") and calls_before == sum(r["method"] == "tools/call" for r in REQUESTS))
        packs_off = pack_install.dat_bat_tat("community.baizhi", False)
        check("actual pack disable removes catalog and resolution", packs_off["ok"] and mcp_catalog.get("baizhi") is None and not any(c["id"] == cid for c in mcp_store.resolved()))
        check("disable preserves saved account fields", bool(mcp_store.get_connection(cid)) and SYNTHETIC_KEY not in json.dumps(mcp_store.get_connection(cid)))
        check("actual pack reenable restores resolution", pack_install.dat_bat_tat("community.baizhi", True)["ok"] and any(c["id"] == cid for c in mcp_store.resolved()))
        missing, error = mcp_store.add_connection("baizhi", {"fields": {}, "url": url})
        check("direct API is not claimed to reject missing key", not error and bool(missing))
        missing_health = await mcp_hub.validate_connection(missing)
        check("native HTTP missing credential validation fails", not missing_health["ok"])
        mcp_store.delete_connection(missing)
        mcp_store.delete_connection(cid)
        check("synthetic accounts deleted", mcp_store.list_connections() == before)
    finally:
        await mcp_client.pool.close_all()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    print(json.dumps({"result": "PASS" if all(c["pass"] for c in CHECKS) else "FAIL", "checks": CHECKS,
                      "hostVersion": (HOST / "VERSION").read_text().strip(),
                      "packSha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                      "network": {"destination": "loopback fake MCP only", "totalHttpRequests": len(REQUESTS),
                                  "toolsCalls": sum(r["method"] == "tools/call" for r in REQUESTS),
                                  "realBaizhiModelRequests": 0}, "requests": REQUESTS}, ensure_ascii=False))


try:
    asyncio.run(run())
finally:
    socket.socket.connect = original_connect
    TEMP.cleanup()
