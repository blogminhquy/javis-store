# Nguồn của gói theo repo gốc `AgriciDaniel/claude-seo`

Skill trong gói này chép **nguyên văn** từ [AgriciDaniel/claude-seo](https://github.com/AgriciDaniel/claude-seo), nhánh `main`:
không dịch, không rút gọn, không sửa frontmatter.

Mỗi ngày kho so lại với repo gốc (`tools/theo-goc.py`). Repo gốc đổi thì gói lên một bản
vá, Javis báo trong chuông và bạn bấm Cập nhật. Skill bạn đã tự sửa thì Javis giữ lại,
không ghi đè. Commit gốc của bản đang phát hành nằm ở trường `upstream.commit` trong
`index.json`.

Giấy phép gốc: **MIT**, nguyên văn ở `docs/LICENSE-goc*` nếu repo có.

| Skill | Thư mục gốc |
|---|---|
| `seo` | `skills/seo` |
| `seo-agentic` | `skills/seo-agentic` |
| `seo-audit` | `skills/seo-audit` |
| `seo-backlinks` | `skills/seo-backlinks` |
| `seo-cluster` | `skills/seo-cluster` |
| `seo-competitor-pages` | `skills/seo-competitor-pages` |
| `seo-content` | `skills/seo-content` |
| `seo-content-brief` | `skills/seo-content-brief` |
| `seo-drift` | `skills/seo-drift` |
| `seo-ecommerce` | `skills/seo-ecommerce` |
| `seo-flow` | `skills/seo-flow` |
| `seo-geo` | `skills/seo-geo` |
| `seo-google` | `skills/seo-google` |
| `seo-hreflang` | `skills/seo-hreflang` |
| `seo-images` | `skills/seo-images` |
| `seo-local` | `skills/seo-local` |
| `seo-maps` | `skills/seo-maps` |
| `seo-page` | `skills/seo-page` |
| `seo-plan` | `skills/seo-plan` |
| `seo-programmatic` | `skills/seo-programmatic` |
| `seo-schema` | `skills/seo-schema` |
| `seo-sitemap` | `skills/seo-sitemap` |
| `seo-sxo` | `skills/seo-sxo` |
| `seo-technical` | `skills/seo-technical` |

Tệp dùng chung chép kèm vào skill, đúng chỗ bộ cài chính thức của tác giả đặt:

| Trong gói | Gốc ở repo |
|---|---|
| `skills/seo/scripts` | `scripts` |
| `skills/seo/data` | `data` |
| `skills/seo/schema` | `schema` |
| `skills/seo/requirements.txt` | `requirements.txt` |
| `skills/seo/.claude-plugin/plugin.json` | `.claude-plugin/plugin.json` |

Chữ duy nhất gói viết lại, trong tệp `.md` (giống bộ cài tay của tác giả). Javis không đặt
`CLAUDE_PLUGIN_ROOT` như plugin Claude Code, còn `CLAUDE_SKILL_DIR` là thư mục skill đang nạp:

| Gốc | Trong gói |
|---|---|
| `"${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo"` | `bash "${CLAUDE_SKILL_DIR}/../seo/scripts/claude-seo"` |
| `${CLAUDE_PLUGIN_ROOT}/skills/` | `${CLAUDE_SKILL_DIR}/../` |
