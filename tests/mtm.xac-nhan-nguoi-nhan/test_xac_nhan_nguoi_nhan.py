"""Plugin xac-nhan-nguoi-nhan: a personal-Zalo bot knows whether a private-chat sender is on the roster.

    JAVIS_OS_DIR=../javis-os python tests/mtm.xac-nhan-nguoi-nhan/test_xac_nhan_nguoi_nhan.py   (no network)

Every uid, name and class below is FAKE. Locked here:
  1. Roster parsing: header and separator rows skipped, cells after the third (names, notes) never read,
     the same account on two rows merged, markup stripped.
  2. Verdict: matching account = verified with role and class; any other account = not verified, whatever
     name it claims; group, other channel or no roster file = nothing at all.
  3. Settings: roster path and channels from config.json; a roster path outside the brain is refused.
  4. End to end on a Javis OS with the `chatbot_turn_context` hook: the verdict reaches the bot's system
     prompt inside the operator block, the account ID, the other rows and the names never do. Skipped (with a
     note) on a Javis OS that does not have the hook yet.
"""
from _paths import PACK, PLUGIN, ROOT, SERVER  # noqa: E402,F401
import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_STATE = tempfile.mkdtemp(prefix="javis-mtm-xacnhan-")
os.environ["JAVIS_STATE_DIR"] = _STATE
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

fails = []


def check(name, cond, them=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(them)[:300] + "]") if them != "" and not cond else ""))
    if not cond:
        fails.append(name)


