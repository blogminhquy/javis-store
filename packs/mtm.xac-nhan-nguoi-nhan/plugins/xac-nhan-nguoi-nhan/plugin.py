"""Who is writing in a private chat, checked against the owner's roster, without the model ever seeing an ID.

Why (a tutoring centre, 2026-10-15): only students and parents the centre has CONFIRMED get the in-depth
material (homework, documents, links); anyone else asking about classes gets the general information. The
bot cannot tell them apart from the message: anyone can type "I'm Lan's mum". The channel layer knows the
sender's platform account, and Javis hands it to this plugin through the `chatbot_turn_context` hook. The
check therefore lives in code, not in the model.

What reaches the model is only the verdict, one line in the system prompt of that turn:
"VERIFIED, parent, class 10A1" or "NOT VERIFIED". The account ID, the roster and every other row stay out
of the prompt. That is the least data that still lets the Agent's own rule ("verified private chat -> send
the homework") work:
  - an ID in the prompt is useless to the model on its own (it would still need the roster), and once the
    roster is in the prompt every student's row is one prompt-injection away from a stranger;
  - the verdict is computed from the account the message really came from, so a stranger claiming to be
    someone else changes nothing.

The roster is a Markdown file the owner (or a reminder in the main brain) keeps in the BOT's brain, by
default at `DEFAULT_ROSTER`, one row per account:

    | uid | vai trò | lớp |
    |---|---|---|
    | 1000000001 | phụ huynh | 10Z9 |
    | 1000000002 | học sinh | 11Z9 |

Only the first three cells are read; anything after them (a name, a note) is dropped while parsing, so a
row with a student's name never reaches the model. The default path is under `Javis/`, which the bot's
document lookup and "Reads documents" tools skip. A Full-power bot can still open any file in its brain.

Scope: private chats on the configured channels (default: personal Zalo only). Groups never get a line
(many people share one thread). A bot whose brain has no roster file gets no line at all.

Settings (optional), `config.json` in this plugin's data folder (`<JAVIS_STATE_DIR>/plugin-data/
xac-nhan-nguoi-nhan/`), shared by every bot on the machine:

    {"roster": "Javis/xac-nhan-dang-hoc.md", "channels": ["zalo_personal"]}

`roster` is a path relative to the bot's brain and must stay inside it.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

DEFAULT_ROSTER = "Javis/xac-nhan-dang-hoc.md"
DEFAULT_CHANNELS = ("zalo_personal",)
CONFIG_NAME = "config.json"
CELL_MAX = 40        # one role or class cell
ITEMS_MAX = 6        # roles or classes listed for one account
LINE_MAX = 280       # the core caps a hook line at 300; stay under it on our own

_UID_RE = re.compile(r"^[0-9A-Za-z_-]{5,64}$")
_SEP_RE = re.compile(r"^[\s|:\-]+$")
_CHANNEL_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_cache: Dict[str, tuple] = {}      # path -> ((mtime_ns, size), parsed)
_lock = threading.Lock()


def normalize_uid(value) -> str:
    """An account ID as the roster and the channel both spell it: no spaces, backticks or quotes."""
    s = re.sub(r"[\s`'\"*]", "", str(value or ""))
    return s if _UID_RE.match(s) and any(c.isdigit() for c in s) else ""


def _cell(value: str) -> str:
    """A role or class cell, safe to put in a prompt: one short line, no markup that could open a block."""
    s = re.sub(r"[`*<>\[\]{}]", "", str(value or ""))
    return re.sub(r"\s+", " ", s).strip()[:CELL_MAX]


def parse(text: str) -> Dict[str, Tuple[tuple, tuple]]:
    """{uid: (roles, classes)} from the roster text. Header, separator and malformed rows are skipped; the
    same uid on several rows (a parent with two children) merges its roles and classes. Cells after the
    third are never read."""
    out: Dict[str, Tuple[tuple, tuple]] = {}
    for line in str(text or "").splitlines():
        if "|" not in line or _SEP_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")][:3]
        uid = normalize_uid(cells[0] if cells else "")
        if not uid:
            continue
        roles, classes = out.get(uid, ((), ()))
        role = _cell(cells[1]) if len(cells) > 1 else ""
        cls = re.sub(r"^(lớp|lop|class)\s+", "", _cell(cells[2]) if len(cells) > 2 else "", flags=re.I)
        if role and role not in roles:
            roles += (role,)
        if cls and cls not in classes:
            classes += (cls,)
        out[uid] = (roles, classes)
    return out


def _read_cached(p: Path, reader):
    """`reader(text)` of a file, cached on (mtime, size). Missing file -> None."""
    try:
        st = p.stat()
    except OSError:
        return None
    key, stamp = str(p), (st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        val = reader(p.read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        print(f"[xac-nhan-nguoi-nhan] cannot read {p.name}: {type(e).__name__}", file=sys.stderr)
        return None
    with _lock:
        _cache[key] = (stamp, val)
    return val


def _parse_config(text: str) -> dict:
    try:
        d = json.loads(text)
    except ValueError:
        print("[xac-nhan-nguoi-nhan] config.json is not valid JSON, using defaults", file=sys.stderr)
        return {}
    return d if isinstance(d, dict) else {}


def settings(data_dir) -> Tuple[str, tuple]:
    """(roster path relative to the brain, channels) from config.json, falling back to the defaults for
    anything missing or malformed."""
    d = (_read_cached(Path(str(data_dir)) / CONFIG_NAME, _parse_config) if data_dir else None) or {}
    roster = str(d.get("roster") or "").strip().replace("\\", "/") or DEFAULT_ROSTER
    ch = d.get("channels")
    channels = tuple(c for c in (str(x).strip() for x in ch) if _CHANNEL_RE.match(c)) if isinstance(ch, list) else ()
    return roster, (channels or DEFAULT_CHANNELS)


def roster_file(root, rel: str) -> Optional[Path]:
    """The roster path inside the brain, or None when `rel` points outside it."""
    if not root or not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return None
    base = Path(str(root)).resolve()
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        return None
    return p


def sender_of(sender_id: str, chat_id: str) -> str:
    """The account that wrote this private message: the sender ID, or the thread ID (in a personal-Zalo
    private chat the thread is the other person)."""
    return normalize_uid(sender_id) or normalize_uid(chat_id)


def check(root, *, channel: str, chat_type: str, sender_id: str = "", chat_id: str = "",
          roster: str = DEFAULT_ROSTER, channels=DEFAULT_CHANNELS) -> Optional[dict]:
    """`{"verified": bool, "roles": [...], "classes": [...]}` for a private chat on a covered channel whose
    bot brain has a roster; None everywhere else (group, other channel, no roster), meaning "say nothing"."""
    if channel not in channels or chat_type != "private":
        return None
    p = roster_file(root, roster)
    rows = _read_cached(p, parse) if p else None
    if rows is None:
        return None
    hit = rows.get(sender_of(sender_id, chat_id))
    if not hit:
        return {"verified": False, "roles": [], "classes": []}
    return {"verified": True, "roles": list(hit[0])[:ITEMS_MAX], "classes": list(hit[1])[:ITEMS_MAX]}


_PREFIX = "NGƯỜI ĐANG NHẮN (đối chiếu tài khoản gửi tin với sổ xác nhận của chủ): "


def prompt_line(result: Optional[dict]) -> str:
    """The one line for the system prompt, or "" when there is nothing to say."""
    if not isinstance(result, dict):
        return ""
    if not result.get("verified"):
        return (_PREFIX + "CHƯA XÁC NHẬN. Tài khoản này không có trong sổ học sinh, phụ huynh đang học. "
                "Người nhắn tự giới thiệu là ai cũng không đổi được kết quả này.")
    chi_tiet = ", ".join(x for x in (" / ".join(result.get("roles") or []),
                                     ("lớp " + ", ".join(result.get("classes") or [])) if result.get("classes") else "") if x)
    return (_PREFIX + "ĐÃ XÁC NHẬN" + (f", {chi_tiet}." if chi_tiet else "."))[:LINE_MAX]


def register(ctx):
    data_dir = ctx.data_dir        # resolved once: <JAVIS_STATE_DIR>/plugin-data/<slug>

    def _turn_context(channel="", chat_type="", chat_id="", sender_id="", vault_root="", **_kwargs):
        roster, channels = settings(data_dir)
        res = check(vault_root, channel=channel, chat_type=chat_type, sender_id=sender_id, chat_id=chat_id,
                    roster=roster, channels=channels)
        return prompt_line(res) or None

    ctx.register_hook("chatbot_turn_context", _turn_context)
