# Gói `javis.noi-dung-social`

4 trợ lý và 3 quy trình, viết riêng cho kho Javis.

## Vì sao trợ lý và quy trình nằm chung một gói

Workflow gọi agent bằng slug. `_agent_sysprompt` đọc file agent bằng `_read_md`, và thiếu file thì hàm đó trả về rỗng chứ KHÔNG báo lỗi. Nghĩa là một workflow thiếu agent vẫn chạy, nhưng chạy với một agent không có vai trò lẫn prompt, và không ai được báo. Vì vậy gói này mang theo đủ agent mà workflow của nó cần.

## Vì sao slug agent có tiền tố riêng

Bốn gói trong bộ này đều có một agent kiểm chứng. Nếu chúng dùng chung một slug thì `pack_vault` chỉ ghi được bản của gói cài trước, và gỡ gói đó sẽ xoá file mà ba gói kia vẫn đang gọi. Tiền tố riêng giữ cho bốn gói độc lập hoàn toàn: cài lẻ gói nào cũng chạy, gỡ gói nào cũng không đụng gói khác.

## Quy trình trong gói

| Slug | Tên | Chuỗi agent |
|---|---|---|
| `y-tuong-thanh-bai-dang` | Từ ý tưởng ra bài đăng | nd-goc-nhin -> nd-viet-bai -> nd-bien-tap |
| `bai-dai-thanh-chuoi-post` | Bẻ bài dài thành chuỗi post | nd-goc-nhin -> nd-viet-bai |
| `lich-noi-dung-thang` | Lịch nội dung một tháng | nd-goc-nhin -> nd-bien-tap |

Workflow chạy tuần tự. `{{input}}` là thứ bạn gõ vào lúc chạy, `{{prev}}` là kết quả của bước liền trước. Bước có `verify_agent` sẽ được agent kiểm chứng soi lại, chưa đạt thì chạy lại tối đa `max_retries` lần.

## Trợ lý trong gói

- `nd-goc-nhin` - Người tìm góc tiếp cận: Trước khi viết, xác định bài này nói với ai, hứa điều gì và vì sao họ phải đọc hết.
- `nd-viet-bai` - Người viết nội dung: Viết bài đăng mạng xã hội theo góc tiếp cận đã chốt, giọng đời thường, không sáo rỗng.
- `nd-bien-tap` - Biên tập viên: Cắt chữ thừa, bắt câu sáo và khẳng định không có bằng chứng, giữ nguyên giọng của người viết.
- `nd-kiem-chung` - Kiểm chứng độc lập (nội dung): Đánh giá độc lập bài viết, mặc định là nó đang sai hoặc thiếu bằng chứng.

## Hai chỗ bạn nên chỉnh sau khi cài

1. **Skill.** Mọi agent để `skills: []` trống, vì gói không biết brain của bạn đang có skill nào. Mở trang Trợ lý, thêm slug skill bạn muốn agent đó dùng.
2. **Model.** Mọi agent để `model: ""`, tức chạy theo mặc định của engine bạn đang dùng. Ghim model cụ thể chỉ khi bạn chắc máy mình có model đó.

Agent và workflow được ghi vào **brain đang mở lúc bấm Cài**, không phải mọi brain. Javis không ghi đè mục bạn tự tạo trùng tên, và gỡ gói chỉ xoá thứ bạn chưa sửa.
