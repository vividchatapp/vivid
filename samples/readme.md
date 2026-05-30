# Samples Directory

This directory contains example files that demonstrate the structure and content used by the `vivid.py` Telegram bot.

## `roles/`

The `roles/` subdirectory within `samples` holds example role `.txt` files. These files define the persona, speech style, and core rules for the AI when it's operating in a specific role.

### How they relate to `vivid.py`:

- When you use the `.role` command in `vivid.py`, the bot lists and loads these `.txt` files from the `roles/` directory (or the `ROLES_DIR` specified in `env.json`).
- Each `.txt` file represents a distinct persona that the AI can adopt.
- The content of these files is used to construct the system prompt for the LLM, guiding its responses according to the defined character.