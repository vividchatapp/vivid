"""
Telegram Bot for Interactive LLM Roleplay and Conversation Management.

Supports multiple Ollama instances.
Features include dynamic role switching, conversation saving/loading,
context summarization (recap), and an 'Edit & Resend' workflow.
"""

import time
import os
import json
import sys
import asyncio
from pathlib import Path
from telegram import Update
from telegram.ext import Application, PrefixHandler, MessageHandler, filters, ContextTypes
from ollama import AsyncClient

# --- INITIALIZATION ---
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "env.json"

if not CONFIG_PATH.exists():
    print(f"❌ Error: {CONFIG_PATH} not found.")
    exit(1)

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

# Optional paths override
PATHS_CONFIG_PATH = BASE_DIR / "env.paths.json"
if PATHS_CONFIG_PATH.exists():
    try:
        with open(PATHS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            CONFIG.update(json.load(f))
    except Exception as e:
        print(f"⚠️ Warning: Failed to load {PATHS_CONFIG_PATH}: {e}")

LAST_LATENCY = 0
TOTAL_MESSAGES_SENT = 0
TELEGRAM_USER_ID = int(CONFIG.get("TELEGRAM_USER_ID", 0))

CONTEXT_LIMIT = 50  # Number of recent messages to send

# Suggested default params for the bot
GENERATION_OPTIONS = {
    "repeat_penalty": 1.2,
    "top_p": 0.9,
    "temperature": 0.8,
    "repeat_last_n": 64,
    "num_ctx": CONFIG.get("OLLAMA_NUM_CTX", 4096)
}

if len(sys.argv) > 1:
    VIVID_PREFIX = sys.argv[1]
else:
    VIVID_PREFIX = CONFIG.get("VIVID_PREFIX", "bot")

TELEGRAM_TOKEN = CONFIG.get("TELEGRAM_TOKENS", {}).get(VIVID_PREFIX)

suffix = "_WINDOWS" if os.name == 'nt' else "_LINUX"

env_chats_path = CONFIG.get(f"CHATS_PATH{suffix}", str(BASE_DIR / "chats"))
CHATS_DIR = Path(env_chats_path).expanduser().resolve()
CHATS_DIR.mkdir(parents=True, exist_ok=True)

env_roles_path = CONFIG.get(f"ROLES_PATH{suffix}", "./roles")
ROLES_DIR = Path(env_roles_path).expanduser().resolve()
ROLES_DIR.mkdir(parents=True, exist_ok=True)

ROLE_IMAGES_DIR = ROLES_DIR / "images"
ROLE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

env_chars_path = CONFIG.get(f"CHARACTERS_PATH{suffix}", "./characters")
CHARACTERS_DIR = Path(env_chars_path).expanduser().resolve()
CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)

env_scenes_path = CONFIG.get(f"SCENES_PATH{suffix}", "./scenes")
SCENES_DIR = Path(env_scenes_path).expanduser().resolve()
SCENES_DIR.mkdir(parents=True, exist_ok=True)

env_settings_path = CONFIG.get(f"SETTINGS_PATH{suffix}", "./settings")
SETTINGS_DIR = Path(env_settings_path).expanduser().resolve()
SETTINGS_DIR.mkdir(parents=True, exist_ok=True)

env_context_path = CONFIG.get(f"CONTEXT_PATH{suffix}", "./context")
CONTEXT_DIR = Path(env_context_path).expanduser().resolve()
CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

env_stories_path = CONFIG.get(f"STORIES_PATH{suffix}", "./stories")
STORIES_DIR = Path(env_stories_path).expanduser().resolve()
STORIES_DIR.mkdir(parents=True, exist_ok=True)

# --- CLIENTS ---
OLLAMA_LOCAL = CONFIG.get("OLLAMA_LOCAL", [])
OLLAMA_ONLINE = CONFIG.get("OLLAMA_ONLINE", [])

ollama_clients = {}
provider_display_info = {}
PROVIDER_CATEGORIES = {"local": [], "online": []}

for i, cfg in enumerate(OLLAMA_LOCAL):
    name = f"local_{i}"
    is_dict = isinstance(cfg, dict)
    url = cfg.get("url") if is_dict else cfg
    display = cfg.get("description", url) if is_dict else url
    ollama_clients[name] = AsyncClient(host=url, timeout=None)
    PROVIDER_CATEGORIES["local"].append(name)
    provider_display_info[name] = display

for i, cfg in enumerate(OLLAMA_ONLINE):
    name = f"online_{i}"
    is_dict = isinstance(cfg, dict)
    url = cfg.get("url", "https://ollama.com") if is_dict else cfg
    display = cfg.get("description", url) if is_dict else url
    api_key = cfg.get("api_key") if is_dict else None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    ollama_clients[name] = AsyncClient(host=url, headers=headers, timeout=None)
    PROVIDER_CATEGORIES["online"].append(name)
    provider_display_info[name] = display

AI_PROVIDERS = PROVIDER_CATEGORIES["local"] + PROVIDER_CATEGORIES["online"]

# --- GLOBAL STATE ---
# Stores {provider_name: {model_name: "good"|"bad"|"unknown"}} for Ollama online models
FILTERED_OLLAMA_MODELS_STATUS = {}

# --- HELPERS ---
def load_ollama_models_status():
    """Loads the ollama_models.json file from the settings directory."""
    global FILTERED_OLLAMA_MODELS_STATUS
    file_path = SETTINGS_DIR / "ollama_models.json"
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                FILTERED_OLLAMA_MODELS_STATUS = json.load(f)
        except Exception as e:
            print(f"⚠️ Warning: Failed to load ollama_models.json: {e}")
            FILTERED_OLLAMA_MODELS_STATUS = {}
    else:
        FILTERED_OLLAMA_MODELS_STATUS = {}

