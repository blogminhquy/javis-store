"""Plugin zalo-friend: công cụ kết bạn Zalo (tìm người, trạng thái, gửi, nhận, từ chối lời mời).

    JAVIS_OS_DIR=../javis-os python tests/javis.zalo/test_zalo_friend.py   (KHÔNG mạng: thay chỗ chạy CLI bằng bản ghi lại lệnh)

Chủ dự án (2026-10-11): Javis cần CÓ KHẢ NĂNG kết bạn, còn kết bạn với ai do model quyết định hoặc người dùng yêu cầu, không dựng
tính năng cứng. Nên test canh đúng ba thứ:
  1. Mỗi tool gọi ĐÚNG lệnh CLI với ĐÚNG tham số, và id sai khuôn bị chặn trước khi chạm Zalo.
  2. Thành công/thất bại đọc đúng: CLI thoát mã 0 cả khi Zalo từ chối ("✗"), và có thể in "undefined" thay JSON khi đã làm xong ("✓").
  3. Phân quyền theo mức chung: tool đọc là readonly, tool hành động là full. Không có luồng tự động nào.
"""
from _paths import PACK, ROOT, SERVER  # noqa: E402,F401
import asyncio
import importlib.util
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import zalo_cli  # noqa: E402

fails = []


def check(name, cond, them=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(them) + "]") if them and not cond else ""))
    if not cond:
        fails.append(name)


spec = importlib.util.spec_from_file_location("zalo_friend_plugin", PACK / "plugins" / "zalo-friend" / "plugin.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

MOT = [{"id": "c1", "label": "Zalo chính", "home": "/state/zalo-a"}]
RAN = []
REPLY = {"rc": 0, "out": "{}", "err": ""}


def fake_world(rc=0, out="{}", err=""):
    zalo_cli.AUTO_INSTALL = False
    zalo_cli.connections = lambda: list(MOT)
    zalo_cli.check = lambda: None
    zalo_cli.shutil = type("S", (), {"which": staticmethod(lambda n: "/usr/bin/npx" if n == "npx" else None)})
    zalo_cli.os = type("O", (), {"name": "posix", "environ": {}, "path": __import__("os").path})
    REPLY.update(rc=rc, out=out, err=err)

    async def run(argv, home, timeout=120):
        RAN.append({"argv": argv, "home": home})
        return REPLY["rc"], REPLY["out"], REPLY["err"]
    zalo_cli.run = run


def call(fn, args):
    RAN.clear()
    return asyncio.run(fn(args, None))


def cli_args():
    a = RAN[-1]["argv"]
    return a[a.index("--json") + 1:]


# ============================================================
# 1. Tìm người
# ============================================================
fake_world(out=json.dumps({"uid": "8812345", "display_name": "Lan Anh", "avatar": "https://a/b.jpg"}))
r = json.loads(call(P._find_user, {"query": "0912 345 678"}))
check("tìm theo số điện thoại: gọi friend find với số đã bỏ dấu cách", cli_args() == ["friend", "find", "0912345678"], cli_args())
check("tìm ra user_id và tên", r["ok"] and r["user_id"] == "8812345" and r["name"] == "Lan Anh", r)
fake_world(out=json.dumps({"data": {"uid": "77", "zaloName": "minhquy"}}))
call(P._find_user, {"query": "@minhquy"})
check("tìm theo username: gọi friend find-username, bỏ @", cli_args() == ["friend", "find-username", "minhquy"], cli_args())
fake_world()
r = call(P._find_user, {"query": "Lan Anh ở nhóm spa"})
check("tìm theo tên tự do: không gọi CLI, chỉ sang zalo_group_members", r.startswith("ERROR") and "zalo_group_members" in r and not RAN, r)
fake_world(out="{}")
r = call(P._find_user, {"query": "0900000000"})
check("không có ai khớp: báo rõ, không bịa id", r.startswith("ERROR") and "không có người dùng" in r, r)

# ============================================================
# 2. Trạng thái
# ============================================================
fake_world(out=json.dumps({"is_friend": 0, "is_requested": 0, "is_requesting": 1}))
r = json.loads(call(P._status, {"user_id": "8812345"}))
check("trạng thái: gọi friend request-status", cli_args() == ["friend", "request-status", "8812345"], cli_args())
check("trạng thái: dịch đúng hai chiều lời mời (họ đang mời mình)", r["they_sent_request"] is True and r["i_sent_request"] is False
      and r["is_friend"] is False, r)

# ============================================================
# 3. Hành động
# ============================================================
fake_world(out='""')
r = json.loads(call(P._add, {"user_id": "8812345", "message": "Chào chị, em bên spa"}))
check("gửi lời mời: friend add kèm lời nhắn", cli_args() == ["friend", "add", "8812345", "-m", "Chào chị, em bên spa"], cli_args())
check("gửi lời mời: báo thành công", r["ok"] is True)
call(P._add, {"user_id": "8812345"})
check("không có lời nhắn thì không truyền -m rỗng", cli_args() == ["friend", "add", "8812345"], cli_args())
fake_world(out="undefined", err="  ✓ Accepted friend request from 8812345")
r = call(P._accept, {"user_id": "8812345"})
check("nhận lời mời: friend accept", cli_args() == ["friend", "accept", "8812345"], cli_args())
check("CLI in 'undefined' thay JSON nhưng có dấu ✓: vẫn là thành công", json.loads(r)["ok"] is True, r)
fake_world(out="", err="  ✗ User 8812345 may have blocked you or is unreachable.")
r = call(P._add, {"user_id": "8812345"})
check("Zalo từ chối (✗, mã thoát 0): báo lỗi kèm lý do", r.startswith("ERROR") and "blocked" in r, r)
fake_world(out='""')
call(P._reject, {"user_id": "8812345"})
check("từ chối lời mời: friend reject", cli_args() == ["friend", "reject", "8812345"], cli_args())

for bad in ("", "abc", "../x", "12"):
    fake_world()
    r = call(P._add, {"user_id": bad})
    check(f"user_id sai khuôn {bad!r}: chặn trước khi chạm Zalo", r.startswith("ERROR") and not RAN, r)
fake_world()
r = call(P._add, {"user_id": "8812345", "message": "x" * 200})
check("lời nhắn quá dài: chặn", r.startswith("ERROR") and not RAN, r)

# ============================================================
# 4. Đăng ký và phân quyền
# ============================================================
import yaml  # noqa: E402

y = yaml.safe_load((PACK / "plugins" / "zalo-friend" / "plugin.yaml").read_text(encoding="utf-8"))


class Ctx:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kw):
        self.tools.append(kw)


