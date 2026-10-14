#!/usr/bin/env node
// Pancake chat MCP: read and work the conversations of ONE Pancake page (pages.fm) through Pancake's public Page API.
//
// Written for the Javis store (javis-store/mcp-nguon/pancake-chat-mcp) because no usable Pancake chat MCP existed
// (2026-10-14): the community ones had no license, scraped a browser token, or sent the Pancake token to third-party
// hosts. This one is small on purpose so a reviewer can read all of it:
//   - no dependencies, Node >= 18 (global fetch), stdio JSON-RPC (one JSON message per line);
//   - talks to https://pages.fm ONLY. The page token goes in the query string because that is how Pancake's API
//     authenticates; it is never logged and is cut out of every error message;
//   - one page per connection: PANCAKE_PAGE_ID + PANCAKE_PAGE_ACCESS_TOKEN (Page settings > Tools; it does not expire).
//
// API reference: Pancake's public OpenAPI (https://developer.pancake.biz), Page API v1 and v2.

"use strict";

const VERSION = "1.0.0";
const PAGE_ID = String(process.env.PANCAKE_PAGE_ID || "").trim();
const TOKEN = String(process.env.PANCAKE_PAGE_ACCESS_TOKEN || "").trim();
const MAX_OUT = 60000; // characters of JSON handed back to the model per call

// Tests point the server at a local fake API. Only a loopback address is accepted, so a misconfigured env can never
// send the page token to another host.
function apiRoot() {
  const o = String(process.env.PANCAKE_API_BASE || "").trim();
  if (o && /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(o)) return o;
  return "https://pages.fm";
}

function redact(s) {
  let t = String(s || "");
  if (TOKEN) t = t.split(TOKEN).join("***");
  return t.replace(/page_access_token=[^&\s"]+/g, "page_access_token=***");
}

// ---- time helpers ----------------------------------------------------------------------------------------------
// The model gives dates as "YYYY-MM-DD" (Vietnam time) or unix seconds. Pancake wants unix seconds (UTC) for most
// reports and a "dd/mm/yyyy HH:MM:SS - dd/mm/yyyy HH:MM:SS" range for the staff and engagement reports.
const VN = 7 * 3600;

function toUnix(v, endOfDay) {
  if (v === undefined || v === null || v === "") return null;
  if (typeof v === "number" || /^\d{9,11}$/.test(String(v))) return Math.floor(Number(v));
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(v).trim());
  if (!m) throw new Error(`ngày không hợp lệ: ${v} (dùng YYYY-MM-DD hoặc unix giây)`);
  const base = Date.UTC(+m[1], +m[2] - 1, +m[3]) / 1000 - VN;
  return endOfDay ? base + 86399 : base;
}

function range(args, defaultDays) {
  const now = Math.floor(Date.now() / 1000);
  const until = toUnix(args.until, true) ?? now;
  const since = toUnix(args.since, false) ?? until - defaultDays * 86400;
  if (since > until) throw new Error("since phải trước until");
  return { since, until };
}

function vnText(unix) {
  const d = new Date((unix + VN) * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())}/${p(d.getUTCMonth() + 1)}/${d.getUTCFullYear()} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

// ---- HTTP --------------------------------------------------------------------------------------------------------
async function call(method, version, path, query, body) {
  if (!PAGE_ID || !TOKEN) throw new Error("thiếu PANCAKE_PAGE_ID hoặc PANCAKE_PAGE_ACCESS_TOKEN");
  const url = new URL(`${apiRoot()}/api/public_api/${version}/pages/${encodeURIComponent(PAGE_ID)}${path}`);
  for (const [k, v] of Object.entries(query || {})) {
    if (v === undefined || v === null || v === "") continue;
    url.searchParams.set(k, Array.isArray(v) ? v.join(",") : String(v));
  }
  url.searchParams.set("page_access_token", TOKEN);
  const opt = { method, headers: { Accept: "application/json" }, signal: AbortSignal.timeout(30000), redirect: "error" };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opt);
  } catch (e) {
    throw new Error(redact(`không gọi được Pancake: ${e && e.message ? e.message : e}`));
  }
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text.slice(0, 2000) };
  }
  if (!res.ok || (data && data.success === false)) {
    const why = (data && (data.message || data.error || data.error_message)) || `HTTP ${res.status}`;
    throw new Error(redact(`Pancake báo lỗi: ${typeof why === "string" ? why : JSON.stringify(why)}`));
  }
  return data;
}

