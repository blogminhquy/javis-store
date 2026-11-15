"""Javis Gate: cổng cho ChatGPT, Claude và AI khác làm việc cùng Javis qua một MCP connector.

Vì sao là một gói, không phải tính năng có sẵn
----------------------------------------------
Bản 0.64.21 đưa cửa này vào lõi Javis. Chủ repo gỡ nó ở 0.64.26 vì nó đi ngược nguyên tắc của
Javis: mọi cuộc chat nằm chung MỘT nền tảng. Ai vẫn muốn chat trên chatgpt.com hay claude.ai bằng
gói của mình thì cài gói này, và việc cài là lựa chọn có chủ ý của họ.

2.0.0 (Javis 0.440.0): tên mới Javis Gate (mã cũ `javis.javis-trong-chatgpt`, plugin
`javis-trong-chatgpt`), thêm Claude, và bốn tool chat lõi Javis cấp riêng cho cửa này: AI bên ngoài
đọc hội thoại của Javis và nhắn THẲNG vào khung chat như chủ gõ, thay vì giao việc qua Kanban. Lượt
đó chạy trên Javis như mọi lượt chat: chủ xem chữ chạy, bấm Dừng được, Javis học được.

2.2.0: lõi Javis cho cửa này chọn model (ghim cho hội thoại) và effort (chỉ cho một tin) trong câu lệnh, và báo chủ
qua chuông khi việc AI bên ngoài giao xong mà nó đã thôi chờ. Mọi AI đi qua cửa đều có, không riêng ChatGPT.

Cũng 2.2.0 (chủ dự án 14/11: "không chỉ ChatGPT, các công cụ AI khác nối vào cũng được như vậy"), hai đường nối
cho công cụ ngoài ChatGPT và Claude, cả hai do CHỦ mở trên trang của gói (trang đòi đăng nhập dashboard):
  - Công cụ xin nối bằng OAuth với địa chỉ quay về lạ thì bị từ chối như cũ, nhưng địa chỉ đó hiện trên trang
    của gói ("đang xin kết nối"); chủ bấm Cho phép là địa chỉ đó vào danh sách riêng của máy này.
  - Khoá riêng (`jg_...`) cho công cụ chỉ biết dán header Authorization (n8n, script, vài trình soạn mã). Mỗi khoá
    một tên, là tên hiện trên khung chat ("từ Cursor"), chỉ lưu dạng băm, thu hồi từng khoá được.

Cách chạy
---------
Người dùng chat trên chatgpt.com hay claude.ai như bình thường, còn AI đó tự gọi sang Javis qua
tính năng MCP connector chính thức. Javis vốn đã là một MCP server đầy đủ (hub mà Claude Code và
Codex dùng); gói này chỉ thêm CỬA: OAuth 2.1 tối giản, đúng phần các AI đó dùng.

  - khám phá: RFC 9728 (protected resource) + RFC 8414 (authorization server)
  - đăng ký client động: RFC 7591 (ChatGPT tự đăng ký, không phải dán client id)
  - mã cấp quyền + PKCE S256 (bắt buộc, không nhận `plain`)
  - làm mới token có XOAY vòng (dùng lại refresh cũ là thu hồi cả họ), thu hồi: RFC 7009

Bước "Cho phép" dựa vào ĐĂNG NHẬP DASHBOARD sẵn có (mật khẩu + 2FA), không thêm bí mật nào.

Cần Javis 0.64.26 trở lên: bản đó mở `ctx.register_http` và `ctx.register_well_known`.

Ranh giới an toàn
-----------------
- Javis chưa có mật khẩu quản trị thì mọi cửa OAuth trả 404: cửa đồng ý dựa vào đăng nhập.
- `redirect_uri` chỉ nhận máy của OpenAI, của Anthropic, chính máy của chủ (localhost, cho
  Claude Code và Claude Desktop), hoặc một gốc CHỦ tự cho phép trên trang của gói (trang đòi đăng nhập).
  Kẻ tự đăng ký client trỏ về máy mình rồi dụ chủ bấm "Cho phép" sẽ không nhận được mã; địa chỉ của
  hắn chỉ nằm trong danh sách "đang xin kết nối", kèm lời dặn chỉ cho phép công cụ chủ vừa tự nối.
- Khoá riêng (`jg_...`) chỉ chủ tạo được, chỉ hiện một lần, lưu dạng băm; "Ngắt mọi kết nối" xoá cả khoá.
- Token, mã cấp quyền chỉ lưu dạng BĂM SHA-256. Tệp lộ ra không dùng được để gọi.
- Token của cửa này KHÔNG phải `hub_token` của Claude Code/Codex, và ngược lại.
- Mức quyền (full / auto / suggest) chủ chọn trên trang của gói; hub ép ở lớp cứng như mọi
  engine khác, đọc lại TỪNG lượt gọi nên hạ quyền có hiệu lực ngay.
- Các cửa máy chủ OpenAI gọi (đổi token, MCP...) khai `no_cookie`: lõi Javis gỡ cookie trước
  khi giao request cho gói, nên không trang lạ nào mượn được phiên của chủ qua đó.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import urlencode, urlparse

TTL_ACCESS = 3600                   # 1 giờ; ChatGPT tự làm mới
TTL_REFRESH = 30 * 24 * 3600        # 30 ngày không dùng thì phải cho phép lại
TTL_MA = 300                        # mã cấp quyền sống 5 phút, dùng MỘT lần
TTL_YEU_CAU = 600                   # trang Cho phép mở quá 10 phút thì bắt đầu lại từ ChatGPT
MAX_CLIENT = 20                     # chặn ai đó đăng ký client tới đầy đĩa
MAX_YEU_CAU = 100
MUC_QUYEN = ("full", "auto", "suggest")
MUC_MAC_DINH = "full"               # quyết định 2026-09-10 của chủ repo: Javis tự làm

_HOST_REDIRECT = ("chatgpt.com", "chat.openai.com", "claude.ai", "claude.com")
_DUOI_REDIRECT = (".chatgpt.com", ".openai.com", ".claude.ai", ".claude.com", ".anthropic.com")
# Ứng dụng chạy trên máy của chủ (Claude Code, Claude Desktop) nhận mã qua localhost (RFC 8252):
# mã chỉ tới được chính máy đang mở trình duyệt, nên kẻ gian ở máy khác không nhặt được.
_HOST_MAY_NHA = ("localhost", "127.0.0.1", "::1")
# Plugin này từng tên `javis-trong-chatgpt` (gói 1.x). Lõi Javis chuyển đường /ext/<tên cũ>/ sang
# đây; trang khám phá OAuth cũng phải nhận tên cũ thì connector đã dán vào ChatGPT mới chạy tiếp.
SLUG_CU = "javis-trong-chatgpt"

# Địa chỉ quay về chủ tự cho phép thêm: https://<host> của một công cụ web, hoặc <scheme>://<host> của một ứng dụng
# trên máy (RFC 8252, scheme riêng như cursor://). Không bao giờ nhận các scheme dưới đây, kể cả khi chủ bấm.
_SCHEME_CAM = ("http", "javascript", "data", "file", "vbscript", "about", "blob", "ws", "wss", "ftp", "mailto")
MAX_NGUON_THEM = 30
MAX_CHO_DUYET = 10                  # địa chỉ lạ vừa xin nối, giữ vài cái mới nhất cho chủ xem
TTL_CHO_DUYET = 7 * 24 * 3600
MAX_KHOA_RIENG = 20
TIEN_TO_KHOA = "jg_"
GHI_LAN_DUNG_S = 600                # "dùng lần cuối" của khoá ghi xuống đĩa tối đa 10 phút một lần

_KHOA = threading.Lock()

HUONG_DAN = ("https://github.com/blogminhquy/javis-store/blob/main/packs/"
             "javis.gate/docs/HUONG-DAN.md")


# ============================================================
# Đường và địa chỉ
# ============================================================

def _p(ctx, duoi: str = "", slug: str = "") -> str:
    """Đường tuyệt đối của gói trên Javis: /ext/<slug>/<duoi>."""
    return f"/ext/{slug or ctx.slug}/" + duoi


def _slug_goi(ctx, request) -> str:
    """Tên plugin trong ĐƯỜNG mà client vừa gọi: tên cũ nếu nó vẫn dùng địa chỉ của gói 1.x.
    Trả đúng tên đó trong metadata để `resource` khớp địa chỉ client đã lưu."""
    duong = str(request.url.path or "")
    return SLUG_CU if f"/ext/{SLUG_CU}/" in duong + "/" else ctx.slug


def _goc(request) -> str:
    """Gốc công khai của Javis, không có "/" cuối.

    Ưu tiên tên miền riêng đã khai ở trang Thương hiệu: nó ỔN ĐỊNH, còn header proxy thì đổi
    theo đường người ta đi vào. Issuer của OAuth mà đổi giữa chừng là ChatGPT coi như máy khác."""
    try:
        import config as cfgmod
        custom = ((cfgmod.read_settings().get("domain") or {}).get("custom") or "").strip().lower()
    except Exception:
        custom = ""
    if custom:
        return f"https://{custom}"
    import web_security
    return web_security.external_base(
        request.url.scheme, request.url.netloc, request.headers.get("x-forwarded-proto", ""),
        request.headers.get("x-forwarded-host", "")).rstrip("/")


def _co_mat_khau() -> bool:
    try:
        import config as cfgmod
        return bool(cfgmod.auth_enabled())
    except Exception:
        return False


def _co_phien(request) -> bool:
    import config as cfgmod
    return bool(cfgmod.valid_session(request.cookies.get("javis_session", "")))


def metadata_as(ctx, goc: str) -> dict:
    return {
        "issuer": goc,
        "authorization_endpoint": goc + _p(ctx, "oauth/authorize"),
        "token_endpoint": goc + _p(ctx, "oauth/token"),
        "registration_endpoint": goc + _p(ctx, "oauth/register"),
        "revocation_endpoint": goc + _p(ctx, "oauth/revoke"),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["javis"],
    }


def metadata_pr(ctx, goc: str, slug: str = "") -> dict:
    return {
        "resource": goc + _p(ctx, "mcp", slug),
        "authorization_servers": [goc],
        "scopes_supported": ["javis"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Javis",
    }


def www_authenticate(ctx, goc: str, mo_ta: str = "", slug: str = "") -> str:
    """Câu thách thức trả kèm 401. ChatGPT đọc `resource_metadata` để biết đi đâu đăng nhập.
    `mo_ta` PHẢI là ASCII: header HTTP chỉ nhận Latin-1, viết tiếng Việt vào là trả 500."""
    mo_ta = mo_ta.encode("ascii", "ignore").decode()
    phan = ['Bearer realm="javis"', 'error="invalid_token"']
    if mo_ta:
        phan.append(f'error_description="{mo_ta}"')
    phan.append(f'resource_metadata="{goc}/.well-known/oauth-protected-resource{_p(ctx, "mcp", slug)}"')
    return ", ".join(phan)


# ============================================================
# Cài đặt + kho (thư mục dữ liệu riêng của gói, không nằm trong brain)
# ============================================================

def _doc_json(p, mac_dinh: dict) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return mac_dinh


def _ghi_json(p, d: dict) -> None:
    tam = p.with_suffix(".tmp")
    tam.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tam, 0o600)
    except OSError:
        pass
    os.replace(tam, p)


def muc_quyen(ctx) -> str:
    m = _doc_json(ctx.data_dir / "cai-dat.json", {}).get("muc_quyen")
    return m if m in MUC_QUYEN else MUC_MAC_DINH


def dat_muc_quyen(ctx, m: str) -> bool:
    if m not in MUC_QUYEN:
        return False
    with _KHOA:
        d = _doc_json(ctx.data_dir / "cai-dat.json", {})
        d["muc_quyen"] = m
        _ghi_json(ctx.data_dir / "cai-dat.json", d)
    return True


def nguon_them(ctx) -> list:
    """Các gốc địa chỉ quay về chủ đã cho phép thêm trên trang của gói."""
    ds = _doc_json(ctx.data_dir / "cai-dat.json", {}).get("nguon_them")
    return [g for g in ds if isinstance(g, str) and goc_uri(g + "/") == g] if isinstance(ds, list) else []


def dat_nguon(ctx, goc: str, cho: bool) -> tuple:
    """Chủ cho phép (cho=True) hoặc bỏ một gốc. (ok, câu lỗi). Cho phép xong thì gốc đó rời danh sách đang chờ."""
    g = goc_uri(str(goc or "").strip().rstrip("/") + "/")
    if not g:
        return False, "Địa chỉ không hợp lệ. Cần dạng https://ten-mien hoặc ung-dung://ten (không nhận http)."
    with _KHOA:
        d = _doc_json(ctx.data_dir / "cai-dat.json", {})
        ds = [x for x in d.get("nguon_them") or [] if isinstance(x, str) and x != g]
        if cho:
            if len(ds) >= MAX_NGUON_THEM:
                return False, "Đã đủ số địa chỉ cho phép thêm. Bỏ bớt cái không dùng."
            ds.append(g)
        d["nguon_them"] = ds
        _ghi_json(ctx.data_dir / "cai-dat.json", d)
        kho = _doc(ctx)
        if kho["cho_duyet"].pop(g, None) is not None:
            _ghi(ctx, kho)
    return True, ""


def _doc(ctx) -> dict:
    """Kho: client, token, yêu cầu đang chờ Cho phép, mã cấp quyền. Nằm TRÊN ĐĨA chứ không
    trong RAM: Javis nạp lại plugin mỗi khi bật/tắt một plugin bất kỳ, và một yêu cầu đang chờ
    mà mất giữa chừng là người dùng bấm Cho phép rồi nhận câu "quá hạn"."""
    d = _doc_json(ctx.data_dir / "kho.json", {})
    for k in ("clients", "tokens", "yeu_cau", "ma", "khoa_rieng", "cho_duyet"):
        if not isinstance(d.get(k), dict):
            d[k] = {}
    return d


def _ghi(ctx, d: dict) -> None:
    # Chỉ dọn bản HẾT HẠN. Token đã thu hồi giữ tới hạn của nó: refresh đã xoay mà bị dùng lại
    # là dấu hiệu có người cầm bản sao, và phải còn bản ghi thì mới nhận ra được.
    now = time.time()
    for k in ("tokens", "yeu_cau", "ma"):
        d[k] = {h: t for h, t in d[k].items() if t.get("het", 0) > now}
    d["cho_duyet"] = dict(sorted(((g, x) for g, x in d.get("cho_duyet", {}).items()
                                  if x.get("luc", 0) > now - TTL_CHO_DUYET),
                                 key=lambda gx: gx[1].get("luc", 0))[-MAX_CHO_DUYET:])
    _ghi_json(ctx.data_dir / "kho.json", d)


def _bam(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")


def goc_uri(uri: str) -> str:
    """"<scheme>://<host>" viết thường của một địa chỉ quay về, hoặc "" nếu không phải thứ chủ cho phép thêm được
    (scheme cấm, thiếu host, scheme lạ hình thù). Đây là đơn vị chủ bấm Cho phép, không phải từng đường dẫn."""
    try:
        u = urlparse(str(uri or "").strip())
    except Exception:
        return ""
    sc = (u.scheme or "").lower()
    h = (u.hostname or "").lower()
    if not sc or not h or sc in _SCHEME_CAM or not re.fullmatch(r"[a-z][a-z0-9+.-]{1,40}", sc):
        return ""
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", h) or h in _HOST_MAY_NHA:
        return ""
    return f"{sc}://{h}"


def redirect_hop_le(uri: str, them=()) -> bool:
    """Máy của OpenAI, của Anthropic, chính máy của chủ, hoặc một gốc chủ đã tự cho phép trên trang của gói."""
    try:
        u = urlparse(uri)
    except Exception:
        return False
    if not u.hostname:
        return False
    h = u.hostname.lower()
    if h in _HOST_MAY_NHA:
        return u.scheme in ("http", "https")
    g = goc_uri(uri)
    if g and g in them:
        return True
    if u.scheme != "https":
        return False
    return h in _HOST_REDIRECT or any(h.endswith(d) for d in _DUOI_REDIRECT)


_TEN_AI = (("chatgpt", "ChatGPT"), ("openai", "ChatGPT"), ("claude", "Claude"), ("anthropic", "Claude"),
           ("gemini", "Gemini"), ("grok", "Grok"), ("copilot", "Copilot"), ("cursor", "Cursor"),
           ("mistral", "Le Chat"), ("le chat", "Le Chat"), ("perplexity", "Perplexity"), ("codex", "Codex"))


def ten_ai(ten: str) -> str:
    """Tên ngắn của AI đang gọi, để nhãn "từ ChatGPT" / "từ Claude" / "từ Gemini" trên khung chat của Javis.
    Codex đứng sau OpenAI vì client của Codex CLI hay mang cả chữ "OpenAI"; tên lạ thì giữ nguyên, cắt 40 chữ."""
    t = re.sub(r"\s+", " ", str(ten or "")).strip()
    thap = t.lower()
    if "codex" in thap:
        return "Codex"
    for chu, ten_ngan in _TEN_AI:
        if chu in thap:
            return ten_ngan
    return t[:40] or "AI"


def _noi(uri: str, q: dict) -> str:
    return uri + ("&" if "?" in uri else "?") + urlencode(q)


# ============================================================
# Luồng OAuth
# ============================================================

def dang_ky_client(ctx, body: dict) -> tuple:
    """(mã HTTP, JSON). RFC 7591, chỉ phần ChatGPT dùng: client công khai, không bí mật."""
    uris = body.get("redirect_uris") if isinstance(body, dict) else None
    ten = str((body or {}).get("client_name") or "AI")[:120] if isinstance(body, dict) else "AI"
    them = nguon_them(ctx)
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
        return 400, {"error": "invalid_redirect_uri", "error_description": "redirect_uris is required"}
    la = [u for u in uris if not redirect_hop_le(u, them)]
    if la:
        # Ghi lại để chủ thấy trên trang của gói và bấm Cho phép nếu đúng là công cụ chủ vừa tự nối.
        with _KHOA:
            d = _doc(ctx)
            for u in la:
                g = goc_uri(u)
                if g:
                    d["cho_duyet"][g] = {"ten": ten[:60], "luc": time.time()}
            _ghi(ctx, d)
        return 400, {"error": "invalid_redirect_uri",
                     "error_description": ("this redirect address is not allowed yet. The Javis owner can allow it "
                                           "on the Javis Gate page in Javis (Plugins, Javis Gate, Open page), then "
                                           "connect again")}
    with _KHOA:
        d = _doc(ctx)
        if len(d["clients"]) >= MAX_CLIENT:
            # Bỏ client cũ nhất CHƯA từng nhận token: đó là những lần kết nối bỏ dở.
            dang_dung = {t.get("client") for t in d["tokens"].values()}
            cu = sorted((c for c in d["clients"].values() if c["id"] not in dang_dung),
                        key=lambda c: c.get("tao", 0))
            if not cu:
                return 400, {"error": "invalid_client_metadata",
                             "error_description": "Quá nhiều kết nối. Ngắt bớt trên trang của gói."}
            d["clients"].pop(cu[0]["id"], None)
        cid = "gate-" + secrets.token_urlsafe(16)
        d["clients"][cid] = {"id": cid, "ten": ten, "redirect_uris": uris, "tao": time.time()}
        _ghi(ctx, d)
    return 201, {"client_id": cid, "client_name": ten, "redirect_uris": uris,
                 "token_endpoint_auth_method": "none",
                 "grant_types": ["authorization_code", "refresh_token"],
                 "response_types": ["code"]}


def bat_dau_uy_quyen(ctx, q: dict) -> tuple:
    """Kiểm yêu cầu từ ChatGPT.

    ("trang_loi", câu)   - không tin được redirect_uri nên KHÔNG được chuyển hướng về đó
    ("chuyen", url)      - lỗi báo ngược về ChatGPT theo đúng OAuth
    ("ok", yeu_cau)      - hiện trang Cho phép
    """
    cid = str(q.get("client_id") or "")
    with _KHOA:
        d = _doc(ctx)
        client = d["clients"].get(cid)
        if not client:
            return "trang_loi", "Không nhận ra kết nối này. Mở lại ChatGPT hoặc Claude và bấm kết nối lại."
        ru = str(q.get("redirect_uri") or "")
        if ru not in client["redirect_uris"]:
            return "trang_loi", "Địa chỉ quay về không khớp với lúc đăng ký."
        if not redirect_hop_le(ru, nguon_them(ctx)):
            # Chủ đã bỏ gốc này khỏi danh sách cho phép thêm sau lúc công cụ đăng ký.
            return "trang_loi", "Địa chỉ quay về này không còn được cho phép. Mở trang Javis Gate để cho phép lại."
        state = str(q.get("state") or "")
        if q.get("response_type") != "code":
            return "chuyen", _noi(ru, {"error": "unsupported_response_type",
                                       "error_description": "chỉ hỗ trợ code", "state": state})
        if not q.get("code_challenge") or q.get("code_challenge_method") != "S256":
            return "chuyen", _noi(ru, {"error": "invalid_request",
                                       "error_description": "bắt buộc PKCE S256", "state": state})
        yc = {"id": secrets.token_urlsafe(24), "client": cid, "ten": client["ten"],
              "redirect_uri": ru, "state": state, "challenge": str(q["code_challenge"])[:128],
              "het": time.time() + TTL_YEU_CAU}
        while len(d["yeu_cau"]) >= MAX_YEU_CAU:
            d["yeu_cau"].pop(min(d["yeu_cau"], key=lambda k: d["yeu_cau"][k]["het"]), None)
        d["yeu_cau"][yc["id"]] = yc
        _ghi(ctx, d)
    return "ok", yc


def ket_thuc_uy_quyen(ctx, yid: str, cho_phep: bool):
    """Chủ bấm Cho phép / Từ chối. Trả URL quay về ChatGPT, hoặc None nếu yêu cầu hết hạn."""
    with _KHOA:
        d = _doc(ctx)
        yc = d["yeu_cau"].pop(yid or "", None)
        if not yc or yc.get("het", 0) < time.time():
            _ghi(ctx, d)
            return None
        q = {}
        if cho_phep:
            ma = secrets.token_urlsafe(32)
            d["ma"][_bam(ma)] = {"client": yc["client"], "redirect_uri": yc["redirect_uri"],
                                 "challenge": yc["challenge"], "het": time.time() + TTL_MA}
            q["code"] = ma
        else:
            q = {"error": "access_denied", "error_description": "the Javis owner declined"}
        if yc["state"]:
            q["state"] = yc["state"]
        _ghi(ctx, d)
    return _noi(yc["redirect_uri"], q)


def _cap_token(d: dict, cid: str, ho: str) -> dict:
    """Một cặp access + refresh. `ho` gom các thế hệ refresh của cùng một lần cho phép."""
    now = time.time()
    at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(40)
    d["tokens"][_bam(at)] = {"loai": "access", "client": cid, "ho": ho, "het": now + TTL_ACCESS}
    d["tokens"][_bam(rt)] = {"loai": "refresh", "client": cid, "ho": ho, "het": now + TTL_REFRESH}
    return {"access_token": at, "token_type": "Bearer", "expires_in": TTL_ACCESS,
            "refresh_token": rt, "scope": "javis"}


def doi_token(ctx, f: dict) -> tuple:
    """(mã HTTP, JSON) cho POST token. Client công khai: không có client_secret, chỉ PKCE."""
    loai = f.get("grant_type")
    cid = str(f.get("client_id") or "")
    if loai == "authorization_code":
        ma, ver = str(f.get("code") or ""), str(f.get("code_verifier") or "")
        if not ma or not ver or not cid:
            return 400, {"error": "invalid_request"}
        with _KHOA:
            d = _doc(ctx)
            rec = d["ma"].pop(_bam(ma), None)       # MỘT lần, kể cả khi các bước sau hỏng
            _ghi(ctx, d)
            if not rec or rec["client"] != cid or rec.get("het", 0) < time.time():
                return 400, {"error": "invalid_grant"}
            ru = f.get("redirect_uri")
            if ru and ru != rec["redirect_uri"]:
                return 400, {"error": "invalid_grant", "error_description": "redirect_uri không khớp"}
            if not secrets.compare_digest(_s256(ver), rec["challenge"]):
                return 400, {"error": "invalid_grant", "error_description": "PKCE không khớp"}
            if cid not in d["clients"]:
                return 400, {"error": "invalid_client"}
            out = _cap_token(d, cid, secrets.token_urlsafe(12))
            _ghi(ctx, d)
        return 200, out
    if loai == "refresh_token":
        rt = str(f.get("refresh_token") or "")
        if not rt or not cid:
            return 400, {"error": "invalid_request"}
        with _KHOA:
            d = _doc(ctx)
            rec = d["tokens"].get(_bam(rt))
            if not rec or rec.get("loai") != "refresh" or rec.get("client") != cid:
                return 400, {"error": "invalid_grant"}
            if rec.get("thu_hoi"):
                # Refresh ĐÃ XOAY mà còn bị dùng lại: có người cầm bản sao. Thu hồi cả họ, bắt
                # cho phép lại, thay vì để hai bên cùng giữ một kết nối.
                for t in d["tokens"].values():
                    if t.get("ho") == rec.get("ho"):
                        t["thu_hoi"] = True
                _ghi(ctx, d)
                return 400, {"error": "invalid_grant", "error_description": "refresh đã dùng rồi"}
            if rec["het"] < time.time():
                return 400, {"error": "invalid_grant"}
            rec["thu_hoi"] = True
            out = _cap_token(d, cid, rec["ho"])
            _ghi(ctx, d)
        return 200, out
    return 400, {"error": "unsupported_grant_type"}


def kiem_token(ctx, raw: str):
    """Bản ghi của một access token còn hạn, hoặc None."""
    if not raw:
        return None
    rec = _doc(ctx)["tokens"].get(_bam(raw))
    if not rec or rec.get("loai") != "access" or rec.get("thu_hoi") or rec["het"] < time.time():
        return None
    return rec


def ten_client(ctx, rec) -> str:
    """Tên AI của một token (theo client đã đăng ký), cho nhãn nguồn trên khung chat."""
    c = _doc(ctx)["clients"].get(str((rec or {}).get("client") or "")) or {}
    return ten_ai(c.get("ten") or "")


def thu_hoi(ctx, raw: str) -> None:
    """RFC 7009: thu hồi một token và cả họ của nó. Không nói token có tồn tại hay không."""
    if not raw:
        return
    with _KHOA:
        d = _doc(ctx)
        rec = d["tokens"].get(_bam(raw))
        if not rec:
            return
        for t in d["tokens"].values():
            if t.get("ho") == rec.get("ho"):
                t["thu_hoi"] = True
        _ghi(ctx, d)


def tao_khoa_rieng(ctx, ten: str) -> tuple:
    """Khoá riêng cho một công cụ chỉ biết dán header. (ok, dict). Khoá thô chỉ trả MỘT lần, kho giữ bản băm."""
    ten = re.sub(r"\s+", " ", str(ten or "")).strip()[:40]
    if not ten:
        return False, {"error": "Đặt tên cho khoá (tên công cụ sẽ dùng nó, ví dụ Cursor, n8n)."}
    with _KHOA:
        d = _doc(ctx)
        if len(d["khoa_rieng"]) >= MAX_KHOA_RIENG:
            return False, {"error": "Đã đủ số khoá. Thu hồi bớt khoá không dùng."}
        raw = TIEN_TO_KHOA + secrets.token_urlsafe(32)
        kid = secrets.token_hex(4)
        d["khoa_rieng"][_bam(raw)] = {"id": kid, "ten": ten, "tao": time.time(), "dung": 0}
        _ghi(ctx, d)
    return True, {"khoa": raw, "id": kid, "ten": ten}


def kiem_khoa_rieng(ctx, raw: str):
    """Bản ghi của một khoá riêng còn hiệu lực, hoặc None. Ghi "dùng lần cuối" thưa thôi để khỏi ghi đĩa mỗi lượt."""
    if not raw or not raw.startswith(TIEN_TO_KHOA):
        return None
    h = _bam(raw)
    rec = _doc(ctx)["khoa_rieng"].get(h)
    if not rec:
        return None
    if time.time() - float(rec.get("dung") or 0) > GHI_LAN_DUNG_S:
        with _KHOA:
            d = _doc(ctx)
            if h in d["khoa_rieng"]:
                d["khoa_rieng"][h]["dung"] = time.time()
                _ghi(ctx, d)
    return rec


def xoa_khoa_rieng(ctx, kid: str) -> bool:
    with _KHOA:
        d = _doc(ctx)
        h = next((h for h, r in d["khoa_rieng"].items() if r.get("id") == str(kid or "")), None)
        if not h:
            return False
        d["khoa_rieng"].pop(h)
        _ghi(ctx, d)
    return True


def ds_khoa_rieng(ctx) -> list:
    """Khoá riêng để vẽ trên trang: tên, ngày tạo, lần dùng cuối. Không bao giờ có khoá thô hay bản băm."""
    return sorted(({"id": r.get("id"), "ten": r.get("ten"), "tao": r.get("tao"), "dung": r.get("dung")}
                   for r in _doc(ctx)["khoa_rieng"].values()), key=lambda r: r.get("tao") or 0)


def ngat_tat_ca(ctx) -> int:
    """Nút "Ngắt mọi kết nối": xoá sạch client, token, yêu cầu, mã và khoá riêng. Trả số kết nối đã xoá."""
    with _KHOA:
        d = _doc(ctx)
        n = len(d["clients"]) + len(d["khoa_rieng"])
        _ghi(ctx, {"clients": {}, "tokens": {}, "yeu_cau": {}, "ma": {}, "khoa_rieng": {}, "cho_duyet": {}})
    return n


def ds_ket_noi(ctx) -> list:
    """Kết nối OAuth còn hiệu lực, mỗi lần chủ bấm Cho phép là một dòng (một họ refresh): tên AI, ngày nối, lần dùng
    gần nhất (refresh xoay mỗi lần AI làm mới, nên hạn của nó trừ TTL_REFRESH là lúc làm mới cuối)."""
    now = time.time()
    d = _doc(ctx)
    ra = {}
    for t in d["tokens"].values():
        if t.get("loai") != "refresh" or t.get("thu_hoi") or t.get("het", 0) <= now:
            continue
        c = d["clients"].get(str(t.get("client") or "")) or {}
        cu = ra.get(t.get("ho"))
        if cu and cu["het"] >= t["het"]:
            continue
        ra[t.get("ho")] = {"ho": t.get("ho"), "ten": ten_ai(c.get("ten") or ""), "tao": c.get("tao"),
                           "het": t["het"], "dung": t["het"] - TTL_REFRESH}
    return sorted(ra.values(), key=lambda x: x.get("tao") or 0)


def ngat_mot(ctx, ho: str) -> bool:
    """Ngắt một kết nối OAuth (cả họ refresh của nó). Công cụ đó phải bấm kết nối lại mới dùng tiếp."""
    with _KHOA:
        d = _doc(ctx)
        trung = [t for t in d["tokens"].values() if t.get("ho") == str(ho or "") and not t.get("thu_hoi")]
        if not ho or not trung:
            return False
        for t in trung:
            t["thu_hoi"] = True
        _ghi(ctx, d)
    return True


def so_ket_noi(ctx) -> int:
    """Số lần cho phép còn hiệu lực (mỗi họ refresh còn sống là một), cộng số khoá riêng."""
    now = time.time()
    d = _doc(ctx)
    return len({t.get("ho") for t in d["tokens"].values()
                if t.get("loai") == "refresh" and not t.get("thu_hoi") and t.get("het", 0) > now}) + len(d["khoa_rieng"])


# ============================================================
# HTML (chữ tối thiểu 16px, đọc trên điện thoại)
# ============================================================

_CSS = """
:root { color-scheme: light dark; --nen:#f4f3ef; --the:#fff; --chu:#1d1d1f; --phu:#5f5f66;
        --vien:#dcdad4; --nhan:#c2410c; --nhan-nhat:#fff4ec; --nhan-chu:#fff; --loi:#c81e1e; --loi-nhat:#fdeeee;
        --tot:#15803d; --tot-nhat:#e9f7ee; --goi-y:#f7f6f2; }
@media (prefers-color-scheme: dark) {
  :root { --nen:#121212; --the:#1d1d1d; --chu:#eee; --phu:#a8a8ad; --vien:#363636; --nhan:#f0773a;
          --nhan-nhat:#2c1c13; --nhan-chu:#160c06; --loi:#f87171; --loi-nhat:#2c1515; --tot:#4ade80;
          --tot-nhat:#12251a; --goi-y:#252525; }
}
* { box-sizing: border-box; }
body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
       background:var(--nen); color:var(--chu); font-size:17px; line-height:1.55;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:16px; }
body.dai { align-items:flex-start; }
.the { background:var(--the); border:1px solid var(--vien); border-radius:16px; padding:28px 24px;
       width:100%; max-width:480px; }
.trang { width:100%; max-width:560px; padding-bottom:24px; }
h1 { font-size:24px; margin:0 0 6px; line-height:1.25; }
h2 { font-size:19px; margin:0; line-height:1.3; }
h3 { font-size:17px; margin:0; }
p { margin:0 0 14px; }
.phu { color:var(--phu); font-size:16px; }
ul { margin:0 0 18px; padding-left:20px; }
li { margin-bottom:6px; }
label { display:block; font-size:16px; margin:12px 0 6px; }
input, select { width:100%; font-size:17px; padding:12px; border-radius:10px;
                border:1px solid var(--vien); background:transparent; color:inherit; }
button { width:100%; font-size:17px; min-height:48px; padding:12px; border-radius:12px; border:0; cursor:pointer;
         margin-top:12px; font-family:inherit; }
.chinh { background:var(--nhan); color:var(--nhan-chu); font-weight:600; }
.phu-nut { background:transparent; color:var(--chu); border:1px solid var(--vien); }
.nguy { background:transparent; color:var(--loi); border:1.5px solid var(--loi); font-weight:600; }
.loi { color:var(--loi); font-size:16px; margin-top:10px; }
.tot { color:var(--tot); font-size:16px; margin-top:10px; }
code { font-size:16px; word-break:break-all; }
a { color:var(--nhan); }
.hop { display:flex; gap:8px; align-items:stretch; }
.hop input { flex:1; }
.hop button { width:auto; margin:0; padding:0 16px; }
pre { font-size:15px; white-space:pre-wrap; overflow-wrap:anywhere; background:var(--goi-y); padding:12px;
      border-radius:10px; border:1px solid var(--vien); margin:8px 0; }
button:focus-visible, input:focus-visible, summary:focus-visible, a:focus-visible {
      outline:3px solid var(--nhan); outline-offset:2px; }

/* trang cài đặt */
.dau { padding:8px 4px 18px; }
.dau p { margin:0 0 12px; }
.chip { display:inline-flex; align-items:center; gap:8px; font-size:16px; font-weight:600; padding:6px 12px;
        border-radius:999px; }
.chip::before { content:""; width:9px; height:9px; border-radius:50%; background:currentColor; }
.chip.tot { background:var(--tot-nhat); color:var(--tot); margin:0; }
.chip.loi { background:var(--loi-nhat); color:var(--loi); margin:0; }
.khoi { background:var(--the); border:1px solid var(--vien); border-radius:16px; padding:18px 18px 20px;
        margin-bottom:14px; }
.tieu { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.nut-i { width:40px; min-height:40px; height:40px; margin:0; padding:0; flex:none; border-radius:50%;
         border:1.5px solid var(--vien); background:transparent; color:var(--phu); font:italic 700 18px Georgia,serif; }
.nut-i[aria-expanded="true"] { background:var(--nhan); border-color:var(--nhan); color:var(--nhan-chu); }
.goi-y { background:var(--goi-y); border-radius:12px; padding:14px 14px 4px; margin-top:12px; font-size:16px; }
.goi-y p { margin:0 0 10px; }
.mo-ta { color:var(--phu); font-size:16px; margin:6px 0 0; }
.dia-chi { font:16px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:var(--goi-y);
           border:1px solid var(--vien); border-radius:12px; padding:12px 14px; margin-top:14px;
           overflow-wrap:anywhere; user-select:all; }
.canh-bao { background:var(--loi-nhat); color:var(--loi); border-radius:12px; padding:12px 14px; margin-top:12px;
            font-size:16px; }
.tabs { display:flex; gap:6px; background:var(--the); border:1px solid var(--vien); border-radius:12px; padding:4px;
        margin-bottom:12px; }
.tabs button { margin:0; min-height:42px; padding:8px 6px; border-radius:9px; background:transparent;
               color:var(--phu); font-size:16px; }
.tabs button[aria-selected="true"] { background:var(--nhan); color:var(--nhan-chu); font-weight:600; }
ol.buoc { list-style:none; margin:0 0 10px; padding:0; counter-reset:b; }
ol.buoc li { counter-increment:b; position:relative; padding:0 0 12px 40px; margin:0; }
ol.buoc li::before { content:counter(b); position:absolute; left:0; top:-1px; width:28px; height:28px;
                     border-radius:50%; background:var(--nhan); color:var(--nhan-chu); font-size:15px;
                     font-weight:700; display:flex; align-items:center; justify-content:center; }
.chon { display:flex; gap:12px; align-items:flex-start; margin:10px 0 0; padding:14px; border:1.5px solid var(--vien);
        border-radius:12px; cursor:pointer; font-size:17px; }
.chon input { width:22px; height:22px; margin:2px 0 0; flex:none; accent-color:var(--nhan); }
.chon b { display:block; }
.chon small { display:block; color:var(--phu); font-size:16px; line-height:1.45; margin-top:2px; }
.chon:has(input:checked) { border-color:var(--nhan); background:var(--nhan-nhat); }
.tb { font-size:16px; margin:10px 0 0; color:var(--phu); }
.tb:empty { display:none; }
.dong { display:flex; gap:12px; align-items:center; justify-content:space-between; padding:12px 0;
        border-bottom:1px solid var(--vien); }
.dong:last-child { border-bottom:0; }
.dong > div { min-width:0; overflow-wrap:anywhere; }
.dong b { display:block; }
.dong .phu { display:block; overflow-wrap:anywhere; }
.dong button { width:auto; margin:0; min-height:42px; padding:8px 14px; flex:none; }
.trong { color:var(--phu); font-size:16px; margin:10px 0 0; }
details.khoi > summary { list-style:none; cursor:pointer; display:flex; align-items:center; gap:10px; }
details.khoi > summary::-webkit-details-marker { display:none; }
details.khoi > summary::after { content:""; width:10px; height:10px; margin-left:auto; flex:none;
        border-right:2.5px solid var(--phu); border-bottom:2.5px solid var(--phu); transform:rotate(45deg);
        margin-top:-4px; }
details.khoi[open] > summary::after { transform:rotate(-135deg); margin-top:4px; }
.so { background:var(--nhan); color:var(--nhan-chu); border-radius:999px; font-size:15px; font-weight:700;
      padding:1px 9px; vertical-align:2px; margin-left:4px; }
.nguy-hiem .mo-ta { margin:0; }
.phan { margin-top:18px; padding-top:16px; border-top:1px solid var(--vien); }
.xep { display:flex; flex-direction:column; gap:10px; margin-top:12px; }
.xep input, .xep button { margin:0; }
.the-moi { border:1.5px solid var(--nhan); border-radius:12px; padding:14px; margin-top:12px; }
.nguy-hiem { border-color:var(--loi-nhat); }
.cuoi { text-align:center; margin:18px 0 0; font-size:16px; }
"""


def _trang(than: str, status: int = 200, khung: bool = True):
    # `same-origin`, KHÔNG phải `no-referrer`. URL trang Cho phép mang state và code_challenge
    # nên không được rò sang trang khác qua Referer, và cả hai giá trị đều chặn được điều đó.
    # Nhưng `no-referrer` còn làm trình duyệt gửi `Origin: null` khi nộp form, hàng rào CSRF
    # của Javis coi "null" là nguồn lạ và chặn nút Cho phép (đo trên Chromium thật 23/09).
    from starlette.responses import HTMLResponse
    return HTMLResponse(
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="same-origin"><title>Javis Gate</title>'
        f'<style>{_CSS}</style></head>'
        + (f'<body><div class="the">{than}</div></body></html>' if khung
           else f'<body class="dai"><main class="trang">{than}</main></body></html>'),
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                 "Content-Security-Policy": "frame-ancestors 'none'"})


_MO_TA_MUC = {
    "full": "Toàn quyền: đọc dữ liệu, nhắn vào khung chat để Javis làm, và làm việc thật (gửi tin, tạo đơn, đăng bài...)",
    "auto": "Tự làm có giới hạn: đọc dữ liệu và viết nháp, không làm việc ra bên ngoài",
    "suggest": "Chỉ đọc và gợi ý: không ghi file, không làm việc ra bên ngoài",
}


_MUC_THE = (
    ("full", "Toàn quyền", "Đọc, nhắn vào khung chat để Javis làm, và làm việc thật như gửi tin, tạo đơn, đăng bài."),
    ("auto", "Tự làm có giới hạn", "Đọc, ghi chép, viết nháp. Không nhắn vào khung chat, không làm gì ra bên ngoài."),
    ("suggest", "Chỉ đọc và gợi ý", "Chỉ đọc, kể cả hội thoại. Không ghi gì."),
)


def _trang_dang_nhap():
    """Trình duyệt chưa vào dashboard: đăng nhập NGAY TẠI ĐÂY rồi tải lại, không bắt đi vòng."""
    return _trang("""
<h1>Đăng nhập Javis</h1>
<p class="phu">Một AI (ChatGPT, Claude...) đang xin quyền dùng Javis của bạn. Đăng nhập để xem và quyết định.</p>
<form id="f">
  <label for="u">Tài khoản</label><input id="u" name="username" autocomplete="username" required>
  <label for="p">Mật khẩu</label>
  <input id="p" name="password" type="password" autocomplete="current-password" required>
  <div id="o" style="display:none">
    <label for="c">Mã xác thực 2 lớp</label>
    <input id="c" name="code" inputmode="numeric" autocomplete="one-time-code">
  </div>
  <div id="e" class="loi" role="alert"></div>
  <button class="chinh" type="submit">Đăng nhập</button>
</form>
<script>
document.getElementById("f").onsubmit = async (ev) => {
  ev.preventDefault();
  const e = document.getElementById("e"); e.textContent = "";
  let r, d = {};
  try {
    r = await fetch("/auth/login", {method: "POST", body: new FormData(ev.target),
                                    credentials: "same-origin"});
    d = await r.json();
  } catch (x) { e.textContent = "Không gọi được Javis. Thử lại."; return; }
  if (d.ok) { location.reload(); return; }
  if (d.needs_2fa) document.getElementById("o").style.display = "";
  e.textContent = d.error || "Đăng nhập không thành công.";
};
</script>""")


def _trang_dong_y(ctx, yc: dict):
    muc = muc_quyen(ctx)
    u = urlparse(yc["redirect_uri"])
    noi = (u.hostname or "") if (u.scheme or "").lower() in ("http", "https") else goc_uri(yc["redirect_uri"])
    them = goc_uri(yc["redirect_uri"]) in nguon_them(ctx)
    ai = html.escape(ten_ai(yc["ten"]))
    ghi_chu = ('<p class="phu">Đây là địa chỉ bạn đã tự cho phép thêm trên trang của gói. Chỉ bấm Cho phép nếu '
               'bạn vừa tự kết nối từ công cụ đó.</p>') if them else ""
    return _trang(f"""
<h1>Cho phép {ai} dùng Javis?</h1>
<p><b>{html.escape(yc["ten"])}</b> trên <code>{html.escape(noi)}</code> muốn gọi công cụ của Javis.</p>
{ghi_chu}
<ul>
  <li>Dùng mọi kết nối bạn đã bật trong Javis (bán hàng, quảng cáo, lịch, Zalo...)</li>
  <li>Đọc hội thoại của Javis, và ở mức Toàn quyền thì nhắn vào khung chat để Javis làm tiếp,
      chọn cả model (ghim cho hội thoại đó, bạn đổi lại được) và effort cho tin nó gửi</li>
  <li>Đọc và ghi trong brain đang mở, tạo việc và nhắc lịch</li>
  <li>Mức quyền: {html.escape(_MO_TA_MUC.get(muc, muc))}</li>
</ul>
<p class="phu">Đổi mức quyền hoặc ngắt kết nối bất cứ lúc nào trên trang của gói
(trang Plugin, thẻ Javis Gate, nút Mở trang).</p>
<form method="post" action="{_p(ctx, "oauth/authorize")}">
  <input type="hidden" name="yeu_cau" value="{html.escape(yc["id"])}">
  <button class="chinh" name="hanh_dong" value="cho_phep" type="submit">Cho phép</button>
  <button class="phu-nut" name="hanh_dong" value="tu_choi" type="submit">Từ chối</button>
</form>""")


def _ngay(ts) -> str:
    """Ngày giờ theo múi giờ của chủ, bỏ năm khi là năm nay (dòng ngắn đọc trên điện thoại)."""
    if not ts:
        return "chưa dùng"
    mau = "%d/%m %H:%M" if time.localtime(float(ts)).tm_year == time.localtime().tm_year else "%d/%m/%Y %H:%M"
    try:
        import localefmt
        return localefmt.format_ts(ts, mau)
    except Exception:
        return time.strftime(mau, time.localtime(float(ts)))


def _nut_i(ma: str, nhan: str) -> str:
    """Nút (i) cạnh tiêu đề: bấm mới mở phần hướng dẫn `ma`. Chủ dự án 15/11: hướng dẫn gọn lại sau nút i."""
    return (f'<button class="nut-i" type="button" data-i aria-expanded="false" aria-controls="{ma}" '
            f'aria-label="{html.escape(nhan)}" title="{html.escape(nhan)}">i</button>')


def _goi_y(ma: str, than: str) -> str:
    return f'<div class="goi-y" id="{ma}" hidden>{than}</div>'


def _ds_nguon(ctx) -> tuple:
    """(html, số đang chờ): địa chỉ đang xin nối (nút Cho phép) rồi địa chỉ đã cho phép thêm (nút Bỏ)."""
    phan = []
    cho = sorted(_doc(ctx)["cho_duyet"].items(), key=lambda gx: -float(gx[1].get("luc") or 0))
    for g, x in cho:
        phan.append(f'<div class="dong"><div><b>{html.escape(str(x.get("ten") or "Công cụ lạ"))}</b><span class="phu">'
                    f'{html.escape(g)}<br>xin lúc {html.escape(_ngay(x.get("luc")))}</span></div>'
                    f'<button class="chinh" type="button" data-nguon="{html.escape(g)}" data-cho="1">Cho phép</button></div>')
    for g in nguon_them(ctx):
        phan.append(f'<div class="dong"><div><b>{html.escape(g.split("://", 1)[-1])}</b><span class="phu">'
                    f'{html.escape(g)}<br>đã cho phép</span></div>'
                    f'<button class="phu-nut" type="button" data-nguon="{html.escape(g)}" data-cho="0">Bỏ</button></div>')
    return "".join(phan) or '<p class="trong">Chưa có công cụ nào xin kết nối.</p>', len(cho)


def _ds_dang_noi(ctx) -> tuple:
    """(html, số dòng) của mục Đang kết nối: từng lần Cho phép (OAuth) và từng khoá riêng, mỗi dòng một nút ngắt."""
    phan = [f'<div class="dong"><div><b>{html.escape(k["ten"])}</b><span class="phu">dùng gần nhất '
            f'{html.escape(_ngay(k.get("dung")))}</span></div>'
            f'<button class="phu-nut" type="button" data-ngat-mot="{html.escape(str(k["ho"]))}" '
            f'data-ten="{html.escape(k["ten"])}">Ngắt</button></div>' for k in ds_ket_noi(ctx)]
    phan += [f'<div class="dong"><div><b>{html.escape(str(k.get("ten") or ""))}</b><span class="phu">khoá riêng, '
             + (f'dùng gần nhất {html.escape(_ngay(k.get("dung")))}' if k.get("dung") else "chưa dùng")
             + f'</span></div><button class="phu-nut" type="button" data-xoa-khoa="{html.escape(str(k.get("id") or ""))}" '
             f'data-ten="{html.escape(str(k.get("ten") or ""))}">Thu hồi</button></div>' for k in ds_khoa_rieng(ctx)]
    return "".join(phan) or ('<p class="trong">Chưa có AI nào kết nối. Chép địa chỉ ở trên rồi dán vào ChatGPT, '
                             'Claude hay công cụ bạn dùng.</p>'), len(phan)


def _trang_loi(cau: str, status: int = 400):
    return _trang(f"<h1>Không kết nối được</h1><p>{html.escape(cau)}</p>", status)


_HD_KET_NOI = """
<div data-tabs>
<div class="tabs" role="tablist">
  <button type="button" role="tab" aria-selected="true" aria-controls="tab-gpt">ChatGPT</button>
  <button type="button" role="tab" aria-selected="false" aria-controls="tab-claude">Claude</button>
  <button type="button" role="tab" aria-selected="false" aria-controls="tab-khac">Khác</button>
</div>
<div id="tab-gpt" role="tabpanel"><ol class="buoc">
  <li>Mở <b>chatgpt.com trên máy tính</b>, vào <b>Settings</b>, rồi <b>Apps &amp; Connectors</b>.</li>
  <li>Mở <b>Advanced settings</b>, bật <b>Developer mode</b>.</li>
  <li>Bấm <b>Create</b>. Tên: <b>Javis</b>. MCP Server URL: dán địa chỉ. Authentication: <b>OAuth</b>.</li>
  <li>Tích ô tin tưởng, bấm <b>Create</b>, rồi bấm <b>Cho phép</b> trên trang Javis hiện ra.</li>
</ol><p class="phu">Cần gói trả phí. Gói Business: quản trị viên bật Create custom MCP connectors trước.</p></div>
<div id="tab-claude" role="tabpanel" hidden><ol class="buoc">
  <li>Trên <b>claude.ai</b> hoặc Claude Desktop, vào <b>Settings</b>, rồi <b>Connectors</b>.</li>
  <li>Bấm <b>Add custom connector</b>. Tên: <b>Javis</b>. Dán địa chỉ, bấm <b>Add</b>.</li>
  <li>Bấm <b>Connect</b>, rồi bấm <b>Cho phép</b> trên trang Javis hiện ra.</li>
</ol><p class="phu">Cần gói trả phí (Pro, Max, Team, Enterprise).</p></div>
<div id="tab-khac" role="tabpanel" hidden><ol class="buoc">
  <li>Trong công cụ, tìm chỗ thêm <b>MCP server</b> bằng địa chỉ, rồi dán địa chỉ.</li>
  <li>Công cụ chạy trên máy bạn (Claude Code, Codex, Gemini CLI...): đăng nhập là xong.</li>
  <li>Công cụ web khác bị từ chối lần đầu: mở mục <b>Công cụ AI khác</b> bên dưới, bấm <b>Cho phép</b>, rồi nối lại.</li>
  <li>Công cụ chỉ cho dán khoá (n8n, script): tạo <b>khoá riêng</b> ở mục đó.</li>
</ol></div>
</div>"""


def _trang_cai_dat(ctx, request):
    """Trang của gói (chủ dự án 15/11: "các phần thông tin hướng dẫn làm gọn lại sau nút i, các bước dễ đọc").
    Thứ tự theo việc chủ cần: trạng thái, địa chỉ để chép, mức quyền, ai đang nối, công cụ khác (thu gọn), rồi nút xoá
    hết tách riêng ở cuối. Mọi giải thích nằm sau nút (i), bấm mới mở."""
    goc = _goc(request)
    url = goc + _p(ctx, "mcp")
    muc = muc_quyen(ctx)
    https = goc.startswith("https://")
    dau = f"""<header class="dau"><h1>Javis Gate</h1>
<p class="phu">Cổng để ChatGPT, Claude và AI khác đọc hội thoại và giao việc cho Javis.</p>"""
    if not _co_mat_khau():
        return _trang(dau + """<span class="chip loi">Đang đóng</span></header>
<section class="khoi"><h2>Cần mật khẩu quản trị</h2>
<p class="mo-ta">Bước Cho phép dựa vào đăng nhập Javis, nên cổng chỉ mở khi Javis có mật khẩu. Đặt mật khẩu trong
Cài đặt rồi quay lại trang này.</p></section>""", khung=False)
    chip = ('<span class="chip tot">Sẵn sàng kết nối</span>' if https
            else '<span class="chip loi">Chưa có https</span>')
    canh_bao = "" if https else (
        '<div class="canh-bao">ChatGPT và Claude chỉ nhận địa chỉ https. Gắn tên miền riêng cho Javis (Cài đặt, mục '
        'Giọng nói, thương hiệu và truy cập) rồi tải lại trang này.</div>')
    the_muc = "".join(
        f'<label class="chon"><input type="radio" name="muc" value="{m}"{" checked" if m == muc else ""}>'
        f'<span><b>{html.escape(ten)}</b><small>{html.escape(mo_ta)}</small></span></label>'
        for m, ten, mo_ta in _MUC_THE)
    ds_nguon, so_cho = _ds_nguon(ctx)
    ds_noi, so_noi = _ds_dang_noi(ctx)
    cfg = json.dumps({"url": url, "duong": {k: _p(ctx, v) for k, v in (
        ("cai_dat", "cai-dat"), ("nguon", "nguon"), ("khoa", "khoa"), ("khoa_xoa", "khoa-xoa"), ("ngat", "ngat"),
        ("ngat_mot", "ngat-mot"))}}, ensure_ascii=False).replace("</", "<\\/")
    than = f"""{dau}{chip}</header>

<section class="khoi">
  <div class="tieu"><h2>Địa chỉ kết nối</h2>{_nut_i("i-noi", "Cách kết nối")}</div>
  <p class="mo-ta">Dán vào ChatGPT, Claude hay công cụ AI bạn dùng.</p>
  {_goi_y("i-noi", _HD_KET_NOI)}
  <div class="dia-chi" id="url">{html.escape(url)}</div>
  <button class="chinh" id="chep" type="button">Chép địa chỉ</button>
  {canh_bao}
</section>

<section class="khoi">
  <div class="tieu"><h2>Mức quyền</h2>{_nut_i("i-muc", "Về mức quyền")}</div>
  {_goi_y("i-muc", '<p>Javis tự chặn ở phía mình theo mức này, AI bên ngoài không tự nâng quyền được.</p>'
                   '<p>Đổi là có hiệu lực từ lượt gọi kế tiếp, không cần kết nối lại. Áp cho mọi AI đang nối.</p>')}
  <div role="radiogroup" aria-label="Mức quyền">{the_muc}</div>
  <p class="tb" id="tb-muc" role="status"></p>
</section>

<section class="khoi">
  <div class="tieu"><h2>Đang kết nối{f' <span class="so">{so_noi}</span>' if so_noi else ""}</h2>{_nut_i("i-dang", "Về các kết nối")}</div>
  {_goi_y("i-dang", '<p>Mỗi dòng là một lần bạn bấm Cho phép, hoặc một khoá riêng. Bấm Ngắt hay Thu hồi là công '
                    'cụ đó mất kết nối ngay.</p><p>Kết nối không dùng 30 ngày tự hết hạn.</p>')}
  {ds_noi}
</section>

<details class="khoi"{" open" if so_cho else ""}>
  <summary><h2>Công cụ AI khác</h2>{f'<span class="so">{so_cho}</span>' if so_cho else ""}</summary>
  <p class="mo-ta">Cho công cụ ngoài ChatGPT và Claude: Le Chat, Copilot, n8n...</p>
  <div class="phan">
    <div class="tieu"><h3>Đang xin kết nối</h3>{_nut_i("i-xin", "Về công cụ xin kết nối")}</div>
    {_goi_y("i-xin", '<p>Javis tự nhận ChatGPT, Claude và ứng dụng chạy trên chính máy bạn. Công cụ khác xin nối '
                     'thì hiện ở đây.</p><p><b>Chỉ bấm Cho phép khi bạn vừa tự nối từ đúng công cụ đó</b>, rồi nối '
                     'lại từ công cụ. Địa chỉ lạ cứ để, sau 7 ngày tự mất. Không bao giờ nhận địa chỉ http.</p>')}
    {ds_nguon}
  </div>
  <div class="phan">
    <div class="tieu"><h3>Khoá riêng</h3>{_nut_i("i-khoa", "Về khoá riêng")}</div>
    {_goi_y("i-khoa", '<p>Cho công cụ chỉ biết dán khoá vào header (n8n, script, trình soạn mã). Tên khoá là tên '
                      'hiện trên khung chat của Javis.</p><p><b>Ai cầm khoá là dùng được Javis</b> ở mức quyền '
                      'trên. Đừng dán vào chỗ công khai. Khoá chỉ hiện một lần.</p>')}
    <div class="xep">
      <input id="ten-khoa" maxlength="40" placeholder="Tên công cụ, ví dụ n8n" aria-label="Tên công cụ">
      <button class="chinh" id="tao-khoa" type="button">Tạo khoá</button>
    </div>
    <div id="khoa-moi"></div>
  </div>
</details>

<section class="khoi nguy-hiem">
  <p class="mo-ta">Cần cắt hết một lần (mất điện thoại, nghi lộ khoá): ngắt mọi AI đang nối và xoá mọi khoá riêng.
  Muốn dùng lại phải nối lại từ đầu.</p>
  <button class="nguy" id="ngat" type="button">Ngắt mọi kết nối</button>
</section>

<p class="cuoi"><a href="{HUONG_DAN}" target="_blank" rel="noopener">Xem hướng dẫn đầy đủ</a></p>
<script id="cfg" type="application/json">{cfg}</script>
<script>{_JS_CAI_DAT}</script>"""
    return _trang(than, khung=False)


# JS của trang cài đặt, chuỗi thường (không phải f-string) để khỏi nhân đôi ngoặc; đường dẫn đọc từ khối #cfg.
_JS_CAI_DAT = """
(() => {
const C = JSON.parse(document.getElementById("cfg").textContent);
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
function chu(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
async function goi(duong, body) {
  const r = await fetch(duong, {method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  const d = await r.json().catch(() => ({}));
  if (!r.ok || !d.ok) throw new Error(d.error || ("Lỗi " + r.status));
  return d;
}
async function chep(text, nut) {
  try { await navigator.clipboard.writeText(text); }
  catch (x) {
    const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t);
    t.select(); document.execCommand("copy"); t.remove();
  }
  const cu = nut.textContent; nut.textContent = "Đã chép";
  setTimeout(() => { nut.textContent = cu; }, 1600);
}
$$("[data-i]").forEach((b) => b.onclick = () => {
  const mo = b.getAttribute("aria-expanded") !== "true";
  b.setAttribute("aria-expanded", String(mo));
  document.getElementById(b.getAttribute("aria-controls")).hidden = !mo;
});
$$("[role=tab]").forEach((t) => t.onclick = () => {
  $$("[role=tab]", t.closest("[data-tabs]")).forEach((x) => {
    const on = x === t;
    x.setAttribute("aria-selected", String(on));
    document.getElementById(x.getAttribute("aria-controls")).hidden = !on;
  });
});
$("#chep").onclick = (ev) => chep(C.url, ev.currentTarget);
$$("input[name=muc]").forEach((r) => r.onchange = async () => {
  const tb = $("#tb-muc"); tb.className = "tb"; tb.textContent = "Đang lưu...";
  try { await goi(C.duong.cai_dat, {muc_quyen: r.value});
        tb.className = "tb tot"; tb.textContent = "Đã lưu. Có hiệu lực từ lượt gọi kế tiếp."; }
  catch (x) { tb.className = "tb loi"; tb.textContent = x.message; }
});
$$("[data-nguon]").forEach((b) => b.onclick = async () => {
  const cho = b.dataset.cho === "1";
  if (cho && !confirm("Cho phép " + b.dataset.nguon + "? Chỉ đồng ý nếu bạn vừa tự kết nối từ công cụ này.")) return;
  try { await goi(C.duong.nguon, {goc: b.dataset.nguon, cho: cho}); location.reload(); }
  catch (x) { alert(x.message); }
});
$$("[data-ngat-mot]").forEach((b) => b.onclick = async () => {
  if (!confirm("Ngắt " + b.dataset.ten + "? Muốn dùng lại phải kết nối lại từ công cụ đó.")) return;
  try { await goi(C.duong.ngat_mot, {ho: b.dataset.ngatMot}); location.reload(); }
  catch (x) { alert(x.message); }
});
$$("[data-xoa-khoa]").forEach((b) => b.onclick = async () => {
  if (!confirm("Thu hồi khoá " + b.dataset.ten + "? Công cụ đang dùng nó sẽ mất kết nối ngay.")) return;
  try { await goi(C.duong.khoa_xoa, {id: b.dataset.xoaKhoa}); location.reload(); }
  catch (x) { alert(x.message); }
});
$("#tao-khoa").onclick = async () => {
  const o = $("#khoa-moi");
  try {
    const d = await goi(C.duong.khoa, {ten: $("#ten-khoa").value});
    const cau_hinh = JSON.stringify({mcpServers: {javis: {url: C.url,
      headers: {Authorization: "Bearer " + d.khoa}}}}, null, 2);
    o.innerHTML = '<div class="the-moi"><p><b>Khoá cho ' + chu(d.ten) + '</b></p>'
      + '<p class="phu">Chép ngay: Javis chỉ hiện khoá này một lần.</p>'
      + '<div class="dia-chi" id="khoa-tho"></div><button class="chinh" id="chep-khoa" type="button">Chép khoá</button>'
      + '<ol class="buoc" style="margin-top:16px"><li>Địa chỉ: địa chỉ kết nối ở trên.</li>'
      + '<li>Header: <code>Authorization: Bearer &lt;khoá&gt;</code></li>'
      + '<li>Công cụ nhận cấu hình dạng JSON thì dán đoạn dưới (tên ô có thể khác).</li></ol>'
      + '<pre id="cau-hinh"></pre><button class="phu-nut" id="chep-cau-hinh" type="button">Chép cấu hình</button>'
      + '<button class="phu-nut" type="button" onclick="location.reload()">Xong</button></div>';
    $("#khoa-tho").textContent = d.khoa;
    $("#cau-hinh").textContent = cau_hinh;
    $("#chep-khoa").onclick = (ev) => chep(d.khoa, ev.currentTarget);
    $("#chep-cau-hinh").onclick = (ev) => chep(cau_hinh, ev.currentTarget);
  } catch (x) { o.innerHTML = '<p class="loi">' + chu(x.message) + '</p>'; }
};
$("#ngat").onclick = async () => {
  if (!confirm("Ngắt mọi kết nối, kể cả mọi khoá riêng? Muốn dùng lại phải kết nối lại từ từng công cụ.")) return;
  try { await goi(C.duong.ngat); location.reload(); }
  catch (x) { alert(x.message); }
};
})();
"""


# ============================================================
# Handler HTTP
# ============================================================

def _tat():
    # 404 chứ không phải 403: chưa có mật khẩu nghĩa là cửa này KHÔNG tồn tại, không quảng bá gì.
    from starlette.responses import JSONResponse
    return JSONResponse({"error": "not_found"}, status_code=404)


def _json(payload, status=200):
    from starlette.responses import JSONResponse
    return JSONResponse(payload, status_code=status,
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def _doc_form(request) -> dict:
    """Thân POST dạng form hoặc JSON. OAuth chuẩn là form; vài client gửi JSON."""
    loai = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in loai:
            d = await request.json()
            return d if isinstance(d, dict) else {}
        return dict(await request.form())
    except Exception:
        return {}


def register(ctx):
    # ---- khám phá (bắt buộc nằm ở gốc tên miền, nên đi qua register_well_known) ----
    def wk_pr(request, ctx):
        if not _co_mat_khau():
            return _tat()
        duoi = request.url.path[len("/.well-known/oauth-protected-resource"):].rstrip("/")
        if duoi not in ("", _p(ctx, "mcp"), _p(ctx, "mcp", SLUG_CU)):
            return _tat()
        return _json(metadata_pr(ctx, _goc(request), SLUG_CU if duoi == _p(ctx, "mcp", SLUG_CU) else ""))

    def wk_as(request, ctx):
        if not _co_mat_khau():
            return _tat()
        return _json(metadata_as(ctx, _goc(request)))

    ctx.register_well_known("oauth-protected-resource", wk_pr)
    ctx.register_well_known("oauth-authorization-server", wk_as)
    ctx.register_well_known("openid-configuration", wk_as)

    # ---- đăng ký client (RFC 7591) ----
    async def dang_ky(request, ctx):
        if not _co_mat_khau():
            return _tat()
        try:
            body = await request.json()
        except Exception:
            body = {}
        ma, d = dang_ky_client(ctx, body if isinstance(body, dict) else {})
        return _json(d, ma)

    ctx.register_http("oauth/register", dang_ky, methods=("POST",), public=True, no_cookie=True)

    # ---- trang Cho phép ----
    # GET công khai nhưng GIỮ cookie: chưa đăng nhập thì hiện ô đăng nhập ngay tại đó.
    # POST KHÔNG công khai: lõi Javis tự đòi phiên trình duyệt thật và chặn CSRF.
    def uy_quyen(request, ctx):
        from starlette.responses import RedirectResponse
        if not _co_mat_khau():
            return _trang_loi("Javis chưa có mật khẩu quản trị nên chưa kết nối được AI bên ngoài.", 404)
        kq, gia_tri = bat_dau_uy_quyen(ctx, dict(request.query_params))
        if kq == "trang_loi":
            return _trang_loi(gia_tri)
        if kq == "chuyen":
            return RedirectResponse(gia_tri, status_code=302)
        if not _co_phien(request):
            return _trang_dang_nhap()
        return _trang_dong_y(ctx, gia_tri)

    async def uy_quyen_xac_nhan(request, ctx):
        from starlette.responses import RedirectResponse
        if not _co_mat_khau():
            return _trang_loi("Javis chưa có mật khẩu quản trị.", 404)
        f = await _doc_form(request)
        url = ket_thuc_uy_quyen(ctx, str(f.get("yeu_cau") or ""), f.get("hanh_dong") == "cho_phep")
        if not url:
            return _trang_loi("Yêu cầu này đã quá hạn. Mở lại ChatGPT hoặc Claude và kết nối lại.")
        return RedirectResponse(url, status_code=302)

    ctx.register_http("oauth/authorize", uy_quyen, public=True)
    ctx.register_http("oauth/authorize", uy_quyen_xac_nhan, methods=("POST",))

    # ---- đổi token + thu hồi ----
    async def token(request, ctx):
        if not _co_mat_khau():
            return _tat()
        ma, d = doi_token(ctx, await _doc_form(request))
        return _json(d, ma)

    async def thu_hoi_token(request, ctx):
        if not _co_mat_khau():
            return _tat()
        thu_hoi(ctx, str((await _doc_form(request)).get("token") or ""))
        return _json({})

    ctx.register_http("oauth/token", token, methods=("POST",), public=True, no_cookie=True)
    ctx.register_http("oauth/revoke", thu_hoi_token, methods=("POST",), public=True, no_cookie=True)

    # ---- MCP ----
    async def mcp(request, ctx):
        if not _co_mat_khau():
            return _tat()
        raw = str(request.headers.get("authorization") or "")
        raw = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        rec = kiem_token(ctx, raw)
        nguon = ten_client(ctx, rec) if rec else ""
        if not rec:
            # Khoá riêng chủ tạo trên trang của gói cho công cụ chỉ biết dán header. Tên khoá là tên nguồn.
            rec = kiem_khoa_rieng(ctx, raw)
            nguon = ten_ai(rec.get("ten") or "") if rec else ""
        if not rec:
            from starlette.responses import JSONResponse
            return JSONResponse(
                {"error": "unauthorized", "error_description": "reconnect from ChatGPT or Claude"},
                status_code=401,
                headers={"WWW-Authenticate": www_authenticate(
                    ctx, _goc(request), "missing, invalid or expired token", _slug_goi(ctx, request))})
        import mcp_hub
        # Mức quyền đọc TỪNG LƯỢT, không đóng băng vào token: chủ hạ quyền là có hiệu lực ngay.
        # include_ambient=False: tool native của tài khoản Claude không liên quan ở đây.
        # raw_vault=None: brain do Javis tự suy (brain đang mở), không nhận header từ ngoài.
        # gate: bốn tool chat của lõi (tìm, đọc, gửi vào hội thoại, đọc kết quả), kèm tên AI đang gọi
        # để khung chat của Javis ghi "từ ChatGPT" / "từ Claude".
        return await mcp_hub.tra_loi_jsonrpc(request, muc_quyen(ctx), include_plugins=True,
                                             include_ambient=False, raw_vault=None,
                                             gate={"source": nguon})

    def mcp_khac(request, ctx):
        # Streamable HTTP không trạng thái: không có luồng do máy chủ mở, không có phiên để xoá.
        if not _co_mat_khau():
            return _tat()
        from starlette.responses import JSONResponse
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32000, "message": "Chỉ nhận POST."}},
                            status_code=405, headers={"Allow": "POST"})

    ctx.register_http("mcp", mcp, methods=("POST",), public=True, no_cookie=True)
    ctx.register_http("mcp", mcp_khac, methods=("GET", "DELETE"), public=True, no_cookie=True)

    # ---- trang của gói (lõi Javis đòi phiên trình duyệt thật cho cả ba) ----
    ctx.register_http("", lambda request, ctx: _trang_cai_dat(ctx, request))

    async def cai_dat(request, ctx):
        f = await _doc_form(request)
        if not dat_muc_quyen(ctx, str(f.get("muc_quyen") or "")):
            return _json({"ok": False, "error": "Mức quyền không hợp lệ."}, 400)
        return _json({"ok": True, "muc_quyen": muc_quyen(ctx)})

    ctx.register_http("cai-dat", cai_dat, methods=("POST",))

    async def nguon(request, ctx):
        f = await _doc_form(request)
        ok, loi = dat_nguon(ctx, str(f.get("goc") or ""), f.get("cho") in (True, "1", "true", 1))
        return _json({"ok": ok, **({"error": loi} if loi else {})}, 200 if ok else 400)

    async def khoa(request, ctx):
        ok, d = tao_khoa_rieng(ctx, str((await _doc_form(request)).get("ten") or ""))
        return _json({"ok": ok, **d}, 200 if ok else 400)

    async def khoa_xoa(request, ctx):
        ok = xoa_khoa_rieng(ctx, str((await _doc_form(request)).get("id") or ""))
        return _json({"ok": ok, **({} if ok else {"error": "Không thấy khoá này."})}, 200 if ok else 404)

    # Không `public`: lõi Javis đòi phiên trình duyệt thật và chặn CSRF, nên chỉ chủ mở được cửa mới.
    ctx.register_http("nguon", nguon, methods=("POST",))
    ctx.register_http("khoa", khoa, methods=("POST",))
    ctx.register_http("khoa-xoa", khoa_xoa, methods=("POST",))

    async def ngat_mot_http(request, ctx):
        ok = ngat_mot(ctx, str((await _doc_form(request)).get("ho") or ""))
        return _json({"ok": ok, **({} if ok else {"error": "Không thấy kết nối này."})}, 200 if ok else 404)

    ctx.register_http("ngat-mot", ngat_mot_http, methods=("POST",))
    ctx.register_http("ngat", lambda request, ctx: _json({"ok": True, "da_ngat": ngat_tat_ca(ctx)}),
                      methods=("POST",))
