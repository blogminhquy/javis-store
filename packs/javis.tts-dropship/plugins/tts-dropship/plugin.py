"""Plugin gói: bán dropship trên sàn TTS (dropship.thitruongsi.com).

Sàn TTS không có MCP server và không phát hành API key công khai, nên gói này gọi THẲNG
api.thitruongsi.com bằng chính token phiên đăng nhập của người dùng, lấy từ Local Storage của
trình duyệt và cất trong kết nối "tts-dropship" (mã hoá bởi secrets_store). Kết nối đó là một
connector ẢO: nó không có URL MCP để mở phiên, nó chỉ giữ token cho plugin này dùng, giống cách
Meta Ads Graph API đang làm.

Bốn điều quyết định hình dạng của file này
-----------------------------------------
1. **Token sống khoảng 3 ngày.** Hạn nằm ngay trong JWT nên đọc được mà không chạm mạng. Mọi
   tool kiểm hạn TRƯỚC khi gọi, và khi hết hạn thì trả về câu nói rõ phải làm gì, chứ không để
   sàn trả 401 rồi model đoán mò. TTS chưa công bố địa chỉ đổi refresh token, nên phần tự gia
   hạn chỉ chạy khi người dùng tự điền ô `refresh_url`; để trống là không gia hạn, và điều đó
   được nói thẳng chứ không giả vờ có.

2. **Cờ `dropship=true` phải đi kèm gần như mọi request.** Thiếu nó sàn trả dữ liệu của tài
   khoản mua sỉ thường, tức là số liệu SAI mà trông vẫn đúng. Vì vậy cờ này đặt ở lớp client
   (`_api`), không để từng tool tự nhớ.

3. **Đây là API nội bộ, không có tài liệu chính thức.** Bản khảo sát bắt được tên operation và
   danh sách trường của ba truy vấn GraphQL, nhưng không bắt được nguyên văn tài liệu truy vấn.
   Nên tài liệu đó nằm trong `graphql.json` như DỮ LIỆU và sửa được lúc chạy qua tool
   `tts_graphql`, không phải đóng gói lại. Cùng lý do, mọi tool ghi đều nhận `raw_body` để đè
   nguyên thân request khi sàn đổi hình dạng.

4. **Đơn đã tạo thì KHÔNG SỬA ĐƯỢC.** Sàn không có PUT/PATCH cho đơn, chỉ có huỷ rồi lên lại.
   Nên `tts_create_order`, `tts_cancel_order` và `tts_order_action` bắt xác nhận hai bước: lần
   gọi đầu chỉ dựng bản xem trước và KHÔNG chạm mạng ghi, phải gọi lại với `confirm=true` mới
   thực thi. Đây là chốt trong MÃ, độc lập với mức quyền của kết nối.

Cố ý KHÔNG có trong gói này
---------------------------
- **Rút tiền.** `/v1/pay/api/wallet/withdrawals` chuyển tiền ra khỏi sàn. Gói chỉ đọc biểu phí
  rút; không có đường thực thi, kể cả ở mức Toàn quyền.
- **Thêm/xoá tài khoản ngân hàng.** Chỉ đọc danh sách.
- **Tìm sản phẩm bằng ảnh, đồng bộ TikTok Shop, tải ảnh vận đơn.** Ba đường này bản khảo sát
  chỉ ghi được địa chỉ chứ chưa bắt được hình dạng thân request. Viết đại ra một tool gọi sai
  thì tệ hơn là không có tool, nên để lại cho bản sau.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

CONNECTOR_ID = "tts-dropship"
API = "https://api.thitruongsi.com"
WEB = "https://dropship.thitruongsi.com"

# Tự đặt hàng đợi: sàn chưa công bố giới hạn nào, nên giữ khoảng 4 request mỗi giây và lùi dần
# khi bị 429/5xx. Đây là phép lịch sự với một API nội bộ, và cũng là cách không bị chặn IP.
_MIN_INTERVAL = 0.25
_RETRY_STATUS = (429, 500, 502, 503, 504)
_MAX_RETRY = 3

# Token còn dưới ngần này thì thử gia hạn trước khi dùng (giây).
_REFRESH_TRUOC = 24 * 3600

# Trần kích thước một câu trả lời. Sàn trả nguyên danh sách sản phẩm kèm mô tả HTML, đủ để nuốt
# trọn cửa sổ ngữ cảnh của model nếu không chặn.
_TRAN_KY_TU = 40000

# Hình dạng một JWT: ba khối base64url nối bằng dấu chấm, khối đầu luôn bắt đầu
# bằng "eyJ" (chính là '{"' đã mã hoá).
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")

_nhip = {"lan_cuoi": 0.0}


# ============================================================
# Kết nối và token
# ============================================================
def _conn():
    """Kết nối tts-dropship đang bật (ưu tiên cái đặt mặc định). None nếu chưa có."""
    try:
        import mcp_store
    except Exception:
        return None
    co = [c for c in mcp_store.list_connections()
          if c.get("connector_id") == CONNECTOR_ID and c.get("enabled", True)]
    if not co:
        return None
    for c in co:
        if c.get("is_default"):
            return c
    return co[0]


def _secrets(conn):
    try:
        import mcp_store
    except Exception:
        return {}
    return mcp_store.connection_secrets(conn["id"]) or {}


def _jwt_exp(tok: str) -> int:
    """Hạn dùng ghi trong JWT, kiểu Unix. 0 nếu không đọc được.

    KHÔNG xác thực chữ ký, và không cần: đây chỉ để biết khi nào nên đi xin token mới. Người
    duy nhất bị lừa bởi một exp giả trong token của chính mình là chính họ."""
    try:
        than = tok.split(".")[1]
        than += "=" * (-len(than) % 4)
        return int(json.loads(base64.urlsafe_b64decode(than.encode())).get("exp") or 0)
    except Exception:
        return 0


def _con_lai(tok: str) -> int:
    exp = _jwt_exp(tok)
    return int(exp - time.time()) if exp else -1


def _het_han_noi_gi(con_lai: int) -> str:
    if con_lai == -1:
        return ("Token không đọc được hạn dùng. Có thể bạn dán thiếu hoặc dán nhầm ô. Vào trang "
                "Kết nối > TTS Dropship > Sửa, rồi dán lại nguyên văn giá trị @publicToken.")
    return (f"Token TTS đã hết hạn {abs(con_lai) // 3600} giờ trước. Mở "
            f"{WEB}, đăng nhập lại, bấm F12 > Application > Local Storage, copy giá trị mới của "
            "@publicToken rồi dán lại ở trang Kết nối > TTS Dropship > Sửa. Token của sàn sống "
            "khoảng 3 ngày nên việc này lặp lại vài ngày một lần, trừ khi bạn điền được ô "
            "'Địa chỉ gia hạn token'.")


async def _gia_han(conn, sec):
    """Đổi refresh token lấy access token mới. (token_moi, lý_do_thất_bại).

    Chỉ chạy khi người dùng TỰ điền `refresh_url`: TTS không công bố địa chỉ này và gói không
    đoán bừa một cái. Thân request gửi kèm cả ba cách gọi tên thường gặp vì cùng lý do; server
    nào cũng bỏ qua khoá nó không biết."""
    url = (sec.get("refresh_url") or "").strip()
    rt = (sec.get("refresh_token") or "").strip()
    if not url:
        return None, "chưa điền 'Địa chỉ gia hạn token' nên Javis không tự gia hạn"
    if not rt:
        return None, "chưa dán @refreshToken nên không có gì để đổi"
    if not url.lower().startswith("https://"):
        return None, "'Địa chỉ gia hạn token' phải là https"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, json={"refresh_token": rt, "refreshToken": rt,
                                        "grant_type": "refresh_token"},
                             headers={"accept": "application/json"})
        d = r.json() if r.content else {}
    except Exception as e:
        return None, f"{type(e).__name__} khi gọi địa chỉ gia hạn"
    if not isinstance(d, dict):
        return None, "địa chỉ gia hạn trả về thứ không phải JSON object"
    tang = d.get("data") if isinstance(d.get("data"), dict) else d
    moi = ""
    for k in ("access_token", "accessToken", "token", "publicToken", "public_token"):
        if isinstance(tang.get(k), str) and tang[k].count(".") == 2:
            moi = tang[k].strip()
            break
    if not moi:
        return None, "không tìm thấy access token trong câu trả lời của địa chỉ gia hạn"
    rt_moi = ""
    for k in ("refresh_token", "refreshToken"):
        if isinstance(tang.get(k), str) and tang[k].strip():
            rt_moi = tang[k].strip()
            break
    try:
        import mcp_store
        patch = {"access_token": moi}
        if rt_moi:
            patch["refresh_token"] = rt_moi
        mcp_store.update_connection(conn["id"], {"fields": patch})
    except Exception as e:
        # Gia hạn được nhưng không ghi lại được thì vẫn dùng token mới cho lượt này; chỉ là lượt
        # sau phải gia hạn lại. Đổi một phiền toái nhỏ lấy việc không làm hỏng bản ghi kết nối.
        return moi, f"lấy được token mới nhưng chưa lưu lại được ({type(e).__name__})"
    return moi, ""


async def _token():
    """(token, lỗi). Kiểm hạn trước, tự gia hạn nếu người dùng đã bật, rồi mới trả về."""
    conn = _conn()
    if not conn:
        return None, ("Chưa kết nối TTS Dropship. Vào trang Kết nối, chọn 'TTS Dropship "
                      "(thitruongsi.com)', làm theo hướng dẫn lấy token từ trình duyệt rồi bấm "
                      "Kết nối. Sau đó gọi lại tool này.")
    sec = _secrets(conn)
    tok = (sec.get("access_token") or "").strip()
    if not tok:
        return None, ("Kết nối TTS Dropship chưa có token. Vào trang Kết nối > TTS Dropship > "
                      "Sửa và dán giá trị @publicToken.")
    con_lai = _con_lai(tok)
    if con_lai > _REFRESH_TRUOC:
        return tok, None
    moi, vi_sao = await _gia_han(conn, sec)
    if moi:
        return moi, None
    if con_lai <= 0:
        return None, _het_han_noi_gi(con_lai) + f" (tự gia hạn không chạy: {vi_sao})"
    # Còn hạn nhưng sắp hết: vẫn chạy bình thường, cảnh báo đi kèm kết quả chứ không chặn.
    return tok, None


def _canh_bao_han():
    """Câu cảnh báo kèm vào kết quả khi token sắp hết hạn, hoặc rỗng."""
    conn = _conn()
    if not conn:
        return ""
    tok = (_secrets(conn).get("access_token") or "").strip()
    if not tok:
        return ""
    con_lai = _con_lai(tok)
    if 0 < con_lai <= _REFRESH_TRUOC:
        return (f"Token TTS còn khoảng {max(1, con_lai // 3600)} giờ là hết hạn. Vào trang Kết "
                "nối > TTS Dropship > Sửa để dán token mới trước khi nó chết giữa một đơn.")
    return ""


def _check():
    """check_fn của mọi tool: chặn sớm khi chưa kết nối. Không chạm mạng."""
    conn = _conn()
    if not conn:
        return ("Chưa kết nối TTS Dropship. Vào trang Kết nối, chọn 'TTS Dropship "
                "(thitruongsi.com)' và làm theo hướng dẫn lấy token từ trình duyệt.")
    if not (_secrets(conn).get("access_token") or "").strip():
        return "Kết nối TTS Dropship chưa có token. Vào trang Kết nối > TTS Dropship > Sửa."
    return None


# ============================================================
# Lớp HTTP
# ============================================================
async def _nhip_do():
    """Giãn các request cho đủ thưa. Không dùng Lock để không dính chuyện lock buộc vào một
    event loop khác với loop đang chạy; sai lệch vài mili giây ở đây không đổi điều gì."""
    import asyncio
    cho = _nhip["lan_cuoi"] + _MIN_INTERVAL - time.monotonic()
    if cho > 0:
        await asyncio.sleep(cho)
    _nhip["lan_cuoi"] = time.monotonic()


def _che(s: str) -> str:
    """Bỏ token ra khỏi một câu lỗi rồi cắt ngắn, trước khi cho nó đi tiếp.

    Bắt theo HÌNH DẠNG JWT chứ không theo ranh giới khoảng trắng, vì câu lỗi hay gặp nhất lại
    là một thân JSON do sàn trả về, nơi token nằm gọn trong dấu nháy kép và không có khoảng
    trắng nào quanh nó. Cắt trước rồi mới che thì một token bị cắt đôi vẫn lộ nửa đầu, nên thứ
    tự ở đây là che trước, cắt sau."""
    s = _JWT_RE.sub("<token>", str(s or ""))
    return s[:400]


async def _api(method, path, *, params=None, body=None, multipart=None, dropship=True,
               timeout=45, tra_loi_json=False):
    """Gọi api.thitruongsi.com. Trả (dữ_liệu, lỗi) - đúng một vế khác None.

    Cờ `dropship=true` cắm ở ĐÂY chứ không ở từng tool: quên nó một lần là sàn trả số liệu của
    tài khoản mua sỉ thường, và con số đó trông không khác gì số đúng."""
    import asyncio

    tok, loi = await _token()
    if loi:
        return None, loi
    p = dict(params or {})
    if dropship:
        p.setdefault("dropship", "true")
    url = API + "/" + str(path).lstrip("/")
    headers = {"authorization": f"Bearer {tok}", "accept": "application/json",
               "origin": WEB, "referer": WEB + "/"}
    try:
        import httpx
    except Exception as e:
        return None, f"thiếu thư viện httpx trong máy chủ Javis ({type(e).__name__})"

    # Đường khiếu nại của sàn nhận multipart/form-data. httpx chỉ dựng multipart khi `files`
    # KHÁC RỖNG, nên vài trường chữ không kèm tệp phải đi bằng dạng `(None, giá_trị)` - đây là
    # cách chuẩn để một phần multipart là chữ thuần. Truyền qua `data=` sẽ ra
    # x-www-form-urlencoded và sàn từ chối, mà thông báo lỗi thì không nói vì sao.
    tep = None
    if multipart:
        tep = {str(k): (None, "" if v is None else str(v)) for k, v in multipart.items()}

    cho = 1.0
    for lan in range(_MAX_RETRY + 1):
        await _nhip_do()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
                r = await c.request(method.upper(), url, params=p, json=body, files=tep,
                                    headers=headers)
        except Exception as e:
            if lan < _MAX_RETRY:
                await asyncio.sleep(cho)
                cho *= 2
                continue
            return None, f"không gọi được sàn TTS ({type(e).__name__}: {_che(e)})"
        if r.status_code in _RETRY_STATUS and lan < _MAX_RETRY:
            await asyncio.sleep(cho)
            cho *= 2
            continue
        if r.status_code in (401, 403):
            return None, ("Sàn TTS từ chối token (HTTP %d). %s"
                          % (r.status_code, _het_han_noi_gi(_con_lai(tok))))
        try:
            d = r.json() if r.content else {}
        except Exception:
            d = None
        if r.status_code >= 400:
            # GraphQL báo lỗi TRUY VẤN bằng mã 4xx kèm thân JSON hợp lệ (TTS dùng 422). Nuốt
            # nó thành một dòng "HTTP 422" là vứt đúng câu nói ra sai ở chỗ nào, và người đọc
            # chỉ còn một con số không hành động được. Trả thân về cho lớp GraphQL đọc.
            if tra_loi_json and isinstance(d, dict) and d.get("errors"):
                return d, None
            chi_tiet = _che(json.dumps(d, ensure_ascii=False) if d is not None else r.text)
            return None, f"sàn TTS trả HTTP {r.status_code}: {chi_tiet}"
        return d, None
    return None, "sàn TTS liên tục lỗi sau nhiều lần thử lại"


# ============================================================
# GraphQL
# ============================================================
_QS_PHAI_MA_HOA = "&=?#%+"


def _qs_gt(v) -> str:
    """Mã hoá đúng những ký tự sẽ làm vỡ chuỗi query, giữ nguyên phần còn lại.

    Bản khảo sát bắt được nguyên văn một chuỗi thật của sàn với dấu cách và tiếng Việt không mã
    hoá (`keyword=áo thun`), nên mã hoá toàn bộ là đi chệch khỏi thứ sàn thực sự nhận."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return "".join("%%%02X" % ord(c) if c in _QS_PHAI_MA_HOA else c for c in str(v))


