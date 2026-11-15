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
:root { color-scheme: light dark; --nen:#f6f5f2; --the:#fff; --chu:#1d1d1f; --phu:#6b6b70;
        --vien:#dcdad4; --nhan:#c2410c; --nhan-chu:#fff; --loi:#dc2626; --tot:#15803d; }
@media (prefers-color-scheme: dark) {
  :root { --nen:#141414; --the:#1f1f1f; --chu:#eee; --phu:#a3a3a8; --vien:#3a3a3a;
          --tot:#4ade80; --loi:#f87171; }
}
* { box-sizing: border-box; }
body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
       background:var(--nen); color:var(--chu); font-size:17px; line-height:1.5;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:16px; }
.the { background:var(--the); border:1px solid var(--vien); border-radius:16px; padding:28px 24px;
       width:100%; max-width:480px; }
h1 { font-size:22px; margin:0 0 8px; }
h2 { font-size:18px; margin:22px 0 8px; }
p { margin:0 0 14px; }
.phu { color:var(--phu); font-size:16px; }
ul { margin:0 0 18px; padding-left:20px; }
li { margin-bottom:6px; }
label { display:block; font-size:16px; margin:12px 0 6px; }
input, select { width:100%; font-size:17px; padding:12px; border-radius:10px;
                border:1px solid var(--vien); background:transparent; color:inherit; }
button { width:100%; font-size:17px; padding:13px; border-radius:10px; border:0; cursor:pointer;
         margin-top:12px; }
.chinh { background:var(--nhan); color:var(--nhan-chu); }
.phu-nut { background:transparent; color:var(--chu); border:1px solid var(--vien); }
.loi { color:var(--loi); font-size:16px; margin-top:10px; }
.tot { color:var(--tot); font-size:16px; margin-top:10px; }
code { font-size:16px; word-break:break-all; }
a { color:var(--nhan); }
.hop { display:flex; gap:8px; align-items:stretch; }
.hop input { flex:1; }
.hop button { width:auto; margin:0; padding:0 16px; }
.dong { display:flex; gap:10px; align-items:center; justify-content:space-between; padding:10px 0;
        border-bottom:1px solid var(--vien); }
.dong div { min-width:0; overflow-wrap:anywhere; }
.dong button { width:auto; margin:0; padding:9px 14px; flex:none; }
pre { font-size:15px; white-space:pre-wrap; overflow-wrap:anywhere; background:var(--nen); padding:12px;
      border-radius:10px; border:1px solid var(--vien); margin:8px 0; }
.the-moi { border:1px solid var(--nhan); border-radius:12px; padding:14px; margin-top:12px; }
"""


def _trang(than: str, status: int = 200):
    # `same-origin`, KHÔNG phải `no-referrer`. URL trang Cho phép mang state và code_challenge
    # nên không được rò sang trang khác qua Referer, và cả hai giá trị đều chặn được điều đó.
    # Nhưng `no-referrer` còn làm trình duyệt gửi `Origin: null` khi nộp form, hàng rào CSRF
    # của Javis coi "null" là nguồn lạ và chặn nút Cho phép (đo trên Chromium thật 23/09).
    from starlette.responses import HTMLResponse
    return HTMLResponse(
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="same-origin"><title>Javis Gate</title>'
        f'<style>{_CSS}</style></head><body><div class="the">{than}</div></body></html>',
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                 "Content-Security-Policy": "frame-ancestors 'none'"})


_MO_TA_MUC = {
    "full": "Toàn quyền: đọc dữ liệu, nhắn vào khung chat để Javis làm, và làm việc thật (gửi tin, tạo đơn, đăng bài...)",
    "auto": "Tự làm có giới hạn: đọc dữ liệu và viết nháp, không làm việc ra bên ngoài",
    "suggest": "Chỉ đọc và gợi ý: không ghi file, không làm việc ra bên ngoài",
}


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
    if not ts:
        return "chưa dùng"
    try:
        import localefmt
        return localefmt.format_ts(ts, "%d/%m/%Y %H:%M")
    except Exception:
        return time.strftime("%d/%m/%Y %H:%M", time.localtime(float(ts)))


def _ds_nguon(ctx) -> str:
    """Hai danh sách của mục 3: địa chỉ đang xin nối (nút Cho phép) và địa chỉ đã cho phép thêm (nút Bỏ)."""
    phan = []
    cho = sorted(_doc(ctx)["cho_duyet"].items(), key=lambda gx: -float(gx[1].get("luc") or 0))
    for g, x in cho:
        phan.append(f'<div class="dong"><div><code>{html.escape(g)}</code><br><span class="phu">'
                    f'{html.escape(str(x.get("ten") or ""))}, {html.escape(_ngay(x.get("luc")))}</span></div>'
                    f'<button class="chinh" type="button" data-nguon="{html.escape(g)}" data-cho="1">Cho phép</button></div>')
    for g in nguon_them(ctx):
        phan.append(f'<div class="dong"><div><code>{html.escape(g)}</code><br><span class="phu">đã cho phép</span></div>'
                    f'<button class="phu-nut" type="button" data-nguon="{html.escape(g)}" data-cho="0">Bỏ</button></div>')
    return "".join(phan) or '<p class="phu">Chưa có công cụ nào xin kết nối.</p>'


def _ds_khoa(ctx) -> str:
    phan = [f'<div class="dong"><div><b>{html.escape(str(k.get("ten") or ""))}</b><br><span class="phu">tạo '
            f'{html.escape(_ngay(k.get("tao")))}, dùng lần cuối {html.escape(_ngay(k.get("dung")))}</span></div>'
            f'<button class="phu-nut" type="button" data-xoa-khoa="{html.escape(str(k.get("id") or ""))}" '
            f'data-ten="{html.escape(str(k.get("ten") or ""))}">Thu hồi</button></div>' for k in ds_khoa_rieng(ctx)]
    return "".join(phan)


def _trang_loi(cau: str, status: int = 400):
    return _trang(f"<h1>Không kết nối được</h1><p>{html.escape(cau)}</p>", status)


def _trang_cai_dat(ctx, request):
    goc = _goc(request)
    url = goc + _p(ctx, "mcp")
    muc = muc_quyen(ctx)
    https = goc.startswith("https://")
    if not _co_mat_khau():
        than_chinh = ('<p class="loi">Javis chưa có mật khẩu quản trị. Bước Cho phép dựa vào '
                      'đăng nhập, nên cửa này đang đóng. Đặt mật khẩu ở Cài đặt rồi quay lại.</p>')
    else:
        chon = "".join(
            f'<option value="{m}"{" selected" if m == muc else ""}>{html.escape(_MO_TA_MUC[m])}</option>'
            for m in MUC_QUYEN)
        canh_bao = "" if https else (
            '<p class="loi">ChatGPT và Claude chỉ nhận địa chỉ https. Gắn tên miền riêng cho Javis '
            '(Cài đặt, mục Giọng nói, thương hiệu và truy cập) rồi tải lại trang này.</p>')
        than_chinh = f"""
<h2>1. Dán địa chỉ này vào ChatGPT, Claude hay công cụ AI khác</h2>
<p class="phu"><b>ChatGPT:</b> Settings, Apps &amp; Connectors, bật Developer mode, rồi Create. Ô "MCP Server URL", xác thực OAuth.</p>
<p class="phu"><b>Claude:</b> Settings, Connectors, Add custom connector. Dán địa chỉ rồi bấm Add.</p>
<p class="phu"><b>Công cụ khác</b> (Gemini CLI, Codex, Cursor, Le Chat, Copilot...): chỗ thêm "MCP server" bằng
địa chỉ. Công cụ đòi đăng nhập OAuth thì làm như ChatGPT; công cụ chỉ cho dán header thì tạo khoá ở mục 4.</p>
<div class="hop"><input id="url" readonly value="{html.escape(url)}">
<button class="phu-nut" id="chep" type="button">Chép</button></div>
{canh_bao}
<h2>2. Mức quyền của AI bên ngoài</h2>
<select id="muc">{chon}</select>
<div id="tb" class="phu" role="status"></div>
<h2>3. Công cụ đang xin kết nối</h2>
<p class="phu">Javis tự nhận ChatGPT, Claude và ứng dụng chạy trên chính máy bạn. Công cụ khác xin kết nối thì hiện
ở đây. Chỉ bấm Cho phép khi bạn vừa tự kết nối từ đúng công cụ đó, rồi kết nối lại từ công cụ.</p>
{_ds_nguon(ctx)}
<h2>4. Khoá riêng cho công cụ chỉ biết dán header</h2>
<p class="phu">Cho n8n, script của bạn, hay trình soạn mã không đăng nhập OAuth được. Mỗi công cụ một khoá, tên khoá
là tên hiện trên khung chat của Javis. Ai cầm khoá là dùng được Javis ở mức quyền trên, nên đừng dán nó vào chỗ
công khai.</p>
<div class="hop"><input id="ten-khoa" maxlength="40" placeholder="Tên công cụ, ví dụ Cursor">
<button class="chinh" id="tao-khoa" type="button">Tạo khoá</button></div>
<div id="khoa-moi"></div>
{_ds_khoa(ctx)}
<h2>5. Kết nối đang dùng: <span id="so">{so_ket_noi(ctx)}</span></h2>
<button class="phu-nut" id="ngat" type="button">Ngắt mọi kết nối</button>
<script>
const tb = document.getElementById("tb");
const URL_MCP = {json.dumps(url)};
function chu(s) {{ const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }}
document.getElementById("chep").onclick = async (ev) => {{
  const o = document.getElementById("url");
  try {{ await navigator.clipboard.writeText(o.value); }}
  catch (x) {{ o.select(); document.execCommand("copy"); }}
  ev.target.textContent = "Đã chép";
  setTimeout(() => ev.target.textContent = "Chép", 1500);
}};
async function goi(duong, body) {{
  const r = await fetch(duong, {{method: "POST", credentials: "same-origin",
    headers: {{"Content-Type": "application/json"}}, body: JSON.stringify(body || {{}})}});
  const d = await r.json().catch(() => ({{}}));
  if (!r.ok || !d.ok) throw new Error(d.error || ("Lỗi " + r.status));
  return d;
}}
document.getElementById("muc").onchange = async (ev) => {{
  tb.className = "phu"; tb.textContent = "Đang lưu...";
  try {{ await goi({json.dumps(_p(ctx, "cai-dat"))}, {{muc_quyen: ev.target.value}});
         tb.className = "tot"; tb.textContent = "Đã lưu. Có hiệu lực từ lượt gọi kế tiếp."; }}
  catch (x) {{ tb.className = "loi"; tb.textContent = x.message; }}
}};
document.getElementById("ngat").onclick = async () => {{
  if (!confirm("Ngắt mọi kết nối, kể cả mọi khoá riêng? Muốn dùng lại phải kết nối lại từ từng công cụ.")) return;
  try {{ await goi({json.dumps(_p(ctx, "ngat"))});
         location.reload(); }}
  catch (x) {{ tb.className = "loi"; tb.textContent = x.message; }}
}};
document.querySelectorAll("[data-nguon]").forEach((b) => b.onclick = async () => {{
  const cho = b.dataset.cho === "1";
  if (cho && !confirm("Cho phép " + b.dataset.nguon + "? Chỉ đồng ý nếu bạn vừa tự kết nối từ công cụ này.")) return;
  try {{ await goi({json.dumps(_p(ctx, "nguon"))}, {{goc: b.dataset.nguon, cho: cho}}); location.reload(); }}
  catch (x) {{ alert(x.message); }}
}});
document.querySelectorAll("[data-xoa-khoa]").forEach((b) => b.onclick = async () => {{
  if (!confirm("Thu hồi khoá " + b.dataset.ten + "? Công cụ đang dùng nó sẽ mất kết nối ngay.")) return;
  try {{ await goi({json.dumps(_p(ctx, "khoa-xoa"))}, {{id: b.dataset.xoaKhoa}}); location.reload(); }}
  catch (x) {{ alert(x.message); }}
}});
document.getElementById("tao-khoa").onclick = async () => {{
  const o = document.getElementById("khoa-moi");
  try {{
    const d = await goi({json.dumps(_p(ctx, "khoa"))}, {{ten: document.getElementById("ten-khoa").value}});
    const cau_hinh = JSON.stringify({{mcpServers: {{javis: {{url: URL_MCP,
      headers: {{Authorization: "Bearer " + d.khoa}}}}}}}}, null, 2);
    o.innerHTML = '<div class="the-moi"><p><b>Khoá cho ' + chu(d.ten) + '</b>. Chép ngay: Javis chỉ hiện khoá này '
      + 'MỘT lần.</p><div class="hop"><input id="khoa-tho" readonly><button class="phu-nut" id="chep-khoa" '
      + 'type="button">Chép</button></div><p class="phu">Trong công cụ: địa chỉ là địa chỉ ở mục 1, header '
      + '<code>Authorization: Bearer &lt;khoá&gt;</code>. Nhiều công cụ nhận cấu hình dạng này (tên ô có thể khác):</p>'
      + '<pre id="cau-hinh"></pre><button class="phu-nut" type="button" onclick="location.reload()">Xong</button></div>';
    document.getElementById("khoa-tho").value = d.khoa;
    document.getElementById("cau-hinh").textContent = cau_hinh;
    document.getElementById("chep-khoa").onclick = async (ev) => {{
      try {{ await navigator.clipboard.writeText(d.khoa); }}
      catch (x) {{ const i = document.getElementById("khoa-tho"); i.select(); document.execCommand("copy"); }}
      ev.target.textContent = "Đã chép";
    }};
  }} catch (x) {{ o.innerHTML = '<p class="loi">' + chu(x.message) + '</p>'; }}
}};
</script>"""
    return _trang(f"""
<h1>Javis Gate</h1>
<p class="phu">Cổng cho ChatGPT, Claude và AI khác làm việc cùng Javis. AI đó đọc hội thoại của Javis
và nhắn vào khung chat như bạn gõ, nên bạn xem được tiến độ ngay trên Javis.
<a href="{HUONG_DAN}" target="_blank" rel="noopener">Hướng dẫn từng bước</a></p>
{than_chinh}""")


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
    ctx.register_http("ngat", lambda request, ctx: _json({"ok": True, "da_ngat": ngat_tat_ca(ctx)}),
                      methods=("POST",))
