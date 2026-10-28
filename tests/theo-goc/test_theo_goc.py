"""Test `tools/theo-goc.py` trên một repo git dựng tại chỗ, không chạm mạng hay kho thật.

    python tests/theo-goc/test_theo_goc.py

Phủ: lần đầu dựng gói 1.0.0 kèm mục danh mục; repo gốc không đổi thì không ra bản; đổi chữ thì
lên bản vá và không cần duyệt; đổi script thì lên bản vá và cần duyệt (lượt --khong-ma để lại nó);
commit chỉ đụng phần ngoài
skill thì không ra bản; liên kết mềm hay tên nhạy cảm thì từ chối và gói đứng yên.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

GOC = Path(__file__).resolve().parents[2]


def _nap():
    spec = importlib.util.spec_from_file_location("theo_goc", GOC / "tools" / "theo-goc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


class TheoGoc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tg-test-"))
        self.up = self.tmp / "up" / "tac-gia" / "kho-skill.git"
        (self.up / "skills" / "viet-thu").mkdir(parents=True)
        (self.up / "skills" / "viet-thu" / "SKILL.md").write_text(
            "---\nname: viet-thu\ndescription: Viết thư\n---\nThân skill.\n", encoding="utf-8")
        (self.up / "skills" / "viet-thu" / "chay.sh").write_text("echo 1\n", encoding="utf-8")
        (self.up / "README.md").write_text("repo\n", encoding="utf-8")
        (self.up / "LICENSE").write_text("MIT\n", encoding="utf-8")
        git(self.up, "init", "-q", "-b", "main")
        self.commit("đầu")

        self.kho = self.tmp / "kho"
        for d in ("theo-goc", "packs", "dist"):
            (self.kho / d).mkdir(parents=True)
        (self.kho / "index.json").write_text(json.dumps(
            {"format": "javis-pack-index", "format_version": 1, "updated": "", "packs": []}), encoding="utf-8")
        (self.kho / "theo-goc" / "javis.thu.json").write_text(json.dumps({
            "repo": "tac-gia/kho-skill", "ref": "main",
            "chep": [{"tu": "skills", "den": "skills"}],
            "goi": {"name": {"vi": "Thư", "en": "Letters"}, "description": {"vi": "a", "en": "a"},
                    "author": "Tác giả", "category": "van-phong",
                    "category_label": {"vi": "Văn phòng", "en": "Office"}, "license": "MIT"}}),
            encoding="utf-8")

        self.tg = _nap()
        self.tg.CAU_HINH = self.kho / "theo-goc"
        self.tg.GOI = self.kho / "packs"
        self.tg.INDEX = self.kho / "index.json"
        self.tg.DIST = self.kho / "dist"
        self.tg.GIT_GOC = (self.tmp / "up").as_uri() + "/"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, msg):
        git(self.up, "add", "-A")
        git(self.up, "commit", "-q", "-m", msg)

    def muc(self):
        d = json.loads((self.kho / "index.json").read_text(encoding="utf-8"))
        return next(x for x in d["packs"] if x["id"] == "javis.thu")

    def dong_bo(self):
        return self.tg.dong_bo("javis.thu", hom_nay="2026-10-28")

    def test_lan_dau_dung_goi(self):
        kq = self.dong_bo()
        self.assertTrue(kq["doi"])
        m = self.muc()
        self.assertEqual(m["version"], "1.0.0")
        self.assertEqual(m["upstream"]["repo"], "tac-gia/kho-skill")
        self.assertEqual(len(m["upstream"]["commit"]), 40)
        self.assertEqual(m["provides"]["skills"], ["viet-thu"])
        goi = self.kho / "packs" / "javis.thu"
        self.assertEqual((goi / "skills" / "viet-thu" / "SKILL.md").read_bytes(),
                         (self.up / "skills" / "viet-thu" / "SKILL.md").read_bytes(), "phải chép nguyên văn")
        self.assertTrue((goi / "docs" / "LICENSE-goc.txt").is_file())
        with zipfile.ZipFile(self.kho / m["download"]["url"]) as z:
            self.assertIn("skills/viet-thu/SKILL.md", z.namelist())
            self.assertIn("javis-pack.yaml", z.namelist())

    def test_khong_doi_thi_khong_ra_ban(self):
        self.dong_bo()
        self.assertFalse(self.dong_bo()["doi"])
        (self.up / "README.md").write_text("repo khác\n", encoding="utf-8")
        self.commit("chỉ đổi README")
        self.assertFalse(self.dong_bo()["doi"], "commit ngoài phần skill không ra bản mới")
        self.assertEqual(self.muc()["version"], "1.0.0")

    def test_doi_chu_va_doi_ma(self):
        self.dong_bo()
        (self.up / "skills" / "viet-thu" / "SKILL.md").write_text(
            "---\nname: viet-thu\ndescription: Viết thư\n---\nThân mới.\n", encoding="utf-8")
        self.commit("sửa chữ")
        kq = self.dong_bo()
        self.assertTrue(kq["doi"])
        self.assertFalse(kq["can_duyet"], "chỉ đổi chữ thì không cần duyệt")
        self.assertEqual(self.muc()["version"], "1.0.1")
        (self.up / "skills" / "viet-thu" / "chay.sh").write_text("echo 2\n", encoding="utf-8")
        self.commit("sửa script")
        kq = self.dong_bo()
        self.assertTrue(kq["can_duyet"], "đổi script thì phải chờ duyệt")
        self.assertEqual(kq["tep_ma"], ["skills/viet-thu/chay.sh"])
        self.assertEqual(self.muc()["version"], "1.0.2")

    def test_khong_ma_de_lai_goi_can_duyet(self):
        self.dong_bo()
        (self.up / "skills" / "viet-thu" / "chay.sh").write_text("echo 3\n", encoding="utf-8")
        self.commit("sửa script")
        kq = self.tg.dong_bo("javis.thu", hom_nay="2026-10-28", bo_qua_ma=True)
        self.assertTrue(kq["doi"] and kq["can_duyet"] and not kq["ghi"])
        self.assertEqual(self.muc()["version"], "1.0.0", "lượt --khong-ma không ghi gói có tệp mã đổi")
        self.assertEqual((self.kho / "packs" / "javis.thu" / "skills" / "viet-thu" / "chay.sh").read_text(), "echo 1\n")

    def test_tu_choi_lien_ket_mem_va_ten_nhay_cam(self):
        self.dong_bo()
        for ten, tao in (("lien-ket", lambda p: os.symlink("/etc/passwd", p)),
                         (".env", lambda p: p.write_text("KEY=1\n"))):
            p = self.up / "skills" / "viet-thu" / ten
            tao(p)
            self.commit("thêm " + ten)
            with self.assertRaises(self.tg.LoiDongBo):
                self.dong_bo()
            self.assertEqual(self.muc()["version"], "1.0.0", "lỗi thì gói đứng yên ở bản cũ")
            p.unlink()
            self.commit("bỏ " + ten)

    def cau_hinh(self, chep):
        p = self.kho / "theo-goc" / "javis.thu.json"
        c = json.loads(p.read_text(encoding="utf-8"))
        c["chep"] = chep
        p.write_text(json.dumps(c), encoding="utf-8")

    def test_chi_bo_doi_ten(self):
        for ten in ("noi-bo", "ban-quyen"):
            (self.up / "skills" / ten).mkdir()
            (self.up / "skills" / ten / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        self.commit("thêm skill nội bộ")
        self.cau_hinh([{"tu": "skills", "den": "skills", "bo": ["noi-bo", "ban-quyen"],
                        "doi_ten": {"viet-thu": "tg-viet-thu"}}])
        self.dong_bo()
        goi = self.kho / "packs" / "javis.thu" / "skills"
        self.assertEqual(sorted(p.name for p in goi.iterdir()), ["tg-viet-thu"])
        self.assertEqual((goi / "tg-viet-thu" / "SKILL.md").read_bytes(),
                         (self.up / "skills" / "viet-thu" / "SKILL.md").read_bytes(), "đổi tên thư mục, nội dung giữ nguyên")
        self.assertEqual(self.muc()["provides"]["skills"], ["tg-viet-thu"])
        self.cau_hinh([{"tu": "skills", "den": "skills", "chi": ["ban-quyen"]}])
        self.dong_bo()
        self.assertEqual(sorted(p.name for p in goi.iterdir()), ["ban-quyen"])
        self.cau_hinh([{"tu": "skills", "den": "skills", "chi": ["khong-co"]}])
        with self.assertRaises(self.tg.LoiDongBo):
            self.dong_bo()

    def test_skill_o_goc_repo(self):
        (self.up / "SKILL.md").write_text("---\nname: goc\n---\nThân.\n", encoding="utf-8")
        (self.up / "helpers").mkdir()
        (self.up / "helpers" / "a.py").write_text("print(1)\n", encoding="utf-8")
        (self.up / "tests").mkdir()
        (self.up / "tests" / "t.py").write_text("x\n", encoding="utf-8")
        (self.up / ".github").mkdir()
        (self.up / ".github" / "ci.yml").write_text("x\n", encoding="utf-8")
        self.commit("skill ở gốc")
        self.cau_hinh([{"tu": ".", "den": "skills", "ten": "goc", "bo_tep": ["tests", "skills"]}])
        self.dong_bo()
        goi = self.kho / "packs" / "javis.thu" / "skills" / "goc"
        co = sorted(p.relative_to(goi).as_posix() for p in goi.rglob("*") if p.is_file())
        self.assertEqual(co, ["LICENSE", "README.md", "SKILL.md", "helpers/a.py"])
        self.assertEqual(self.muc()["tier"], "code", "có tệp .py thì gói là bậc code")

    def test_cau_hinh_sai_bi_tu_choi(self):
        p = self.kho / "theo-goc" / "javis.thu.json"
        c = json.loads(p.read_text(encoding="utf-8"))
        for sai in ({"repo": "khong-co-gach"}, {"ref": "a..b"}, {"chep": [{"tu": "../x", "den": "skills"}]},
                    {"chep": [{"tu": "skills", "den": "plugins"}]},
                    {"chep": [{"tu": "skills", "den": "skills", "bo_tep": ["../x"]}]},
                    {"chep": [{"tu": "skills", "den": "skills", "doi_ten": ["a"]}]},
                    {"chep": [{"tu": "skills", "den": "skills", "doi_ten": {"viet-thu": "Ten Sai"}}]}):
            p.write_text(json.dumps(dict(c, **sai)), encoding="utf-8")
            with self.assertRaises(self.tg.LoiDongBo):
                self.dong_bo()


if __name__ == "__main__":
    unittest.main()
