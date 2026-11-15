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

## Thiết kế trang của gói

Chủ kho 15/11/2026: "lưu lại phong cách thiết kế, lần sau làm theo cho anh đỡ phải nhắc". Phong cách đầy đủ, gom từ mọi
lần chủ đã sửa giao diện, ở `docs/dev/design-style.md` của repo Javis OS
(https://github.com/blogminhquy/javis-os/blob/main/docs/dev/design-style.md). Đọc nó trước khi vẽ trang HTML cho một
gói, và chạy mục 0 "Kiểm trước khi gửi" trước khi báo xong. Mấy điểm chính:

- **Màn mới hoặc thiết kế lại:** làm bản HTML cho chủ duyệt trước khi code, trừ khi chủ bảo làm thẳng. Gửi ảnh trước và
  sau, chụp ở khổ 390px, cả giao diện sáng lẫn tối.
- **Mẫu là trang `javis.gate` 2.2.2** (`packs/javis.gate/plugins/javis-gate/plugin.py`, hàm `_trang_cai_dat`). Thứ tự:
  trạng thái, việc chính (một nút lớn), cài đặt dạng thẻ chọn, danh sách có nút riêng từng dòng, phần ít dùng thu gọn,
  vùng nguy hiểm ở cuối.
- **Cỡ:** chữ thân 17px, không chữ nào dưới 16px, nút cao 48px, không tràn ngang.
- **Giải thích:** mọi lời giải thích nằm sau nút (i), đóng sẵn. Các bước có số tròn, mỗi bước một câu.
- **Gọn và thẳng hàng:** không lặp tiêu đề, một mép trái chung, icon thay chữ khi đủ nghĩa, chỉ in đậm thứ quan trọng.
- **Câu chữ:** tiếng Việt như văn nói, không thuật ngữ. Logo hãng phải là logo thật.
- **Giao diện mặc định là sáng** (chủ chốt 05/11/2026). Trang của gói theo đúng lựa chọn đã lưu của dashboard
  (`localStorage["javis.theme"]`), cùng màu và font Be Vietnam Pro (`/static/fonts/fonts.css`). Chép `_JS_GIAO_DIEN` và
  `_CSS` của `javis.gate` 2.2.2 khi làm trang gói mới; đừng dùng `prefers-color-scheme`.