def save_ollama_models_status():
    """Saves the current FILTERED_OLLAMA_MODELS_STATUS to ollama_models.json."""
    file_path = SETTINGS_DIR / "ollama_models.json"
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(FILTERED_OLLAMA_MODELS_STATUS, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Error saving ollama_models.json: {e}")

CURRENT_PROVIDER = AI_PROVIDERS[0] if AI_PROVIDERS else "none"
CURRENT_MODEL = "llama3.2:3b"
CACHED_MODELS = []
CURRENT_ROLE = "Assistant"
ACTIVE_CHARACTERS = []
ACTIVE_SCENES = []
CURRENT_MODE = "chat"
THINK_MODE = False
VERBOSE_MODE = False
TRACE_MODE = False
LAZY_MODE = False
def save_to_json(name):
    """Saves the current chat_history to a JSON file in the CHATS_DIR."""
    filename = f"{VIVID_PREFIX}_{name}.json" if name == "last_session" else f"{name}.json"
    file_path = CHATS_DIR / filename
    data = {
        "metadata": {
            "model": CURRENT_MODEL,
            "role": CURRENT_ROLE,
            "provider": CURRENT_PROVIDER,
            "context_limit": CONTEXT_LIMIT,
            "mode": CURRENT_MODE,
            "active_characters": ACTIVE_CHARACTERS,
            "active_scenes": ACTIVE_SCENES,
            "num_ctx": GENERATION_OPTIONS.get("num_ctx", 4096),
            "lazy_mode": LAZY_MODE
        },
        "history": chat_history
    }
    temp_path = file_path.with_suffix(".tmp")
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    temp_path.replace(file_path)

async def generate_response(messages, options=None):
    """Centralized helper to call the appropriate LLM provider."""
    if options is None:
        options = GENERATION_OPTIONS

    client = ollama_clients[CURRENT_PROVIDER]
    call_opts = {**options}
    if THINK_MODE:
        call_opts["think"] = True
    res = await client.chat(model=CURRENT_MODEL, messages=messages, options=call_opts)
    return res.message.content

def load_from_json(name):
    """
    Loads a conversation history from CHATS_DIR.
    Supports both new shared naming and legacy prefixed naming.
    Returns True if successful, False otherwise.
    """
    global chat_history, CURRENT_MODEL, CURRENT_ROLE, CURRENT_PROVIDER, CONTEXT_LIMIT, ACTIVE_CHARACTERS, ACTIVE_SCENES, CURRENT_MODE, LAZY_MODE
    clean_name = name.replace(".json", "")
    # Try to load without prefix first (new shared style), fallback to prefix for legacy
    file_path = (CHATS_DIR / f"{clean_name}.json").resolve()
    if not file_path.exists():
        file_path = (CHATS_DIR / f"{VIVID_PREFIX}_{clean_name}.json").resolve()

    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and "history" in data:
                    chat_history = data["history"]
                    meta = data.get("metadata", {})
                    CURRENT_MODEL = meta.get("model", CURRENT_MODEL)
                    CURRENT_ROLE = meta.get("role", CURRENT_ROLE)
                    CURRENT_PROVIDER = meta.get("provider", CURRENT_PROVIDER)
                    CONTEXT_LIMIT = meta.get("context_limit", CONTEXT_LIMIT)
                    CURRENT_MODE = meta.get("mode", "chat")
                    ACTIVE_CHARACTERS = meta.get("active_characters", [])
                    ACTIVE_SCENES = meta.get("active_scenes", [])
                    LAZY_MODE = meta.get("lazy_mode", False)
                    if "num_ctx" in meta:
                        GENERATION_OPTIONS["num_ctx"] = meta["num_ctx"]
                else:
                    chat_history = data
                return True
        except Exception as e:
            print(f"❌ Error reading JSON: {e}")
            return False
    return False

async def reply_and_log(update: Update, text: str, is_command=True):
    """Helper to send a message, capture IDs, and log to history for session persistence."""
    user_msg_id = update.message.message_id

    # Ensure the user's command is logged if it hasn't been already
    already_logged = any(user_msg_id in m.get("msg_ids", []) for m in chat_history[-2:])
    if not already_logged:
        chat_history.append({
            "role": "user", "content": update.message.text,
            "msg_ids": [user_msg_id], "is_command": True, "incoming": True
        })
    
    sent_ids = []
    for i in range(0, len(text), 4000):
        sent_msg = await update.message.reply_text(text[i:i+4000], parse_mode='Markdown')
        sent_ids.append(sent_msg.message_id)
    
    chat_history.append({
        "role": "assistant", "content": text, 
        "msg_ids": sent_ids, "is_command": is_command
    })
    save_to_json("last_session")

async def reply_transient(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, delay: int = 5):
    """Sends a message that deletes itself after a delay and is NOT logged to history."""
    try:
        # Physically remove the trigger command from the Telegram screen for a clean UI
        try: await update.message.delete()
        except: pass
        
        sent_msg = await update.message.reply_text(text, parse_mode='Markdown')
        async def _delete():
            await asyncio.sleep(delay)
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=sent_msg.message_id)
            except:
                pass
        asyncio.create_task(_delete())
    except Exception as e:
        print(f"⚠️ Failed to send transient reply: {e}")

def get_role_content(role_name):
    """
    Reads the persona/role definition from a .txt file.
    Returns a fallback string if the role file is missing.
    """
    role_file = ROLES_DIR / f"{role_name}.txt"
    if role_file.exists():
        content = role_file.read_text(encoding='utf-8').strip()
        if not content: return "You are a helpful AI assistant."
        return content
    return "You are a helpful AI assistant."

def get_character_content(char_name):
    """
    Reads the character description from a .txt file.
    """
    char_file = CHARACTERS_DIR / f"{char_name}.txt"
    if char_file.exists():
        return char_file.read_text(encoding='utf-8').strip()
    return ""

def get_scene_content(scene_name):
    """
    Reads the scene description from a .txt file.
    """
    scene_file = SCENES_DIR / f"{scene_name}.txt"
    if scene_file.exists():
        return scene_file.read_text(encoding='utf-8').strip()
    return ""

async def h_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias for help_command."""
    await help_command(update, context) 

async def c_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await chat_actions(update, context)


# --- COMMANDS ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the available bot commands to the user."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    help_text = (
        "🤖 **i5 Assistant Help**\n"
        "================================\n"
        "**🎭 Roles**\n"
        "• `.role` - List available roles\n"
        "• `.role [n]` - Switch to role\n"
        "• `.role edit [n]` - Get role text to edit\n"
        "• `.role save [name] [text]` - Save role profile\n"
        "• `.reload` - Reload role from disk\n"
        "• `.rs [n]` - Summarize current role\n\n"
        "**👤 Characters**\n"
        "• `.char` - List characters\n"
        "• `.char [n/name] on/off` - Toggle characters\n"
        "• `.char all off` - Deactivate all characters\n"
        "• `.char edit [n]` - Get character bio to edit\n"
        "• `.char save [name] [text]` - Save character bio\n\n"
        "**🎬 Scenes**\n"
        "• `.scene` - List scenes\n"
        "• `.scene [n/name] on/off` - Toggle scene settings\n"
        "• `.scene all off` - Deactivate all scenes\n"
        "• `.scene edit [n]` - Get scene description to edit\n"
        "• `.scene save [name] [text]` - Save scene description\n\n"
        "**🧠 AI Providers & Models**\n"
        "• `.provider` - List providers\n"
        "• `.provider [n]` - Switch provider\n"
        "• `.model` - List available models\n"
        "• `.model [n]` - Switch model\n"
        "• `.model pull [name]` - Pull a model\n"
        "• `.model test` - Test online models for subscription requirements\n"
        "• `.mf [n/next/prev]` - List or cycle accessible models\n"
        "• `.model rm [n]` - Remove a model\n"
        "• `.model loaded` - Sync with RAM\n"
        "• `.think` - Toggle Think Mode\n\n"
        "• `.llmctx [nk]` - Set/show model context window (e.g. 8k)\n\n"
        "**💾 Conversation & Chats**\n"
        "• `.chat save [name]` - Save conversation\n"
        "• `.chat load [name]` - Load conversation\n"
        "• `.clear` - Wipe bot memory (context)\n"
        "• `.clean` - Wipe memory and delete messages from chat UI\n"
        "• `.mode [chat/story]` - Toggle history behavior\n"
        f"• `.recap [n]` - Summarize last {CONTEXT_LIMIT} msgs using prompt n\n"
        "• `.story save [name]` - Save assistant story to txt\n"
        "• `.ask [text]` - Ask a question outside of roleplay context\n"
        "• `.del` - Delete last msg + response\n"
        "• `.resend` - Resend last user message\n"
        "• `.context [n]` - Set/show limit\n"
        "• `.last [n]` - Show last n messages (default 3)\n\n"
        "**⚙️ Status & Shortcuts**\n"
        "• `.status` - Show current settings\n"
        "• `.verbose` - Toggle Verbose Status\n"
        "• `.trace [on/off]` - Write payloads to context folder\n"
        "• `.lazy` - Toggle Lazy Mode (commands without dots)\n"
        "**Synonyms:** `.r`=.role, `.rs`=.rolesummary, `.p`=.provider, `.m`=.model, `.s`=.status, `.h`=.help, `.c`=.chat, `.cl`=.clean, `.mf`=.modelsfiltered, `.sc`=.scene, `.l`=.last, `.mo`=.mode, `.lz`=.lazy, `.mc`=.llmctx"
    )
    await reply_and_log(update, help_text)

async def p_command(update: Update, context: ContextTypes.DEFAULT_TYPE):  
    await provider_command(update, context)  

async def provider_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main entry point for .provider command. 
    Lists providers if no args, otherwise switches provider.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args:
        await list_providers(update, context)
    else:
        await switch_provider(update, context)

async def m_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await model_command(update, context)


async def list_providers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all configured Ollama providers with category headers."""
    text = "🌐 **AI Providers**\n\n"
    idx = 1
    
    if PROVIDER_CATEGORIES["local"]:
        text += "🏠 **Ollama Local**\n"
        for p in PROVIDER_CATEGORIES["local"]:
            active = "✅" if p == CURRENT_PROVIDER else ""
            desc = provider_display_info.get(p, "")
            text += f"{idx}. `{desc}` {active}\n"
            idx += 1
        text += "\n"

    if PROVIDER_CATEGORIES["online"]:
        text += "☁️ **Ollama Online**\n"
        for p in PROVIDER_CATEGORIES["online"]:
            active = "✅" if p == CURRENT_PROVIDER else ""
            desc = provider_display_info.get(p, "")
            text += f"{idx}. `{desc}` {active}\n"
            idx += 1
        text += "\n"
    await reply_and_log(update, text)

