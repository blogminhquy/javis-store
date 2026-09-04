"""Kiểm mọi gói trong kho trước khi trộn. Chạy tay hoặc để CI chạy trên mỗi Pull Request:

    python tools/kiem-tra.py

Không chạm mạng, không cần thư viện ngoài.

Vì sao repo này cần một trình kiểm
----------------------------------
Kho là DỮ LIỆU MÀ NGƯỜI DÙNG SẼ CÀI, và nó tới máy họ mà không qua một bản Javis mới. Nghĩa là
không có lần review nào của app đứng chắn phía sau: sửa một dòng trong `packs/` là mọi máy đang
chạy Javis thấy ngay ở lần làm mới danh mục kế tiếp.

Điều đó đặc biệt đúng với PHÂN LOẠI QUYỀN. Javis xếp một tool vào ba nhóm đọc / ghi / nguy hiểm,
và mức "Ghi nháp" tự chạy được mọi thứ trong nhóm ghi. Nhưng `mcp_catalog.classify` KHÔNG có
nhánh nào tự đoán ra "nguy hiểm": tool nào không được khuôn khai rõ thì cùng lắm rơi vào nhóm
ghi. Nên một tool xoá dữ liệu hay tiêu tiền mà khuôn quên khai `danger` sẽ tự chạy được ở mức
Ghi nháp, im lặng, cho tới lúc nó xoá thật.

Trước 0.55.36 những phép kiểm này nằm trong repo Javis OS (`test_hostinger_connector.py`,
`test_n8n_connector.py`, `test_shopify_mcp.py`, `test_webcake_env.py`). Khuôn connector dọn sang
đây thì phép kiểm dọn theo - giữ lại bên đó chỉ canh được bản chụp lúc dọn nhà, không canh được
thứ người dùng thật sự cài.

Kiểm những gì
-------------
1. Cấu trúc gói: manifest đọc được, id khớp tên thư mục, tệp khai trong `provides` có thật.
2. Khuôn connector: JSON hợp lệ, đủ trường bắt buộc, icon hợp lệ (xem `tu-catalog.py`).
3. AN TOÀN: mặc định chỉ đọc, và không tool nào có tên kiểu phá huỷ / tiêu tiền mà lại nằm ở
   nhóm đọc hay ghi.
4. Bí mật: không nhét credential vào `url` (Javis không che `url`, nó ra thẳng giao diện và log).
5. Danh mục: mọi mục trong `index.json` có gói thật, có tệp zip thật, sha256 và size khớp, và
   nội dung zip khớp thư mục nguồn.
6. Không có ký tự em dash (luật của chủ kho, vì nó làm trình đọc màn hình vấp).
"""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent
loi = []
canh = []


def sai(ten, chi_tiet=""):
    loi.append(ten + (("  [" + str(chi_tiet) + "]") if chi_tiet else ""))


def check(ten, dieu_kien, chi_tiet=""):
    if not dieu_kien:
        sai(ten, chi_tiet)
    return bool(dieu_kien)


# ============================================================
# Tên tool nghe là biết KHÔNG hoàn tác được, hoặc tiêu tiền thật.
#
# Danh sách này cố ý RỘNG và cố ý gây phiền: một cái tên bị bắt oan thì tác giả gói khai lại
# một dòng là xong, còn một cái lọt lưới thì người dùng mất dữ liệu hoặc mất tiền. Đổi cân
# bằng đó theo hướng ngược lại là đổi sai chiều.
# ============================================================
# TIỀN, hoặc mất mát không dựng lại được bằng thao tác thường. Không có ngoại lệ: một tool tên
# kiểu này mà nằm ở nhóm ghi nghĩa là mức "Ghi nháp" tiêu được tiền của người dùng.
TIEN = [r"purchase", r"buy", r"pay", r"payment", r"checkout", r"charge", r"refund",
        r"invoice", r"billing", r"subscribe", r"renew", r"transfer"]

