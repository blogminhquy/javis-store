"""A fake Muse Gateway on 127.0.0.1 for the javis.muse tests: the admin routes the plugin uses, video jobs that
succeed on the second poll (or fail once when the prompt says "flaky"), and the job content path.

Each job's `output.url` points at a SECOND local server (`Ngoai`), the way the real gateway hands out an absolute
signed link built from whatever Host it saw. The plugin must never fetch it: it carries the admin key, and only the
gateway's own `/v1/jobs/<id>/content` may see that key. `Ngoai.hits` counts every request that server gets.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "test-admin-key"


class Fake:
    accounts: list = []
    jobs: dict = {}
    flaky_seen: set = set()
    n = 0
    cred_patches = 0
    ngoai = ""

    @classmethod
    def reset(cls):
        cls.accounts, cls.jobs, cls.flaky_seen, cls.n, cls.cred_patches = [], {}, set(), 0, 0


class Ngoai:
    hits: list = []


class _NgoaiHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        Ngoai.hits.append({"path": self.path, "auth": self.headers.get("Authorization")})
        self.send_response(200)
        self.send_header("Content-Length", "4")
        self.end_headers()
        self.wfile.write(b"LEAK")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        if self.headers.get("Authorization") != "Bearer " + KEY:
            self._send(401, {"error": {"message": "API key không hợp lệ"}})
            return False
        return True

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _acct(self, aid):
        return next((a for a in Fake.accounts if a["id"] == aid), None)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if not self._auth():
            return
        if self.path == "/api/admin/accounts":
            return self._send(200, {"data": [{k: v for k, v in a.items() if k != "credential"} for a in Fake.accounts]})
        if self.path.startswith("/v1/jobs/") and self.path.endswith("/content"):
            j = Fake.jobs.get(self.path.split("/")[3])
            if not j or j["polls"] < 2 or j["fail"]:
                return self._send(409, {"error": {"message": "job chưa xong"}})
            return self._send(200, raw=b"MP4!" * 3000, ctype="video/mp4")
        if self.path.startswith("/v1/jobs/"):
            j = Fake.jobs.get(self.path.rsplit("/", 1)[1])
            if not j:
                return self._send(404, {"error": {"message": "no job"}})
            j["polls"] += 1
            if j["polls"] < 2:
                return self._send(200, {"id": j["id"], "status": "running", "progress_text": "Đang tạo..."})
            if j["fail"]:
                return self._send(200, {"id": j["id"], "status": "failed", "error": {"message": "Muse từ chối"}})
            return self._send(200, {"id": j["id"], "status": "succeeded", "output": {
                "url": f"{Fake.ngoai}/files/{j['id']}.mp4?sig=x", "mime_type": "video/mp4", "bytes": 12000}})
        self._send(404, {"error": {"message": "nope"}})

    def do_POST(self):
        if not self._auth():
            return
        body = self._json()
        if self.path == "/api/admin/accounts":
            Fake.n += 1
            a = {"id": f"acc{Fake.n}", "name": body["name"], "enabled": True, "status": "online",
                 "created_at": f"2026-10-31T00:00:{Fake.n:02d}Z", "credential": body["credential"],
                 "credential_masked": "hatch_sess=..." + str(Fake.n), "limits": {}, "live": {"connected": True},
                 "requests": 0, "failures": 0}
            Fake.accounts.append(a)
            ok = "BAD" not in body["credential"]
            return self._send(200, {"account": {"id": a["id"]}, "test": {"ok": ok, "error": "" if ok else "hết phiên"}})
        if self.path.endswith("/test"):
            a = self._acct(self.path.split("/")[-2])
            ok = a is not None and "BAD" not in a["credential"]
            return self._send(200, {"ok": ok, "error": "" if ok else "hết phiên", "latency_ms": 5})
        if self.path == "/v1/videos/generations":
            if not any(a["enabled"] for a in Fake.accounts):
                return self._send(503, {"error": {"message": "no account"}})
            Fake.n += 1
            jid = f"job{Fake.n}"
            fail = "flaky" in body["prompt"] and body["prompt"] not in Fake.flaky_seen
            if fail:
                Fake.flaky_seen.add(body["prompt"])
            Fake.jobs[jid] = {"id": jid, "polls": 0, "fail": fail, "body": body}
            return self._send(202, {"id": jid, "status": "queued"})
        self._send(404, {"error": {"message": "nope"}})

    def do_PATCH(self):
        if not self._auth():
            return
        a = self._acct(self.path.rsplit("/", 1)[1])
        if not a:
            return self._send(404, {"error": {"message": "no account"}})
        body = self._json()
        if "enabled" in body:
            a["enabled"] = bool(body["enabled"])
        if "limits" in body:
            a["limits"] = body["limits"]
        if "credential" in body:
            a["credential"] = body["credential"]
            Fake.cred_patches += 1
        self._send(200, {"ok": True})

    def do_DELETE(self):
        if not self._auth():
            return
        a = self._acct(self.path.rsplit("/", 1)[1])
        if not a:
            return self._send(404, {"error": {"message": "no account"}})
        Fake.accounts.remove(a)
        self._send(200, {"ok": True})


def start():
    """Start the fake gateway and the outside server. Returns (gateway_url, [servers to shut down])."""
    ngoai = ThreadingHTTPServer(("127.0.0.1", 0), _NgoaiHandler)
    threading.Thread(target=ngoai.serve_forever, daemon=True).start()
    Fake.ngoai = f"http://127.0.0.1:{ngoai.server_address[1]}"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", [srv, ngoai]
