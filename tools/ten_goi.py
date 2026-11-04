"""Danh bạ tên gói (`plan-names.json`): mã gói nhà cung cấp gửi -> tên họ đang bán.

Vì sao có tệp này
-----------------
Javis hiện tên gói thuê bao (Claude, ChatGPT qua Codex, Grok, Antigravity) trên trang Models và
vòng hạn mức. Chỉ Claude Code tự gửi tên đang bán (`planDisplayName`). Codex chỉ gửi mã, và mã
không theo kịp tên: `prolite` là gói 100 đô, `pro` là gói 200 đô đã đổi tên. Ghi bảng tên trong mã
Javis thì mỗi lần nhà cung cấp đổi tên phải ra bản mới, nên bảng nằm ở đây: sửa một dòng là mọi máy
thấy ở lần làm mới kế tiếp (Javis đọc tệp này, giữ 6 giờ như danh mục gói).

Lịch "Dò tên gói nhà cung cấp" (chạy trên cloud mỗi tuần) đọc tin đổi tên rồi mở Pull Request sửa
tệp này. Chủ kho bấm duyệt; không có gì tự trộn.

Luật
----
- `providers.<nhà>.<mã>`: tên hiện cho người dùng. Mã chữ thường, chữ số và `_`; tên 1-40 ký tự
  chữ, số, khoảng trắng và `.+-`, giống luật Claude Code áp cho tên gói của nó.
- Chỉ bốn nhà: `claude`, `codex`, `grok`, `agy`.
- Mã chưa chắc tên thì để ở `pending` kèm lý do, đừng đoán vào `providers`: tên sai còn tệ hơn mã.
"""
import json
import re
from pathlib import Path

NHA = ("claude", "codex", "grok", "agy")
MA = re.compile(r"^[a-z0-9_]{1,64}$")


def ten_hop_le(v) -> bool:
    if not isinstance(v, str) or not 1 <= len(v) <= 40 or not v[0].isalnum():
        return False
    if len(v) > 1 and not (v[-1].isalnum() or v[-1] == "+"):
        return False
    return all(c.isalnum() or c in " .+-" for c in v)


def kiem(tep: Path) -> list:
    """Danh sách lỗi; rỗng là đạt."""
    if not tep.is_file():
        return [f"{tep.name}: thiếu tệp"]
    try:
        d = json.loads(tep.read_text(encoding="utf-8"))
    except ValueError as e:
        return [f"{tep.name}: JSON hỏng ({e})"]
    loi = []
    if d.get("format") != "javis-plan-names":
        loi.append(f"{tep.name}: format phải là javis-plan-names")
    if int(d.get("format_version") or 0) != 1:
        loi.append(f"{tep.name}: format_version phải là 1")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d.get("updated") or "")):
        loi.append(f"{tep.name}: updated phải là YYYY-MM-DD")
    pv = d.get("providers")
    if not isinstance(pv, dict):
        return loi + [f"{tep.name}: thiếu providers"]
    for nha, bang in pv.items():
        if nha not in NHA:
            loi.append(f"{tep.name}: nhà lạ `{nha}` (chỉ {', '.join(NHA)})")
            continue
        if not isinstance(bang, dict):
            loi.append(f"{tep.name}: providers.{nha} phải là bảng mã -> tên")
            continue
        for ma, ten in bang.items():
            if not MA.match(str(ma)):
                loi.append(f"{tep.name}: providers.{nha}: mã `{ma}` sai mẫu")
            if not ten_hop_le(ten):
                loi.append(f"{tep.name}: providers.{nha}.{ma}: tên `{ten}` sai mẫu")
    for nha, bang in (d.get("pending") or {}).items():
        for ma in (bang or {}):
            if ma in (pv.get(nha) or {}):
                loi.append(f"{tep.name}: `{nha}.{ma}` vừa có tên vừa nằm ở pending")
    return loi