tc = Ctx()
P.register(tc)
by = {t["name"]: t for t in tc.tools}
check("năm tool, khớp plugin.yaml", sorted(by) == sorted(y["tools"]) and len(by) == 5, sorted(by))
check("tool đọc chạy ở mọi mức", all(by[n]["min_mode"] == "readonly" for n in ("zalo_find_user", "zalo_friend_status")))
check("tool hành động cần mức Toàn quyền, như gửi tin Zalo",
      all(by[n]["min_mode"] == "full" for n in ("zalo_add_friend", "zalo_accept_friend", "zalo_reject_friend")))
check("mọi tool có check_fn và handler", all(callable(t["check_fn"]) and callable(t["handler"]) for t in tc.tools))
check("plugin không có hook tự chạy (không luồng tự động nào)", y.get("hooks") == [] and not hasattr(P, "on_event"))
check("không dùng em dash", chr(0x2014) not in open(__file__, encoding="utf-8").read()
      and chr(0x2014) not in (PACK / "plugins" / "zalo-friend" / "plugin.py").read_text(encoding="utf-8")
      and chr(0x2014) not in (PACK / "plugins" / "zalo-friend" / "plugin.yaml").read_text(encoding="utf-8"))

if fails:
    print("\nFAIL - test_zalo_friend: " + str(len(fails)) + " lỗi: " + ", ".join(fails))
    sys.exit(1)
print("\nOK - test_zalo_friend: tất cả pass")