async def switch_provider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches the active CURRENT_PROVIDER based on list index."""
    global CURRENT_PROVIDER
    try:
        index = int(context.args[0]) - 1
        CURRENT_PROVIDER = AI_PROVIDERS[index]
        display = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
        await reply_transient(update, context, f"🚀 Using: `{display}`")
    except:
        await reply_and_log(update, "❌ Usage: `.provider [n]`")

async def list_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lists models available on the currently selected Ollama provider.
    Headers and model names are wrapped in backticks to prevent Telegram
    from interpreting underscores as Markdown or auto-detecting URLs.
    """
    if CURRENT_PROVIDER not in ollama_clients:
        await reply_and_log(update, "⚠️ No active Ollama provider selected.")
        return

    try:
        client = ollama_clients[CURRENT_PROVIDER]
        response = await client.list()
        
        global CACHED_MODELS
        # Use getattr or check for attributes to prevent crashes on unexpected responses
        models = getattr(response, 'models', [])
        # Sort models by name to ensure consistent indexing
        models.sort(key=lambda x: x.model)
        CACHED_MODELS = [m.model for m in models]

        model_list = []
        for i, m in enumerate(models):
            size_bytes = getattr(m, 'size', 0)
            size_gb = size_bytes / (1024**3)
            
            # Using m.model since that is the attribute that works for you
            model_list.append(f"{i+1}) `{m.model}` ({size_gb:.2f} GB)")
        
        display = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
        text = f"🤖 **`{display}` Models:**\n" + "\n".join(model_list)
        await reply_and_log(update, text)
    except Exception as e:
        display = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
        await reply_and_log(update, f"❌ Failed to reach {display}: {e}")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main entry point for .model command.
    Lists models if no args, otherwise switches model by index or sub-command.
    """
    if not context.args:
        await list_models(update, context)
    else:
        await switch_model(update, context)

async def switch_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles model switching and management sub-commands:
    - 'loaded': Syncs CURRENT_MODEL with whatever is in the provider's RAM.
    - 'pull': Triggers an async model download.
    - 'rm': Deletes a model from the provider.
    """
    global CURRENT_MODEL
    global CACHED_MODELS
    
    if CURRENT_PROVIDER not in ollama_clients:
        await reply_and_log(update, "⚠️ No active Ollama provider selected.")
        return

    client = ollama_clients[CURRENT_PROVIDER]
    
    sub_cmd = context.args[0].lower()

    # --- TEST MODELS (.model test) ---
    if sub_cmd == "test":
        await test_ollama_models(update, context)
        return

    # --- A. SYNC WITH RAM ---
    if sub_cmd == "loaded":
        try:
            resp = await client.ps()
            if hasattr(resp, 'models') and resp.models:
                CURRENT_MODEL = resp.models[0].model
                await reply_transient(update, context, f"🔄 **Syncing:** Now using `{CURRENT_MODEL}`.")
            else:
                display = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
                await reply_and_log(update, f"ℹ️ No models loaded in RAM on `{display}`.")
        except Exception as e:
            display = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
            await reply_and_log(update, f"❌ Failed to check {display}: {e}")
        return

    # --- B. PULL MODEL (.model pull name) ---
    if sub_cmd == "pull" and len(context.args) > 1:
        await pull_model(update, context)
        return

    # --- C. REMOVE MODEL (.model rm 2) ---
    if sub_cmd == "rm" and len(context.args) > 1:
        await delete_model(update, context)
        return

    # --- D. SWITCH BY NUMBER (.model 3) ---
    try:
        index = int(sub_cmd) - 1
        if not CACHED_MODELS:
            resp = await client.list()
            models = getattr(resp, 'models', [])
            models.sort(key=lambda x: x.model)
            CACHED_MODELS = [m.model for m in models]
            
        CURRENT_MODEL = CACHED_MODELS[index]
        await reply_transient(update, context, f"🧠 Switched to: `{CURRENT_MODEL}`")
    except:
        await reply_and_log(update, "❌ Invalid command or index. Use `.model` for options.")