# PHÁ HUỶ hoặc gây tác động ra ngoài. Khác nhóm trên ở chỗ mức độ THẬT SỰ phụ thuộc dịch vụ:
# xoá một dòng trong ghi chú Google Keep rơi vào thùng rác, còn xoá một bản ghi DNS thì hạ cả
# website. Máy không phân biệt được, người viết gói thì có.
#
# Nên luật ở đây là: mặc định CHẶN, và tác giả gói gỡ chặn bằng cách liệt kê tên tool vào
# `ghi_da_can_nhac` của khuôn. Không phải để cho dễ - mà để quyết định đó có tên, nằm trong
# dữ liệu, và người review đọc được. Một cảnh báo in ra rồi trôi đi thì không ai đọc.
PHA = [r"delete", r"remove", r"destroy", r"drop", r"purge", r"wipe", r"erase",
       r"order", r"execute", r"run", r"trigger", r"deploy", r"restart", r"reboot",
       r"reset", r"send", r"publish", r"post", r"broadcast", r"cancel", r"restore",
       r"revoke", r"migrate", r"rotate"]

# Tên CHỨA một từ trên nhưng thật ra chỉ đọc. Liệt kê từng cái, không nới thành mẫu chung -
# nới một lần là thủng cả hàng rào.
THA = {
    "get_order", "list_orders", "search_orders", "get_invoice", "list_invoices",
    "get_payment", "list_payments", "get_execution", "search_executions", "list_executions",
    "get_deployment", "list_deployments", "get_post", "list_posts", "search_posts",
    "get_subscription", "list_subscriptions", "get_transfer", "list_transfers",
}


def _khop(ten, mau):
    t = str(ten or "").lower()
    if t in THA or "*" in t or "?" in t:
        return False
    return any(re.search(p, t) for p in mau)


def doc_manifest(thu_muc: Path):
    """Đọc `javis-pack.yaml` mà không cần PyYAML.

    Manifest trong kho có hai kiểu viết: kiểu MỘT DÒNG do `tu-catalog.py` sinh ra
    (`name: {vi: "..."}`), và kiểu KHỐI người ta viết tay (`name:` rồi thụt vào `vi: ...`).
    Hàm này nhận cả hai, và chỉ hai - gặp cấu trúc lạ hơn thì nó báo thiếu trường chứ không âm
    thầm hiểu sai. Tự đọc để CI không phải cài thư viện, và cái giá của lựa chọn đó nằm gọn
    trong đúng hàm này.
    """
    tep = thu_muc / "javis-pack.yaml"
    if not tep.exists():
        return None
    d, khoa, khoi = {}, None, []

    def dong_khoi():
        """Gộp các dòng thụt vào của một khoá thành list hoặc map.

        Ba hình dạng gặp thật trong kho: list phẳng (`- a`), map phẳng (`vi: ...`), và map của
        list (`plugins:` rồi `- plugins/tinh-gia`). Không đi sâu hơn hai tầng - gói nào cần thế
        thì đã vượt khỏi đặc tả spec 1.
        """
        if khoa is None or not khoi:
            return
        if all(x.lstrip().startswith("- ") or not x.strip() for x in khoi):
            d[khoa] = [x.lstrip()[2:].strip().strip('"').strip("'") for x in khoi if x.strip()]
            return
        con, con_hien = {}, None
        for x in khoi:
            if x.lstrip().startswith("- "):
                if con_hien is not None:
                    con.setdefault(con_hien, [])
                    if isinstance(con[con_hien], list):
                        con[con_hien].append(x.lstrip()[2:].strip().strip('"').strip("'"))
                continue
            m = re.match(r"\s+([a-zA-Z_]+):\s*(.*)$", x)
            if not m:
                continue
            con_hien, gt = m.group(1), m.group(2).strip()
            if gt.startswith("[") and gt.endswith("]"):
                con[con_hien] = [y.strip().strip('"').strip("'")
                                 for y in gt[1:-1].split(",") if y.strip()]
            elif gt:
                con[con_hien] = gt.strip('"').strip("'")
            else:
                con[con_hien] = []
        if con:
            d[khoa] = con

    for dong in tep.read_text(encoding="utf-8").splitlines():
        if not dong.strip() or dong.lstrip().startswith("#"):
            continue
        if dong[0] in " 	":
            khoi.append(dong)
            continue
        dong_khoi()
        khoi = []
        m = re.match(r"([a-zA-Z_]+):\s*(.*)$", dong)
        if not m:
            khoa = None
            continue
        khoa, gt = m.group(1), m.group(2).strip()
        if gt.startswith("{") and gt.endswith("}"):
            d[khoa] = {k.strip(): v.strip().strip('"').strip("'")
                       for k, v in (x.split(":", 1) for x in gt[1:-1].split(",") if ":" in x)}
        elif gt:
            d[khoa] = gt.strip('"').strip("'")
    dong_khoi()
    return d


