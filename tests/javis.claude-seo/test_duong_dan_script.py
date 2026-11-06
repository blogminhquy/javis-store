"""Lệnh trong skill của javis.claude-seo phải chạy được ở nơi Javis cài skill.

    python tests/javis.claude-seo/test_duong_dan_script.py

Repo gốc viết lệnh dạng `"${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo" run x.py`. Biến đó chỉ có khi
skill chạy như plugin Claude Code. Javis cài skill vào `skills/<slug>/` của brain và KHÔNG đặt
`CLAUDE_PLUGIN_ROOT`, nên lệnh thành `/scripts/claude-seo` và chết với exit 127 (review PR #53).
Bộ cài tay của chính tác giả (install.sh) cũng gặp đúng chuyện này và viết lại token trong mọi
tệp `.md`. Gói này viết lại sang `${CLAUDE_SKILL_DIR}`, biến mà Claude Code thay bằng thư mục của
skill đang nạp.

Lỗi thứ hai cùng đường chạy: Javis cài MỌI tệp của gói ở mode 0644 (`pack_install.py` không nghe bit
thực thi trong zip), nên gọi thẳng launcher là exit 126 dù đường dẫn đúng. Lệnh phải gọi qua `bash`.

Lỗi thứ ba (1.0.2): Javis bản đang chạy chưa thay `${CLAUDE_SKILL_DIR}` cho engine khác Claude Code
(API, Codex, Grok, Antigravity). Engine đó nhận NGUYÊN chữ, bash thay biến rỗng, lệnh thành
`/../seo/scripts/claude-seo` và chết exit 127. Lệnh phải tự lùi về `skills/seo` tính từ gốc brain
(cwd mặc định của terminal Javis và các CLI engine) khi biến rỗng, mà vẫn giữ nguyên chữ
`${CLAUDE_SKILL_DIR}` để Claude Code (và Javis bản mới) thay bằng đường dẫn tuyệt đối như cũ.

Test dựng đúng điều kiện đó: chép skill vào `skills/` của một brain tạm (tên có dấu cách) với mọi tệp
0644 như Javis cài, không có `CLAUDE_PLUGIN_ROOT`, rồi chạy NGUYÊN lệnh viết trong SKILL.md với
`--help` (argparse của launcher: không dựng môi trường, không ghi gì) theo ba cách engine nhận lệnh:
biến môi trường có đặt; Claude Code thay chữ, cwd ở chỗ khác; engine khác nhận nguyên chữ, cwd là gốc brain.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GOI = Path(__file__).resolve().parents[2] / "packs" / "javis.claude-seo" / "skills"
# `bash "<đường dẫn>"`, đường dẫn có thể gồm một `$(...)` (dạng tự lùi khi biến rỗng, xem docstring).
LENH = re.compile(r'(bash "(?:\$\([^)]*\)|[^"\s])*?/claude-seo")')
DUONG = re.compile(r"\$\{CLAUDE_SKILL_DIR\}(/[A-Za-z0-9_./-]+)")
# Skill repo gốc có mà gói cố ý không mang (theo-goc/javis.claude-seo.json, `bo`): tham chiếu "xem thêm"
# tới chúng không có đích ở đâu cả, kể cả trước khi viết lại.
BO = ("seo-image-gen", "seo-dataforseo")


def _md():
    return sorted(GOI.rglob("*.md"))


def _skill_cua(p: Path) -> Path:
    return GOI / p.relative_to(GOI).parts[0]


class DuongDanScript(unittest.TestCase):
    def test_khong_con_bien_chi_plugin_moi_co(self):
        # Dùng làm ĐƯỜNG DẪN (`${CLAUDE_PLUGIN_ROOT}/...`). Một câu giải thích nhắc tên biến thì vô hại.
        con = [p.relative_to(GOI).as_posix() for p in _md()
               if "${CLAUDE_PLUGIN_ROOT}/" in p.read_text(encoding="utf-8")]
        self.assertEqual(con, [], "Javis không đặt CLAUDE_PLUGIN_ROOT: lệnh sẽ thành /scripts/claude-seo")

    def _chay_moi_lenh(self, cach: str):
        """Chạy mọi lệnh launcher trong mọi SKILL.md theo một `cach` engine nhận lệnh."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
        da_chay, can_chay = set(), set()
        with tempfile.TemporaryDirectory() as tmp:
            goc_brain = Path(tmp) / "My Brain"
            brain = goc_brain / "skills"
            shutil.copytree(GOI, brain)
            for f in brain.rglob("*"):
                if f.is_file():
                    f.chmod(0o644)          # đúng như pack_install.py của Javis đặt
            for p in sorted(brain.glob("*/SKILL.md")):
                if "scripts/claude-seo" in p.read_text(encoding="utf-8"):
                    can_chay.add(p.parent.name)
                for lenh in sorted(set(LENH.findall(p.read_text(encoding="utf-8")))):
                    e, cwd = dict(env), str(goc_brain)
                    if cach == "bien":                  # biến môi trường có đặt
                        e["CLAUDE_SKILL_DIR"] = str(p.parent)
                    elif cach == "claude-code":         # Claude Code thay chữ, cwd không phải gốc brain
                        lenh = lenh.replace("${CLAUDE_SKILL_DIR}", str(p.parent))
                        cwd = tmp
                    with self.subTest(cach=cach, skill=p.parent.name, lenh=lenh):
                        kq = subprocess.run(["bash", "-c", f"{lenh} --help"], env=e, cwd=cwd,
                                            capture_output=True, text=True, timeout=60)
                        self.assertEqual(kq.returncode, 0, kq.stderr[-500:])
                        self.assertIn("Claude SEO managed Python runtime", kq.stdout)
                        da_chay.add(p.parent.name)
        # Skill nào nhắc launcher thì phải có ít nhất một lệnh khớp dạng trên và chạy được: lệnh viết
        # dạng khác (thiếu ngoặc kép, biến khác) sẽ lọt khỏi regex và lộ ra ở đây thay vì im lặng qua.
        self.assertTrue(can_chay)
        self.assertEqual(sorted(can_chay - da_chay), [])

    def test_lenh_chay_khi_bien_co_dat(self):
        self._chay_moi_lenh("bien")

    def test_lenh_chay_khi_claude_code_thay_chu(self):
        self._chay_moi_lenh("claude-code")

    def test_lenh_chay_khi_engine_khong_thay_bien(self):
        # API, Codex, Grok, Antigravity trên Javis chưa thay biến: nhận nguyên chữ, cwd là gốc brain.
        self._chay_moi_lenh("nguyen-chu")

    def test_duong_dan_tham_chieu_ton_tai(self):
        thieu = []
        for p in _md():
            for duoi in DUONG.findall(p.read_text(encoding="utf-8")):
                if "claude-seo" in duoi.rsplit("/", 1)[-1] or any(f"/../{b}/" in duoi for b in BO):
                    continue
                dich = (_skill_cua(p) / duoi.lstrip("/")).resolve()
                if not dich.exists():
                    thieu.append(f"{p.relative_to(GOI).as_posix()}: {duoi}")
        self.assertEqual(thieu, [])


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=1).result.wasSuccessful() else 1)