const id = (v, name) => {
  const s = String(v ?? "").trim();
  if (!s) throw new Error(`thiếu ${name}`);
  return encodeURIComponent(s);
};

// ---- tools -------------------------------------------------------------------------------------------------------
const S = (props, required) => ({ type: "object", properties: props, required: required || [], additionalProperties: false });
const DATE = { type: "string", description: "Ngày YYYY-MM-DD (giờ Việt Nam) hoặc unix giây" };
const RO = { readOnlyHint: true, destructiveHint: false, openWorldHint: true };
const RW = { readOnlyHint: false, destructiveHint: false, openWorldHint: true };

const TOOLS = [
  {
    name: "pancake_conversations_list",
    description: "Danh sách hội thoại của page (inbox và bình luận), mới cập nhật trước. Lọc theo loại, thẻ, khoảng thời gian; " +
      "lấy trang sau bằng last_conversation_id = id hội thoại cuối của trang trước.",
    inputSchema: S({
      type: { type: "string", enum: ["INBOX", "COMMENT"], description: "INBOX = tin nhắn, COMMENT = bình luận" },
      tags: { type: "string", description: "id thẻ, nhiều thẻ cách nhau dấu phẩy" },
      unread_first: { type: "boolean" },
      since: DATE, until: DATE,
      last_conversation_id: { type: "string" },
    }),
    annotations: RO,
    run: (a) => call("GET", "v2", "/conversations", {
      type: a.type ? [a.type] : undefined, tags: a.tags, unread_first: a.unread_first,
      since: toUnix(a.since, false), until: toUnix(a.until, true), last_conversation_id: a.last_conversation_id,
      order_by: "updated_at",
    }),
  },
  {
    name: "pancake_messages_get",
    description: "Tin nhắn của một hội thoại, 30 tin mỗi lần. Xem tin cũ hơn: current_count = số tin đã lấy.",
    inputSchema: S({ conversation_id: { type: "string" }, current_count: { type: "number" } }, ["conversation_id"]),
    annotations: RO,
    run: (a) => call("GET", "v1", `/conversations/${id(a.conversation_id, "conversation_id")}/messages`,
      { current_count: a.current_count }),
  },
  {
    name: "pancake_customers_list",
    description: "Khách hàng của page tạo hoặc cập nhật trong khoảng thời gian (mặc định 30 ngày), có phân trang.",
    inputSchema: S({
      since: DATE, until: DATE,
      page_number: { type: "integer", minimum: 1 }, page_size: { type: "integer", minimum: 1, maximum: 100 },
      order_by: { type: "string", enum: ["inserted_at", "updated_at"] },
    }),
    annotations: RO,
    run: (a) => {
      const r = range(a, 30);
      return call("GET", "v1", "/page_customers", {
        since: r.since, until: r.until, page_number: a.page_number || 1, page_size: a.page_size || 50,
        order_by: a.order_by || "updated_at",
      });
    },
  },
  {
    name: "pancake_tags_list",
    description: "Các thẻ (tag) của page, kèm id để lọc hội thoại hay gắn thẻ.",
    inputSchema: S({}),
    annotations: RO,
    run: () => call("GET", "v1", "/tags", {}),
  },
  {
    name: "pancake_posts_list",
    description: "Bài đăng của page trong khoảng thời gian (mặc định 30 ngày), tối đa 30 bài mỗi trang.",
    inputSchema: S({
      since: DATE, until: DATE, page_number: { type: "integer", minimum: 1 },
      page_size: { type: "integer", minimum: 1, maximum: 30 },
      type: { type: "string", enum: ["video", "photo", "text", "livestream"] },
    }),
    annotations: RO,
    run: (a) => {
      const r = range(a, 30);
      return call("GET", "v1", "/posts", {
        since: r.since, until: r.until, page_number: a.page_number || 1, page_size: a.page_size || 30, type: a.type,
      });
    },
  },
  {
    name: "pancake_staff_list",
    description: "Nhân viên đang trực page, kèm id để phân công hội thoại.",
    inputSchema: S({}),
    annotations: RO,
    run: () => call("GET", "v1", "/users", {}),
  },
  {
    name: "pancake_stats_page",
    description: "Thống kê chung của page theo thời gian (tin nhắn, bình luận, khách mới...). Mặc định 7 ngày.",
    inputSchema: S({ since: DATE, until: DATE }),
    annotations: RO,
    run: (a) => call("GET", "v1", "/statistics/pages", range(a, 7)),
  },
  {
    name: "pancake_stats_tags",
    description: "Số lần dùng từng thẻ trong khoảng thời gian. Mặc định 7 ngày.",
    inputSchema: S({ since: DATE, until: DATE }),
    annotations: RO,
    run: (a) => call("GET", "v1", "/statistics/tags", range(a, 7)),
  },
  {
    name: "pancake_stats_staff",
    description: "Hiệu suất từng nhân viên (số hội thoại, tin trả lời, tốc độ phản hồi). Mặc định 7 ngày.",
    inputSchema: S({ since: DATE, until: DATE }),
    annotations: RO,
    run: (a) => {
      const r = range(a, 7);
      return call("GET", "v1", "/statistics/users", { date_range: `${vnText(r.since)} - ${vnText(r.until)}` });
    },
  },
  {
    name: "pancake_stats_engagement",
    description: "Lượng tương tác của khách theo ngày, hoặc theo giờ (by_hour). Mặc định 7 ngày.",
    inputSchema: S({ since: DATE, until: DATE, by_hour: { type: "boolean" } }),
    annotations: RO,
    run: (a) => {
      const r = range(a, 7);
      return call("GET", "v1", "/statistics/customer_engagements",
        { date_range: `${vnText(r.since)} - ${vnText(r.until)}`, by_hour: a.by_hour });
    },
  },
  {
    name: "pancake_conversation_tag",
    description: "Gắn hoặc gỡ một thẻ trên hội thoại.",
    inputSchema: S({
      conversation_id: { type: "string" }, tag_id: { type: "string" },
      action: { type: "string", enum: ["add", "remove"] },
    }, ["conversation_id", "tag_id", "action"]),
    annotations: RW,
    run: (a) => call("POST", "v1", `/conversations/${id(a.conversation_id, "conversation_id")}/tags`, {},
      { action: a.action, tag_id: String(a.tag_id) }),
  },
  {
    name: "pancake_conversation_mark_read",
    description: "Đánh dấu hội thoại đã đọc (read = true) hoặc chưa đọc (read = false).",
    inputSchema: S({ conversation_id: { type: "string" }, read: { type: "boolean" } }, ["conversation_id", "read"]),
    annotations: RW,
    run: (a) => call("POST", "v1",
      `/conversations/${id(a.conversation_id, "conversation_id")}/${a.read === false ? "unread" : "read"}`, {}, {}),
  },
  {
    name: "pancake_conversation_assign",
    description: "Phân công hội thoại cho nhân viên (id lấy từ pancake_staff_list). Danh sách rỗng là bỏ phân công.",
    inputSchema: S({ conversation_id: { type: "string" }, assignee_ids: { type: "array", items: { type: "string" } } },
      ["conversation_id", "assignee_ids"]),
    annotations: RW,
    run: (a) => call("POST", "v1", `/conversations/${id(a.conversation_id, "conversation_id")}/assign`, {},
      { assignee_ids: (a.assignee_ids || []).map(String) }),
  },
  {
    name: "pancake_customer_add_note",
    description: "Thêm một ghi chú chữ vào hồ sơ khách (page_customer_id lấy từ pancake_customers_list).",
    inputSchema: S({ page_customer_id: { type: "string" }, message: { type: "string", minLength: 1 } },
      ["page_customer_id", "message"]),
    annotations: RW,
    run: (a) => call("POST", "v1", `/page_customers/${id(a.page_customer_id, "page_customer_id")}/notes`, {},
      { message: String(a.message) }),
  },
  {
    name: "pancake_send_message",
    description: "GỬI tin cho khách thật, dưới tên page: trả lời inbox, hoặc trả lời một bình luận (reply_to_comment_id). " +
      "Không thu hồi được.",
    inputSchema: S({
      conversation_id: { type: "string" }, message: { type: "string", minLength: 1 },
      reply_to_comment_id: { type: "string", description: "id bình luận cần trả lời; bỏ trống là trả lời inbox" },
    }, ["conversation_id", "message"]),
    annotations: { readOnlyHint: false, destructiveHint: true, openWorldHint: true },
    run: (a) => call("POST", "v1", `/conversations/${id(a.conversation_id, "conversation_id")}/messages`, {},
      a.reply_to_comment_id
        ? { action: "reply_comment", message_id: String(a.reply_to_comment_id), message: String(a.message) }
        : { action: "reply_inbox", message: String(a.message) }),
  },
];