# ============================================================
# 1-4. Từng gói
# ============================================================
thu_muc_goi = sorted(p for p in (GOC / "packs").iterdir() if p.is_dir())
if not thu_muc_goi:
    sai("thư mục packs/ rỗng")

connector_theo_goi = {}
for tm in thu_muc_goi:
    ten = tm.name
    man = doc_manifest(tm)
    if not check(f"{ten}: có javis-pack.yaml đọc được", man is not None):
        continue
    check(f"{ten}: id khớp tên thư mục", man.get("id") == ten, man.get("id"))
    check(f"{ten}: có version", bool(man.get("version")))
    check(f"{ten}: có name", bool(man.get("name")))
    check(f"{ten}: format đúng", man.get("format") == "javis-pack", man.get("format"))
    check(f"{ten}: spec = 1", str(man.get("spec")) == "1", man.get("spec"))

    duong_con = (man.get("provides") or {}).get("connectors") or []
    connector_theo_goi[ten] = []
    for rel in duong_con:
        tep = tm / rel
        if not check(f"{ten}: tệp khai trong provides có thật ({rel})", tep.exists()):
            continue
        try:
            con = json.loads(tep.read_text(encoding="utf-8"))
        except Exception as e:
            sai(f"{ten}: {rel} không phải JSON hợp lệ", e)
            continue
        connector_theo_goi[ten].append(con)
        cid = con.get("id") or ""
        check(f"{ten}: connector có id", bool(cid))
        for truong in ("name", "category", "description", "transport"):
            check(f"{ten}/{cid}: có {truong}", bool(con.get(truong)))

        # Icon: đường dẫn tuyệt đối của dashboard KHÔNG dùng được trong gói, và .svg thì
        # endpoint phục vụ ảnh của gói cố ý từ chối (mở thẳng một tab là chạy script bên trong).
        icon = str(con.get("icon") or "")
        check(f"{ten}/{cid}: icon không phải đường dẫn tuyệt đối", not icon.startswith("/"), icon)
        check(f"{ten}/{cid}: icon không phải .svg", not icon.lower().endswith(".svg"), icon)
        if icon.startswith("assets/"):
            check(f"{ten}/{cid}: tệp icon có thật", (tm / icon).exists(), icon)

        # Bí mật không được nằm trong url: Javis lưu `url` KHÔNG qua secrets_store và trả
        # nguyên nó ra giao diện, nên token trong url là rơi thẳng ra dashboard và ra log.
        u = str(con.get("url") or "").lower()
        for tham in ("jwt=", "token=", "api_key=", "apikey=", "secret=", "password=", "access_token="):
            check(f"{ten}/{cid}: không nhét credential vào url", tham not in u, tham)

        # An toàn: mặc định KHÔNG được là toàn quyền. `readonly` và `safe` đều hợp lệ - vài
        # dịch vụ chỉ có nghĩa khi ghi được bản nháp (Sheets, Tasks, Docs), và bắt chúng về
        # chỉ-đọc là đẩy người dùng đi nâng quyền tay ngay lần dùng đầu, tức làm mòn chính
        # thao tác nâng quyền. Javis vẫn ép gói về chỉ đọc ở phía nó; dòng này canh chuyện gói
        # KHAI toàn quyền, vì đó là lời nói dối với người đọc gói.
        dp = con.get("default_perm", "readonly")
        check(f"{ten}/{cid}: default_perm không phải full", dp in ("readonly", "safe"), dp)

        meta = con.get("tool_meta") or {}
        da_can_nhac = {str(x) for x in (con.get("ghi_da_can_nhac") or [])}
        for nhom in ("read", "write"):
            for tool in (meta.get(nhom) or []):
                check(f"{ten}/{cid}: tool '{tool}' đụng tới TIỀN mà khai ở nhóm {nhom} - "
                      f"phải là danger", not _khop(tool, TIEN))
                check(f"{ten}/{cid}: tool '{tool}' nghe như phá huỷ mà khai ở nhóm {nhom} - "
                      f"chuyển sang danger, hoặc thêm tên nó vào `ghi_da_can_nhac` của khuôn "
                      f"nếu bạn đã cân nhắc và thấy nó nhẹ",
                      not _khop(tool, PHA) or tool in da_can_nhac)
        check(f"{ten}/{cid}: `ghi_da_can_nhac` không được liệt kê tool đụng tiền",
              not [x for x in da_can_nhac if _khop(x, TIEN)],
              [x for x in da_can_nhac if _khop(x, TIEN)])
        if meta.get("danger"):
            check(f"{ten}/{cid}: khai tool nguy hiểm thì phải có câu cảnh báo `risk`",
                  bool(str(con.get("risk") or "").strip()))

