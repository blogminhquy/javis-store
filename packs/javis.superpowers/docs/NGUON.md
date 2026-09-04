# Nguồn của gói `javis.superpowers`

Lấy từ **[Jesse Vincent](https://github.com/obra/superpowers)**, thư mục [https://github.com/obra/superpowers/tree/main/skills](https://github.com/obra/superpowers/tree/main/skills).

Giấy phép gốc: **MIT** - nguyên văn ở `docs/LICENSE.txt`.

## Đã sửa những gì

Nội dung skill (thân file, `references/`, `scripts/`) **giữ nguyên bản gốc, không dịch**. Chỉ frontmatter `SKILL.md` được chỉnh cho khớp Javis:

- `description` dài hơn 150 ký tự được rút gọn (router của Javis cắt đúng ở đó nên phần dư vốn mất im lặng). Mô tả gốc chép xuống cuối file.
- Thêm `group` để trang Kỹ năng gom nhóm.
- Ba skill được đổi tên thư mục (`sp-` phía trước) vì `javis.hermes-dev` đã có skill trùng slug; `pack_vault` BỎ QUA mục trùng chứ không ghi đè, nên không đổi là chúng âm thầm không được cài.
- KHÔNG mang theo `.hermes-plugin/`, `hooks/` và `scripts/` ở gốc repo: chúng viết cho API của Hermes và Claude Code (`ctx.register_skill`, hook `pre_llm_call`), mà Javis chỉ có `register_tool`/`register_hook` với hook `pre_tool_call`/`post_tool_call`. Phần việc chúng làm thì skill router của Javis đã lo sẵn.
- Mỗi `SKILL.md` có một dòng ghi nguồn ở cuối.

## Bảng đối chiếu

| Slug trong Javis | Thư mục gốc |
|---|---|
| `using-superpowers` | `skills/using-superpowers` |
| `brainstorming` | `skills/brainstorming` |
| `writing-plans` | `skills/writing-plans` |
| `executing-plans` | `skills/executing-plans` |
| `subagent-driven-development` | `skills/subagent-driven-development` |
| `dispatching-parallel-agents` | `skills/dispatching-parallel-agents` |
| `sp-test-driven-development` | `skills/test-driven-development` |  (đổi tên: đã có skill trùng slug trong kho)
| `sp-systematic-debugging` | `skills/systematic-debugging` |  (đổi tên: đã có skill trùng slug trong kho)
| `sp-requesting-code-review` | `skills/requesting-code-review` |  (đổi tên: đã có skill trùng slug trong kho)
| `receiving-code-review` | `skills/receiving-code-review` |
| `verification-before-completion` | `skills/verification-before-completion` |
| `using-git-worktrees` | `skills/using-git-worktrees` |
| `finishing-a-development-branch` | `skills/finishing-a-development-branch` |
| `writing-skills` | `skills/writing-skills` |
