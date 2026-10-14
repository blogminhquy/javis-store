# pancake-chat-mcp

MCP server for the conversations of ONE Pancake page (pages.fm), built for the Javis store.

- Talks to `https://pages.fm` only, through Pancake's public Page API.
- Env: `PANCAKE_PAGE_ID`, `PANCAKE_PAGE_ACCESS_TOKEN` (Pancake: page settings > Tools; the token does not expire).
- No dependencies. Node >= 18. stdio transport.

Tools: conversations, messages, customers, tags, posts, staff, page/tag/staff/engagement statistics (read);
tag a conversation, mark read/unread, assign, add a customer note (write); send a message to a customer (danger).

Packed into `../../mcp/` by `python tools/mcp_dong_goi.py pancake-chat-mcp`.