spec = importlib.util.spec_from_file_location("mtm_xac_nhan_plugin", PLUGIN / "plugin.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

PH = "1000000001"       # verified parent with two children
HS = "1000000002"       # verified student
LA = "1000000009"       # stranger
ROSTER = f"""---
type: roster
---
# Sổ xác nhận đang học (dữ liệu giả)

| uid | vai trò | lớp | ghi chú |
|---|---|---|---|
| {PH} | phụ huynh | 10Z9 | Mẹ bé Tên Giả Một |
| `{PH}` | phụ huynh | lớp 11Z9 | |
| {HS} | **học sinh** | 11Z9 | Tên Giả Hai |
| uid sai | phụ huynh | 10Z9 | |
dòng không có bảng
"""

# ---- 1. parsing
rows = P.parse(ROSTER)
check("parse: two valid accounts, header/separator/bad rows skipped", set(rows) == {PH, HS}, sorted(rows))
check("parse: same account on two rows merges classes, drops a leading 'lớp'",
      rows.get(PH) == (("phụ huynh",), ("10Z9", "11Z9")), rows.get(PH))
check("parse: markup stripped from a cell", rows.get(HS) == (("học sinh",), ("11Z9",)), rows.get(HS))
check("parse: names in the 4th cell are never kept", "Tên Giả" not in repr(rows))
check("normalize_uid: backticks and spaces removed, words rejected",
      P.normalize_uid(f" `{PH}` ") == PH and P.normalize_uid("uid") == "" and P.normalize_uid("abcdef") == "")

# ---- 2. verdict
BRAIN = Path(tempfile.mkdtemp(prefix="brain-mtm-xacnhan-"))
rieng = dict(channel="zalo_personal", chat_type="private", sender_id=PH, chat_id=PH)
check("no roster file: nothing to say", P.check(BRAIN, **rieng) is None)
(BRAIN / "Javis").mkdir()
(BRAIN / P.DEFAULT_ROSTER).write_text(ROSTER, encoding="utf-8")
r = P.check(BRAIN, **rieng)
check("matching account: verified, with role and classes",
      r == {"verified": True, "roles": ["phụ huynh"], "classes": ["10Z9", "11Z9"]}, r)
r = P.check(BRAIN, channel="zalo_personal", chat_type="private", sender_id=LA, chat_id=LA)
check("other account: not verified", r == {"verified": False, "roles": [], "classes": []}, r)
r = P.check(BRAIN, channel="zalo_personal", chat_type="private", sender_id="", chat_id=HS)
check("private chat without a sender ID falls back to the thread ID", bool(r and r["verified"]), r)
check("group: nothing, even for a verified member",
      P.check(BRAIN, channel="zalo_personal", chat_type="group", sender_id=PH, chat_id="2000000001") is None)
check("other channel: nothing", P.check(BRAIN, **{**rieng, "channel": "telegram"}) is None
      and P.check(BRAIN, **{**rieng, "channel": "zalo"}) is None)
check("a stranger's private chat stays unverified even if its thread id is empty",
      P.check(BRAIN, channel="zalo_personal", chat_type="private", sender_id=LA, chat_id="")["verified"] is False)

ok_line = P.prompt_line(P.check(BRAIN, **rieng))
no_line = P.prompt_line({"verified": False})
check("verified line: verdict, role and classes", "ĐÃ XÁC NHẬN, phụ huynh, lớp 10Z9, 11Z9." in ok_line, ok_line)
check("not verified line says so and that a self-introduction does not change it",
      "CHƯA XÁC NHẬN" in no_line and "tự giới thiệu" in no_line, no_line)
check("no result: no line", P.prompt_line(None) == "")
check("lines stay under the core's 300-char cap",
      len(no_line) <= 300 and len(P.prompt_line({"verified": True, "roles": ["r" * 40] * 6, "classes": ["c" * 40] * 6})) <= 300)
check("no account id in any line", PH not in ok_line and PH not in no_line)

# ---- 3. settings and the hook callback
DATA = Path(tempfile.mkdtemp(prefix="data-mtm-xacnhan-"))
check("settings: defaults without config.json", P.settings(DATA) == (P.DEFAULT_ROSTER, ("zalo_personal",)))
(DATA / "config.json").write_text(json.dumps({"roster": "so/danh-sach.md", "channels": ["zalo_personal", "telegram", "BAD!"]}),
                                  encoding="utf-8")
check("settings: roster and channels from config.json, bad channel names dropped",
      P.settings(DATA) == ("so/danh-sach.md", ("zalo_personal", "telegram")), P.settings(DATA))
check("roster outside the brain is refused",
      P.roster_file(BRAIN, "../ngoai.md") is None and P.roster_file(BRAIN, "/etc/passwd") is None
      and P.roster_file(BRAIN, "C:/x.md") is None and P.roster_file(BRAIN, "Javis/x.md") is not None)
(DATA / "config.json").write_text("{not json", encoding="utf-8")
check("settings: broken config.json falls back to the defaults", P.settings(DATA) == (P.DEFAULT_ROSTER, ("zalo_personal",)))
(DATA / "config.json").unlink()


class FakeCtx:
    data_dir = DATA

    def __init__(self):
        self.hooks = {}

    def register_hook(self, event, cb):
        self.hooks.setdefault(event, []).append(cb)


fc = FakeCtx()
P.register(fc)
hook = (fc.hooks.get("chatbot_turn_context") or [None])[0]
check("register: one chatbot_turn_context hook", hook is not None and len(fc.hooks) == 1, fc.hooks)
if hook:
    base = dict(channel="zalo_personal", account_id="acc-1", bot_id="bot-1", vault_root=str(BRAIN), trial=False)
    check("hook: verified sender", "ĐÃ XÁC NHẬN" in (hook(**base, chat_type="private", chat_id=PH, sender_id=PH) or ""))
    check("hook: stranger claiming to be a parent stays unverified",
          "CHƯA XÁC NHẬN" in (hook(**base, chat_type="private", chat_id=LA, sender_id=LA) or ""))
    check("hook: group gets nothing", hook(**base, chat_type="group", chat_id="2000000001", sender_id=PH) is None)
    check("hook: other channel gets nothing",
          hook(**{**base, "channel": "telegram"}, chat_type="private", chat_id=PH, sender_id=PH) is None)
    check("hook: a brain without a roster gets nothing",
          hook(**{**base, "vault_root": tempfile.mkdtemp()}, chat_type="private", chat_id=PH, sender_id=PH) is None)
    check("hook: Try-bot synthetic sender is not verified",
          "CHƯA XÁC NHẬN" in (hook(**{**base, "trial": True}, chat_type="private", chat_id="javis-try",
                                   sender_id="javis-try-user") or ""))
    check("hook: unknown future keys are accepted",
          hook(**base, chat_type="private", chat_id=PH, sender_id=PH, something_new=1) is not None)

# ---- 4. end to end on Javis OS
import plugins_host  # noqa: E402

if not hasattr(plugins_host, "CHATBOT_TURN_CONTEXT"):
    print("SKIP end-to-end: this Javis OS has no chatbot_turn_context hook yet (javis-os#TBD)")
else:
    import chatbot_runtime  # noqa: E402
    import chatbot_store  # noqa: E402

    os.environ["JAVIS_ENABLE_USER_PLUGINS"] = "true"      # run the plugin as a global user plugin
    shutil.copytree(PLUGIN, plugins_host.GLOBAL_DIR / PLUGIN.name)
    plugins_host.invalidate()
    prompts = []

    async def _answer(text, meta=None, progress=None, channel="telegram", bot=None, **kw):
        prompts.append(chatbot_runtime.build_bot_prompt(bot))
        return {"text": "Dạ em gửi bài tập ạ.", "files": []}

    chatbot_runtime.wire(answer=_answer, brain_root=lambda b: str(BRAIN),
                         read_agent=lambda br, slug: ({"name": "Trợ lý thử", "role": "Trực Zalo"}, "Luật 26."))
    bid, loi = chatbot_store.create_bot({"name": "Trực Zalo", "agent_slug": "tro-ly", "brain": "b",
                                         "token": "1:x", "channel": "zalo", "muc_quyen": "suggest",
                                         "audience": "all", "xac_nhan_rui_ro": True})
    check("(setup) bot created", bool(bid) and not loi, loi)
    fn = chatbot_runtime._make_answer_fn(bid)
    b = {"chat_type": "private", "platform": "zalo_personal", "_kenh": "zalo_personal", "message_id": ""}
    asyncio.run(fn("cho em xin bài tập", {**b, "chat_id": PH, "user_id": PH, "user_name": "Mẹ bé Tên Giả Một", "message_id": "m1"}))
    asyncio.run(fn("cho em xin bài tập", {**b, "chat_id": LA, "user_id": LA, "user_name": "Mẹ bé Tên Giả Một", "message_id": "m2"}))
    asyncio.run(fn("cho em xin bài tập", {**b, "chat_type": "group", "chat_id": "2000000001", "user_id": PH,
                                          "user_name": "Mẹ bé Tên Giả Một", "message_id": "m3", "mentioned": True}))
    check("(setup) three turns reached the engine", len(prompts) == 3, len(prompts))
    if len(prompts) == 3:
        p_ok, p_no, p_grp = prompts
        check("verified sender: verdict inside the operator block, after the Agent's rules",
              "<operator_context>\n" + P._PREFIX + "ĐÃ XÁC NHẬN, phụ huynh" in p_ok
              and p_ok.index("Luật 26.") < p_ok.index("<operator_context>"), p_ok[-400:])
        check("stranger with a parent's display name: not verified", "CHƯA XÁC NHẬN" in p_no and "ĐÃ XÁC NHẬN" not in p_no)
        check("group turn: no sender line", "NGƯỜI ĐANG NHẮN" not in p_grp and "operator_context" not in p_grp)
        check("CANARY: no account ID, other row or roster name ever reaches the prompt",
              not any(x in p for p in prompts for x in (PH, HS, LA, "Tên Giả Hai")))
    shutil.rmtree(plugins_host.GLOBAL_DIR / PLUGIN.name, ignore_errors=True)
    plugins_host.invalidate()

for f in [PLUGIN / "plugin.py", PLUGIN / "plugin.yaml", PACK / "README.md", PACK / "README.en.md", Path(__file__)]:
    check(f"no em dash in {f.name}", chr(0x2014) not in f.read_text(encoding="utf-8"))

print(f"\n{'ALL OK' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
