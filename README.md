# Kho cài đặt Javis

Trợ lý, kỹ năng, quy trình, công cụ và kết nối làm sẵn cho [Javis OS](https://github.com/blogminhquy/javis-os).

Javis đọc thẳng `index.json` trong repo này, nên **thêm một mục ở đây là mọi máy đang chạy Javis
thấy ngay** - không phải chờ bản cập nhật nào cả. Đó là toàn bộ lý do kho tách ra thành một repo
riêng.

## Người dùng Javis

Không cần làm gì với repo này. Mở Javis, vào tab **Kho cài đặt** trên bất kỳ trang năng lực nào
(Trợ lý, Kỹ năng, Quy trình, Plugin, Kết nối), rồi bấm **Cài đặt**.

Javis tải gói về, **mở ra cho bạn xem bên trong có gì rồi mới hỏi**. Gói có chứa mã Python thì
màn hình xác nhận nói thẳng điều đó và bắt gõ lại mã gói trước khi cho cài.

Kho không tới được cũng không sao: gói đã cài chạy bình thường, và bạn vẫn cài được từ tệp `.zip`.

## Bố cục repo

    index.json          danh mục - thứ Javis đọc
    packs/<id>/         MÃ NGUỒN từng gói, để ai cũng đọc được trước khi cài
    packs/<id>/assets/  logo của gói: thẻ trong Kho cài đặt và trang Kết nối cùng dùng tệp này
    dist/<id>-<ver>.zip tệp Javis thật sự tải về
    tools/dong-goi.py   đóng gói một thư mục thành .zip và in dấu vân tay

Mã nguồn nằm ngay cạnh tệp phát hành là có chủ ý: gói **chạy được mã Python trong máy chủ Javis
của bạn**, nên bạn phải đọc được nó mà không cần tải gì về trước.

## Muốn đóng góp một gói

Xem [CONTRIBUTING.md](CONTRIBUTING.md). Tóm tắt: mở Pull Request kèm mã nguồn, tệp `.zip` và một
mục trong `index.json`. Mọi gói đều **được người phát hành kho đọc mã trước khi trộn vào**.

## Định dạng

Định dạng `index.json` và cách viết một gói: xem
[docs/dev/pack-store-index.md](https://github.com/blogminhquy/javis-os/blob/main/docs/dev/pack-store-index.md)
trong repo Javis OS.
