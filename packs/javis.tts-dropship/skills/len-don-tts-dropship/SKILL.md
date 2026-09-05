---
name: Lên đơn TTS Dropship
description: Lên đơn dropship sàn TTS từ tin nhắn khách: tách địa chỉ, tránh tạo khách trùng, báo giá ship, xem trước rồi mới tạo đơn thật.
description_en: "Place a TTS dropship order from a customer chat message: parse the address, avoid duplicate customers, quote shipping, preview before creating the real order."
group: Bán hàng
---

# Lên đơn TTS Dropship

## Khi nào dùng

Khi người dùng dán một tin nhắn khách kiểu "Nguyễn Văn A 0912345678, 12 Lê Lợi, P.1, Q.1,
TPHCM, lấy 2 cái áo thun trắng size L" và muốn lên đơn trên sàn dropship.thitruongsi.com.
Cũng dùng khi họ hỏi "đơn của chị Lan tới đâu rồi", "sản phẩm này lãi bao nhiêu", "tháng này
tiền về bao nhiêu".

Không dùng cho sàn khác. Pancake POS, Shopify, TikTok Shop đều có kết nối riêng.

## Điều phải biết trước, vì nó quyết định cả quy trình

**Đơn đã tạo thì sàn KHÔNG cho sửa.** Không có PUT, không có PATCH cho một đơn. Sai một chữ
trong địa chỉ là phải huỷ đơn rồi lên lại từ đầu, và huỷ nhiều lần thì ảnh hưởng uy tín tài
khoản bán. Vì vậy toàn bộ công sức nằm ở bước KIỂM TRƯỚC KHI TẠO, không phải ở bước sửa sau.

**Sàn cũng không cho sửa hay xoá khách hàng.** Tạo nhầm một khách là nó nằm lại vĩnh viễn.
Địa chỉ sai thì cách duy nhất là thêm một địa chỉ mới rồi đặt làm mặc định.

**Mỗi nhà cung cấp là một đơn riêng.** Giỏ có hàng của 3 shop thì ra 3 đơn, 3 phí vận chuyển,
3 mã vận đơn. Nói trước cho người dùng biết, đừng để họ tưởng là một đơn.

## Quy trình lên đơn

Sáu bước, chạy đúng thứ tự này.

**1. Tách địa chỉ.** Dán nguyên đoạn tin nhắn vào `tts_customers` với `action=parse_address`.
Nó trả về tỉnh, quận, phường, số nhà đã chuẩn hoá. Đừng tự tách bằng mắt: tên phường xã Việt
Nam trùng nhau rất nhiều và sàn khớp theo mã địa giới của chính nó.

**2. Tìm khách cũ trước khi tạo mới.** `tts_customers` với `action=search`, tìm theo SỐ ĐIỆN
THOẠI chứ không theo tên (tên Việt trùng nhiều, số điện thoại thì không). Có rồi thì lấy
`customer_id` và đi tiếp. Chưa có mới gọi `tts_customer_write` với `action=create`.

**3. Tìm hàng.** `tts_products` với `action=search`. Muốn ra hàng lãi cao thì
`sort_by=dropship_profit`. Chọn xong lấy `product_id`, `shop_id` và đúng `variant_id` của phân
loại khách muốn bằng `action=get`. Sai variant là giao nhầm màu nhầm size.

**4. Báo giá ship.** `tts_shipping_rates` với `destination` lấy từ bước 1, `items` là hàng ở
bước 3, `shop_id` là kho gửi. Lấy NGUYÊN một mục trong `data.rates` để dùng ở bước sau, đừng
chép tay lại từng trường.

**5. Xem trước.** Gọi `tts_create_order` KHÔNG có `confirm`. Nó không chạm mạng ghi, chỉ dựng
bản tóm tắt: khách, hàng, số lượng, giá bán, phí ship, lãi ước tính. **Đọc lại bản này cho
người dùng bằng lời**, nêu rõ tên người nhận, số điện thoại, địa chỉ đầy đủ và tổng tiền khách
phải trả.

**6. Tạo thật.** Người dùng xác nhận rồi mới gọi lại `tts_create_order` với `confirm=true`.
Giỏ nhiều shop thì truyền mảng `orders`, mỗi mục một shop. Kết quả trả về trạng thái từng đơn:
nếu có đơn lỗi thì **chỉ lên lại đúng đơn đó**, đừng gọi lại cả lượt, vì các đơn đã tạo là
thật và không tự huỷ.

## Tính lợi nhuận thật

`dropship_profit` trong kết quả tìm kiếm chỉ là lãi GỢI Ý của sàn. Lãi thật của một đơn còn
phụ thuộc hai thứ nữa:

- **Khuyến mãi của shop**: `tts_suppliers` với `action=promotions`. Freeship và thưởng theo
  đơn thay đổi hẳn con số.
- **Phí vận chuyển bạn tài trợ**: nằm trong đơn, không nằm trong sản phẩm.

Số cuối cùng chỉ có sau khi đơn đã tạo, ở `tts_orders` với `action=get`: khối hoa hồng trong
đó có tổng giá bán, tổng giá nhà cung cấp, tổng thưởng, phí ship đã tài trợ và tổng lợi nhuận.
Khi người dùng hỏi "đơn này lãi bao nhiêu", trả lời bằng con số đó chứ đừng nhân tay.

## Tiền về khi nào

`tts_finance` với `action=income` cho tổng quan: tiền chờ đối soát, thuế thu nhập cá nhân tạm
giữ, tiền rút được. `action=escrow` cho từng đơn kèm ngày giải ngân.

Điều hay bị hỏi: tiền của một đơn đã giao vẫn bị giữ cho tới khi đơn được đánh giá. Muốn mở
khoá thì `tts_order_action` với `action=rate`, và nó chỉ gửi được một lần.

Gói này cố ý không có tool rút tiền. Người dùng muốn rút thì vào ví trên web của sàn.

## Chỗ sai hay gặp

**Quên rằng token sống có 3 ngày.** Mọi tool báo rõ khi token hết hạn, nhưng đừng để người
dùng phát hiện giữa lúc đang lên đơn cho khách. Thấy `canh_bao` về hạn token trong kết quả thì
nhắc luôn một câu.

**Gọi `tts_create_order` với `confirm=true` ngay lần đầu.** Đừng. Bước xem trước tồn tại vì
đơn không sửa được. Kể cả khi người dùng nói "cứ lên đơn đi", vẫn đọc bản xem trước ra rồi hỏi
một câu, mất năm giây và tránh một đơn phải huỷ.

**Tạo khách mới mà không tìm trước.** Khách trùng không xoá được.

**Đoán `variant_id`.** Luôn lấy từ `tts_products` `action=get`, đừng suy từ tên phân loại.

**Kết luận "sàn đổi API" khi một tool lỗi.** Chạy `tts_health_check` trước. Nó chỉ ra đường
nào hỏng và phân biệt được ba nguyên nhân: token hết hạn, sàn đổi tên trường GraphQL, hay sàn
thật sự đổi endpoint. Trường hợp giữa thì sửa được ngay bằng `tts_graphql` với `action=set`,
không phải cài lại gói.

## Loop chạy nền

Không bao giờ để một loop tự gọi `tts_create_order`, `tts_cancel_order` hay `tts_order_action`,
kể cả khi kết nối đã ở mức Toàn quyền. Loop được phép đọc: theo dõi vận đơn, cảnh báo đơn treo
quá lâu ở `wait_confirm`, báo tiền sắp về. Việc lên đơn luôn cần một người nhìn vào bản xem
trước.
