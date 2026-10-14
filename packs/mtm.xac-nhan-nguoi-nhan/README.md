# Xác nhận người nhắn (trung tâm dạy học)

***Tiếng Việt** · [English](README.en.md)*

Trung tâm chỉ gửi bài tập, tài liệu, link cho học sinh và phụ huynh **đã xác nhận**; người khác hỏi thì nhận thông tin chung. Bot không phân biệt được hai nhóm qua lời nhắn, vì ai cũng gõ được "em là mẹ bé An". Gói này đối chiếu **tài khoản gửi tin** với sổ của trung tâm và chỉ đưa cho bot một dòng kết luận.

## Cần gì

- Javis OS có hook `chatbot_turn_context` (PR javis-os#TBD). Bản cũ hơn: gói cài được nhưng không làm gì.
- Bot chuyên trách trực **Zalo cá nhân**.

## Bật thế nào (tự chọn, mặc định không làm gì)

1. Cài gói ở **Kho cài đặt**, đọc mã trước khi đồng ý.
2. Trong **brain của bot**, tạo `Javis/xac-nhan-dang-hoc.md`:

   ```
   | uid | vai trò | lớp |
   |---|---|---|
   | 1000000001 | phụ huynh | 10Z9 |
   | 1000000002 | học sinh | 11Z9 |
   ```

   `uid` là mã tài khoản Zalo của người nhắn. Chỉ ba cột đầu được đọc; cột sau (tên, ghi chú) bị bỏ ngay lúc đọc.
3. Trong file Agent của bot, viết luật dùng kết luận, ví dụ: "Chat riêng ĐÃ XÁC NHẬN thì gửi bài tập của đúng lớp; CHƯA XÁC NHẬN thì chỉ tư vấn chung."

Bot không có sổ thì không có dòng nào, bot chạy y như trước.

## Bot thấy gì

Một dòng trong khối `<operator_context>` của lượt đó, ví dụ `NGƯỜI ĐANG NHẮN (...): ĐÃ XÁC NHẬN, phụ huynh, lớp 10Z9.` hoặc `CHƯA XÁC NHẬN`. **Không bao giờ** có mã tài khoản hay dòng khác của sổ.

- Nhóm: không có dòng nào.
- Kênh khác Zalo cá nhân: không có dòng nào (đổi được, xem dưới).
- Lượt **Thử bot** dùng người gửi giả nên luôn ra `CHƯA XÁC NHẬN`.

## Cài đặt thêm (không bắt buộc)

Tệp `<JAVIS_STATE_DIR>/plugin-data/xac-nhan-nguoi-nhan/config.json`, chung mọi bot trên máy:

```json
{"roster": "Javis/xac-nhan-dang-hoc.md", "channels": ["zalo_personal"]}
```

`roster` là đường dẫn tương đối trong brain của bot, không ra ngoài brain được. Nên để sổ trong `Javis/`: phần tra tài liệu của bot bỏ qua thư mục này. Bot ở mức Toàn quyền vẫn tự mở được mọi tệp trong brain.

## Riêng tư

Gói không gọi mạng và không ghi gì. Sổ là dữ liệu học sinh: giữ ở brain riêng của bot, không chép vào tài liệu bot đọc được.
