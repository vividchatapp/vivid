# Context & Trace Directory 📝

This folder is used by **Vivid Chat** to store raw LLM transaction logs for debugging and inspection.

## 🛠️ How to use
By default, this folder may be empty. To start logging data here, use the following command in Telegram:
- `.trace on`

To disable logging:
- `.trace off`

## 📂 File Descriptions
When tracing is active, the bot generates JSON files prefixed with your `VIVID_PREFIX` (e.g., `bot_` or `pi_`):

1. **`[prefix]_last_payload.json`**: The exact array of messages (System Prompt + History) sent to the AI provider during a standard chat or story session.
2. **`[prefix]_last_response.json`**: The raw metadata and text returned by the AI provider.
3. **`[prefix]_ask_payload.json`**: The specific "neutral" context used when using the `.ask` command.
4. **`[prefix]_ask_response.json`**: The response specifically for the last `.ask` query.

## 🧠 Debugging Tips
- Use these files to verify if your **Characters** and **Scenes** are being injected correctly into the system prompt.
- Check the `last_payload` to see exactly how many messages are being sent based on your current `.context [n]` limit.