// ---- MCP over stdio ------------------------------------------------------------------------------------------------
function send(msg) {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

function out(data) {
  let t = JSON.stringify(data);
  if (t.length > MAX_OUT) t = t.slice(0, MAX_OUT) + `\n... (cắt bớt, còn ${t.length - MAX_OUT} ký tự; lọc hẹp lại hoặc lấy trang sau)`;
  return t;
}

async function handle(m) {
  const { id: rid, method, params } = m;
  if (method === "initialize") {
    return {
      protocolVersion: (params && params.protocolVersion) || "2025-06-18",
      capabilities: { tools: {} },
      serverInfo: { name: "pancake-chat-mcp", version: VERSION },
    };
  }
  if (method === "ping") return {};
  if (method === "tools/list") {
    return { tools: TOOLS.map(({ name, description, inputSchema, annotations }) => ({ name, description, inputSchema, annotations })) };
  }
  if (method === "tools/call") {
    const t = TOOLS.find((x) => x.name === (params && params.name));
    if (!t) throw Object.assign(new Error(`không có tool ${params && params.name}`), { code: -32602 });
    try {
      const data = await t.run((params && params.arguments) || {});
      return { content: [{ type: "text", text: out(data) }] };
    } catch (e) {
      return { content: [{ type: "text", text: "Lỗi: " + redact(e && e.message ? e.message : e) }], isError: true };
    }
  }
  if (rid === undefined) return undefined; // notifications need no answer
  throw Object.assign(new Error(`không hỗ trợ ${method}`), { code: -32601 });
}

let buf = "";
let pending = 0;
let closed = false;
const maybeExit = () => { if (closed && pending === 0) process.exit(0); };
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let m;
    try {
      m = JSON.parse(line);
    } catch {
      send({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "parse error" } });
      continue;
    }
    pending += 1;
    handle(m).then(
      (result) => { if (m.id !== undefined && result !== undefined) send({ jsonrpc: "2.0", id: m.id, result }); },
      (e) => { if (m.id !== undefined) send({ jsonrpc: "2.0", id: m.id, error: { code: e.code || -32603, message: redact(e.message) } }); },
    ).finally(() => { pending -= 1; maybeExit(); });
  }
});
// The client closed stdin: answer the calls still in flight, then exit.
process.stdin.on("end", () => { closed = true; maybeExit(); });
process.stderr.write(`pancake-chat-mcp ${VERSION} (page ${PAGE_ID || "?"})\n`);
