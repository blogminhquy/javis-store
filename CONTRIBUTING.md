# Gửi một gói vào kho

Gói trong kho **chạy trên máy người khác**, và gói bậc `code` chạy Python thật bên trong máy chủ
Javis của họ - đọc được mọi khoá API và mọi tệp mà Javis đọc được. Vì vậy mọi Pull Request đều
được đọc mã trước khi trộn, và phần lớn hướng dẫn dưới đây tồn tại để việc đọc đó nhanh và chắc.

## Các bước

1. Fork repo này.
2. Tạo `packs/<id>/` với `javis-pack.yaml` ở gốc. `id` phải **trùng đúng tên thư mục**, chỉ gồm
   chữ thường, số và `. - _`.
3. Đóng gói và lấy dấu vân tay:

       python tools/dong-goi.py <id>

   Lệnh in ra đường dẫn `.zip` trong `dist/` và chuỗi `sha256`.
4. Thêm một mục vào `packs[]` trong `index.json`, dùng `download.url` **tương đối**
   (`dist/<tên tệp>.zip`) và dán đúng `sha256` lệnh trên vừa in. Gói kết nối thì khai thêm
   `icon` trỏ vào logo **nằm trong gói** (`packs/<id>/assets/<tên>.png`): đó là logo hiện trên
   thẻ trong Kho cài đặt, và cũng là tệp khuôn connector trỏ tới qua `assets/<tên>.png`, nên
   hai chỗ không bao giờ lệch nhau.
5. Chạy trình kiểm trước khi gửi:

       python tools/kiem-tra.py

   Nó soi cấu trúc gói, phân loại quyền, và đối chiếu `.zip` với thư mục nguồn. CI chạy đúng
   lệnh này trên mọi Pull Request, nên chạy trước ở máy mình là biết ngay thay vì đợi.
6. Mở Pull Request. Trong phần mô tả, nói rõ **gói làm gì** và **vì sao cần quyền nó xin**.

Cả mã nguồn lẫn tệp `.zip` đều phải có trong PR. Thiếu một trong hai thì không review được: mã để
đọc, `.zip` để chạy thử.

## Gói của bạn sẽ bị đọc những gì

- **Mọi tệp `.py`.** Gói không có tệp `.py` vẫn có thể chạy mã: `transport: stdio` khiến Javis
  chạy `npx` với toàn bộ biến môi trường của máy chủ. Khai `command`, `args` hay `env` là gói
  thuộc bậc `code`, dù không một dòng Python nào.
- **Địa chỉ gói gọi ra ngoài.** Một connector dựng URL từ dữ liệu người dùng là chỗ phải giải
  thích rõ.
- **Trùng `id` với connector có sẵn.** Bị từ chối thẳng, không có ngoại lệ: một gói khai trùng
  `id` kèm `url_template` khác sẽ âm thầm bẻ hướng một kết nối **đang đăng nhập thật**.
- **Icon.** Phải nằm trong gói. Icon ở xa là một beacon nổ mỗi lần vẽ trang, tức lộ IP và nhịp
  dùng của người cài cho bạn. Dùng `.png`, `.webp` hoặc `.jpg` (256x256 là đủ), không dùng
  `.svg`: đường phục vụ ảnh của gói cố ý từ chối SVG.
- **Mô tả tool.** Nó đi thẳng vào danh sách công cụ của những engine đang cầm quyền chạy lệnh,
  nên mô tả gài chỉ dẫn cho mô hình là lý do từ chối.

## Phân loại quyền: chỗ dễ sai nhất

Javis xếp mỗi tool vào ba nhóm `read` / `write` / `danger`, khai trong `tool_meta` của khuôn
connector. Mức **Ghi nháp** của người dùng tự chạy được mọi thứ trong nhóm `write` mà không hỏi.

Điều phải biết: **Javis KHÔNG tự đoán ra `danger`.** Tool nào bạn không khai rõ thì cùng lắm nó
rơi vào `write` theo tên. Nên một tool xoá dữ liệu hay tiêu tiền mà bạn quên khai sẽ tự chạy ở
mức Ghi nháp, im lặng, cho tới lúc nó xoá thật.

`tools/kiem-tra.py` chặn hai lớp:

- **Đụng tới tiền** (mua, thanh toán, hoàn tiền, gia hạn, chuyển khoản): bắt buộc `danger`,
  không có ngoại lệ.
- **Phá huỷ hoặc tác động ra ngoài** (xoá, huỷ, gửi, đăng, chạy, khởi động lại): mặc định cũng
  bắt buộc `danger`. Nếu bạn đã cân nhắc và thấy trong dịch vụ CỤ THỂ này nó nhẹ - ví dụ xoá
  một dòng trong ghi chú cá nhân, hay huỷ một tác vụ chạy lại được - thì liệt kê tên tool đó
  vào `ghi_da_can_nhac` ở gốc khuôn:

      "ghi_da_can_nhac": ["delete_list_item", "restore_note"]

  Trường này Javis không đọc; nó chỉ để lại dấu vết rằng quyết định ấy là có chủ ý, cho người
  review sau đọc được. Đừng dùng nó để cho qua trình kiểm - dùng thế thì thà khai `danger`.

Khai `danger` thì khuôn phải có câu `risk` nói bằng lời thường chuyện gì có thể mất.

## Điều gì bị từ chối

- `transport: internal` và `auth.type: qr` - hai đường riêng của lõi Javis, không khai bằng gói.
- Gói làm thay việc đã có MCP chính chủ. Đấu MCP đó vào còn hơn.
- Gói tự ý gửi dữ liệu người dùng đi đâu đó, kể cả "để thống kê".

## Ra bản mới

Tăng `version` ở **cả hai** chỗ: manifest trong gói và mục trong `index.json`. Thêm tệp `.zip`
mới vào `dist/` (giữ tệp cũ lại, đừng ghi đè - người dùng cần tải được bản cũ), rồi đổi
`download.url` và `sha256`.

Người đã cài sẽ thấy nút đổi thành **Có bản mới**. Javis **không bao giờ tự cập nhật một gói có
mã** - bản mới có thể đổi mã, và mã đổi mà không ai xem thì chốt chữ ký nội dung thành vô nghĩa.
