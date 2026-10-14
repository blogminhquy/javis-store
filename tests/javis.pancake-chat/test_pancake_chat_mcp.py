"""pancake-chat-mcp (written by the store, mcp-nguon/pancake-chat-mcp) against a FAKE Pancake API on 127.0.0.1.

    python tests/javis.pancake-chat/test_pancake_chat_mcp.py

Runs the PACKED file (mcp/pancake-chat-mcp-<ver>.tgz) through `npx --package`, exactly as the connector does, so a
packaging mistake fails here too. Needs node/npx, no network: the package has no dependencies.

Pinned:
  1. tools/list: the expected tools, each with annotations.
  2. Each tool hits the right Pancake path and API version, with the page token in the query and the right body.
  3. Dates: "YYYY-MM-DD" is Vietnam time; staff/engagement reports get Pancake's "dd/mm/yyyy HH:MM:SS - ..." range.
  4. A Pancake error comes back as isError, and the token is cut out of the message.
  5. Missing credentials: a clear error, no request sent.
"""
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

GOC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(GOC / "tools"))
import mcp_dong_goi  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  [{str(detail)[:400]}]"))
    if not cond:
        _fails.append(name)


TOKEN = "tok-SECRET-123"
SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _do(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null") if n else None
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        SEEN.append({"method": method, "path": u.path, "q": q, "body": body})
        if u.path.endswith("/tags") and method == "GET" and q.get("page_access_token") != TOKEN:
            return self._reply(401, {"success": False, "message": "bad token " + q.get("page_access_token", "")})
        if "boom" in u.path:
            return self._reply(400, {"success": False, "message": f"invalid page_access_token={TOKEN}"})
        self._reply(200, {"success": True, "echo": u.path})

    def do_GET(self):
        self._do("GET")

    def do_POST(self):
        self._do("POST")


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"

m = next(x for x in mcp_dong_goi.doc_nguon() if x["ten"] == "pancake-chat-mcp")
TGZ = mcp_dong_goi.THU_MUC / mcp_dong_goi.ten_tep(m)
NPX = shutil.which("npx")
if not NPX:
    print("bỏ qua: máy không có npx")
    sys.exit(0)


def session(env_extra, calls):
    """Start the packed server, run `calls` [(name, args)], return (tools, [results])."""
    import os
    env = dict(os.environ, PANCAKE_API_BASE=BASE, **env_extra)
    p = subprocess.Popen([NPX, "-y", f"--package={TGZ}", "pancake-chat-mcp"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    for i, (name, args) in enumerate(calls):
        msgs.append({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": {"name": name, "arguments": args}})
    out, err = p.communicate("\n".join(json.dumps(x) for x in msgs) + "\n", timeout=180)
    res = {}
    for line in out.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        res[o.get("id")] = o
    tools = [t["name"] for t in ((res.get(2) or {}).get("result") or {}).get("tools") or []]
    return tools, [(res.get(10 + i) or {}).get("result") or {} for i in range(len(calls))], err, res


ENV = {"PANCAKE_PAGE_ID": "pg1", "PANCAKE_PAGE_ACCESS_TOKEN": TOKEN}
CALLS = [
    ("pancake_conversations_list", {"type": "INBOX", "since": "2026-10-01"}),
    ("pancake_messages_get", {"conversation_id": "pg1_99", "current_count": 30}),
    ("pancake_customers_list", {"since": "2026-10-01", "until": "2026-10-07"}),
    ("pancake_tags_list", {}),
    ("pancake_stats_staff", {"since": "2026-10-01", "until": "2026-10-07"}),
    ("pancake_conversation_tag", {"conversation_id": "pg1_99", "tag_id": "5", "action": "add"}),
    ("pancake_conversation_mark_read", {"conversation_id": "pg1_99", "read": False}),
    ("pancake_customer_add_note", {"page_customer_id": "c7", "message": "khách VIP"}),
    ("pancake_send_message", {"conversation_id": "pg1_99", "message": "Dạ em chào anh"}),
    ("pancake_send_message", {"conversation_id": "pg1_99", "message": "cảm ơn", "reply_to_comment_id": "cm1"}),
    ("pancake_messages_get", {"conversation_id": "boom"}),
]
tools, kq, err, raw = session(ENV, CALLS)

# ---- 1. tools/list ----
MONG = {"pancake_conversations_list", "pancake_messages_get", "pancake_customers_list", "pancake_tags_list",
        "pancake_posts_list", "pancake_staff_list", "pancake_stats_page", "pancake_stats_tags", "pancake_stats_staff",
        "pancake_stats_engagement", "pancake_conversation_tag", "pancake_conversation_mark_read",
        "pancake_conversation_assign", "pancake_customer_add_note", "pancake_send_message"}
check("đủ 15 tool đúng tên", set(tools) == MONG, (sorted(set(tools) ^ MONG), err[-300:]))
tl = {t["name"]: t for t in raw[2]["result"]["tools"]}
check("tool gửi tin đánh dấu destructiveHint", tl["pancake_send_message"]["annotations"]["destructiveHint"] is True)
check("tool đọc đánh dấu readOnlyHint", all(tl[n]["annotations"]["readOnlyHint"] for n in MONG if "_list" in n or "stats" in n))

# ---- 2. paths, versions, token, bodies ----
by = {}
for s in SEEN:
    by.setdefault(s["path"], []).append(s)
P = "/api/public_api/{}/pages/pg1{}"
from datetime import datetime, timedelta, timezone  # noqa: E402
NGAY1 = int(datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=7))).timestamp())   # 2026-10-01 00:00 giờ VN
check("mọi request mang page_access_token", all(s["q"].get("page_access_token") == TOKEN for s in SEEN), SEEN[:2])
c = by.get(P.format("v2", "/conversations"), [{}])[0]
check("hội thoại: API v2, lọc INBOX, since theo giờ VN", c.get("q", {}).get("type") == "INBOX"
      and c["q"].get("since") == str(NGAY1), c)
check("tin nhắn: API v1 + current_count", P.format("v1", "/conversations/pg1_99/messages") in by
      and by[P.format("v1", "/conversations/pg1_99/messages")][0]["q"].get("current_count") == "30")
cu = by.get(P.format("v1", "/page_customers"), [{}])[0].get("q", {})
check("khách hàng: since/until cả ngày, trang 1, 50 dòng",
      cu.get("since") == str(NGAY1) and cu.get("until") == str(NGAY1 + 7 * 86400 - 1)
      and cu.get("page_number") == "1" and cu.get("page_size") == "50", cu)
st = by.get(P.format("v1", "/statistics/users"), [{}])[0].get("q", {})
check("thống kê nhân viên: date_range đúng định dạng Pancake",
      st.get("date_range") == "01/10/2026 00:00:00 - 07/10/2026 23:59:59", st)
tg = by.get(P.format("v1", "/conversations/pg1_99/tags"), [{}])[0]
check("gắn thẻ: POST body action + tag_id", tg.get("method") == "POST" and tg.get("body") == {"action": "add", "tag_id": "5"}, tg)
check("đánh dấu chưa đọc đi /unread", P.format("v1", "/conversations/pg1_99/unread") in by)
nt = by.get(P.format("v1", "/page_customers/c7/notes"), [{}])[0]
check("ghi chú khách: body message", nt.get("body") == {"message": "khách VIP"}, nt)
# The calls run concurrently, so the two sends may arrive in either order.
gui = sorted((s for s in by.get(P.format("v1", "/conversations/pg1_99/messages"), []) if s["method"] == "POST"),
             key=lambda s: s["body"]["action"] != "reply_inbox")
check("gửi inbox: reply_inbox", len(gui) == 2 and gui[0]["body"] == {"action": "reply_inbox", "message": "Dạ em chào anh"}, gui)
check("trả lời bình luận: reply_comment + message_id",
      len(gui) == 2 and gui[1]["body"] == {"action": "reply_comment", "message_id": "cm1", "message": "cảm ơn"}, gui)
check("các lượt gọi thành công không isError", all(not r.get("isError") for r in kq[:-1]), kq[:-1])

# ---- 4. error redaction ----
e = kq[-1]
txt = (e.get("content") or [{}])[0].get("text", "")
check("lỗi Pancake trả isError", e.get("isError") is True, e)
check("token bị che trong lỗi", TOKEN not in txt and "***" in txt, txt)

# ---- 5. missing credentials ----
SEEN.clear()
_t, kq2, _e, _r = session({"PANCAKE_PAGE_ID": "", "PANCAKE_PAGE_ACCESS_TOKEN": ""}, [("pancake_tags_list", {})])
check("thiếu token: báo lỗi rõ, không gửi request", kq2[0].get("isError") and "PANCAKE_PAGE_ACCESS_TOKEN"
      in kq2[0]["content"][0]["text"] and SEEN == [], (kq2, SEEN))

srv.shutdown()
print()
if _fails:
    print(f"FAIL {len(_fails)}: {_fails}")
    sys.exit(1)
print("OK")
