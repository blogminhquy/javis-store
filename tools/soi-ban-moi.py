"""Chạy thử BẢN MỚI NHẤT của từng MCP bên thứ 3 và soi tên tool thật nó trả về.

    JAVIS_OS_DIR=../javis-os python tools/soi-ban-moi.py

Cần mạng, `npx` và `uvx`. CI chạy mỗi ngày (xem `.github/workflows/kiem-tra.yml`).

Vì sao có script này
--------------------
Từ 2026-10-05 connector chạy MCP bên thứ 3 đi theo bản chính thức mới nhất (`@latest`, xem
`ban_chinh_thuc.py`). Cái giá: bên thứ 3 ra bản mới là mọi máy chạy bản đó ngay, kể cả khi bản
đó thêm tool mới mà khuôn chưa phân loại. Và `mcp_catalog.classify` của Javis xếp một tool
KHÔNG khai, tên không chứa từ gợi ý ghi nào, vào nhóm ĐỌC, tức chạy được ở mức Chỉ đọc. Vụ có
thật lúc viết script: `workspace-mcp` 2.x thêm `run_script_function` (chạy Apps Script) và Javis
xếp nó vào nhóm đọc.

`kiem-tra.py` chỉ soi được tên tool mà khuôn KHAI. Script này soi tên tool mà bản mới nhất THẬT
SỰ có, phân loại bằng đúng hàm `classify` của Javis, rồi áp cùng bộ luật tên (`luat_ten_tool.py`):

- ĐỎ: tool nghe như đụng tiền, hoặc phá huỷ / tác động ra ngoài, mà Javis không xếp vào nhóm
  nguy hiểm (trừ khi khuôn liệt kê nó trong `ghi_da_can_nhac`). Sửa bằng cách khai nó trong
  `tool_meta` của khuôn rồi ra bản gói mới.
- Cảnh báo: MCP không khởi động được khi thiếu tài khoản đăng nhập (vài server đòi credential
  ngay lúc mở), và tool chưa khai nằm ở nhóm đọc nhờ đoán theo tên, để người review liếc qua.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ban_chinh_thuc import LENH  # noqa: E402
from luat_ten_tool import PHA, TIEN, _khop  # noqa: E402

GOC = Path(__file__).resolve().parent.parent
TRAN_GIAY = 180   # lần đầu npx/uvx phải tải gói, nên rộng tay


def _nap_classify():
    os_dir = Path(os.environ.get("JAVIS_OS_DIR") or GOC.parent / "javis-os").resolve()
    server = os_dir / "server"
    if not (server / "mcp_catalog.py").is_file():
        sys.exit(f"không thấy Javis OS ở {os_dir}: đặt JAVIS_OS_DIR trỏ tới bản checkout javis-os")
    os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-soi-"))
    sys.path.insert(0, str(server))
    import mcp_catalog
    return mcp_catalog.classify


# Service account GIẢ, đúng hình dạng nhưng khoá rỗng: đủ để vài server chịu khởi động và liệt
# kê tool, không bao giờ đăng nhập được vào đâu.
_SA_GIA = ('{"type": "service_account", "project_id": "javis-soi", "private_key_id": "x", '
           '"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIB\\n-----END PRIVATE KEY-----\\n", '
           '"client_email": "soi@javis-soi.iam.gserviceaccount.com", "client_id": "1", '
           '"token_uri": "https://oauth2.googleapis.com/token"}')


def _env_gia(con, thu_muc):
    """Biến môi trường GIẢ cho mọi ô đăng nhập của khuôn. Vài server đòi credential ngay lúc mở
    (Search Console, Lark), dù chỉ để liệt kê tool. Giá trị giả nên không gọi được API nào."""
    env = dict(os.environ)
    for f in ((con.get("auth") or {}).get("fields") or []):
        ten = f.get("env")
        if not ten:
            continue
        if f.get("file"):
            tep = Path(thu_muc) / f"{ten}.json"
            tep.write_text(_SA_GIA, encoding="utf-8")
            env[ten] = str(tep)
        else:
            env[ten] = "javis-soi-gia"
    return env


def liet_ke_tool(con):
    """(danh sách tên tool, lỗi). Chạy lệnh của khuôn với credential giả, xem `_env_gia`."""
    cmd = shutil.which(con["command"]) or con["command"]
    p = subprocess.Popen([cmd] + [str(a) for a in con.get("args") or []], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env=_env_gia(con, tempfile.mkdtemp(prefix="javis-soi-env-")))
    loi = []
    threading.Thread(target=lambda: loi.append(p.stderr.read()), daemon=True).start()
    hen = threading.Timer(TRAN_GIAY, p.kill)
    hen.start()
    try:
        def gui(o):
            p.stdin.write(json.dumps(o) + "\n")
            p.stdin.flush()

        def nhan(mid):
            while True:
                dong = p.stdout.readline()
                if not dong:
                    p.wait()
                    duoi = (loi[0] if loi else "").strip().splitlines()[-1:] or [""]
                    return None, f"thoát mã {p.returncode}: {duoi[0][:300]}"
                try:
                    m = json.loads(dong)
                except ValueError:
                    continue
                if m.get("id") == mid:
                    return m, ""

        gui({"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "javis-store-soi", "version": "1"}}})
        m, e = nhan(1)
        if not m:
            return None, e
        gui({"jsonrpc": "2.0", "method": "notifications/initialized"})
        ten, con_tro = [], None
        for lan in range(50):
            gui({"jsonrpc": "2.0", "id": 2 + lan, "method": "tools/list",
                 "params": {"cursor": con_tro} if con_tro else {}})
            m, e = nhan(2 + lan)
            if not m:
                return None, e
            kq = m.get("result") or {}
            ten += [t["name"] for t in kq.get("tools") or []]
            con_tro = kq.get("nextCursor")
            if not con_tro:
                break
        return sorted(set(ten)), ""
    except (BrokenPipeError, OSError) as e:
        return None, str(e)
    finally:
        hen.cancel()
        p.kill()


def main():
    classify = _nap_classify()
    do, canh = [], []
    so = 0
    for f in sorted((GOC / "packs").glob("*/connectors/*.json")):
        con = json.loads(f.read_text(encoding="utf-8"))
        if str(con.get("command") or "").lower() not in LENH:
            continue
        so += 1
        nhan_goi = f"{f.parent.parent.name}/{con.get('id')}"
        ten, e = liet_ke_tool(con)
        if ten is None:
            canh.append(f"{nhan_goi}: không mở được để soi ({e})")
            print(f"?? {nhan_goi}: {e}")
            continue
        meta = con.get("tool_meta") or {}
        da_can_nhac = {str(x) for x in (con.get("ghi_da_can_nhac") or [])}
        khai = [str(p).lower() for k in ("read", "write", "danger") for p in (meta.get(k) or [])]
        chua_khai_doc = []
        for t in ten:
            # args={} chứ không phải None: phân loại như LÚC GỌI THẬT. Với args=None, tool cổng
            # (`call_rules`) và tool đa hành động (`arg_rules`) tạm coi là đọc để còn liệt kê
            # được; lúc gọi, args thiếu tên lệnh con thì chúng rơi về mức fail-closed.
            nhom = classify(con, t, {})
            if nhom == "danger":
                continue
            if _khop(t, TIEN):
                do.append(f"{nhan_goi}: tool '{t}' đụng tới TIỀN mà Javis xếp vào nhóm {nhom}")
            elif _khop(t, PHA) and t not in da_can_nhac:
                do.append(f"{nhan_goi}: tool '{t}' nghe như phá huỷ hay tác động ra ngoài mà Javis "
                          f"xếp vào nhóm {nhom}. Khai nó là danger trong tool_meta, hoặc thêm vào "
                          f"`ghi_da_can_nhac` nếu đã cân nhắc và thấy nó nhẹ")
            elif nhom == "read" and not any(fnmatch(t.lower(), p) for p in khai):
                chua_khai_doc.append(t)
        if chua_khai_doc:
            canh.append(f"{nhan_goi}: {len(chua_khai_doc)} tool chưa khai, đang được ĐOÁN là đọc: "
                        + ", ".join(chua_khai_doc))
        print(f"ok {nhan_goi}: {len(ten)} tool")

    print()
    for c in canh:
        print("canh bao: " + c)
    if do:
        print(f"ĐỎ {len(do)} lỗi:")
        for l in do:
            print("  - " + l)
        sys.exit(1)
    print(f"XANH - soi {so} MCP bên thứ 3 ở bản mới nhất, không tool nguy hiểm nào lọt nhóm")


if __name__ == "__main__":
    main()
