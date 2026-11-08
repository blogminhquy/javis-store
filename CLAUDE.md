# Javis Store: quy ước cho phiên Claude Code

Kho này chứa các gói cài thêm cho Javis OS. Gói có mã chạy Python thật trong máy chủ của người dùng, và `README.md`
hứa mọi gói được người phát hành kho đọc mã trước khi trộn: mở PR rồi báo chủ kho duyệt, trừ khi chủ kho bảo merge.

Đọc `CONTRIBUTING.md` trước khi sửa gói. Mấy luật hay bị quên:

- **Tính năng Zalo mới vào plugin `zalo-extras` của gói `javis.zalo`, không tạo plugin mới** (chủ kho, 08/11/2026).
  Cách thêm tool ở mục "Tính năng Zalo mới" trong `CONTRIBUTING.md`. `tests/javis.zalo/test_zalo_extras.py` chặn việc
  thêm thư mục plugin thứ hai.
- Ra bản mới: tăng `version` ở manifest gói và mục `index.json`, đóng zip bằng `python tools/dong-goi.py <id>` (giữ zip
  cũ), dán `sha256` vào `index.json`, chạy `python tools/kiem-tra.py`.
- Gói có mã thì chạy test của nó trên bản Javis OS: `JAVIS_OS_DIR=../javis-os python tests/<id>/test_*.py`.
- Không dùng ký tự gạch ngang dài (em dash) trong mọi tệp.
