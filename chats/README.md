# Chat System (Save / Load)

A lightweight, persistent chat session management system that stores data within the `chats/` directory.

---

## Technical Specifications

### State Persistence
* **Auto-Save:** Continuous runtime synchronization (non-blocking, event-driven).
* **Session Target:** `chats/[BOT_PREFIX]_last_session.json` (Defaults to `vivid_last_session.json`).
* **Isolation:** Sessions are isolated per `BOT_PREFIX`. Changing the prefix routes state tracking to a separate isolated file.

### Fault Tolerance & Recovery
* **Volatile Memory:** Conversation states are maintained in-memory during active execution.
* **Warm Boot Recovery:** On initialization, `vivid.py` scans for the target `[BOT_PREFIX]_last_session.json`. If detected, state is restored to memory; otherwise, a clean session state is initialized.

---

## Command Interface

| Command | Action | Description |
| :--- | :--- | :--- |
| `.chat save [name]` | Snapshot State | Commits the current in-memory session to `chats/[name].json`. |
| `.chat load [name]` | Hydrate State | Overwrites the current active session memory with the contents of `chats/[name].json`. |

---

## Directory Architecture

```text
project/
├── chats/
│   ├── vivid_last_session.json  # Continuous state backup
│   └── [custom_name].json       # User-persisted snapshots
├── README.md                    # System documentation
└── vivid.py                     # Application entry point