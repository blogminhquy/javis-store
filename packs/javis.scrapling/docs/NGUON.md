# Nguồn của gói `javis.scrapling`

Gói này **không mang theo mã của Scrapling**. Nó chỉ là một tờ khai connector trỏ Javis chạy MCP server chính chủ của tác giả, lấy từ PyPI:

```
uvx --from "scrapling[ai]@latest" scrapling mcp
```

- Mã nguồn: <https://github.com/D4Vinci/Scrapling> (BSD-3-Clause)
- Gói PyPI: <https://pypi.org/project/scrapling/>
- Tài liệu MCP: <https://scrapling.readthedocs.io/en/latest/ai/mcp-server.html>

Vì mã tải thẳng từ PyPI lúc chạy, gói không chép lại tệp nào của tác giả và không ghim phiên bản: `@latest` lấy bản mới nhất mỗi lần khởi động. Logo trong `assets/` là logo của dự án Scrapling, thu nhỏ về 256x256.

Skill chính chủ của cùng tác giả (dạy AI viết mã cào bằng Scrapling) là gói riêng `javis.scrapling-skill`, đi theo repo gốc.

## Đã chạy thử gì (bản 0.5.1, ngày 12/11/2026)

- Chạy đúng lệnh trên, liệt kê được 17 tool.
- `make_request` tải một trang thử và lọc bằng CSS selector: trả đúng nội dung.
- `browser_fetch_once` mở trình duyệt ẩn danh: trang có JavaScript được chạy trước khi đọc.
- Biến `SCRAPLING_EXECUTABLE_PATH` (ô "Đường dẫn Chrome có sẵn"): trình duyệt chạy bằng đúng tệp đã chỉ.
- Chưa thử: vượt Cloudflare trên trang thật, vì máy thử không ra được Internet ngoài PyPI.

## Phân loại quyền cho 17 tool

`mcp_catalog.classify` xếp thứ không khớp gì về `read`, nên cả 17 tool đều được kể tên.

| Mức | Gồm gì |
|---|---|
| `read` | `make_request` và `session_make_request` khi là lệnh GET, `list_sessions`, và các lệnh đọc trang trong một phiên trình duyệt đã mở: `browser_fetch`, `browser_extract`, `browser_network_requests`, `browser_screenshot`, `browser_observe`. |
| `write` | `browser_fetch_once` (mở trình duyệt ẩn danh tải một trang), `open_request_session`, `close_session`. |
| `danger` | `browser_open`, `browser_actions`, `browser_evaluate`, `browser_network_request`, `browser_save_state`, `browser_load_state`; mọi lệnh POST, PUT, DELETE; và `browser_fetch_once` khi có `additional_args`. |

Ba chỗ phân loại theo tham số (`call_rules`), vì cùng một tool mà hậu quả khác hẳn nhau:

- `make_request`, `session_make_request`: tham số `method`. GET (kể cả khi bỏ trống, vì mặc định của Scrapling là GET) là đọc. POST, PUT, DELETE gửi dữ liệu lên trang của người khác nên là `danger`.
- `browser_fetch_once`: tham số `additional_args`. Vắng thì là `write`. Có thì là `danger`: nó đè tuỳ chọn khởi động trình duyệt, gồm cả cờ dòng lệnh của Chromium, mà vài cờ trong đó chạy được lệnh tuỳ ý trên máy.

Cả ba tool trên cũng được kể tên trong `tool_meta.danger`. Javis xét `call_rules` trước danh sách `danger`, nên luật theo tham số mới là thứ quyết định; danh sách `danger` chỉ là lưới đỡ, để bản Javis nào không hiểu `call_rules` thì ba tool rơi về mức nguy hiểm thay vì rơi về đọc.

Vì sao `browser_open` là `danger` dù chỉ "mở phiên": nó nhận cả `cdp_url` (nối vào một Chrome đang chạy, tức thấy mọi trang người dùng đang đăng nhập) lẫn `additional_args`. Một luật `call_rules` chỉ canh được một tham số, nên cả tool nằm ở mức cao nhất. Hệ quả: phiên trình duyệt dài và các lệnh bấm, gõ chỉ dùng được ở mức Toàn quyền; mức Ghi nháp tải từng trang bằng `browser_fetch_once`.

`browser_network_request` là `danger` dù chỉ đọc: nó trả header của request đã ghi, trong đó có cookie phiên. Cùng lý do gói Playwright xếp cookie vào mức này.

Điều còn hở, nói thẳng: ở mức Ghi nháp, `browser_fetch_once` vẫn nhận `executable_path` (chạy một tệp có sẵn trên máy thay cho Chromium) và `real_chrome`. Javis chưa có cách canh hai tham số trên cùng một tool. Câu `risk` của khuôn nói điều này cho người dùng trước khi họ nâng mức.

## Mặc định an toàn

Không có ô cấu hình nào bắt buộc. Trình duyệt của Scrapling chạy bằng hồ sơ trống riêng, MCP không mở tham số `user_data_dir`, nên nó không đụng vào hồ sơ Chrome của người dùng trừ khi được nối qua `cdp_url` ở mức Toàn quyền.
