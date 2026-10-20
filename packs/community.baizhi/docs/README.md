# Baizhi MCP (百智云工具包)

This community pack adds a named, optional Baizhi connector to Javis Store and, after installation, to Connections. It does not add a bundled core connector or a search fallback. It was prepared as Baizhi ecosystem promotion; it is not an official Javis or Baizhi endorsement.

The supplier's public README describes `websearch_search` (web search), `web_scrape` (web text) and `web_extract` (structured extraction). These are examples from published documentation, not a tested live schema or a complete allowlist. Available tools depend on the service and your key. This pack does not call any tool for account-label verification.

## Setup and permissions

1. Install the pack through Javis Store. This data-only pack's installation switch starts on in the tested Javis 0.225.0; it only adds the connector template and does not create an account or contact Baizhi.
2. Open Connections, choose Baizhi MCP and enter your own dedicated key. The current key field shows visible text. Do not share screenshots of it. Javis normally encrypts saved keys with its local machine key, but its existing secret store falls back to plaintext when encryption is unavailable. Use a trusted Javis installation and check its encryption setup.
3. Pressing Connect saves an enabled connection and immediately initializes the remote MCP and lists tools. Test can repeat network requests. The fixed destination is `https://agent-toolkit.app.baizhi.cloud/mcp`; authorization is `Authorization: Bearer <your key>`. No key belongs in this package or in the URL.
4. The connection starts at Read-only. This pack sets `read: []`, `write: []`, `danger: ["*"]`, so no remote tool is exposed at Read-only or Draft, including future tools with unfamiliar names. Review the live tool list and your Baizhi account's costs first, then deliberately choose Full connection permission only if you want calls. Ordinary sessions also require Full mode, which does not ask again before each call. In Javis 0.207.0 and later, a Team step in gated mode can list the Full connection's tools, but each call needs the owner's approval of that exact call. Missing, stale or unapproved Team context is rejected.
5. Disable the connection when you do not want Javis to use it. A local disable or session close is not evidence that remote tasks or billing have stopped.

Baizhi is an external hosted service. Requests, URLs and tool arguments are sent to Baizhi. Your account may consume credits or paid allowance, including for tools that only retrieve information. Javis's and this pack's MIT licenses do not mean the hosted service is free or that its backend is open source. Obtain and manage the key and allowance through Baizhi's own account channels.

This package contains connector data, documentation and an original BZ monogram, not vendor server code. No Python plugin, shell command, environment credential, automatic connection or model provider is added. The author name identifies the community contributor, not the service provider. `verified` is false; only the store maintainer decides adoption.

## Verification boundary

Synthetic tests use Javis 0.225.0 (main `417bd45b23ee3755b04816b191eb2bf8de2dd76d`) and a loopback fake MCP. They exercise the real pack loader, native MCP HTTP client, guarded hub and the current HTTP wrapper registered in a fixture app. The full Javis application lifecycle and other middleware are not loaded; the fixture is not a full-app test. Team approvals in the test are synthetic local records, not a human test or a real account action. No real Baizhi or model request, real authenticated tool schema, GUI session or production operation has been verified. Passing local tests does not establish Javis Store acceptance, official release or real service compatibility. The monogram is a community placeholder, not the supplier's official logo.

Supplier documentation: https://github.com/chaitin/baizhi-agent-toolkit
Javis OS: https://github.com/blogminhquy/javis-os

## Tiếng Việt

Gói cộng đồng này thêm mẫu kết nối Baizhi có tên trong Kho cài đặt và trang Kết nối, không thêm vào lõi. Cài gói dữ liệu mặc định bật mẫu, nhưng chưa tạo tài khoản và chưa gọi Baizhi. Bấm Kết nối sau khi nhập key riêng sẽ lưu tài khoản ở trạng thái bật và khởi tạo/liệt kê tool qua mạng. Ô key là chữ nhìn thấy; kho bí mật của Javis có thể lưu chữ thường nếu mã hóa không khả dụng.

Mọi tool, kể cả tên mới, đều xếp `danger`. Chỉ đọc và Ghi nháp không chạy tool. Phiên thông thường cần Toàn quyền cho cả kết nối và phiên, không hỏi lại mỗi lần. Từ Javis 0.207.0, bước nhóm ở chế độ cần duyệt có thể liệt kê tool khi kết nối Toàn quyền, nhưng từng lời gọi đúng tham số phải được chủ nhóm duyệt. Dữ liệu và URL gửi tới Baizhi, có thể tiêu hạn mức/tiền tài khoản. Xem danh sách tool thực và chi phí trước khi nâng quyền. Không có xác minh tài khoản Baizhi thật, GUI hay sản xuất. Logo BZ là hình đại diện cộng đồng tự vẽ, không phải logo chính chủ.
