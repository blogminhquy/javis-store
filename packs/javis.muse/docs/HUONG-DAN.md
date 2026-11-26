# Muse trong Javis

Gói này cho Javis tạo video Muse ngay trong chat, chạy nhiều tài khoản Muse của bạn cùng lúc. Nhờ Javis "tạo 5 cảnh
video 9:16 về quán phở sáng sớm" là các cảnh chạy song song, cảnh lỗi tự làm lại, file `canh-01.mp4`, `canh-02.mp4`...
lưu vào brain, thư mục `attachments/muse-video`.

Muse là ứng dụng AI của Meta ở muse.ai và chưa có API công khai. Javis đi qua **Muse Gateway**, một chương trình chạy
trên máy bạn, đăng nhập bằng cookie của từng tài khoản. Đây là đường không chính thức: Muse đổi cách hoạt động là
gateway có thể ngừng chạy, và tài khoản dùng kiểu này có thể bị khoá.

## 1. Cài Muse Gateway (một lần)

Gói không mang gateway. Lấy bộ cài Muse Gateway từ người phát hành gói, rồi chọn một trong hai cách.

**Máy Windows chạy Javis**

1. Mở thư mục `bin` của bộ cài `muse-full`.
2. Chạy `muse.exe install` trong PowerShell: gateway tự chép vào `%LOCALAPPDATA%\Programs\Muse` và tự chạy mỗi lần đăng nhập Windows.
3. Mở `http://127.0.0.1:8080/health` trên trình duyệt, thấy chữ `ok` là xong.

Đừng chạy `cai-dat.ps1` của bộ cài nếu đã cài gói này: script đó chép thêm một bản plugin cài tay, bản đó sẽ đè bản
trong gói.

**VPS Linux (Docker)**

1. Chép bộ cài `muse-docker` lên VPS và giải nén.
2. Trong thư mục `muse-docker`, chạy `./install.sh` không kèm tham số (chỉ cài gateway).
3. Xem khoá quản trị bằng `docker compose exec muse muse info`.

## 2. Cài gói

1. Trong Javis, mở **Kho cài đặt**, tìm **Muse**, bấm **Cài đặt**.
2. Xem mã gói trên màn hình xác nhận rồi bấm đồng ý, sau đó bật plugin **Muse nhiều tài khoản** ở trang Plugin.
3. Bấm **Mở trang** trên thẻ plugin.

## 3. Nối gateway

Trang Muse báo **Sẵn sàng** hoặc **Chưa có tài khoản** là đã nối được, bỏ qua bước này.

Javis và gateway cùng một máy (Windows, hoặc VPS mà Javis chạy thẳng, không qua Docker): Javis tự đọc khoá trong
`config.json` của gateway, không cần điền gì.

Javis chạy trong Docker, gateway cũng chạy trong Docker trên cùng VPS:

1. Cho hai container chung mạng: `docker network connect <mạng của Javis> muse`.
2. Trên trang Muse, mở mục **Nối gateway**, điền địa chỉ `http://muse:8080`.
3. Điền khoá quản trị (lệnh `muse info` ở bước 1), bấm **Lưu và thử**.

Khoá chỉ lưu trong thư mục dữ liệu của plugin trên máy bạn, trang không bao giờ hiện lại khoá.

## 4. Thêm tài khoản

1. Mở muse.ai trên máy tính và đăng nhập.
2. Bấm F12, chọn thẻ Network, tải lại trang.
3. Bấm dòng đầu tiên, tìm **Cookie** trong Request Headers, chép cả dòng (có `hatch_sess=`).
4. Dán vào ô **Thêm tài khoản** trên trang Muse, mỗi tài khoản một dòng, đặt tên bằng `Tên | cookie`.
5. Bấm **Nạp và kiểm**.

Cookie đi thẳng từ trình duyệt vào gateway, không qua chat. Javis chỉ giữ dấu vân tay của cookie để bỏ qua cookie
trùng. Gateway thì giữ cookie dạng rõ trong `data.json` của nó, nên ai đọc được thư mục dữ liệu gateway là dùng được
tài khoản đó.

**Cookie hết hạn**: dán cookie mới với đúng tên cũ, Javis thay cookie cho tài khoản đó. Máy chạy Javis có màn hình
(Windows) thì bấm nút đăng nhập ở dòng tài khoản một lần: cửa sổ Muse mở ra, đăng nhập xong thì về sau nút gia hạn tự
lấy cookie mới, không phải dán lại.

## 5. Cách chạy

- **Gộp**: mọi tài khoản cùng chạy, gateway chia việc, tài khoản hỏng tự tắt. Hợp khi cần nhanh.
- **Luân phiên**: mỗi lúc một tài khoản, hỏng thì chuyển sang tài khoản kế.
- **Video cùng lúc mỗi tài khoản**: mặc định 2.

## Đã cài tay bản 1.2.0 trước đây

Bản cài tay nằm ở `<thư mục Javis>/_state/plugins/muse-accounts`. Khi thư mục đó còn, Javis dùng bản cài tay và bỏ qua
bản trong gói. Cài gói xong thì xoá (hoặc đổi tên) thư mục đó rồi khởi động lại Javis. Chế độ chạy, cửa sổ đăng nhập đã
lưu và các lô video nằm ở `_state/plugin-data/muse-accounts`, dùng tiếp nguyên vẹn.

Bản trong gói không mang `muse-login.exe` (kho không nhận tệp chạy được mà không đọc được mã). Không có nó, cửa sổ đăng
nhập mở bằng Chrome, hoặc Edge có sẵn trên Windows. Muốn giữ cửa sổ WebView2 cũ thì để `muse-login.exe` ở một thư mục
riêng và đặt biến môi trường `MUSE_LOGIN_EXE` trỏ tới nó.

## Biến môi trường (không bắt buộc)

| Biến | Dùng khi |
|---|---|
| `MUSE_GATEWAY_URL` | Địa chỉ gateway mặc định khác `http://127.0.0.1:8080` (trang Muse lưu địa chỉ thì trang thắng) |
| `MUSE_ADMIN_KEY` | Khoá quản trị, khi không đọc được `config.json` của gateway |
| `MUSE_GATEWAY_CONFIG` | Đường tới `config.json` của gateway, khi gateway để dữ liệu chỗ khác |
| `MUSE_CHROME` | Trình duyệt mở cửa sổ đăng nhập, khi Chrome cài ở chỗ lạ |
| `MUSE_LOGIN_EXE` | Đường tới `muse-login.exe` (chỉ Windows) |

## Gateway mở ra Internet

Mặc định gateway chỉ nghe trên `127.0.0.1`. Nếu mở cổng 8080 ra ngoài, đặt gateway sau HTTPS (nginx, Caddy) trước khi
gửi khoá qua Internet, và chỉ phát khoá `mk_live_...` riêng cho công cụ khác, đừng phát khoá quản trị.
