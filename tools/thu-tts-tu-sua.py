"""Thử phần TỰ SỬA truy vấn GraphQL của gói TTS Dropship, không chạm tới sàn.

    python tools/thu-tts-tu-sua.py

Vì sao có tệp này. Sàn thitruongsi.com là API nội bộ: không tài liệu, không bản thử nghiệm,
và chỉ trả lời khi cầm token đăng nhập thật của một tài khoản thật. Nghĩa là phần tự sửa
lược đồ - đúng phần chạy vào ĐÚNG LÚC sàn vừa đổi và người dùng đang cần lên đơn - lại là
phần không ai chạy thử được trước khi phát hành. Chỗ đó mà sai thì nó không im lặng: nó viết
đè một tài liệu truy vấn hỏng rồi lưu lại.

Nên tệp này giả lập hai thứ, và chỉ hai thứ: câu lỗi sàn trả về (nguyên văn theo dạng của
graphql-js, kèm `locations`) và kết quả soi lược đồ. Còn lại là mã thật của plugin. Chín ca
dưới đây là chín cách sàn đã đổi hoặc có thể đổi, gồm cả ca 05/09/2026 và 07/09/2026 đã gặp.

Chạy được bằng thư viện chuẩn, vài phần trăm giây, nên CI chạy cùng `tools/kiem-tra.py`.
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent
NGUON = GOC / "packs" / "javis.tts-dropship" / "plugins" / "tts-dropship"

# Nạp plugin.py sẽ đẻ ra `__pycache__/` NGAY TRONG thư mục gói, và `tools/kiem-tra.py` so bộ
# tệp trong zip với bộ tệp trên đĩa nên nó báo đỏ ngay sau đó. Tắt ghi bytecode là xong, chứ
# đừng để người chạy thử xong lại phải đi dọn.
sys.dont_write_bytecode = True

_spec = importlib.util.spec_from_file_location("tts_dropship_plugin", NGUON / "plugin.py")
tts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tts)

DOCS = json.loads((NGUON / "graphql.json").read_text(encoding="utf-8"))

# Lược đồ giả của lượt đang thử. Mỗi trường khai (mô tả kiểu, là danh sách, phải chọn tiếp con).
_LUOC_DO = {}


async def _gia_introspect(ten_type):
    t = tts._goc_kieu(ten_type)
    if t not in _LUOC_DO:
        return None, f"sàn không biết type '{t}'"
    return {k: {"kieu": v[0], "danh_sach": v[1], "co_con": v[2]}
            for k, v in _LUOC_DO[t].items()}, None


tts._introspect = _gia_introspect

_hong = []


def dat(luoc):
    global _LUOC_DO
    _LUOC_DO = luoc


def kiem(ten, dieu_kien, chi_tiet=""):
    print(("  ĐẠT   " if dieu_kien else "  HỎNG  ") + ten
          + (("   <- " + str(chi_tiet)) if chi_tiet and not dieu_kien else ""))
    if not dieu_kien:
        _hong.append(ten)


def _cho(doc, chuoi, lan=1):
    """(dòng, cột) 1-based của lần xuất hiện thứ `lan` - đúng hình dạng `locations` của sàn."""
    i = -1
    for _ in range(lan):
        i = doc.index(chuoi, i + 1)
    return doc.count("\n", 0, i) + 1, i - (doc.rfind("\n", 0, i) + 1) + 1


def loi(cau, doc, chuoi, lan=1):
    dong, cot = _cho(doc, chuoi, lan)
    return {"message": cau, "locations": [{"line": dong, "column": cot}]}


def sua(doc, goc, cac_loi):
    return asyncio.run(tts._sua_mot_vong(doc, goc, cac_loi))


def main():
    don = DOCS["OrderListQuery"]["doc"]
    tim = DOCS["searchProductsQuery"]["doc"]

    print("\n1. Sàn đổi tên một trường (ca thật 07/09/2026)")
    doc = don.replace("dropship_supplier_price", "dropship_selling_price")
    dat({"LineItem": {"id": ("ID", False, False), "price": ("Float", False, False),
                      "dropship_supplier_price": ("Float", False, False)}})
    moi, mo_ta, vi_sao = sua(doc, "orders", [
        loi('Cannot query field "dropship_selling_price" on type "LineItem".',
            doc, "dropship_selling_price")])
    kiem("sửa được", bool(moi), vi_sao)
    kiem("đổi đúng tên sàn gợi ý", moi and "dropship_supplier_price" in moi
         and "dropship_selling_price" not in moi)
    kiem("không đụng gì khác",
         moi and moi.replace("dropship_supplier_price", "dropship_selling_price") == doc)

    print("\n2. Một trường đơn hoá ra là khối (ca thật 07/09/2026: bill_of_lading)")
    dat({"BillOfLading": {"code": ("String", False, False),
                          "carrier": ("String", False, False),
                          "status": ("String", False, False),
                          "history": ("Event []", True, True)}})
    moi, mo_ta, vi_sao = sua(don, "orders", [
        loi('Field "bill_of_lading" of type "BillOfLading" must have a selection of subfields. '
            'Did you mean "bill_of_lading { ... }"?', don, "bill_of_lading")])
    kiem("sửa được", bool(moi), vi_sao)
    kiem("điền các trường lấy thẳng được", moi and "bill_of_lading { code carrier status }" in moi)
    kiem("KHÔNG kéo theo khối con lồng nhau", moi and "history" not in moi)

    print("\n3. Cả hai lỗi trên trong CÙNG một lượt")
    doc = don.replace("dropship_supplier_price", "dropship_selling_price")
    dat({"LineItem": {"price": ("Float", False, False),
                      "dropship_supplier_price": ("Float", False, False)},
         "BillOfLading": {"code": ("String", False, False), "carrier": ("String", False, False)}})
    moi, mo_ta, vi_sao = sua(doc, "orders", [
        loi('Cannot query field "dropship_selling_price" on type "LineItem".',
            doc, "dropship_selling_price"),
        loi('Field "bill_of_lading" of type "BillOfLading" must have a selection of subfields.',
            doc, "bill_of_lading")])
    kiem("sửa cả hai trong một vòng", moi and "dropship_supplier_price" in moi
         and "bill_of_lading { code carrier }" in moi, vi_sao)

    print("\n4. Sàn bọc cả kết quả vào một type con (ca thật 05/09/2026)")
    doc = ("query searchProductsQuery($query: String!) { searchProducts(query: $query) "
           "{ id title shop { id name } } }")
    dat({"ProductSearchResponse": {"total": ("Int", False, False),
                                   "products": ("Product []", True, True)}})
    moi, mo_ta, vi_sao = sua(doc, "searchProducts", [
        loi('Cannot query field "id" on type "ProductSearchResponse".', doc, "id"),
        loi('Cannot query field "title" on type "ProductSearchResponse".', doc, "title")])
    kiem("lồng thêm đúng một tầng", moi and
         "searchProducts(query: $query) { products { id title shop { id name } } }" in moi, vi_sao)

    print("\n5. Lớp bọc đang dùng bị đổi tên (products -> items)")
    dat({"ProductSearchResponse": {"total": ("Int", False, False),
                                   "items": ("Product []", True, True)}})
    moi, mo_ta, vi_sao = sua(tim, "searchProducts", [
        loi('Cannot query field "products" on type "ProductSearchResponse".', tim, "products")])
    kiem("THAY tên chứ không lồng thêm tầng nữa", moi and "items {" in moi
         and "products" not in moi and moi.count("{") == tim.count("{"), vi_sao)

    print("\n6. Sàn bỏ hẳn một trường, không có tên nào thay được")
    dat({"Product": {"id": ("ID", False, False), "title": ("String", False, False),
                     "price": ("Float", False, False)}})
    moi, mo_ta, vi_sao = sua(tim, "searchProducts", [
        loi('Cannot query field "badges" on type "Product".', tim, "badges")])
    kiem("bỏ đúng trường đó", moi and "badges" not in moi, vi_sao)
    kiem("truy vấn vẫn đọc được", moi and tts._boc_trong(moi, "searchProducts") is not None)
    kiem("các trường khác còn nguyên", moi and "videos" in moi and "rating_avg" in moi)

    print("\n7. Trường trùng tên ở nhiều khối: chỉ sửa ĐÚNG chỗ sàn chỉ")
    so_id = don.count(" id ")
    dat({"Customer": {"customer_id": ("ID", False, False), "name": ("String", False, False),
                      "phone": ("String", False, False)}})
    dong, cot = _cho(don, "customer { id")
    moi, mo_ta, vi_sao = sua(don, "orders", [
        {"message": 'Cannot query field "id" on type "Customer". Did you mean "customer_id"?',
         "locations": [{"line": dong, "column": cot + len("customer { ")}]}])
    kiem("đổi đúng `id` trong khối customer",
         moi and "customer { customer_id name phone }" in moi, vi_sao)
    kiem("các `id` khác không bị đụng", moi and moi.count(" id ") == so_id - 1)

    print("\n8. Lỗi KHÔNG phải lệch lược đồ thì không được sửa bừa")
    moi, mo_ta, vi_sao = sua(don, "orders", [{"message": "Unauthorized: token expired"}])
    kiem("từ chối sửa", moi is None, "đã sửa bừa một lỗi không hiểu")
    kiem("nói được lý do", bool(vi_sao))

    print("\n9. Chiều ngược lại: một khối hoá ra là giá trị đơn")
    doc = "query Q($query: String!) { orders(query: $query) { items { id shipping { bill } } } }"
    dat({})
    moi, mo_ta, vi_sao = sua(doc, "orders", [
        loi('Field "shipping" must not have a selection since type "String" has no subfields.',
            doc, "shipping")])
    kiem("bỏ khối con, giữ tên trường", moi and "items { id shipping }" in moi, vi_sao)

    print()
    if _hong:
        print("HỎNG %d ca: %s" % (len(_hong), "; ".join(_hong)))
        return 1
    print("Tất cả 9 ca đều đạt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