# ============================================================
# 5. Danh mục khớp gói và khớp zip
# ============================================================
idx = json.loads((GOC / "index.json").read_text(encoding="utf-8"))
check("index.json đúng format", idx.get("format") == "javis-pack-index", idx.get("format"))
check("index.json đúng format_version", int(idx.get("format_version") or 0) == 1)

ids = [g.get("id") for g in idx.get("packs") or []]
check("id trong danh mục không trùng nhau", len(ids) == len(set(ids)))

for g in idx.get("packs") or []:
    gid = g.get("id") or "?"
    tm = GOC / "packs" / gid
    if not check(f"danh mục: {gid} có thư mục gói", tm.is_dir()):
        continue
    tai = g.get("download") or {}
    zp = GOC / str(tai.get("url") or "")
    if not check(f"danh mục: {gid} có tệp zip ({tai.get('url')})", zp.is_file()):
        continue
    b = zp.read_bytes()
    check(f"danh mục: {gid} sha256 khớp tệp zip",
          hashlib.sha256(b).hexdigest() == tai.get("sha256"), hashlib.sha256(b).hexdigest())
    check(f"danh mục: {gid} size khớp tệp zip", len(b) == int(tai.get("size") or 0), len(b))

    # So NỘI DUNG, không so byte. Mức nén DEFLATE khác nhau giữa các bản zlib nên đóng lại
    # trên máy khác ra byte khác dù nội dung y hệt - so byte là đỏ giả trên CI.
    with zipfile.ZipFile(zp) as z:
        trong_zip = {i.filename: z.read(i.filename) for i in z.infolist() if not i.is_dir()}
    tren_dia = {}
    for f in sorted(tm.rglob("*")):
        if f.is_file():
            tren_dia[f.relative_to(tm).as_posix()] = f.read_bytes()
    check(f"danh mục: {gid} zip có đúng bộ tệp như thư mục nguồn",
          set(trong_zip) == set(tren_dia),
          "chỉ trong zip: " + str(sorted(set(trong_zip) - set(tren_dia)))
          + " | chỉ trên đĩa: " + str(sorted(set(tren_dia) - set(trong_zip))))
    khac = [k for k in (set(trong_zip) & set(tren_dia)) if trong_zip[k] != tren_dia[k]]
    check(f"danh mục: {gid} nội dung từng tệp khớp thư mục nguồn", not khac, khac)

    # Gói có mặt trên đĩa mà quên thêm vào danh mục thì không ai thấy nó - lỗi im lặng nhất
    # của cả repo này.
for tm in thu_muc_goi:
    check(f"gói {tm.name} có mặt trong index.json", tm.name in ids)

# ============================================================
# 6. Em dash
#
# Luật của chủ kho, và lý do là kỹ thuật: trình đọc màn hình lẫn bộ đọc thành tiếng vấp ở ký
# tự này. Nhưng chỉ soi tệp DO KHO viết - manifest, khuôn connector, danh mục, tài liệu, công
# cụ. Thân các kỹ năng do người khác viết mang vào thì không: sửa chữ trong tác phẩm của họ
# không phải việc của trình kiểm này, và một luật gõ cửa 250 tệp vendor là một luật bị tắt.
# ============================================================
EM = chr(0x2014)
SOI = ["index.json", "README.md", "CONTRIBUTING.md"]
for f in sorted(GOC.rglob("javis-pack.yaml")) + sorted((GOC / "packs").rglob("connectors/*.json"))         + sorted((GOC / "tools").glob("*.py")) + [GOC / x for x in SOI]:
    if not f.is_file():
        continue
    try:
        s_tep = f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    if EM in s_tep:
        sai(f"{f.relative_to(GOC).as_posix()}: chứa ký tự em dash (dùng gạch ngang thường)")

print()
for c in canh:
    print("canh bao: " + c)
if loi:
    print(f"ĐỎ {len(loi)} lỗi:")
    for l in loi:
        print("  - " + l)
    sys.exit(1)
print(f"XANH - {len(thu_muc_goi)} gói, {len(ids)} mục danh mục, tất cả khớp")
