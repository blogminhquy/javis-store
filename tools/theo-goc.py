"""Gói THEO REPO GỐC: skill chép NGUYÊN VĂN từ repo GitHub của tác giả, đồng bộ lại mỗi ngày.

    python tools/theo-goc.py              đồng bộ mọi gói khai trong theo-goc/
    python tools/theo-goc.py <id> [...]   chỉ những gói này
    python tools/theo-goc.py --kiem       chỉ báo gói nào có bản mới ở repo gốc, không ghi gì
    python tools/theo-goc.py --khong-ma   ghi gói chỉ đổi chữ, gói có tệp mã đổi thì để lại (CI)

Vì sao có loại gói này
----------------------
Gói skill bên thứ 3 trước đây (Hermes, Superpowers, ECC...) là BẢN CHỤP: chép một lần, sửa
frontmatter cho khớp Javis, rồi đứng yên ở 1.0.0. Tác giả gốc sửa skill bao nhiêu lần thì người
dùng Javis vẫn chạy bản cũ, vì không ai nhớ đi chép lại.

Chủ kho đề xuất 2026-10-28: một mục "repo khuyên dùng", giữ nguyên chất repo gốc mà lại được cập
nhật thường xuyên. Tệp này là nửa kho của ý đó:

- Mỗi gói khai một tệp `theo-goc/<id>.json`: repo gốc, nhánh, và thư mục nào chép vào đâu.
- Mỗi ngày job `theo-goc` trong CI chạy tệp này. Repo gốc đổi nội dung skill thì gói lên một
  bản vá (1.0.0 -> 1.0.1), zip đóng lại, `index.json` ghi commit gốc mà nội dung được lấy từ đó.
- Javis đã có sẵn phần còn lại: chuông báo "Store có bản mới", nút Cập nhật qua màn hình xác
  nhận, và luật không ghi đè tệp người dùng đã sửa (`pack_vault.py` bên javis-os).

Giữ nguyên chất nghĩa là KHÔNG sửa một byte nào của skill: không dịch, không rút gọn mô tả,
không thêm `group`. Khác hẳn gói bản chụp ở trên. Cái giá: mô tả dài hơn 150 ký tự bị router
của Javis cắt, nên chỉ nhận vào đây repo có câu mở đầu mô tả đủ nghĩa.

Khi nào cần người duyệt
-----------------------
Kho có luật mọi gói đều được đọc trước khi trộn. Đồng bộ tự động không phá luật đó, nó chia hai:

- Chỉ đổi tệp CHỮ (`.md`, `.txt`, ảnh, dữ liệu `.json`/`.yaml`): đẩy thẳng vào `main`. Repo đã
  được chủ kho chọn là đáng tin, và chữ hướng dẫn đổi là chuyện thường ngày của repo đó.
- Có tệp MÃ đổi hay thêm (script, `.py`, `.js`, `.ts`...): mở Pull Request, chờ chủ kho đọc.
  Javis không bao giờ thấy bản đó cho tới khi PR được trộn.

Job CI chạy hai lượt: `--khong-ma` ghi và đẩy thẳng các gói chỉ đổi chữ, rồi lượt thứ hai ghi các gói
có trong dòng `cho_duyet=` (tệp này ghi vào `$GITHUB_OUTPUT`) lên nhánh riêng kèm Pull Request.

Luật chép, mặc định TỪ CHỐI
---------------------------
Cùng trần với trình cài của Javis (`pack_install.py`), để gói đồng bộ xong luôn cài được:
không liên kết mềm, không tên nhạy cảm (`.env`, khoá riêng), tối đa 500 tệp, 5 MB một tệp,
40 MB cả gói. Vượt trần thì job đỏ và gói đứng yên ở bản cũ, không bao giờ phát hành nửa vời.
"""
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent
CAU_HINH = GOC / "theo-goc"
GOI = GOC / "packs"
INDEX = GOC / "index.json"
DIST = GOC / "dist"
# Chỉ test đổi chỗ này (trỏ vào repo git trên đĩa). Javis và CI luôn lấy từ GitHub.
GIT_GOC = os.environ.get("THEO_GOC_GIT_GOC") or "https://github.com/"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
BO_QUA = ("__pycache__", ".git", ".DS_Store", ".pytest_cache", "node_modules")
TEN_CAM = (".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
DUOI_CAM = (".pem", ".p12", ".pfx", ".key", ".ppk")
# Đuôi coi là CHỮ: đổi chúng không cần người duyệt. Mọi đuôi khác coi là MÃ.
# HTML và CSS tính là chữ: trong skill chúng là mẫu trang để AI dựng theo, không phải thứ Javis chạy.
DUOI_CHU = (".md", ".txt", ".json", ".yaml", ".yml", ".geojson", ".csv", ".png", ".jpg", ".jpeg",
            ".webp", ".gif", ".svg", ".html", ".css", ".dot", ".ttf", ".otf", ".woff", ".woff2")
TEN_CHU = ("LICENSE", "NOTICE", "README", "AUTHORS", "CHANGELOG")
TRAN_SO_TEP, TRAN_MOT_TEP, TRAN_TONG = 500, 5 * 1024 * 1024, 40 * 1024 * 1024


class LoiDongBo(Exception):
    pass


def doc_cau_hinh(pid: str) -> dict:
    """Đọc và kiểm `theo-goc/<id>.json`. Sai một trường là từ chối cả gói."""
    if not _ID_RE.match(pid):
        raise LoiDongBo(f"id lạ: {pid!r}")
    c = json.loads((CAU_HINH / f"{pid}.json").read_text(encoding="utf-8"))
    if not _REPO_RE.match(str(c.get("repo") or "")):
        raise LoiDongBo(f"{pid}: `repo` phải dạng chủ/tên")
    if not _REF_RE.match(str(c.get("ref") or "")) or ".." in c["ref"]:
        raise LoiDongBo(f"{pid}: `ref` (nhánh) không hợp lệ")
    chep = c.get("chep")
    if not isinstance(chep, list) or not chep:
        raise LoiDongBo(f"{pid}: thiếu `chep`")
    for m in chep:
        for k in ("tu", "den"):
            v = str(m.get(k) or "")
            if not v or v.startswith("/") or ".." in v.split("/"):
                raise LoiDongBo(f"{pid}: đường dẫn `{k}` không hợp lệ: {v!r}")
        for k in ("chi", "bo", "bo_tep"):
            if not isinstance(m.get(k, []), list):
                raise LoiDongBo(f"{pid}: `{k}` phải là danh sách")
        if not isinstance(m.get("doi_ten", {}), dict):
            raise LoiDongBo(f"{pid}: `doi_ten` phải là map tên gốc -> tên mới")
        for b in m.get("bo_tep") or []:
            if not b or str(b).startswith("/") or ".." in str(b).split("/"):
                raise LoiDongBo(f"{pid}: `bo_tep` không hợp lệ: {b!r}")
        if m["den"] != "skills":
            raise LoiDongBo(f"{pid}: hiện chỉ chép vào `skills` (đang khai {m['den']!r})")
    goi = c.get("goi") or {}
    for k in ("name", "description", "author", "category", "category_label", "license"):
        if not goi.get(k):
            raise LoiDongBo(f"{pid}: thiếu `goi.{k}`")
    return c


def _git(*args, cwd=None) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise LoiDongBo(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def tai_goc(c: dict, dich: Path) -> str:
    """Clone nông đúng nhánh khai báo. Trả commit đang đứng.

    Chỉ tải những thư mục `chep` cần (sparse checkout, kèm các tệp ở gốc repo như LICENSE): repo như
    GitNexus hay HyperFrames nặng 400 MB mà skill chỉ vài MB, và job này chạy mỗi ngày. Skill nằm ở gốc
    repo (`tu: "."`) thì cần cả cây, nên tải đủ."""
    _git("clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", "--branch", c["ref"],
         f"{GIT_GOC}{c['repo']}.git", str(dich))
    thu_muc = sorted({m["tu"] for m in c["chep"]})
    if "." in thu_muc:
        _git("sparse-checkout", "disable", cwd=dich)
    else:
        _git("sparse-checkout", "set", *thu_muc, cwd=dich)
    return _git("rev-parse", "HEAD", cwd=dich)


def _kiem_tep(p: Path, goc: Path) -> None:
    rel = p.relative_to(goc).as_posix()
    if p.is_symlink():
        raise LoiDongBo(f"liên kết mềm không được chép: {rel}")
    ten = p.name.lower()
    if ten in TEN_CAM or ten.startswith(".env") or ten.endswith(DUOI_CAM) or ten.startswith("id_rsa"):
        raise LoiDongBo(f"tên nhạy cảm: {rel}")
    if p.stat().st_size > TRAN_MOT_TEP:
        raise LoiDongBo(f"tệp quá 5 MB: {rel}")


def _chep_cay(goc: Path, dich: Path, nguon: Path, bo_tep=()) -> None:
    """Chép cả cây `goc` sang `dich`, bỏ thư mục rác và những đường dẫn tương đối nằm trong `bo_tep`."""
    for p in sorted(goc.rglob("*")):
        rel = p.relative_to(goc)
        if any(x in rel.parts for x in BO_QUA) or any(rel.parts[:len(Path(b).parts)] == Path(b).parts
                                                      for b in bo_tep):
            continue
        if rel.parts and rel.parts[0].startswith(".") and goc == nguon:
            continue        # skill ở gốc repo: .github, .claude... là đồ của repo, không phải của skill
        if p.is_symlink() or p.is_file():
            _kiem_tep(p, nguon)
            d = dich / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, d)


def dung_ban_moi(c: dict, nguon: Path, ra: Path) -> list:
    """Dựng nội dung mới vào `ra`, theo đúng hình dạng nó sẽ nằm trong gói. Trả danh sách skill.

    Mỗi mục `chep` lấy các thư mục con có SKILL.md của `tu`. Tuỳ chọn, đều KHÔNG sửa nội dung skill:
      chi      chỉ lấy những skill này (repo có skill nội bộ, hay skill có giấy phép không cho phát lại);
      bo       bỏ những skill này;
      doi_ten  đổi tên THƯ MỤC khi trùng slug với gói khác trong cùng brain (nội dung giữ nguyên);
      ten      `tu` tự nó là một skill (SKILL.md nằm ngay ở đó, thường là gốc repo): tên thư mục trong gói;
      bo_tep   với skill `ten`: đường dẫn tương đối không chép theo (test, ảnh demo, thư mục skill khác)."""
    skills, tu_dau = [], {}
    for m in c["chep"]:
        tu = nguon / m["tu"]
        if not tu.is_dir():
            raise LoiDongBo(f"repo gốc không còn thư mục {m['tu']}")
        if m.get("ten"):
            if not (tu / "SKILL.md").is_file():
                raise LoiDongBo(f"repo gốc không còn {m['tu']}/SKILL.md")
            cap = [(tu, m["ten"])]
        else:
            chi, bo = set(m.get("chi") or []), set(m.get("bo") or [])
            cap = [(d, (m.get("doi_ten") or {}).get(d.name, d.name)) for d in sorted(tu.iterdir())
                   if not d.is_symlink() and d.is_dir() and (d / "SKILL.md").is_file()
                   and (not chi or d.name in chi) and d.name not in bo]
            thieu = chi - {d.name for d, _ in cap}
            if thieu:
                raise LoiDongBo(f"repo gốc không còn skill: {', '.join(sorted(thieu))}")
        for d, ten in cap:
            if not _ID_RE.match(ten):
                raise LoiDongBo(f"tên skill lạ: {ten!r}")
            if ten in skills:
                raise LoiDongBo(f"hai skill trùng tên: {ten}")
            skills.append(ten)
            tu_dau[ten] = m["tu"] if m.get("ten") else f"{m['tu']}/{d.name}"
            _chep_cay(d, ra / m["den"] / ten, nguon, m.get("bo_tep") or ())
    if not skills:
        raise LoiDongBo("repo gốc không có skill nào ở thư mục đã khai")
    (ra / "docs").mkdir(exist_ok=True)
    for ten in c.get("giay_phep") or ["LICENSE"]:
        p = nguon / ten
        if p.is_file() and not p.is_symlink():
            shutil.copyfile(p, ra / "docs" / ("LICENSE-goc" + (Path(ten).suffix or ".txt")))
    (ra / "docs" / "NGUON.md").write_text(_nguon_md(c, tu_dau), encoding="utf-8")
    tep = [p for p in ra.rglob("*") if p.is_file()]
    if len(tep) > TRAN_SO_TEP:
        raise LoiDongBo(f"quá {TRAN_SO_TEP} tệp ({len(tep)})")
    if sum(p.stat().st_size for p in tep) > TRAN_TONG:
        raise LoiDongBo("quá 40 MB")
    return skills


def _nguon_md(c: dict, tu_dau: dict) -> str:
    """Trang nguồn của gói. Không ghi commit hay ngày: tệp này nằm trong phần so sánh, ghi thứ đổi
    mỗi lần chạy thì ngày nào gói cũng lên bản mới dù repo gốc không đổi gì."""
    url = "https://github.com/" + c["repo"]
    dong = [f"# Nguồn của gói theo repo gốc `{c['repo']}`", "",
            f"Skill trong gói này chép **nguyên văn** từ [{c['repo']}]({url}), nhánh `{c['ref']}`:",
            "không dịch, không rút gọn, không sửa frontmatter.", "",
            "Mỗi ngày kho so lại với repo gốc (`tools/theo-goc.py`). Repo gốc đổi thì gói lên một bản",
            "vá, Javis báo trong chuông và bạn bấm Cập nhật. Skill bạn đã tự sửa thì Javis giữ lại,",
            "không ghi đè. Commit gốc của bản đang phát hành nằm ở trường `upstream.commit` trong",
            "`index.json`.", "",
            f"Giấy phép gốc: **{c['goi']['license']}**, nguyên văn ở `docs/LICENSE-goc*` nếu repo có.", "",
            "| Skill | Thư mục gốc |", "|---|---|"]
    for s, tu in tu_dau.items():
        dong.append(f"| `{s}` | `{tu}` |")
    return "\n".join(dong) + "\n"


def _cay(goc: Path, phan: list) -> dict:
    """{đường dẫn tương đối: sha256} của những phần gói do repo gốc quản."""
    ra = {}
    for ten in phan:
        p = goc / ten
        if not p.is_dir():
            continue
        for f in sorted(p.rglob("*")):
            if f.is_file():
                ra[f.relative_to(goc).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return ra


def _ban_cu(thu_muc: Path) -> str:
    try:
        for dong in (thu_muc / "javis-pack.yaml").read_text(encoding="utf-8").splitlines():
            if dong.startswith("version:"):
                return dong.split(":", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _tang_ban(v: str) -> str:
    if not v:
        return "1.0.0"
    a, b, c = (v.split(".") + ["0", "0", "0"])[:3]
    return f"{int(a)}.{int(b)}.{int(c) + 1}"


def _q(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=False)


def viet_manifest(pid: str, c: dict, ban: str, skills: list) -> str:
    g = c["goi"]
    return "\n".join([
        "format: javis-pack",
        "spec: 1",
        f"id: {pid}",
        f"version: {ban}",
        f"name: {{vi: {_q(g['name']['vi'])}, en: {_q(g['name']['en'])}}}",
        f"description: {{vi: {_q(g['description']['vi'])},",
        f"              en: {_q(g['description']['en'])}}}",
        f"author: {{name: {_q(g['author'])}, url: {_q('https://github.com/' + c['repo'])}}}",
        f"license: {g['license']}",
        f"homepage: {_q('https://github.com/' + c['repo'])}",
        f"compat: {{app: {_q(g.get('compat') or '>=0.55.27')}}}",
        "# Gói theo repo gốc: thư mục skills/ do tools/theo-goc.py chép nguyên văn, đừng sửa tay.",
        f"upstream: {{repo: {_q(c['repo'])}, ref: {_q(c['ref'])}}}",
        "provides:",
        f"  skills: [{', '.join(skills)}]",
        "",
    ])


def _tier(thu_muc: Path) -> str:
    """Cùng luật với `packs._tier` bên Javis: có tệp .py là bậc code."""
    return "code" if any(thu_muc.rglob("*.py")) else "data"


def _dong_goi(pid: str) -> Path:
    spec = importlib.util.spec_from_file_location("dong_goi", GOC / "tools" / "dong-goi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.GOC, mod.NGUON, mod.RA = GOI.parent, GOI, DIST
    return mod.dong(pid)


def ghi_index(pid: str, c: dict, ban: str, skills: list, commit: str, zip_: Path, hom_nay: str) -> None:
    d = json.loads(INDEX.read_text(encoding="utf-8"))
    g = c["goi"]
    b = zip_.read_bytes()
    muc = {
        "id": pid,
        "kind": "skill",
        "name": g["name"],
        "description": g["description"],
        "version": ban,
        "author": {"name": g["author"], "url": "https://github.com/" + c["repo"]},
        "category": g["category"],
        "category_label": g["category_label"],
        "tier": _tier(GOI / pid),
        "verified": True,
        "updated": hom_nay,
        "homepage": f"https://github.com/blogminhquy/javis-store/tree/main/packs/{pid}",
        "source": "https://github.com/" + c["repo"],
        "license": g["license"],
        "upstream": {"repo": c["repo"], "ref": c["ref"], "commit": commit, "synced": hom_nay},
        "provides": {"skills": skills},
        "download": {"url": f"dist/{zip_.name}", "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)},
    }
    if g.get("icon"):
        muc["icon"] = g["icon"]
    goi = d["packs"]
    for i, x in enumerate(goi):
        if x.get("id") == pid:
            goi[i] = muc
            break
    else:
        goi.append(muc)
    d["updated"] = hom_nay
    INDEX.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dong_bo(pid: str, chi_kiem: bool = False, hom_nay: str = "", bo_qua_ma: bool = False) -> dict:
    """Đồng bộ một gói. Trả {id, doi, can_duyet, ban, commit, tep_ma, ghi}.

    `bo_qua_ma`: gói có tệp mã đổi thì chỉ báo, không ghi. CI dùng nó để đẩy thẳng bản chỉ đổi chữ
    của các gói khác, còn gói cần duyệt đi riêng một Pull Request."""
    hom_nay = hom_nay or datetime.date.today().isoformat()
    c = doc_cau_hinh(pid)
    thu_muc = GOI / pid
    phan = sorted({m["den"] for m in c["chep"]} | {"docs"})
    with tempfile.TemporaryDirectory(prefix="theo-goc-") as tmp:
        tmp = Path(tmp)
        commit = tai_goc(c, tmp / "goc")
        skills = dung_ban_moi(c, tmp / "goc", tmp / "moi")
        cu, moi = _cay(thu_muc, phan), _cay(tmp / "moi", phan)
        if cu == moi and (thu_muc / "javis-pack.yaml").is_file():
            return {"id": pid, "doi": False, "can_duyet": False, "commit": commit, "ghi": False}
        doi = {k for k in set(cu) | set(moi) if cu.get(k) != moi.get(k)}
        tep_ma = sorted(k for k in doi if not k.lower().endswith(DUOI_CHU) and not k.startswith("docs/")
                        and Path(k).stem.upper() not in TEN_CHU)
        ban = _tang_ban(_ban_cu(thu_muc))
        kq = {"id": pid, "doi": True, "can_duyet": bool(tep_ma), "ban": ban, "commit": commit,
              "tep_ma": tep_ma, "so_tep_doi": len(doi)}
        if chi_kiem or (bo_qua_ma and kq["can_duyet"]):
            return dict(kq, ghi=False)
        thu_muc.mkdir(parents=True, exist_ok=True)
        for ten in phan:
            shutil.rmtree(thu_muc / ten, ignore_errors=True)
            if (tmp / "moi" / ten).is_dir():
                shutil.copytree(tmp / "moi" / ten, thu_muc / ten)
    (thu_muc / "javis-pack.yaml").write_text(viet_manifest(pid, c, ban, skills), encoding="utf-8")
    zip_ = _dong_goi(pid)
    ghi_index(pid, c, ban, skills, commit, zip_, hom_nay)
    return dict(kq, ghi=True)


def main(argv: list) -> int:
    chi_kiem, bo_qua_ma = "--kiem" in argv, "--khong-ma" in argv
    ids = [a for a in argv if not a.startswith("--")] or sorted(p.stem for p in CAU_HINH.glob("*.json"))
    ket_qua, loi = [], []
    for pid in ids:
        try:
            kq = dong_bo(pid, chi_kiem, bo_qua_ma=bo_qua_ma)
        except (LoiDongBo, OSError, ValueError, subprocess.TimeoutExpired) as e:
            loi.append(f"{pid}: {e}")
            print(f"LỖI {pid}: {e}")
            continue
        ket_qua.append(kq)
        if not kq["doi"]:
            print(f"{pid}: giống repo gốc ({kq['commit'][:7]})")
        else:
            print(f"{pid}: bản mới {kq['ban']} từ {kq['commit'][:7]}, {kq['so_tep_doi']} tệp đổi"
                  + (f", có tệp mã cần duyệt: {', '.join(kq['tep_ma'][:10])}" if kq["can_duyet"] else "")
                  + ("" if kq["ghi"] else ", CHƯA ghi"))
    da_ghi = [k for k in ket_qua if k["ghi"]]
    cho = [k["id"] for k in ket_qua if k["doi"] and not k["ghi"]]
    out = os.environ.get("GITHUB_OUTPUT")
    if out and not chi_kiem:
        tom_tat = "; ".join(f"{k['id']} {k['ban']} ({k['commit'][:7]})" for k in da_ghi)
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"doi={'1' if da_ghi else '0'}\n")
            f.write(f"can_duyet={'1' if any(k['can_duyet'] for k in da_ghi) else '0'}\n")
            f.write(f"cho_duyet={' '.join(cho)}\n")
            f.write(f"tom_tat={tom_tat}\n")
    return 1 if loi else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
