# Verified sender (tutoring centre)

*[Tiếng Việt](README.md) · **English***

A tutoring centre sends homework, documents and links only to students and parents it has **verified**; anyone else gets general information. A bot cannot tell the two apart from the message, since anyone can type "I'm An's mum". This pack checks the **sending account** against the centre's roster and gives the bot one verdict line only.

## Requirements

- Javis OS with the `chatbot_turn_context` hook (PR javis-os#TBD). On older versions the pack installs but does nothing.
- A dedicated bot on **personal Zalo**.

## Turning it on (opt-in, does nothing by default)

1. Install the pack from the **Store** and read the code before you agree.
2. In the **bot's brain**, create `Javis/xac-nhan-dang-hoc.md`:

   ```
   | uid | vai trò | lớp |
   |---|---|---|
   | 1000000001 | phụ huynh | 10Z9 |
   | 1000000002 | học sinh | 11Z9 |
   ```

   `uid` is the sender's Zalo account id. Only the first three columns are read; later columns (names, notes) are dropped while parsing.
3. In the bot's Agent file, write the rule that uses the verdict, for example: "In a VERIFIED private chat, send that class's homework; when NOT VERIFIED, give general information only."

A bot without a roster gets no line and behaves exactly as before.

## What the bot sees

One line inside that turn's `<operator_context>` block, such as `NGƯỜI ĐANG NHẮN (...): ĐÃ XÁC NHẬN, phụ huynh, lớp 10Z9.` or `CHƯA XÁC NHẬN` (the bot prompt is Vietnamese). **Never** the account id or any other roster row.

- Groups: no line.
- Channels other than personal Zalo: no line (configurable, see below).
- The **Try bot** turn uses a synthetic sender, so it always shows `CHƯA XÁC NHẬN` (not verified).

## Optional settings

File `<JAVIS_STATE_DIR>/plugin-data/xac-nhan-nguoi-nhan/config.json`, shared by every bot on the machine:

```json
{"roster": "Javis/xac-nhan-dang-hoc.md", "channels": ["zalo_personal"]}
```

`roster` is relative to the bot's brain and cannot point outside it. Keep the roster under `Javis/`: the bot's document lookup skips that folder. A Full-power bot can still open any file in its brain.

## Privacy

The pack makes no network calls and writes nothing. The roster is student data: keep it in the bot's own brain and never copy it into documents the bot can read.