async def pull_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asynchronously pulls a model to the Ollama host with progress updates."""
    if CURRENT_PROVIDER not in ollama_clients: return

    client = ollama_clients[CURRENT_PROVIDER]

    model_name = context.args[1]
    status_msg = await update.message.reply_text(f"📥 Starting pull for `{model_name}`...")
    
    try:
        # Use a stream so the bot doesn't time out
        async for part in await client.pull(model=model_name, stream=True):
            if 'percentage' in part:
                # Update every 10% to avoid hitting Telegram's rate limits
                progress = part['percentage']
                if progress % 10 == 0:
                    await status_msg.edit_text(f"📥 Pulling `{model_name}`: {progress}%")
        
        await status_msg.edit_text(f"✅ Successfully pulled `{model_name}`!")
        global CACHED_MODELS
        CACHED_MODELS = []
    except Exception as e:
        await status_msg.edit_text(f"❌ Pull failed: {e}")

async def delete_model(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """Deletes a specific model from the current Ollama provider."""
    if CURRENT_PROVIDER not in ollama_clients: return

    client = ollama_clients[CURRENT_PROVIDER]
    try:
        # We look at args[1] because args[0] was "rm"
        index = int(context.args[1]) - 1 

        global CACHED_MODELS
        if not CACHED_MODELS:
            resp = await client.list()
            models = getattr(resp, 'models', [])
            models.sort(key=lambda x: x.model)
            CACHED_MODELS = [m.model for m in models]
            
        model_to_delete = CACHED_MODELS[index]
        
        await client.delete(model=model_to_delete)
        CACHED_MODELS = []
        display = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
        await reply_and_log(update, f"🗑️ Deleted `{model_to_delete}` from {display}")
    except Exception as e:
        await reply_and_log(update, f"❌ Delete failed: {e}")


async def reload_role_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Reloads the current role's system prompt from disk without clearing
    user/assistant history (unless history is empty).
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    global chat_history
    global CURRENT_ROLE

    # 1. Fetch the fresh content from the .txt file on disk
    new_content = get_role_content(CURRENT_ROLE)

    if chat_history:
        # 2. Replace the existing system message at index 0
        chat_history[0] = {"role": "system", "content": new_content}
        await reply_transient(update, context, f"🔄 Role `{CURRENT_ROLE}` reloaded.")
    else:
        # 3. If no history exists, initialize it with the reloaded role
        chat_history = [{"role": "system", "content": new_content}]
        await reply_transient(update, context, f"✨ Role `{CURRENT_ROLE}` reloaded.")

async def r_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   """Alias for role_command."""
   await role_command(update, context) 

async def role_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lists available roles or switches to a new one.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    global CURRENT_ROLE
    global chat_history
    role_files = sorted(list(ROLES_DIR.glob("*.txt")))
    
    if not context.args:
        text = "🎭 **Available Roles:**\n"
        for i, f in enumerate(role_files):
            active = "✅" if f.stem == CURRENT_ROLE else ""
            text += f"{i+1}. `{f.stem}` {active}\n"
        await reply_and_log(update, text)
        return

    # Handle .role save [name] [text]
    if len(context.args) >= 3 and context.args[0].lower() == "save":
        target = context.args[1]
        content = " ".join(context.args[2:])
        (ROLES_DIR / f"{target}.txt").write_text(content, encoding='utf-8')
        await reply_and_log(update, f"💾 Role `{target}` saved.")
        return

    # Handle .role edit [n]
    if len(context.args) >= 2 and context.args[0].lower() == "edit":
        try:
            idx = int(context.args[1]) - 1
            selected_file = role_files[idx]
            name = selected_file.stem
            content = selected_file.read_text(encoding='utf-8').strip()
            text = f".role save {name} {content}"
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i+4000])
            return
        except Exception:
            await reply_and_log(update, "❌ Use `.role edit [number]`")
            return

    try:
        idx = int(context.args[0]) - 1
        selected_file = role_files[idx]
        CURRENT_ROLE = selected_file.stem
        content = selected_file.read_text(encoding='utf-8').strip()

        if chat_history:
            chat_history[0] = {"role": "system", "content": content}
        else:
            chat_history = [{"role": "system", "content": content}]
        await reply_transient(update, context, f"✅ Switched to `{CURRENT_ROLE}`.")

        # Display role image if it exists
        for ext in [".jpg", ".jpeg", ".png"]:
            img_path = ROLE_IMAGES_DIR / f"{CURRENT_ROLE}{ext}"
            if img_path.exists():
                try:
                    with open(img_path, 'rb') as photo:
                        await update.message.reply_photo(photo=photo)
                    break
                except Exception as e:
                    print(f"⚠️ Failed to send role image: {e}")
    except:
        await reply_and_log(update, "❌ Use `.role [number]`")

async def chat_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles chat-related actions: .clear, .chat save, and .chat load.
    Providing no arguments lists saved chats.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    global chat_history
    first_word = update.message.text.lower().split()[0] if update.message.text else ""
    is_clear = first_word in [".clear", "clear"]
    is_clean = first_word in [".clean", ".cl", "clean", "cl"]

    if is_clear or is_clean:
        if is_clean:
            chat_id = update.effective_chat.id
            # Physically delete messages from Telegram UI (Bottom-to-Top)
            for msg in reversed(chat_history[1:]):
                for mid in reversed(msg.get("msg_ids", [])):
                    try: await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                    except: pass

        # Reset history to only the system prompt
        if chat_history and chat_history[0]["role"] == "system":
            chat_history = [chat_history[0]]
        else:
            chat_history = [{"role": "system", "content": get_role_content(CURRENT_ROLE), "msg_ids": [], "is_command": False}]

        save_to_json("last_session")

        notice = "🧹 Chat and UI cleaned." if is_clean else "✨ Memory cleared."
        await reply_transient(update, context, notice)
        return

    if not context.args:
        files = sorted(CHATS_DIR.glob("*.json"))
        if not files:
            await update.message.reply_text("📂 No saved chats found.")
            return
        
        chat_names = [f.stem for f in files if "last_session" not in f.name]
        text = "📂 **Saved Chats on Disk:**\n" + "\n".join([f"• `{name}`" for name in chat_names])
        await update.message.reply_text(text, parse_mode='Markdown')
        await reply_and_log(update, text)
        return

    cmd = context.args[0].lower()
    if cmd == "save" and len(context.args) > 1:
        save_to_json(context.args[1])
        await reply_and_log(update, f"💾 Saved as `{context.args[1]}`")
    elif cmd == "load" and len(context.args) > 1:
        if load_from_json(context.args[1]):
            await reply_and_log(update, f"📂 Loaded `{context.args[1]}`")
            
            # Show a preview of the last 5 messages (excluding system prompt at index 0)
            recent = chat_history[1:][-5:]
            if recent:
                await reply_and_log(update, "📜 **Recent History Preview:**")
                for msg in recent:
                    icon = "👤" if msg.get('role') == 'user' else "🤖"
                    text = f"{icon} {msg.get('content', '')}"
                    await reply_and_log(update, text, is_command=True)

async def resend_last_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Finds the most recent user message in history, removes it, and
    re-triggers the handle_message flow. Useful if the bot failed or
    if settings were changed.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    global chat_history
    if len(chat_history) < 2:
        await reply_and_log(update, "❌ No message history to resend.")
        return

    last_user_msg = None
    # Look backwards to find the most recent 'user' entry
    for i in range(len(chat_history) - 1, -1, -1):
        if chat_history[i]["role"] == "user":
            last_user_msg = chat_history[i]["content"]
            # Remove it so handle_message doesn't create a duplicate in history
            msg_to_resend = chat_history.pop(i)
            # Also remove and physically delete the bot's following response if it exists
            if i < len(chat_history) and chat_history[i]["role"] == "assistant":
                bot_msg = chat_history.pop(i)
                for mid in bot_msg.get("msg_ids", []):
                    try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mid)
                    except: pass
            break
            
    if last_user_msg:
        save_to_json("last_session")
        await handle_message(update, context, incoming_text=last_user_msg)
    else:
        await reply_and_log(update, "❌ Could not find a user message to resend.")

async def toggle_lazy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles 'Lazy Mode' allowing commands to work without the leading dot."""
    global LAZY_MODE
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    LAZY_MODE = not LAZY_MODE
    status = "ON 😴 (Commands work without dots)" if LAZY_MODE else "OFF ⚡ (Dots required)"
    await reply_and_log(update, f"Lazy Mode: {status}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, incoming_text=None):
    """
    The primary message handler for LLM interactions.
    Constructs the payload (System Prompt + History Slicing), selects the
    correct provider client, and streams the response back to Telegram.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    global LAST_LATENCY, TOTAL_MESSAGES_SENT, chat_history, ACTIVE_CHARACTERS, ACTIVE_SCENES, LAZY_MODE
    start_time = time.time()
    user_id = str(update.effective_user.id)
    
    # Use the provided text (for resends) or the actual message text
    prompt = incoming_text if incoming_text else update.message.text

    if not prompt:
        return

    # --- LAZY MODE COMMAND CHECK ---
    if LAZY_MODE and not prompt.startswith("."):
        parts = prompt.split()
        if parts:
            cmd_name = parts[0].lower()
            if cmd_name in LAZY_COMMAND_MAP:
                # Simulate PrefixHandler args and execute the mapped function
                context.args = parts[1:]
                await LAZY_COMMAND_MAP[cmd_name](update, context)
                return # Stop here; do not send to LLM

    if not chat_history:
        chat_history = [{"role": "system", "content": get_role_content(CURRENT_ROLE)}]
    
    # Build the dynamic system prompt including active characters
    role_content = get_role_content(CURRENT_ROLE)
    char_blocks = []
    for char_name in ACTIVE_CHARACTERS:
        content = get_character_content(char_name)
        if content:
            char_blocks.append(f"--- CHARACTER: {char_name} ---\n{content}")

    scene_blocks = []
    for scene_name in ACTIVE_SCENES:
        content = get_scene_content(scene_name)
        if content:
            scene_blocks.append(f"--- SCENE/LOCATION: {scene_name} ---\n{content}")
    
    full_system_prompt = role_content
    if char_blocks:
        full_system_prompt += "\n\nACTIVE CHARACTERS IN SCENE:\n" + "\n\n".join(char_blocks)
    
    if scene_blocks:
        full_system_prompt += "\n\nCURRENT SCENE SETTING:\n" + "\n\n".join(scene_blocks)

    # Track ID for the user's incoming prompt
    user_entry = {"role": "user", "content": prompt, "is_command": False}
    if not incoming_text: 
        user_entry["msg_ids"] = [update.message.message_id]
    chat_history.append(user_entry)
    save_to_json("last_session")
    
    history_subset = chat_history[1:][-CONTEXT_LIMIT:]

    if CURRENT_MODE == "story":
        # Filter: Only narrative assistant responses (not command outputs)
        narrative_assistants = [m for m in history_subset[:-1] if m["role"] == "assistant" and not m.get("is_command")]
        payload = [{"role": "system", "content": full_system_prompt}] + narrative_assistants + [history_subset[-1]]
    else:
        # Chat mode: AI sees history, but we still exclude meta-command logs
        clean_history = [m for m in history_subset if not m.get("is_command")]
        payload = [{"role": "system", "content": full_system_prompt}] + clean_history

    # Sanitize payload: LLMs only want 'role' and 'content'
    api_payload = [{"role": m["role"], "content": m["content"]} for m in payload]

    if TRACE_MODE:
        try:
            trace_file = CONTEXT_DIR / f"{VIVID_PREFIX}_last_payload.json"
            with open(trace_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Trace failed: {e}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Use centralized helper
        reply = await generate_response(api_payload)

        if TRACE_MODE:
            try:
                resp_file = CONTEXT_DIR / f"{VIVID_PREFIX}_last_response.json"
                with open(resp_file, 'w', encoding='utf-8') as f:
                    json.dump({"response": reply, "provider": CURRENT_PROVIDER, "model": CURRENT_MODEL}, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Response Trace failed: {e}")

        LAST_LATENCY = round(time.time() - start_time, 2)
        TOTAL_MESSAGES_SENT += 1

        sent_ids = []
        for i in range(0, len(reply), 4000):
            sent_msg = await update.message.reply_text(reply[i:i+4000], parse_mode='Markdown')
            sent_ids.append(sent_msg.message_id)

        chat_history.append({"role": "assistant", "content": reply, "msg_ids": sent_ids, "is_command": False})
        save_to_json("last_session")

        if VERBOSE_MODE:
            minutes = int(LAST_LATENCY // 60)
            seconds = int(LAST_LATENCY % 60)
            latency_str = f"{minutes}m{seconds}s" if minutes > 0 else f"{seconds}s"
            history_json = json.dumps(chat_history)
            size_kb = int(len(history_json.encode('utf-8')) / 1024)
            v_text = f"Status: {latency_str}, {len(payload)}msg, {size_kb}kb"
            await update.message.reply_text(v_text, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        
async def toggle_think(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles 'Think Mode' for Ollama models (adds <think> logic to options)."""
    global THINK_MODE
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    THINK_MODE = not THINK_MODE
    await reply_transient(update, context, f"Think Mode: {'ON 🧠' if THINK_MODE else 'OFF ⚡'}")

async def llmcontext_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets or shows the LLM context window (num_ctx). Requires 'k' suffix."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args:
        await reply_and_log(update, f"🧠 Model Context Window (num_ctx): `{GENERATION_OPTIONS['num_ctx']}`")
        return

    val = context.args[0].lower()
    if not val.endswith("k"):
        await reply_and_log(update, "❌ Error: Context must be specified with 'k' (e.g., 8k, 32k, 128k).")
        return

    try:
        num = int(val[:-1]) * 1024
        GENERATION_OPTIONS["num_ctx"] = num
        save_to_json("last_session")
        await reply_transient(update, context, f"✅ Model context window set to `{num}` ({val}).")
    except ValueError:
        await reply_and_log(update, "❌ Error: Invalid number format. Use e.g., 8k.")

async def toggle_verbose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the display of metadata (latency, size) after each message."""
    global VERBOSE_MODE
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    VERBOSE_MODE = not VERBOSE_MODE
    await reply_transient(update, context, f"Verbose Mode: {'ON ✅' if VERBOSE_MODE else 'OFF ❌'}")

async def toggle_trace(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """Toggles 'Trace Mode' to write LLM payloads to the context folder."""
    global TRACE_MODE
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if context.args:
        arg = context.args[0].lower()
        if arg == "on":
            TRACE_MODE = True
        elif arg == "off":
            TRACE_MODE = False
    else:
        TRACE_MODE = not TRACE_MODE
        
    await reply_transient(update, context, f"Trace Mode: {'ON 📝' if TRACE_MODE else 'OFF ❌'}")


async def s_command(update: Update, context: ContextTypes.DEFAULT_TYPE):   
    await status_command(update, context)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays current configuration and session statistics."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    
    # Calculate size in KB
    history_json = json.dumps(chat_history)
    size_kb = round(len(history_json.encode('utf-8')) / 1024, 1)
    display = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
    status_text = (
        f"📊 **Status**\n---\n"
        f"🌐 Provider: `{display}`\n"
        f"🧠 Model: `{CURRENT_MODEL}`\n"
        f"🎭 Role: `{CURRENT_ROLE}`\n"
        f"📖 Mode: `{CURRENT_MODE}`\n"
        f"📝 Trace Mode: `{'ON' if TRACE_MODE else 'OFF'}`\n"
        f"😴 Lazy Mode: `{'ON' if LAZY_MODE else 'OFF'}`\n"
        f"👥 Active Chars: `{', '.join(ACTIVE_CHARACTERS) if ACTIVE_CHARACTERS else 'None'}`\n"
        f"🎬 Active Scenes: `{', '.join(ACTIVE_SCENES) if ACTIVE_SCENES else 'None'}`\n"
        f"💾 Context: `{len(chat_history)}` msgs `{size_kb}KB` max `{CONTEXT_LIMIT}`\n"
        f"🧠 LLM Ctx: `{GENERATION_OPTIONS['num_ctx']}`"
    )
    await reply_and_log(update, status_text)

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches between chat mode (full history) and story mode (assistant only history)."""
    global CURRENT_MODE, chat_history
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args:
        await reply_and_log(update, f"📖 Current Mode: `{CURRENT_MODE}`\nUsage: `.mode [chat/story]`")
        return

    new_mode = context.args[0].lower()
    if new_mode in ["chat", "story"]:
        CURRENT_MODE = new_mode
        await reply_transient(update, context, f"✅ Mode switched to `{CURRENT_MODE}`.")
    else:
        await reply_and_log(update, "❌ Invalid mode. Use `.mode chat` or `.mode story`.")

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Asks a question about the conversation history.
    Does not include roles, characters, or scenes.
    The question and result are not saved to context.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args:
        await reply_and_log(update, "❌ Usage: `.ask [question]`")
        return

    question = " ".join(context.args)
    # Prepare neutral payload (exclude system persona at index 0)
    history_subset = chat_history[1:][-CONTEXT_LIMIT:]
    payload = [{"role": "system", "content": "You are a helpful assistant. Use the conversation history provided to answer the user's question accurately. Do not adopt any persona."}]
    payload.extend(history_subset)
    payload.append({"role": "user", "content": question})

    # Sanitize payload for API
    api_payload = [{"role": m["role"], "content": m["content"]} for m in payload]

    if TRACE_MODE:
        try:
            trace_file = CONTEXT_DIR / f"{VIVID_PREFIX}_ask_payload.json"
            with open(trace_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Trace failed: {e}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Use centralized helper
        reply = await generate_response(api_payload)

        if TRACE_MODE:
            try:
                resp_file = CONTEXT_DIR / f"{VIVID_PREFIX}_ask_response.json"
                with open(resp_file, 'w', encoding='utf-8') as f:
                    json.dump({"response": reply, "provider": CURRENT_PROVIDER, "model": CURRENT_MODEL}, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Response Trace failed: {e}")

        header = f"❓ **Ask:** {question}\n\n💡 **Answer:**\n"
        full_response = header + reply
        await reply_and_log(update, full_response, is_command=True)
    except Exception as e:
        await reply_and_log(update, f"❌ Ask failed: {e}")

async def delete_last_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Removes the last assistant response and the last user message
    from the active context.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    
    # Need at least more than just the system message (index 0)
    if len(chat_history) < 2:
        await reply_and_log(update, "❌ Not enough messages to delete.")
        return

    chat_id = update.effective_chat.id
    for _ in range(2):
        if len(chat_history) > 1:
            msg = chat_history.pop()
            for mid in msg.get("msg_ids", []):
                try: await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                except: pass

    await reply_and_log(update, "🗑️ Deleted last entries from history and chat.")

async def set_context_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets how many history messages are sent to the LLM per prompt."""
    global CONTEXT_LIMIT
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args:
        await reply_and_log(update, f"📊 Current context limit: `{CONTEXT_LIMIT}` messages")
        return
    
    try:
        new_limit = int(context.args[0])
        if new_limit < 1:
            await reply_and_log(update, "❌ Context limit must be at least 1.")
            return
        CONTEXT_LIMIT = new_limit
        await reply_transient(update, context, f"✅ Context limit changed to `{CONTEXT_LIMIT}` messages.")
    except ValueError:
        await reply_and_log(update, "❌ Usage: `.context [number]`")

async def role_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates a summary of the current role based on instructions in roles_recap.json."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    # 1. Determine instruction index
    index = 0
    if context.args:
        try:
            index = int(context.args[0]) - 1
        except ValueError:
            await reply_and_log(update, "❌ Usage: `.rs [n]` where n is a number.")
            return

    # 2. Load instructions from roles_recap.json
    recap_file = SETTINGS_DIR / "roles_recap.json"
    if not recap_file.exists():
        await reply_and_log(update, f"❌ `roles_recap.json` not found in `{SETTINGS_DIR}`")
        return

    try:
        with open(recap_file, 'r', encoding='utf-8') as f:
            instructions = json.load(f)
        
        if not (0 <= index < len(instructions)):
            await reply_and_log(update, f"❌ Index {index + 1} out of range. (Available: 1-{len(instructions)})")
            return
        
        instruction_text = instructions[index]
    except json.JSONDecodeError as e: # More specific exception handling
        await reply_and_log(update, f"❌ Error parsing `roles_recap.json`: {e}")
        return

    # 3. Construct prompt using the raw role file content
    role_content = get_role_content(CURRENT_ROLE)
    prompt = f"{instruction_text}\n\nROLE PROFILE CONTENT:\n{role_content}"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Use centralized helper
        summary = await generate_response([{"role": "user", "content": prompt}])
        
        await reply_and_log(update, f"🎭 **Role Summary: {CURRENT_ROLE}**\n\n{summary}")
    except Exception as e:
        await reply_and_log(update, f"❌ Role summary failed: {e}")

async def story_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Extracts all assistant messages from the current chat history 
    and saves them as a plain text story file.
    Usage: .story save [name] or .store save [name]
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args or context.args[0].lower() != "save" or len(context.args) < 2:
        await reply_and_log(update, "❌ Usage: `.story save [name]`")
        return

    story_name = context.args[1]
    assistant_content = [msg['content'] for msg in chat_history if msg.get('role') == 'assistant']

    if not assistant_content:
        await reply_and_log(update, "ℹ️ No assistant content found to extract.")
        return

    file_path = STORIES_DIR / f"{story_name}.txt"
    try:
        file_path.write_text("\n\n".join(assistant_content), encoding='utf-8')
        await reply_and_log(update, f"✅ Story extracted to `{file_path.name}`")
    except Exception as e:
        await reply_and_log(update, f"❌ Failed to save story: {e}")

async def recap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Generates a summary of the current conversation history using a 
    pre-defined prompt instruction from recap.json.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if len(chat_history) < 2:
        await reply_and_log(update, "❌ No conversation history to recap.")
        return

    # 1. Grab the history (excluding the system role at [0]) 
    # and limit to the current context limit
    history_to_recap = chat_history[1:][-CONTEXT_LIMIT:]
    
    # 2. Determine which recap instruction to use
    index = 0
    if context.args:
        try:
            index = int(context.args[0]) - 1
        except ValueError:
            await reply_and_log(update, "❌ Usage: `.recap [n]` where n is a number.")
            return

    recap_file = SETTINGS_DIR / "recap.json"
    if not recap_file.exists():
        await reply_and_log(update, f"❌ `recap.json` not found in `{SETTINGS_DIR}`")
        return

    try:
        with open(recap_file, 'r', encoding='utf-8') as f:
            instructions = json.load(f)
        
        if not (0 <= index < len(instructions)):
            await reply_and_log(update, f"❌ Index {index + 1} out of range. (Available: 1-{len(instructions)})")
            return
        
        instruction_text = instructions[index]
    except Exception as e:
        await reply_and_log(update, f"❌ Error reading `recap.json`: {e}")
        return

    # 3. Format history and create prompt
    formatted_history = "\n".join([f"{msg['role'].upper()}: {msg['content']}" for msg in history_to_recap])
    recap_prompt = f"{instruction_text}\n\n{formatted_history}"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Use centralized helper
        summary = await generate_response([{"role": "user", "content": recap_prompt}])

        # 4. Print to Telegram (but DO NOT append to chat_history)
        text = f"📝 **Conversation Recap (Last {len(history_to_recap)} msgs):**\n\n{summary}"
        await reply_and_log(update, text)

    except Exception as e:
        await reply_and_log(update, f"❌ Recap failed: {e}")

async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the last n messages from the chat history."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    n = 3
    if context.args:
        try:
            n = int(context.args[0])
        except ValueError:
            await reply_and_log(update, "❌ Usage: `.last [n]` where n is a number.")
            return

    if not chat_history:
        await reply_and_log(update, "📜 History is empty.")
        return

    # Take the last n messages
    history_slice = chat_history[-n:]
    text = f"📜 **Showing last {len(history_slice)} messages:**\n\n"

    for msg in history_slice:
        role_icon = "👤" if msg['role'] == 'user' else "🤖"
        if msg['role'] == 'system': 
            role_icon = "⚙️"
        
        line = f"{role_icon} **{msg['role'].capitalize()}**:\n{msg['content']}\n\n"
        text += line

    await reply_and_log(update, text)

async def char_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manages active characters. 
    Usage: .char (list), .char [n] on, .char [n] off, .char off all
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    global ACTIVE_CHARACTERS
    char_files = sorted(list(CHARACTERS_DIR.glob("*.txt")))
    available_chars = [f.stem for f in char_files]

    if not context.args:
        text = "👥 **Character Management**\n---\n"
        if not available_chars:
            text += "No character files found in `characters/`."
        for i, name in enumerate(available_chars):
            status = "✅ (ON)" if name in ACTIVE_CHARACTERS else "❌ (OFF)"
            text += f"{i+1}. `{name}` {status}\n"
        text += "\nUse `.char [n] on/off` or `.char all off`"
        await reply_and_log(update, text)
        return

    arg1 = context.args[0].lower()
    
    if arg1 == "all" and len(context.args) > 1 and context.args[1].lower() == "off":
        ACTIVE_CHARACTERS = []
        await update.message.reply_text("🧹 All characters deactivated.")
        save_to_json("last_session")
        return

    if len(context.args) >= 3 and context.args[0].lower() == "save":
        target = context.args[1]
        content = " ".join(context.args[2:])
        (CHARACTERS_DIR / f"{target}.txt").write_text(content, encoding='utf-8')
        await reply_and_log(update, f"💾 Character `{target}` saved.")
        return

    if arg1 == "edit" and len(context.args) > 1:
        try:
            idx = int(context.args[1]) - 1
            name = available_chars[idx]
            content = get_character_content(name)
            text = f".char save {name} {content}"
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i+4000])
            return
        except Exception:
            await reply_and_log(update, "❌ Use `.char edit [number]`")
            return

    target = None
    if arg1.isdigit():
        idx = int(arg1) - 1
        if 0 <= idx < len(available_chars):
            target = available_chars[idx]
    elif arg1 in available_chars:
        target = arg1

    if not target:
        await reply_and_log(update, "❌ Character not found.")
        return

    action = context.args[1].lower() if len(context.args) > 1 else "on"
    if action == "on":
        if target not in ACTIVE_CHARACTERS: ACTIVE_CHARACTERS.append(target)
        await reply_transient(update, context, f"👤 `{target}` activated.")
    elif action == "off":
        if target in ACTIVE_CHARACTERS: ACTIVE_CHARACTERS.remove(target)
        await reply_transient(update, context, f"👤 `{target}` deactivated.")

    save_to_json("last_session")

async def scene_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manages active scene/location descriptions. 
    Usage: .scene (list), .scene [n] on, .scene [n] off, .scene off all
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    global ACTIVE_SCENES
    scene_files = sorted(list(SCENES_DIR.glob("*.txt")))
    available_scenes = [f.stem for f in scene_files]

    if not context.args:
        text = "🎬 **Scene Management**\n---\n"
        if not available_scenes:
            text += "No scene files found in `scenes/`."
        for i, name in enumerate(available_scenes):
            status = "✅ (ON)" if name in ACTIVE_SCENES else "❌ (OFF)"
            text += f"{i+1}. `{name}` {status}\n"
        text += "\nUse `.scene [n] on/off` or `.scene all off`"
        await reply_and_log(update, text)
        return

    arg1 = context.args[0].lower()
    
    if arg1 == "all" and len(context.args) > 1 and context.args[1].lower() == "off":
        ACTIVE_SCENES = []
        await update.message.reply_text("🧹 All scenes deactivated.")
        save_to_json("last_session")
        return

    if len(context.args) >= 3 and context.args[0].lower() == "save":
        target = context.args[1]
        content = " ".join(context.args[2:])
        (SCENES_DIR / f"{target}.txt").write_text(content, encoding='utf-8')
        await reply_and_log(update, f"💾 Scene `{target}` saved.")
        return

    if arg1 == "edit" and len(context.args) > 1:
        try:
            idx = int(context.args[1]) - 1
            name = available_scenes[idx]
            content = get_scene_content(name)
            text = f".scene save {name} {content}"
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i+4000])
            return
        except Exception:
            await reply_and_log(update, "❌ Use `.scene edit [number]`")
            return

    target = None
    if arg1.isdigit():
        idx = int(arg1) - 1
        if 0 <= idx < len(available_scenes):
            target = available_scenes[idx]
    elif arg1 in available_scenes:
        target = arg1

    if not target:
        await reply_and_log(update, "❌ Scene not found.")
        return

    action = context.args[1].lower() if len(context.args) > 1 else "on"
    if action == "on":
        if target not in ACTIVE_SCENES: ACTIVE_SCENES.append(target)
        await reply_transient(update, context, f"🎬 Scene `{target}` activated.")
    elif action == "off":
        if target in ACTIVE_SCENES: ACTIVE_SCENES.remove(target)
        await reply_transient(update, context, f"🎬 Scene `{target}` deactivated.")

    save_to_json("last_session")

async def test_ollama_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Iterates through all models on an Ollama online provider to check for subscription 
    locks. Persistence is handled via ollama_models.json in the settings directory.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if "online" not in CURRENT_PROVIDER:
        await reply_and_log(update, "⚠️ This feature is specifically for testing `OLLAMA_ONLINE` providers.")
        return

    client = ollama_clients[CURRENT_PROVIDER]
    display_name = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
    
    progress_msg = await update.message.reply_text(f"🧪 Initializing model sweep for `{display_name}`...\nThis process verifies accessibility and may take several minutes.")

    try:
        resp = await client.list()
        all_models = getattr(resp, 'models', [])
        all_models.sort(key=lambda x: x.model)
        
        tested_models_status = {}
        last_model_info = ""
        
        for i, m in enumerate(all_models):
            model_name = m.model
            
            # Update progress with the previous result and the current target
            current_display = f"🧪 Testing: `{model_name}` ({i+1}/{len(all_models)})..."
            display_text = f"{last_model_info}\n{current_display}" if last_model_info else current_display
            await progress_msg.edit_text(display_text)

            try:
                test_response = await client.chat(
                    model=model_name, 
                    messages=[{"role": "user", "content": "hello"}], 
                    options={"temperature": 0.1}
                )
                
                if "Error: this model requires a subscription, upgrade for access:" in test_response.message.content:
                    tested_models_status[model_name] = "bad"
                    last_model_info = f"❌ `{model_name}`: Restricted"
                else:
                    tested_models_status[model_name] = "good"
                    last_model_info = f"✅ `{model_name}`: Accessible"
            except Exception as e:
                error_str = str(e).lower()
                if "subscription" in error_str or "upgrade" in error_str:
                    tested_models_status[model_name] = "bad"
                    last_model_info = f"❌ `{model_name}`: Restricted"
                else:
                    tested_models_status[model_name] = "unknown"
                    last_model_info = f"⚠️ `{model_name}`: Unknown Status"
            
            await asyncio.sleep(0.5) # Slightly slower to ensure Telegram updates catch up

        FILTERED_OLLAMA_MODELS_STATUS[CURRENT_PROVIDER] = tested_models_status
        save_ollama_models_status()
        
        summary = f"✅ **Sweep Complete: `{display_name}`**\n\n"
        summary += f"🟢 Standard: {list(tested_models_status.values()).count('good')}\n"
        summary += f"🔴 Restricted: {list(tested_models_status.values()).count('bad')}"
        await progress_msg.edit_text(summary)

    except Exception as e:
        await reply_and_log(update, f"❌ Failed to test models on `{display_name}`: {e}")

async def mf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists and allows selection of 'good' Ollama online models (not requiring subscription)."""
    global CURRENT_MODEL
    if update.effective_user.id != TELEGRAM_USER_ID: return
    if "online" not in CURRENT_PROVIDER:
        await reply_and_log(update, "⚠️ The `.mf` command is only applicable to `OLLAMA_ONLINE` providers.")
        return

    load_ollama_models_status()
    display_name = provider_display_info.get(CURRENT_PROVIDER, CURRENT_PROVIDER)
    provider_model_status = FILTERED_OLLAMA_MODELS_STATUS.get(CURRENT_PROVIDER, {})
    
    if not provider_model_status:
        await reply_and_log(update, f"ℹ️ No filter data found for `{display_name}`. Run `.model test` to scan available models.")
        return

    try:
        resp = await ollama_clients[CURRENT_PROVIDER].list()
        all_models = getattr(resp, 'models', [])
        all_models.sort(key=lambda x: x.model)
        good_models = [m.model for m in all_models if provider_model_status.get(m.model) == "good"]
        
        if not good_models:
            await reply_and_log(update, f"ℹ️ No accessible models detected on `{display_name}`.")
            return

        if not context.args:
            text = f"✨ **Accessible Models (`{display_name}`):**\n"
            for i, name in enumerate(good_models):
                active = "🔹" if name == CURRENT_MODEL else ""
                text += f"{i+1}. `{name}` {active}\n"
            await reply_and_log(update, text)
            return

        try:
            index = int(context.args[0]) - 1
            if 0 <= index < len(good_models):
                CURRENT_MODEL = good_models[index]
                await reply_transient(update, context, f"🧠 Switched to accessible model: `{CURRENT_MODEL}`")
                save_to_json("last_session")
            else:
                await reply_and_log(update, "❌ Invalid model number.")
        except ValueError:
            arg = context.args[0].lower()
            if arg in ["next", "n", "pref", "prev", "p"]:
                # Find where we are in the 'good' list to determine 'next' or 'prev'
                try:
                    curr_idx = good_models.index(CURRENT_MODEL)
                except ValueError:
                    curr_idx = -1 # Start from beginning if current model isn't in 'good' list

                if arg in ["next", "n"]:
                    new_idx = (curr_idx + 1) % len(good_models)
                else: # prev/pref/p
                    new_idx = (curr_idx - 1) % len(good_models)

                CURRENT_MODEL = good_models[new_idx]
                await reply_transient(update, context, f"🧠 Switched to accessible model: `{CURRENT_MODEL}`")
                save_to_json("last_session")
            else:
                await reply_and_log(update, "❌ Usage: `.mf [number/next/prev]`")
    except Exception as e:
        await reply_and_log(update, f"❌ Failed to list models: {e}")

# --- LAZY COMMAND MAPPING ---
# Maps command strings to their respective functions for Lazy Mode routing
LAZY_COMMAND_MAP = {
    "help": help_command, "h": h_command,
    "role": role_command, "r": r_command, "reload": reload_role_command,
    "provider": provider_command, "p": p_command,
    "model": model_command, "m": m_command,
    "think": toggle_think, "verbose": toggle_verbose, "v": toggle_verbose, 
    "trace": toggle_trace, "lazy": toggle_lazy, "lz": toggle_lazy,
    "chat": chat_actions, "chats": chat_actions, "c": c_command,
    "rs": role_summary_command, "rolesummary": role_summary_command,
    "mf": mf_command, "modelsfiltered": mf_command,
    "clear": chat_actions, "clean": chat_actions, "cl": chat_actions,
    "del": delete_last_message, "context": set_context_limit,
    "recap": recap_command, "story": story_command, "store": story_command,
    "last": last_command, "l": last_command, "ask": ask_command,
    "mode": mode_command, "mo": mode_command,
    "status": status_command, "s": s_command,
    "resend": resend_last_message, "char": char_command,
    "scene": scene_command, "sc": scene_command,
    "llmctx": llmcontext_command, "mc": llmcontext_command, "mcontext": llmcontext_command
}

async def handle_failed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Catch-all for messages starting with a dot that do not match a 
    registered command prefix.
    """
    text = update.message.text
    # If it starts with a dot but wasn't caught by PrefixHandlers above
    if text.startswith("."):
        error_text = (
            "⚠️ **Unknown Command or Typo Detected**\n"
            "It looks like you tried a command but added a space or typo. "
            "Please use `.help` to see valid commands."
        )
        await reply_and_log(update, error_text, is_command=True)
        return # Stop here so it doesn't go to the LLM

def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(60)
        .build()
    )
    app.add_handler(PrefixHandler(".","help", help_command))
    app.add_handler(PrefixHandler(".","h", h_command))
    app.add_handler(PrefixHandler(".","role", role_command))
    app.add_handler(PrefixHandler(".","r", r_command))
    app.add_handler(PrefixHandler(".","reload", reload_role_command))
    app.add_handler(PrefixHandler(".","provider", provider_command))
    app.add_handler(PrefixHandler(".","p", p_command))
    app.add_handler(PrefixHandler(".","model", model_command))
    app.add_handler(PrefixHandler(".","m", m_command))
    app.add_handler(PrefixHandler(".","think", toggle_think))
    app.add_handler(PrefixHandler(".","verbose", toggle_verbose))
    app.add_handler(PrefixHandler(".","trace", toggle_trace))
    app.add_handler(PrefixHandler(".","lazy", toggle_lazy))
    app.add_handler(PrefixHandler(".","lz", toggle_lazy))
    app.add_handler(PrefixHandler(".","v", toggle_verbose))
    app.add_handler(PrefixHandler(".","chat", chat_actions))
    app.add_handler(PrefixHandler(".","chats", chat_actions))
    app.add_handler(PrefixHandler(".","c", c_command))
    app.add_handler(PrefixHandler(".","rs", role_summary_command))
    app.add_handler(PrefixHandler(".","rolesummary", role_summary_command))
    app.add_handler(PrefixHandler(".","mf", mf_command))
    app.add_handler(PrefixHandler(".","modelsfiltered", mf_command))
    app.add_handler(PrefixHandler(".","clear", chat_actions))
    app.add_handler(PrefixHandler(".","clean", chat_actions))
    app.add_handler(PrefixHandler(".","cl", chat_actions))
    app.add_handler(PrefixHandler(".","del", delete_last_message))
    app.add_handler(PrefixHandler(".","context", set_context_limit))
    app.add_handler(PrefixHandler(".", "recap", recap_command))
    app.add_handler(PrefixHandler(".", "story", story_command))
    app.add_handler(PrefixHandler(".", "store", story_command))
    app.add_handler(PrefixHandler(".", "last", last_command))
    app.add_handler(PrefixHandler(".", "l", last_command))
    app.add_handler(PrefixHandler(".", "ask", ask_command))
    app.add_handler(PrefixHandler(".", "mode", mode_command))
    app.add_handler(PrefixHandler(".","status", status_command))
    app.add_handler(PrefixHandler(".","s", s_command))
    app.add_handler(PrefixHandler(".", "resend", resend_last_message))
    app.add_handler(PrefixHandler(".", "llmctx", llmcontext_command))
    app.add_handler(PrefixHandler(".", "mc", llmcontext_command))
    app.add_handler(PrefixHandler(".", "mcontext", llmcontext_command))
    app.add_handler(PrefixHandler(".","char", char_command))
    app.add_handler(PrefixHandler(".","scene", scene_command))
    app.add_handler(PrefixHandler(".","sc", scene_command))
    # catch-all for failed commands that start with a dot but aren't recognized
    app.add_handler(MessageHandler(filters.Regex(r"^\."), handle_failed_command))
    # Now this only gets messages that DON'T start with a dot, so normal messages go to the LLM handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Restore the last session automatically on startup
    # Initialize chat_history here, after global variables are set up
    global chat_history
    chat_history = [{"role": "system", "content": get_role_content(CURRENT_ROLE), "msg_ids": [], "is_command": False}]
    load_from_json("last_session")
    print(f"--- Bot Active (Prefix: {VIVID_PREFIX}, Token: {TELEGRAM_TOKEN}) ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()