def _build_qs(cap: dict) -> str:
    """Dựng chuỗi `?a=1&b=2` mà GraphQL của TTS nhận làm biến $query. Bỏ khoá rỗng."""
    phan = [f"{k}={_qs_gt(v)}" for k, v in cap.items() if v is not None and v != ""]
    return "?" + "&".join(phan)


def _ghi_doc(ctx, ten_op, patch):
    """Ghi bản sửa của MỘT operation ra thư mục state. Trả lý do thất bại, hoặc rỗng.

    Ghi NGOÀI gói có chủ ý: bản cập nhật gói thay cả thư mục `plugins/`, nên một bản sửa nằm
    trong đó sẽ bị xoá đúng vào lúc người dùng bấm Cập nhật."""
    de = ctx.data_dir / "graphql.json"
    try:
        hien = json.loads(de.read_text(encoding="utf-8"))
        if not isinstance(hien, dict):
            hien = {}
    except Exception:
        hien = {}
    hien[ten_op] = {**(hien.get(ten_op) or {}), **patch}
    try:
        de.write_text(json.dumps(hien, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return ""


def _gql_docs(ctx):
    """Tài liệu truy vấn: bản trong gói, đè bởi bản người dùng sửa trong thư mục state."""
    goc = {}
    try:
        goc = json.loads((Path(__file__).parent / "graphql.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        de = json.loads((ctx.data_dir / "graphql.json").read_text(encoding="utf-8"))
        if isinstance(de, dict):
            for k, v in de.items():
                if isinstance(v, dict):
                    goc[k] = {**(goc.get(k) or {}), **v}
    except Exception:
        pass
    return goc


# Câu lỗi sàn trả khi truy vấn chọn một trường không còn nằm thẳng trên type gốc nữa. Đây
# CHÍNH LÀ dạng hỏng đã gặp thật ngày 05/09/2026: sàn bọc kết quả vào một type con
# (`ProductSearchResponse`, `OrdersQueryResponse`) thay vì trả phẳng như trước.
_LOI_TRUONG = re.compile(r'Cannot query field "([^"]+)" on type "([^"]+)"')


async def _introspect(ten_type):
    """Hỏi thẳng sàn xem một type có những trường gì. Trả ({tên: mô tả kiểu}, lỗi).

    Vì sao gói tự hỏi thay vì để người viết gói đoán: đây là API nội bộ không tài liệu, và
    người duy nhất biết chắc hình dạng hiện tại là chính máy chủ. Đoán một lần thì đúng được
    một lần; hỏi được thì lần đổi sau cũng tự xử lý."""
    doc = ("query JavisIntrospect($n: String!) { __type(name: $n) { name kind fields { name "
           "type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } "
           "} } }")
    d, loi = await _api("POST", "/graphql", dropship=False, tra_loi_json=True,
                        body={"operationName": "JavisIntrospect",
                              "variables": {"n": ten_type}, "query": doc})
    if loi:
        return None, loi
    if isinstance(d, dict) and d.get("errors"):
        return None, ("sàn không cho soi lược đồ GraphQL (introspection thường bị tắt trên bản "
                      "chạy thật): " + json.dumps(d["errors"], ensure_ascii=False)[:200])
    t = ((d or {}).get("data") or {}).get("__type")
    if not isinstance(t, dict) or not t.get("fields"):
        return None, f"sàn không biết type '{ten_type}'"
    ra = {}
    for f in t["fields"]:
        kieu, sau = f.get("type") or {}, []
        while isinstance(kieu, dict):
            if kieu.get("name"):
                sau.append(kieu["name"])
            if kieu.get("kind") == "LIST":
                sau.append("[]")
            kieu = kieu.get("ofType")
        ra[f.get("name")] = " ".join(x for x in sau if x) or "?"
    return ra, None


def _boc_trong(doc: str, goc: str):
    """Tách tài liệu truy vấn thành (đầu, phần chọn trường của root, đuôi). None nếu không đọc được.

    Cố ý KHÔNG viết một trình phân tích GraphQL: mọi tài liệu trong gói này đều một hình dạng
    (`query Ten($q: T!) { goc(...) { ... } }`), nên đếm ngoặc là đủ và không kéo theo một thư
    viện chỉ để dùng ở đúng một chỗ."""
    # Tìm root như một TRƯỜNG, không như một chuỗi con. `doc.find("searchProducts")` khớp phải
    # chữ đó nằm trong TÊN OPERATION `searchProductsQuery` đứng trước, nên nó cắt cao hơn một
    # tầng và mọi phép lồng sau đó đều lệch. Đòi ngay sau tên phải là `(` hoặc `{` thì
    # `searchProductsQuery(` bị loại vì sau `searchProducts` còn chữ `Query`.
    m = re.search(r"(?<![A-Za-z0-9_])" + re.escape(goc) + r"\s*(\(|\{)", doc)
    if not m:
        return None
    k = m.end() - 1
    if doc[k] == "(":
        # Nhảy qua khối đối số rồi mới tìm phần chọn trường.
        do_ngoac, k = 1, k + 1
        while k < len(doc) and do_ngoac:
            if doc[k] == "(":
                do_ngoac += 1
            elif doc[k] == ")":
                do_ngoac -= 1
            k += 1
        if do_ngoac:
            return None
        j = doc.find("{", k)
    else:
        j = k
    if j < 0:
        return None
    sau, do = j + 1, 1
    while sau < len(doc) and do:
        if doc[sau] == "{":
            do += 1
        elif doc[sau] == "}":
            do -= 1
        sau += 1
    if do:
        return None
    return doc[:j + 1], doc[j + 1:sau - 1].strip(), doc[sau - 1:]


def _lop_boc_don(sel: str) -> str:
    """Tên lớp bọc nếu phần chọn trường chỉ gồm ĐÚNG MỘT trường có khối con. Rỗng nếu khác.

    `products { id title }` trả "products"; `id title` trả rỗng; `total products { id }` cũng
    trả rỗng, vì ở đó `products` không phải lớp bọc duy nhất và bóc nó ra sẽ mất `total`."""
    s = sel.strip()
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\{", s)
    if not m:
        return ""
    tach = _boc_trong(s, m.group(1))
    return m.group(1) if tach and tach[2].strip() == "}" else ""


async def _tu_sua(ctx, ten_op, spec, loi_json):
    """Sàn bọc kết quả vào một type con: tìm ra tên lớp bọc rồi lồng phần chọn trường vào.

    Trả (doc_mới, tên_lớp_bọc, lý_do_thất_bại). Chỉ chạy cho ĐÚNG một dạng lỗi, và chỉ lồng
    thêm một tầng - không tự ý viết lại phần chọn trường của người dùng."""
    # Soi TỪNG câu lỗi, không soi chuỗi JSON đã dump: `json.dumps` biến mọi dấu nháy trong câu
    # lỗi thành `\"`, nên một mẫu tìm dấu nháy thường sẽ không bao giờ khớp. Đúng cái bẫy này
    # đã làm phần tự sửa im lặng không chạy ở bản đầu.
    truong_loi, ten_type = "", ""
    for e in (loi_json or []):
        cau = e.get("message") if isinstance(e, dict) else e
        m = _LOI_TRUONG.search(str(cau or ""))
        if m:
            truong_loi, ten_type = m.group(1), m.group(2)
            break
    if not ten_type:
        return None, "", "lỗi không thuộc dạng 'sàn bọc kết quả vào type con'"
    truong, loi = await _introspect(ten_type)
    if loi:
        return None, "", loi
    goc = spec.get("root") or ""
    tach = _boc_trong(spec.get("doc") or "", goc)
    if not tach:
        return None, "", "không đọc được hình dạng tài liệu truy vấn hiện tại"
    dau, trong, duoi = tach

    # Lớp bọc mà tài liệu ĐANG dùng, nếu có. Khi câu lỗi trỏ vào chính nó thì nghĩa là tên lớp
    # đó SAI, phải THAY, không phải lồng thêm một tầng nữa - lồng tiếp ra ba tầng và sai nặng
    # hơn lúc đầu. Đây là đường hỏng nhiều khả năng xảy ra nhất của bản 1.0.1: tên `products`
    # và `items` trong doc mặc định là suy ra từ câu lỗi của sàn chứ chưa gọi thật để xác minh.
    boc_dang_co = _lop_boc_don(trong)
    if boc_dang_co and boc_dang_co == truong_loi:
        tach2 = _boc_trong(trong, boc_dang_co)
        if tach2:
            trong = tach2[1]

    # Trường đầu tiên là DANH SÁCH đối tượng chính là chỗ dữ liệu chuyển vào. Ưu tiên vài tên
    # hay gặp trước khi rơi về "cái list đầu tiên", để không vớ phải một list phụ nào đó.
    ung = [t for t, k in truong.items() if "[]" in k]
    for uu in ("products", "items", "orders", "data", "nodes", "results", "edges", "list"):
        if uu in ung:
            ung = [uu] + [x for x in ung if x != uu]
            break
    if not ung:
        return None, "", (f"type '{ten_type}' không có trường danh sách nào để lồng vào. "
                          f"Các trường sàn khai: {', '.join(sorted(truong)) or '(rỗng)'}")
    lop = ung[0]
    return dau + " " + lop + " { " + trong + " } " + duoi, lop, ""


async def _gql(ctx, ten_op, bien, cho_tu_sua=True):
    """Gọi POST /graphql. Trả (dữ_liệu_đã_bóc_lớp, lỗi).

    Gặp đúng dạng lỗi "sàn bọc kết quả vào type con" thì tự soi lược đồ, lồng lại phần chọn
    trường, thử LẠI MỘT LẦN, và nếu chạy thì ghi bản sửa ra ngoài gói. Một lần thôi, có chủ ý:
    thử lại vòng vo trên một lược đồ đã đổi hẳn chỉ đốt thời gian và làm câu lỗi khó đọc."""
    docs = _gql_docs(ctx)
    spec = docs.get(ten_op) or {}
    doc = (spec.get("doc") or "").strip()
    if not doc:
        return None, (f"không có tài liệu truy vấn cho '{ten_op}'. Xem bằng tool tts_graphql "
                      "(action=show) rồi sửa bằng action=set.")
    d, loi = await _api("POST", "/graphql", dropship=False, tra_loi_json=True,
                        body={"operationName": ten_op, "variables": bien, "query": doc})
    if loi:
        return None, loi

    ghi_chu = ""
    if isinstance(d, dict) and d.get("errors"):
        moi, lop, vi_sao = (None, "", "đã thử tự sửa một lần rồi")
        if cho_tu_sua:
            moi, lop, vi_sao = await _tu_sua(ctx, ten_op, spec, d["errors"])
        if not moi:
            return None, (
                "sàn TTS từ chối truy vấn GraphQL '%s': %s\n\nĐây là tên trường trong "
                "graphql.json không còn khớp sàn, KHÔNG phải token hay mạng. Javis đã thử tự "
                "sửa nhưng không xong (%s). Soi hình dạng thật bằng tts_graphql "
                "action=introspect (kèm tham số `type` lấy từ câu lỗi trên), rồi ghi lại bằng "
                "action=set. Không phải cài lại gói."
                % (ten_op, json.dumps(d["errors"], ensure_ascii=False)[:400], vi_sao))
        spec2 = {**spec, "doc": moi}
        d2, loi2 = await _api("POST", "/graphql", dropship=False, tra_loi_json=True,
                              body={"operationName": ten_op, "variables": bien, "query": moi})
        if loi2 or (isinstance(d2, dict) and d2.get("errors")):
            chi_tiet = loi2 or json.dumps(d2.get("errors"), ensure_ascii=False)[:300]
            return None, (
                "sàn TTS từ chối truy vấn GraphQL '%s'. Javis đã tự lồng kết quả vào lớp '%s' "
                "rồi thử lại, vẫn không được: %s. Soi bằng tts_graphql action=introspect rồi "
                "ghi lại bằng action=set." % (ten_op, lop, chi_tiet))
        _ghi_doc(ctx, ten_op, {"doc": moi})
        d, spec = d2, spec2
        ghi_chu = ("Sàn đã đổi hình dạng kết quả: nay bọc trong lớp '%s'. Javis tự lồng lại "
                   "truy vấn, thử lại thành công và ĐÃ LƯU bản sửa ngoài gói, nên lần sau "
                   "chạy thẳng. Dữ liệu giờ nằm trong data.%s.%s"
                   % (lop, spec.get("root") or "?", lop))

    than = (d or {}).get("data") if isinstance(d, dict) else None
    goc = spec.get("root") or ""
    ra = None
    if isinstance(than, dict) and goc and goc in than:
        ra = {goc: than[goc], "extensions": (d or {}).get("extensions")}
    elif isinstance(than, dict) and len(than) == 1:
        # Tên trường gốc đổi mà phần chọn trường vẫn đúng: lấy đại cái khoá duy nhất còn hơn
        # trả về nguyên khối `data` rồi để model tự mò.
        ra = {**than, "extensions": (d or {}).get("extensions")}
    else:
        ra = d
    if ghi_chu and isinstance(ra, dict):
        ra["_javis_tu_sua"] = ghi_chu
    return ra, None


# ============================================================
# Định dạng câu trả lời
# ============================================================
def _ra(d, ghi_chu=""):
    """Đóng gói kết quả cho model. Cắt ở trần ký tự để một danh sách dài không nuốt ngữ cảnh."""
    canh = _canh_bao_han()
    goi = {"ok": True, "data": d}
    if ghi_chu:
        goi["ghi_chu"] = ghi_chu
    if canh:
        goi["canh_bao"] = canh
    s = json.dumps(goi, ensure_ascii=False, default=str)
    if len(s) > _TRAN_KY_TU:
        s = s[:_TRAN_KY_TU] + ('... [CẮT BỚT: kết quả quá dài. Thu hẹp bằng limit nhỏ hơn, hoặc '
                               'hỏi đúng một mục thay vì cả danh sách.]')
    return s


def _loi(msg):
    return "ERROR: " + str(msg)


def _int(args, ten, mac_dinh, nho_nhat=1, lon_nhat=200):
    try:
        v = int(args.get(ten) or mac_dinh)
    except (TypeError, ValueError):
        return mac_dinh
    return max(nho_nhat, min(lon_nhat, v))


def _str(args, ten, mac_dinh=""):
    return str(args.get(ten) or mac_dinh).strip()


def _obj(args, ten):
    """Đọc một tham số object. Model nhiều khi đưa vào chuỗi JSON, nhận cả hai."""
    v = args.get(ten)
    if isinstance(v, str) and v.strip():
        try:
            v = json.loads(v)
        except Exception:
            return None
    return v if isinstance(v, (dict, list)) else None


def _tien(x):
    try:
        return f"{float(x):,.0f}"
    except (TypeError, ValueError):
        return str(x)


# ============================================================
# NHÓM A + F: sản phẩm, nhà cung cấp, bài đăng bán, sản phẩm đã lưu
# ============================================================
_SORT = {"_score", "created_at", "total_sales", "dropship_profit", "price"}


async def _products(args, ctx):
    args = args or {}
    act = _str(args, "action", "search").lower()

    if act == "search":
        sort = _str(args, "sort_by", "_score")
        if sort not in _SORT:
            return _loi(f"sort_by phải là một trong: {', '.join(sorted(_SORT))}")
        qs = _build_qs({
            "filter_only_dropship": "true",
            "keyword": _str(args, "keyword"),
            "limit": _int(args, "limit", 20, 1, 100),
            "offset": _int(args, "offset", 0, 0, 100000),
            "sort_by": sort,
            "ascending": "true" if args.get("ascending") else None,
            "filter_category_lv1": _str(args, "category_lv1"),
            "filter_category_lv2": _str(args, "category_lv2"),
            "filter_province": _str(args, "province"),
            "filter_shop_id": _str(args, "shop_id"),
        })
        d, loi = await _gql(ctx, "searchProductsQuery", {"query": qs})
        return _loi(loi) if loi else _ra(d, "Giá trong kết quả: dropship_price là giá bạn trả cho "
                                            "nhà cung cấp, dropship_profit là lãi gợi ý. Lợi "
                                            "nhuận THẬT còn phụ thuộc khuyến mãi của shop, xem "
                                            "bằng tts_suppliers action=promotions.")

    if act == "get":
        pid = _str(args, "product_id")
        if not pid:
            return _loi("thiếu 'product_id'.")
        d, loi = await _gql(ctx, "ProductById", {"id": pid})
        return _loi(loi) if loi else _ra(d)

    if act == "reviews":
        p = {"limit": _int(args, "limit", 20, 1, 100), "offset": _int(args, "offset", 0, 0, 10000)}
        if _str(args, "product_id"):
            p["product_id"] = _str(args, "product_id")
        if _str(args, "shop_id"):
            p["shop_id"] = _str(args, "shop_id")
        d, loi = await _api("GET", "/v1/review/reviews", params=p)
        return _loi(loi) if loi else _ra(d)

    if act == "recent":
        d, loi = await _api("GET", "/v1/analytics/user/recent_products")
        return _loi(loi) if loi else _ra(d)

    if act == "collections":
        cid = _str(args, "collection_id")
        if cid:
            d, loi = await _api("GET", f"/v2/order/api/smart_collections/{cid}.json",
                                params={"limit": _int(args, "limit", 20, 1, 100),
                                        "offset": _int(args, "offset", 0, 0, 10000)})
        else:
            d, loi = await _api("GET", "/v2/order/api/collection_screens/home.json")
        return _loi(loi) if loi else _ra(d)

    return _loi("action phải là: search | get | reviews | recent | collections")


async def _suppliers(args, ctx):
    args = args or {}
    act = _str(args, "action", "search").lower()
    sid = _str(args, "shop_id")

    if act == "search":
        qs = _build_qs({"keyword": _str(args, "keyword"),
                        "limit": _int(args, "limit", 20, 1, 100),
                        "offset": _int(args, "offset", 0, 0, 10000),
                        "filter_province": _str(args, "province")})
        d, loi = await _gql(ctx, "searchShopQuery", {"query": qs})
        return _loi(loi) if loi else _ra(d)

    if act == "get":
        if not sid:
            return _loi("thiếu 'shop_id'.")
        d, loi = await _api("GET", f"/v1/user/api/shop/{sid}")
        return _loi(loi) if loi else _ra(d)

    if act == "promotions":
        if not sid:
            return _loi("thiếu 'shop_id'.")
        d, loi = await _api("GET", "/v2/order/api/price_rules.json", params={"shop_id": sid})
        return _loi(loi) if loi else _ra(d, "price_rules là các chương trình freeship và thưởng "
                                            "theo đơn của shop. Phải cộng vào mới ra lợi nhuận "
                                            "thật của một đơn.")

    if act == "following":
        d, loi = await _api("GET", "/v1/user/api/v4/me/shops/following")
        return _loi(loi) if loi else _ra(d)

    if act == "check":
        if not sid:
            return _loi("thiếu 'shop_id'.")
        d, loi = await _api("GET", f"/v1/user/api/shops/{sid}/is-following")
        return _loi(loi) if loi else _ra(d)

    return _loi("action phải là: search | get | promotions | following | check")


async def _listings(args, ctx):
    pid = _str(args or {}, "product_id")
    if not pid:
        return _loi("thiếu 'product_id'.")
    d, loi = await _api("GET", f"/v2/order/api/products/{pid}/posts.json")
    return _loi(loi) if loi else _ra(d)


async def _listing_write(args, ctx):
    args = args or {}
    act = _str(args, "action", "create").lower()
    than = _obj(args, "raw_body") or _obj(args, "post") or {}
    if act == "create":
        pid = _str(args, "product_id")
        if not pid:
            return _loi("thiếu 'product_id'.")
        if not than:
            return _loi("thiếu nội dung bài đăng: đưa vào 'post' (object) hoặc 'raw_body'.")
        d, loi = await _api("POST", f"/v2/order/api/products/{pid}/posts.json",
                            body=than if _obj(args, "raw_body") else {"post": than})
        return _loi(loi) if loi else _ra(d)
    if act == "update":
        post_id = _str(args, "post_id")
        if not post_id:
            return _loi("thiếu 'post_id'.")
        if not than:
            return _loi("thiếu nội dung sửa: đưa vào 'post' (object) hoặc 'raw_body'.")
        d, loi = await _api("PUT", f"/v2/order/api/product-posts/{post_id}.json",
                            body=than if _obj(args, "raw_body") else {"post": than})
        return _loi(loi) if loi else _ra(d)
    return _loi("action phải là: create | update. Xoá dùng tool tts_listing_delete.")


async def _listing_delete(args, ctx):
    args = args or {}
    post_id = _str(args, "post_id")
    if not post_id:
        return _loi("thiếu 'post_id'.")
    if not args.get("confirm"):
        return _ra({"se_xoa_bai_dang": post_id, "da_thuc_thi": False},
                   "Đây mới là bản xem trước. Bài đăng xoá rồi không lấy lại được. Gọi lại tool "
                   "này với confirm=true nếu chắc chắn.")
    d, loi = await _api("DELETE", f"/v2/order/api/product-posts/{post_id}.json")
    return _loi(loi) if loi else _ra(d, "Đã xoá bài đăng.")


async def _wishlist(args, ctx):
    args = args or {}
    act = _str(args, "action", "list").lower()
    if act == "list":
        d, loi = await _api("GET", "/v1/marketing/wishlists/products",
                            params={"limit": _int(args, "limit", 50, 1, 200),
                                    "offset": _int(args, "offset", 0, 0, 10000)})
        return _loi(loi) if loi else _ra(d)
    if act == "check":
        pid = _str(args, "product_id")
        if not pid:
            return _loi("thiếu 'product_id'.")
        d, loi = await _api("GET", f"/v1/marketing/wishlists/{pid}/is_added")
        return _loi(loi) if loi else _ra(d)
    return _loi("action phải là: list | check")


async def _wishlist_write(args, ctx):
    args = args or {}
    act = _str(args, "action", "add").lower()
    pid = _str(args, "product_id")
    if not pid:
        return _loi("thiếu 'product_id'.")
    if act == "add":
        d, loi = await _api("POST", "/v1/marketing/wishlists",
                            body={"product_id": pid, "dropship": True})
        return _loi(loi) if loi else _ra(d)
    if act == "remove":
        d, loi = await _api("DELETE", f"/v1/marketing/wishlists/{pid}")
        return _loi(loi) if loi else _ra(d)
    return _loi("action phải là: add | remove")


async def _supplier_follow(args, ctx):
    args = args or {}
    sid = _str(args, "shop_id")
    if not sid:
        return _loi("thiếu 'shop_id'.")
    d, loi = await _api("POST", f"/v1/user/api/shops/{sid}/follow")
    return _loi(loi) if loi else _ra(d, "Sàn dùng chung một đường cho theo dõi và bỏ theo dõi, "
                                        "gọi lại lần nữa là đảo trạng thái. Kiểm bằng "
                                        "tts_suppliers action=check.")


# ============================================================
# NHÓM B: giỏ hàng
# ============================================================
async def _cart(args, ctx):
    d, loi = await _api("GET", "/v2/order/api/carts.json")
    return _loi(loi) if loi else _ra(d, "Giỏ nhóm theo nhà cung cấp. MỖI nhà cung cấp lên MỘT "
                                        "đơn riêng, nên giỏ có 3 shop là phải gọi "
                                        "tts_create_order 3 lần, hoặc một lần với tham số "
                                        "'orders' gồm 3 mục.")


async def _cart_update(args, ctx):
    args = args or {}
    raw = _obj(args, "raw_body")
    if raw is not None:
        than = dict(raw) if isinstance(raw, dict) else {"line_items": raw}
    else:
        dong = _obj(args, "items")
        if not isinstance(dong, list) or not dong:
            return _loi("thiếu 'items': mảng các dòng {product_id, variant_id, quantity, "
                        "dropship_selling_price, properties}. Đặt quantity=0 để xoá một dòng.")
        sach = []
        for it in dong:
            if not isinstance(it, dict):
                return _loi("mỗi mục trong 'items' phải là object.")
            sach.append({**it, "dropship": True})
        # Bản khảo sát bắt được TÊN TRƯỜNG của một dòng nhưng không bắt được tên khoá bọc ngoài,
        # nên gửi kèm cả hai cách gọi tên hay gặp. Server bỏ qua khoá nó không biết; nếu sàn
        # khó tính thì dùng 'raw_body' để gửi đúng hình dạng thật.
        than = {"line_items": sach, "items": sach}
    than.setdefault("dropship", True)
    d, loi = await _api("POST", "/v2/order/api/carts/update.json", body=than)
    return _loi(loi) if loi else _ra(d, "Cùng một đường dùng cho thêm mới, đổi số lượng, đổi giá "
                                        "bán và xoá dòng (quantity=0).")


# ============================================================
# NHÓM C: khách hàng và địa chỉ
# ============================================================
async def _customers(args, ctx):
    args = args or {}
    act = _str(args, "action", "search").lower()

    if act == "list":
        d, loi = await _api("GET", "/v2/order/api/customers.json",
                            params={"limit": _int(args, "limit", 20, 1, 100),
                                    "page": _int(args, "page", 1, 1, 10000)})
        return _loi(loi) if loi else _ra(d)

    if act == "search":
        q = _str(args, "query")
        if not q:
            return _loi("thiếu 'query' (tên hoặc số điện thoại).")
        d, loi = await _api("GET", "/v2/order/api/customers/search.json",
                            params={"query": q, "limit": _int(args, "limit", 20, 1, 100)})
        return _loi(loi) if loi else _ra(d, "Tìm TRƯỚC khi tạo khách mới: sàn không có đường sửa "
                                            "hay xoá khách hàng, nên một khách trùng là trùng "
                                            "vĩnh viễn.")

    if act == "parse_address":
        dc = _str(args, "address")
        if not dc:
            return _loi("thiếu 'address' (dán nguyên đoạn tin nhắn của khách).")
        d, loi = await _api("PUT", "/v2/order/api/address_parse.json", params={"address": dc})
        return _loi(loi) if loi else _ra(d, "Luôn ĐỌC LẠI địa chỉ đã tách cho khách xác nhận "
                                            "trước khi lên đơn. Đơn đã tạo thì không sửa được, "
                                            "sai một chữ là phải huỷ rồi lên lại.")

    if act == "locations":
        d, loi = await _api("GET", "/v1/user/api/vi/locations.json")
        return _loi(loi) if loi else _ra(d)

    return _loi("action phải là: list | search | parse_address | locations")


async def _customer_write(args, ctx):
    args = args or {}
    act = _str(args, "action", "create").lower()
    raw = _obj(args, "raw_body")

    if act == "create":
        if raw is not None:
            than = raw
        else:
            ten, sdt = _str(args, "name"), _str(args, "phone")
            if not ten or not sdt:
                return _loi("thiếu 'name' hoặc 'phone'. Có đoạn địa chỉ thô thì chạy "
                            "tts_customers action=parse_address trước.")
            than = {"customer": {
                "name": ten, "phone": sdt,
                "address1": _str(args, "address1"),
                "province": _str(args, "province"),
                "city": _str(args, "district") or _str(args, "city"),
                "ward": _str(args, "ward"),
                "dropship": True,
            }}
        d, loi = await _api("POST", "/v2/order/api/customers.json", body=than)
        return _loi(loi) if loi else _ra(d, "Sàn KHÔNG có đường sửa hay xoá khách hàng. Sai thì "
                                            "chỉ có cách thêm một địa chỉ mới rồi đặt làm mặc "
                                            "định (action=add_address rồi set_default).")

    if act == "add_address":
        cid = _str(args, "customer_id")
        if not cid:
            return _loi("thiếu 'customer_id'.")
        if raw is not None:
            than = raw
        else:
            than = {"address": {
                "name": _str(args, "name"), "phone": _str(args, "phone"),
                "address1": _str(args, "address1"),
                "province": _str(args, "province"),
                "city": _str(args, "district") or _str(args, "city"),
                "ward": _str(args, "ward"),
                "dropship": True,
            }}
        d, loi = await _api("POST", f"/v2/order/api/customers/{cid}/addresses.json", body=than)
        return _loi(loi) if loi else _ra(d)

    if act == "set_default":
        cid, aid = _str(args, "customer_id"), _str(args, "address_id")
        if not cid or not aid:
            return _loi("thiếu 'customer_id' hoặc 'address_id'.")
        d, loi = await _api(
            "PUT", f"/v2/order/api/customers/{cid}/addresses/{aid}/default.json")
        return _loi(loi) if loi else _ra(d)

    return _loi("action phải là: create | add_address | set_default")


# ============================================================
# NHÓM D: vận chuyển và tạo đơn
# ============================================================
async def _shipping_rates(args, ctx):
    args = args or {}
    if _obj(args, "raw_body") is not None:
        than = _obj(args, "raw_body")
    else:
        dest = _obj(args, "destination")
        items = _obj(args, "items")
        sid = _str(args, "shop_id")
        if not isinstance(dest, dict) or not dest:
            return _loi("thiếu 'destination': {province, city, ward, address1}.")
        if not isinstance(items, list) or not items:
            return _loi("thiếu 'items': mảng {product_id, variant_id, quantity}.")
        if not sid:
            return _loi("thiếu 'shop_id' (kho gửi hàng, tức nhà cung cấp).")
        than = {"rate": {"destination": dest,
                         "items": [{**i, "dropship": True} for i in items if isinstance(i, dict)],
                         "origin": {"shop_id": sid}}}
    d, loi = await _api("POST", "/v2/order/api/shipping_rates.json", body=than)
    return _loi(loi) if loi else _ra(d, "Lấy nguyên một mục trong data.rates để đưa vào "
                                        "'shipping_lines' của tts_create_order.")


def _chuan_don(o: dict) -> dict:
    """Dựng thân một đơn từ tham số của model. Không chạm mạng."""
    if not isinstance(o, dict):
        return {}
    if isinstance(o.get("raw_body"), dict):
        return o["raw_body"]
    don = {"dropship": True}
    for k in ("shop_id", "cart_token", "note", "payment_method", "source_name",
              "source_identifier", "discount_codes", "shipping_lines", "shipping"):
        if o.get(k) not in (None, "", [], {}):
            don[k] = o[k]
    don.setdefault("payment_method", "COD")
    dong = o.get("line_items")
    if isinstance(dong, list):
        don["line_items"] = [{**i, "dropship": True} for i in dong if isinstance(i, dict)]
    # Đơn từ sàn khác (TikTok Shop...) không có khách của TTS: sàn nhận customer=null kèm
    # source_identifier và một khối shipping kiểu marketplace.
    if o.get("marketplace") or o.get("source_identifier"):
        don["customer"] = None
        don.setdefault("shipping", {"method": "marketplace", "pay_by": "buyer", "price": 0,
                                    "carrier": str(o.get("marketplace") or "marketplace")})
    else:
        if o.get("customer_id"):
            don["customer"] = {"id": str(o["customer_id"])}
        if isinstance(o.get("shipping_address"), dict):
            don["shipping_address"] = o["shipping_address"]
    return don


def _xem_truoc(don: dict) -> dict:
    """Tóm tắt một đơn để người đọc kiểm trước khi bấm thật. Chỉ đọc thân request."""
    dong = don.get("line_items") or []
    tong_ban = 0.0
    tong_von = 0.0
    tom = []
    for i in dong:
        sl = float(i.get("quantity") or 0)
        ban = float(i.get("dropship_selling_price") or 0)
        von = float(i.get("dropship_price") or 0)
        tong_ban += sl * ban
        tong_von += sl * von
        tom.append({"variant_id": i.get("variant_id"), "product_id": i.get("product_id"),
                    "so_luong": sl, "gia_ban": _tien(ban),
                    "gia_ncc": _tien(von) if von else "chưa truyền vào"})
    ship = 0.0
    for s in (don.get("shipping_lines") or []):
        if isinstance(s, dict):
            try:
                ship += float(s.get("total_price") or s.get("price") or 0)
            except (TypeError, ValueError):
                pass
    ra = {
        "shop_id": don.get("shop_id"),
        "khach": (don.get("customer") or {}).get("id") if isinstance(don.get("customer"), dict)
                 else ("đơn từ sàn khác" if don.get("source_identifier") else "CHƯA CÓ"),
        "dia_chi_nhan": don.get("shipping_address") or don.get("shipping") or "CHƯA CÓ",
        "so_dong_hang": len(dong),
        "hang": tom,
        "tong_tien_khach_tra": _tien(tong_ban),
        "phi_van_chuyen_trong_don": _tien(ship),
        "thanh_toan": don.get("payment_method"),
        "ghi_chu_cho_nguoi_ban": don.get("note") or "",
    }
    if tong_von:
        ra["tong_gia_ncc"] = _tien(tong_von)
        ra["lai_gop_uoc_tinh"] = _tien(tong_ban - tong_von)
        ra["_luu_y_lai"] = ("Đây là lãi gộp theo giá bạn truyền vào, CHƯA trừ phí vận chuyển bạn "
                            "tài trợ và CHƯA cộng thưởng khuyến mãi của shop. Số thật xem ở "
                            "tts_orders action=get sau khi đơn đã tạo.")
    return ra


async def _create_order(args, ctx):
    args = args or {}
    nhieu = _obj(args, "orders")
    danh_sach = nhieu if isinstance(nhieu, list) else [args]
    don_list = []
    for o in danh_sach:
        don = _chuan_don(o if isinstance(o, dict) else {})
        if not don.get("shop_id"):
            return _loi("mỗi đơn phải có 'shop_id'. Một nhà cung cấp là MỘT đơn riêng, giỏ nhiều "
                        "shop thì truyền mảng 'orders'.")
        if not don.get("line_items"):
            return _loi(f"đơn của shop {don.get('shop_id')} chưa có 'line_items'.")
        if not don.get("customer") and not don.get("shipping_address") \
                and not don.get("source_identifier"):
            return _loi(f"đơn của shop {don.get('shop_id')} chưa có người nhận. Truyền "
                        "'customer_id' (tìm bằng tts_customers) hoặc 'shipping_address', hoặc "
                        "'marketplace' + 'source_identifier' cho đơn từ sàn khác.")
        don_list.append(don)

    if not args.get("confirm"):
        return _ra({"so_don_se_tao": len(don_list),
                    "cac_don": [_xem_truoc(d) for d in don_list],
                    "da_thuc_thi": False},
                   "ĐÂY MỚI LÀ BẢN XEM TRƯỚC, chưa có đơn nào được tạo. Đọc lại cho khách xác "
                   "nhận tên, số điện thoại, địa chỉ, hàng và giá. Đơn đã tạo thì sàn KHÔNG cho "
                   "sửa, chỉ có huỷ rồi lên lại. Đồng ý rồi thì gọi lại đúng tool này với "
                   "confirm=true.")

    ket_qua = []
    for don in don_list:
        d, loi = await _api("POST", "/v2/order/api/orders.json", body={"order": don})
        if loi:
            ket_qua.append({"shop_id": don.get("shop_id"), "ok": False, "loi": loi})
            continue
        tao = (d or {}).get("data", {}).get("order") if isinstance(d, dict) else None
        ket_qua.append({"shop_id": don.get("shop_id"), "ok": True,
                        "ma_don": (tao or {}).get("name") or (tao or {}).get("id"),
                        "order": tao or d})
    xong = sum(1 for k in ket_qua if k["ok"])
    return _ra({"da_thuc_thi": True, "thanh_cong": xong, "that_bai": len(ket_qua) - xong,
                "chi_tiet": ket_qua},
               ("Tất cả các đơn đã tạo." if xong == len(ket_qua) else
                "MỘT SỐ ĐƠN LỖI. Các đơn đã tạo là THẬT và không tự huỷ, đừng gọi lại cả lượt: "
                "chỉ lên lại đúng những shop báo ok=false."))


async def _cancel_order(args, ctx):
    args = args or {}
    oid = _str(args, "order_id")
    ly_do = _str(args, "reason")
    if not oid:
        return _loi("thiếu 'order_id'.")
    if not ly_do:
        return _loi("thiếu 'reason' (lý do huỷ, sàn bắt buộc).")
    if not args.get("confirm"):
        return _ra({"se_huy_don": oid, "ly_do": ly_do, "da_thuc_thi": False},
                   "Đây mới là bản xem trước. Huỷ đơn là dứt điểm, không khôi phục lại được, và "
                   "huỷ nhiều lần ảnh hưởng uy tín tài khoản bán. Gọi lại với confirm=true nếu "
                   "chắc chắn.")
    d, loi = await _api("POST", f"/v2/order/api/orders/{oid}/confirmed_cancelled.json",
                        body={"order": {"id": oid, "confirm_status": "cancelled",
                                        "confirm_cancelled_reason": ly_do}})
    return _loi(loi) if loi else _ra(d, "Đã gửi yêu cầu huỷ.")


# ============================================================
# NHÓM E: đơn hàng
# ============================================================
_TRANG_THAI = ("any", "wait_confirm", "wait_checkout", "wait_pickup", "in_transit",
               "delivered", "wait_rate", "rated", "cancelled")


async def _orders(args, ctx):
    args = args or {}
    act = _str(args, "action", "list").lower()

    if act == "list":
        tt = _str(args, "buyer_status", "any")
        if tt not in _TRANG_THAI:
            return _loi("buyer_status phải là một trong: " + ", ".join(_TRANG_THAI))
        qs = _build_qs({"dropship": "true", "buyer_status": tt,
                        "page": _int(args, "page", 1, 1, 10000),
                        "limit": _int(args, "limit", 20, 1, 100),
                        "query": _str(args, "query")})
        d, loi = await _gql(ctx, "OrderListQuery", {"query": qs})
        return _loi(loi) if loi else _ra(d)

    oid = _str(args, "order_id")
    if act == "get":
        if not oid:
            return _loi("thiếu 'order_id'.")
        d, loi = await _api("GET", f"/v2/order/api/orders/{oid}.json")
        return _loi(loi) if loi else _ra(d, "Trong đây có khối hoa hồng: tổng giá bán, tổng giá "
                                            "NCC, lợi nhuận bán hàng, tổng thưởng, phí vận "
                                            "chuyển bạn tài trợ và tổng lợi nhuận. Đây mới là "
                                            "lãi THẬT của đơn.")
    if act == "tracking":
        if not oid:
            return _loi("thiếu 'order_id'.")
        d, loi = await _api("GET", f"/v2/order/api/orders/{oid}/fulfillment_events.json")
        return _loi(loi) if loi else _ra(d)

    return _loi("action phải là: list | get | tracking")


async def _order_action(args, ctx):
    args = args or {}
    act = _str(args, "action").lower()
    oid = _str(args, "order_id")
    if not oid:
        return _loi("thiếu 'order_id'.")

    if act == "rate":
        diem = _int(args, "rating", 5, 1, 5)
        noi_dung = _str(args, "content")
        if not args.get("confirm"):
            return _ra({"se_danh_gia_don": oid, "so_sao": diem, "noi_dung": noi_dung,
                        "da_thuc_thi": False},
                       "Đây mới là bản xem trước. Đánh giá chỉ gửi được MỘT LẦN, không sửa được, "
                       "và nó mở khoá tiền đối soát của đơn. Gọi lại với confirm=true nếu chắc "
                       "chắn.")
        than = _obj(args, "raw_body") or {"order_id": oid, "rating": diem, "content": noi_dung}
        d, loi = await _api("POST", "/v1/review/ratings", body=than)
        return _loi(loi) if loi else _ra(d, "Đã gửi đánh giá.")

    if act == "ticket":
        mo_ta = _str(args, "description")
        loai = _str(args, "type", "other")
        if not mo_ta:
            return _loi("thiếu 'description' (mô tả vấn đề của đơn).")
        if not args.get("confirm"):
            return _ra({"se_mo_khieu_nai_cho_don": oid, "loai": loai, "mo_ta": mo_ta,
                        "da_thuc_thi": False},
                       "Đây mới là bản xem trước. Khiếu nại gửi đi là sàn và nhà cung cấp đều "
                       "thấy. Gọi lại với confirm=true nếu chắc chắn. Gói này chưa gửi kèm được "
                       "ảnh, cần ảnh thì mở khiếu nại thẳng trên web của sàn.")
        d, loi = await _api("POST", "/v2/order/api/orders-tickets.json",
                            multipart={"order_id": oid, "type": loai, "description": mo_ta})
        return _loi(loi) if loi else _ra(d, "Đã mở khiếu nại.")

    return _loi("action phải là: rate | ticket. Huỷ đơn dùng tool tts_cancel_order.")


# ============================================================
# NHÓM G: tài chính và tài khoản
# ============================================================
_TAI_CHINH = {
    "income": ("GET", "/v1/pay/api/finance/income_summary"),
    "wallet": ("GET", "/v1/pay/api/finance/wallet_transactions"),
    "wallet_types": ("GET", "/v1/pay/api/finance/transaction_types"),
    "escrow": ("GET", "/v1/pay/api/finance/escrow/transactions"),
    "escrow_statuses": ("GET", "/v1/pay/api/finance/escrow/transaction_statuses"),
    "tax": ("GET", "/v1/pay/api/finance/tax_payments"),
    "banks": ("GET", "/v1/pay/api/banks/my_accounts"),
    "withdraw_fee": ("GET", "/v1/pay/api/wallet/withdrawal_fee"),
    "affiliate": ("GET", "/v1/marketing/api/affiliate/statistic"),
    "profile": ("GET", "/v1/user/dropship-profile/me"),
    "streak": ("GET", "/v2/order/api/user/streak/stats.json"),
    "notifications": ("GET", "/v3/notification/notifications/count_unseen"),
}


async def _finance(args, ctx):
    args = args or {}
    act = _str(args, "action", "income").lower()
    if act not in _TAI_CHINH:
        return _loi("action phải là một trong: " + " | ".join(sorted(_TAI_CHINH)))
    method, path = _TAI_CHINH[act]
    p = {}
    for k in ("limit", "page", "offset", "from", "to", "status", "month", "year"):
        if _str(args, k):
            p[k] = _str(args, k)
    d, loi = await _api(method, path, params=p or None)
    if loi:
        return _loi(loi)
    ghi = ""
    if act == "withdraw_fee":
        ghi = ("Gói này CỐ Ý không có tool rút tiền. Muốn rút thì vào ví trên web của sàn, "
               "Javis chỉ đọc biểu phí.")
    elif act == "escrow":
        ghi = "Tiền theo từng đơn kèm ngày giải ngân. Đơn chưa đánh giá thì tiền còn bị giữ."
    return _ra(d, ghi)


# ============================================================
# Chẩn đoán
# ============================================================
_SOI = [
    ("hồ sơ tài khoản", "GET", "/v1/user/dropship-profile/me"),
    ("tiền chờ đối soát", "GET", "/v1/pay/api/finance/income_summary"),
    ("giỏ hàng", "GET", "/v2/order/api/carts.json"),
    ("danh mục tỉnh/quận/phường", "GET", "/v1/user/api/vi/locations.json"),
    ("trang chủ gợi ý", "GET", "/v2/order/api/collection_screens/home.json"),
]


async def _health(args, ctx):
    conn = _conn()
    if not conn:
        return _loi(_check())
    tok = (_secrets(conn).get("access_token") or "").strip()
    con_lai = _con_lai(tok)
    bao = {
        "ket_noi": conn.get("label") or conn.get("id"),
        "muc_quyen_ket_noi": conn.get("perm"),
        # Thứ tự ở đây có nghĩa: `_con_lai` trả -1 cho token KHÔNG ĐỌC ĐƯỢC HẠN, và -1 cũng
        # thoả `<= 0`. Hỏi "không đọc được" trước thì mới không báo nhầm một token dán thiếu
        # thành "hết hạn", vì hai bệnh đó chữa bằng hai cách khác nhau.
        "token_con_lai": ("không đọc được hạn" if con_lai == -1 else
                          "hết hạn" if con_lai <= 0 else f"khoảng {con_lai // 3600} giờ"),
        "tu_gia_han": bool((_secrets(conn).get("refresh_url") or "").strip()),
        "rest": {},
        "graphql": {},
    }
    for ten, method, path in _SOI:
        d, loi = await _api(method, path)
        bao["rest"][ten] = "ok" if not loi else f"LỖI: {loi[:180]}"
    docs = _gql_docs(ctx)
    thu = {"searchProductsQuery": {"query": _build_qs(
        {"filter_only_dropship": "true", "keyword": "áo", "limit": 1, "offset": 0,
         "sort_by": "_score"})},
        "OrderListQuery": {"query": _build_qs(
            {"dropship": "true", "buyer_status": "any", "page": 1, "limit": 1})}}
    for op, bien in thu.items():
        if op not in docs:
            bao["graphql"][op] = "thiếu tài liệu truy vấn"
            continue
        d, loi = await _gql(ctx, op, bien)
        bao["graphql"][op] = "ok" if not loi else f"LỖI: {loi[:220]}"
    hong = [k for k, v in {**bao["rest"], **bao["graphql"]}.items() if str(v).startswith("LỖI")]
    return _ra(bao, ("Mọi đường đều sống." if not hong else
                     "Đường hỏng: " + ", ".join(hong) + ". Lỗi GraphQL gần như luôn là tên trường "
                     "trong graphql.json không còn khớp sàn, sửa bằng tool tts_graphql "
                     "(action=set) chứ không phải cài lại gói. Lỗi REST 401/403 là token hết hạn. "
                     "Đây là API nội bộ của sàn, không có tài liệu chính thức và sàn có thể đổi "
                     "bất cứ lúc nào."))


async def _graphql_doc(args, ctx):
    args = args or {}
    act = _str(args, "action", "show").lower()
    de = ctx.data_dir / "graphql.json"

    if act == "show":
        return _ra({"dang_dung": _gql_docs(ctx),
                    "co_ban_sua_rieng": de.exists(),
                    "duong_dan_ban_sua": str(de)},
                   "Sửa bằng action=set (truyền 'operation' và 'doc', kèm 'root' nếu tên trường "
                   "gốc đổi). Quay về mặc định của gói bằng action=reset.")

    if act == "introspect":
        ten_type = _str(args, "type")
        if not ten_type:
            return _loi("thiếu 'type': tên type lấy nguyên văn trong câu lỗi của sàn, ví dụ "
                        "ProductSearchResponse trong 'Cannot query field \"id\" on type "
                        "\"ProductSearchResponse\"'.")
        truong, loi = await _introspect(ten_type)
        if loi:
            return _loi(loi)
        return _ra({"type": ten_type, "truong": truong},
                   "Đây là hình dạng THẬT sàn đang khai, không phải suy đoán. Trường nào có "
                   "'[]' là danh sách, và thường chính nó là chỗ dữ liệu đã chuyển vào. Ghi lại "
                   "truy vấn bằng action=set.")

    if act == "set":
        op = _str(args, "operation")
        doc = _str(args, "doc")
        if not op or not doc:
            return _loi("thiếu 'operation' hoặc 'doc'.")
        if "query" not in doc and "mutation" not in doc:
            return _loi("'doc' phải là một tài liệu GraphQL (bắt đầu bằng query hoặc mutation).")
        patch = {"doc": doc}
        if _str(args, "root"):
            patch["root"] = _str(args, "root")
        vi_sao = _ghi_doc(ctx, op, patch)
        if vi_sao:
            return _loi(f"không ghi được bản sửa: {vi_sao}")
        return _ra({"da_luu": op, "duong_dan": str(de)},
                   "Bản sửa này nằm ngoài gói nên bản cập nhật gói sau đó không xoá mất. Chạy "
                   "tts_health_check để xem truy vấn đã chạy chưa.")

    if act == "reset":
        try:
            if de.exists():
                de.unlink()
        except Exception as e:
            return _loi(f"không xoá được bản sửa: {type(e).__name__}: {e}")
        return _ra({"da_quay_ve_mac_dinh": True})

    return _loi("action phải là: show | introspect | set | reset")


# ============================================================
# Đăng ký
# ============================================================
def _t(ctx, name, desc, handler, min_mode, emoji, props, required=None):
    ctx.register_tool(name=name, description=desc, handler=handler, min_mode=min_mode,
                      emoji=emoji,
                      schema={"type": "object", "properties": props,
                              "required": list(required or [])},
                      check_fn=_check)


def register(ctx):
    _t(ctx, "tts_products",
       "TTS Dropship: tìm và đọc sản phẩm để bán dropship. action=search (từ khoá, lọc, sắp xếp "
       "theo dropship_profit để ra hàng lãi cao) | get (chi tiết 1 sản phẩm kèm biến thể và giá "
       "từng bậc) | reviews | recent (hàng vừa xem) | collections (khối gợi ý trang chủ). "
       "dropship_price là giá trả nhà cung cấp, dropship_profit là lãi gợi ý.",
       _products, "readonly", "🔎", {
           "action": {"type": "string", "enum": ["search", "get", "reviews", "recent", "collections"],
                      "description": "Mặc định search"},
           "keyword": {"type": "string", "description": "Từ khoá tìm (action=search)"},
           "product_id": {"type": "string", "description": "Bắt buộc với action=get"},
           "shop_id": {"type": "string", "description": "Lọc theo nhà cung cấp"},
           "collection_id": {"type": "string", "description": "Bỏ trống thì lấy trang chủ"},
           "sort_by": {"type": "string",
                       "enum": ["_score", "created_at", "total_sales", "dropship_profit", "price"],
                       "description": "Mặc định _score. dropship_profit = lãi cao nhất trước"},
           "ascending": {"type": "boolean", "description": "Sắp xếp tăng dần"},
           "category_lv1": {"type": "string"}, "category_lv2": {"type": "string"},
           "province": {"type": "string", "description": "Lọc theo tỉnh của kho hàng"},
           "limit": {"type": "integer", "description": "Mặc định 20, tối đa 100"},
           "offset": {"type": "integer"},
       })

    _t(ctx, "tts_suppliers",
       "TTS Dropship: nhà cung cấp. action=search | get (hồ sơ shop) | promotions (freeship và "
       "thưởng theo đơn, PHẢI cộng vào mới ra lợi nhuận thật) | following | check (đang theo dõi "
       "shop này chưa).",
       _suppliers, "readonly", "🏪", {
           "action": {"type": "string",
                      "enum": ["search", "get", "promotions", "following", "check"]},
           "shop_id": {"type": "string"}, "keyword": {"type": "string"},
           "province": {"type": "string"},
           "limit": {"type": "integer"}, "offset": {"type": "integer"},
       })

    _t(ctx, "tts_cart",
       "TTS Dropship: xem giỏ hàng, đã nhóm sẵn theo nhà cung cấp. Mỗi nhà cung cấp sẽ thành một "
       "đơn riêng khi lên đơn.",
       _cart, "readonly", "🧺", {})

    _t(ctx, "tts_customers",
       "TTS Dropship: khách hàng và địa chỉ (chỉ đọc). action=search (theo tên hoặc số điện "
       "thoại, LUÔN chạy trước khi tạo khách mới) | list | parse_address (dán nguyên đoạn tin "
       "nhắn của khách, trả về tỉnh, quận, phường, số nhà đã tách) | locations.",
       _customers, "readonly", "👤", {
           "action": {"type": "string",
                      "enum": ["search", "list", "parse_address", "locations"]},
           "query": {"type": "string", "description": "Tên hoặc số điện thoại (action=search)"},
           "address": {"type": "string",
                       "description": "Đoạn địa chỉ thô của khách (action=parse_address)"},
           "limit": {"type": "integer"}, "page": {"type": "integer"},
       })

    _t(ctx, "tts_shipping_rates",
       "TTS Dropship: báo giá vận chuyển cho một đơn dự kiến. Trả về danh sách hãng vận chuyển "
       "kèm service_code và phí. Lấy nguyên một mục trong data.rates đưa vào 'shipping_lines' của "
       "tts_create_order.",
       _shipping_rates, "readonly", "🚚", {
           "destination": {"type": "object",
                           "description": "{province, city, ward, address1} - lấy từ parse_address"},
           "items": {"type": "array", "items": {"type": "object"},
                     "description": "[{product_id, variant_id, quantity}]"},
           "shop_id": {"type": "string", "description": "Kho gửi hàng, tức nhà cung cấp"},
           "raw_body": {"type": "object", "description": "Đè nguyên thân request khi sàn đổi hình dạng"},
       })

    _t(ctx, "tts_orders",
       "TTS Dropship: đọc đơn hàng. action=list (lọc theo buyer_status: wait_confirm, wait_pickup, "
       "in_transit, delivered, cancelled...) | get (chi tiết 1 đơn KÈM khối hoa hồng, đây mới là "
       "lãi thật) | tracking (nhật ký hành trình và mã vận đơn). Chỉ đọc, không đổi gì.",
       _orders, "readonly", "📦", {
           "action": {"type": "string", "enum": ["list", "get", "tracking"]},
           "order_id": {"type": "string"},
           "buyer_status": {"type": "string", "enum": list(_TRANG_THAI),
                            "description": "Mặc định any"},
           "query": {"type": "string", "description": "Từ khoá: mã đơn hoặc tên khách"},
           "page": {"type": "integer"}, "limit": {"type": "integer"},
       })

    _t(ctx, "tts_finance",
       "TTS Dropship: tiền và tài khoản. action=income (tiền chờ đối soát, thuế tạm giữ, tiền rút "
       "được) | escrow (tiền theo từng đơn kèm ngày giải ngân) | wallet | tax | banks | "
       "withdraw_fee | affiliate | profile | streak | notifications. Không có đường rút tiền.",
       _finance, "readonly", "💰", {
           "action": {"type": "string", "enum": sorted(_TAI_CHINH)},
           "limit": {"type": "string"}, "page": {"type": "string"},
           "from": {"type": "string"}, "to": {"type": "string"},
           "month": {"type": "string"}, "year": {"type": "string"},
       })

    _t(ctx, "tts_listings",
       "TTS Dropship: xem các bài đăng bán (nội dung 'Đăng bán' của sàn) đã soạn cho một sản "
       "phẩm. Đọc trước khi viết bài mới để khỏi trùng nội dung. Soạn bài mới bằng "
       "tts_listing_write.",
       _listings, "readonly", "📝", {"product_id": {"type": "string"}}, ["product_id"])

    _t(ctx, "tts_wishlist",
       "TTS Dropship: sản phẩm đã lưu. action=list | check (sản phẩm này đã lưu chưa).",
       _wishlist, "readonly", "⭐", {
           "action": {"type": "string", "enum": ["list", "check"]},
           "product_id": {"type": "string"},
           "limit": {"type": "integer"}, "offset": {"type": "integer"},
       })

    _t(ctx, "tts_health_check",
       "TTS Dropship: kiểm tra kết nối còn sống không. Gọi thử vài đường đọc của sàn và hai truy "
       "vấn GraphQL, báo đường nào hỏng, kèm hạn còn lại của token. Chạy tool này trước khi kết "
       "luận là sàn đổi API.",
       _health, "readonly", "🩺", {})

    _t(ctx, "tts_cart_update",
       "TTS Dropship: sửa giỏ hàng. Cùng một đường dùng cho thêm mới, đổi số lượng, đổi giá bán "
       "và xoá dòng (đặt quantity=0).",
       _cart_update, "safe", "🧺", {
           "items": {"type": "array", "items": {"type": "object"},
                     "description": "[{product_id, variant_id, quantity, dropship_selling_price, properties}]"},
           "raw_body": {"type": "object", "description": "Đè nguyên thân request khi sàn đổi hình dạng"},
       })

    _t(ctx, "tts_customer_write",
       "TTS Dropship: tạo khách hàng và địa chỉ. action=create | add_address | set_default. "
       "LUÔN chạy tts_customers action=search trước để khỏi tạo trùng: sàn KHÔNG có đường sửa "
       "hay xoá khách hàng, sai địa chỉ thì chỉ thêm được địa chỉ mới rồi đặt làm mặc định.",
       _customer_write, "safe", "👤", {
           "action": {"type": "string", "enum": ["create", "add_address", "set_default"]},
           "name": {"type": "string", "description": "Tên người nhận"},
           "phone": {"type": "string"},
           "address1": {"type": "string", "description": "Số nhà, đường"},
           "province": {"type": "string", "description": "Tỉnh/thành"},
           "district": {"type": "string", "description": "Quận/huyện"},
           "ward": {"type": "string", "description": "Phường/xã"},
           "customer_id": {"type": "string"}, "address_id": {"type": "string"},
           "raw_body": {"type": "object"},
       })

    _t(ctx, "tts_listing_write",
       "TTS Dropship: soạn bài đăng bán cho một sản phẩm. action=create (cần product_id) | "
       "update (cần post_id). Nội dung đưa vào tham số 'post'.",
       _listing_write, "safe", "📝", {
           "action": {"type": "string", "enum": ["create", "update"]},
           "product_id": {"type": "string"}, "post_id": {"type": "string"},
           "post": {"type": "object", "description": "Nội dung bài đăng"},
           "raw_body": {"type": "object"},
       })

    _t(ctx, "tts_wishlist_write",
       "TTS Dropship: lưu hoặc bỏ lưu một sản phẩm. action=add | remove.",
       _wishlist_write, "safe", "⭐", {
           "action": {"type": "string", "enum": ["add", "remove"]},
           "product_id": {"type": "string"},
       }, ["product_id"])

    _t(ctx, "tts_supplier_follow",
       "TTS Dropship: theo dõi một nhà cung cấp. Sàn dùng chung một đường cho theo dõi và bỏ "
       "theo dõi, gọi lại lần nữa là đảo trạng thái.",
       _supplier_follow, "safe", "🏪", {"shop_id": {"type": "string"}}, ["shop_id"])

    _t(ctx, "tts_graphql",
       "TTS Dropship: xem, SOI LƯỢC ĐỒ và sửa tài liệu truy vấn GraphQL của gói. Dùng khi sàn "
       "đổi hình dạng kết quả. action=show | introspect (cần 'type' lấy từ câu lỗi, hỏi thẳng "
       "sàn xem type đó có trường gì THẬT) | set (cần operation và doc) | reset. Bản sửa nằm "
       "ngoài gói nên bản cập nhật gói không xoá mất.",
       _graphql_doc, "safe", "🛠️", {
           "action": {"type": "string", "enum": ["show", "introspect", "set", "reset"]},
           "type": {"type": "string",
                    "description": "Tên type GraphQL cần soi, ví dụ ProductSearchResponse"},
           "operation": {"type": "string",
                         "enum": ["searchProductsQuery", "ProductById", "searchShopQuery",
                                  "OrderListQuery"]},
           "doc": {"type": "string", "description": "Tài liệu GraphQL đầy đủ"},
           "root": {"type": "string", "description": "Tên trường gốc trong kết quả"},
       })

    _t(ctx, "tts_create_order",
       "TTS Dropship: TẠO ĐƠN HÀNG THẬT trên sàn. Hai bước bắt buộc: gọi lần đầu KHÔNG có confirm "
       "để lấy bản xem trước (khách, hàng, giá, phí ship, lãi ước tính), đọc lại cho khách xác "
       "nhận, rồi gọi lại với confirm=true. Sàn KHÔNG cho sửa đơn đã tạo, sai là phải huỷ rồi lên "
       "lại. Mỗi nhà cung cấp là một đơn riêng: giỏ nhiều shop thì truyền mảng 'orders'.",
       _create_order, "full", "🧾", {
           "shop_id": {"type": "string", "description": "Nhà cung cấp của đơn này"},
           "customer_id": {"type": "string", "description": "Lấy từ tts_customers"},
           "shipping_address": {"type": "object"},
           "line_items": {"type": "array", "items": {"type": "object"},
                          "description": "[{product_id, variant_id, quantity, dropship_selling_price, properties}]"},
           "shipping_lines": {"type": "array", "items": {"type": "object"},
                              "description": "Lấy nguyên từ tts_shipping_rates"},
           "payment_method": {"type": "string", "description": "Mặc định COD"},
           "note": {"type": "string", "description": "Ghi chú cho người bán"},
           "discount_codes": {"type": "array", "items": {"type": "object"},
                              "description": "[{price_rule: id}]"},
           "cart_token": {"type": "string"}, "source_name": {"type": "string"},
           "marketplace": {"type": "string",
                           "description": "Đơn từ sàn khác, ví dụ TikTok Shop"},
           "source_identifier": {"type": "string", "description": "Mã đơn bên sàn kia"},
           "orders": {"type": "array", "items": {"type": "object"},
                      "description": "Nhiều đơn một lượt, mỗi mục là một shop"},
           "confirm": {"type": "boolean",
                       "description": "Để trống hoặc false = chỉ xem trước. true = tạo đơn THẬT"},
           "raw_body": {"type": "object"},
       })

    _t(ctx, "tts_cancel_order",
       "TTS Dropship: HUỶ một đơn. Hai bước: gọi lần đầu không có confirm để xem trước, rồi gọi "
       "lại với confirm=true. Huỷ là dứt điểm, không khôi phục lại được.",
       _cancel_order, "full", "🚫", {
           "order_id": {"type": "string"},
           "reason": {"type": "string", "description": "Lý do huỷ, sàn bắt buộc"},
           "confirm": {"type": "boolean"},
       }, ["order_id", "reason"])

    _t(ctx, "tts_order_action",
       "TTS Dropship: hành động trên một đơn đã giao. action=rate (đánh giá, chỉ gửi được một "
       "lần và nó mở khoá tiền đối soát) | ticket (mở khiếu nại với sàn). Cả hai đều bắt "
       "confirm=true ở lần gọi thứ hai.",
       _order_action, "full", "⭐", {
           "action": {"type": "string", "enum": ["rate", "ticket"]},
           "order_id": {"type": "string"},
           "rating": {"type": "integer", "description": "1 tới 5 sao (action=rate)"},
           "content": {"type": "string", "description": "Nội dung đánh giá"},
           "description": {"type": "string", "description": "Mô tả vấn đề (action=ticket)"},
           "type": {"type": "string", "description": "Loại khiếu nại"},
           "confirm": {"type": "boolean"},
           "raw_body": {"type": "object"},
       }, ["action", "order_id"])

    _t(ctx, "tts_listing_delete",
       "TTS Dropship: xoá một bài đăng bán. Bắt confirm=true ở lần gọi thứ hai.",
       _listing_delete, "full", "🗑️", {
           "post_id": {"type": "string"}, "confirm": {"type": "boolean"},
       }, ["post_id"])
