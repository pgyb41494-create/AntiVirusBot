# AntiVirusBot

Discord bot that logs AV simulation events **per server** — no channel ID required.

## Setup Discord bot

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** → copy `DISCORD_TOKEN`
3. **OAuth2 → URL Generator** → scopes: `bot` + `applications.commands`
4. Bot permissions: `Send Messages`, `Embed Links`
5. Invite bot to your server(s)

## Railway deploy

1. Push to GitHub → deploy on Railway
2. Set variables:
   - `DISCORD_TOKEN`
   - `API_URL` — your AntiVirusAPI Railway URL
   - `BOT_API_KEY` — same as API's `BOT_API_KEY`

No `DISCORD_CHANNEL_ID` needed.

## Slash commands (per server)

| Command | Description |
|---------|-------------|
| `/watch` | Enable logging in **this channel** (server-wide) |
| `/unwatch` | Stop logging for this server |
| `/reset` | Skip backlog **for this server only** — cursor jumps to latest event |
| `/status` | API + watch status for this server |
| `/latest` | Most recent event embed |

### Typical flow

1. Invite bot to server
2. In your log channel: `/watch`
3. Run simulator — events appear in that channel
4. If it's slow or spamming old events: `/reset` (per guild)

## Behavior

- Works in **any server** — each guild configures its own channel via `/watch`
- **Per-guild cursor** — `last_event_id` is tracked separately per server
- **`/reset`** sets this server's cursor to the latest API event (no backlog, faster)
- **No duplicate commands** — slash-only, synced once globally at startup
- **No duplicate posts** — each guild only receives events with `id >` its cursor
- Guild config persists in `data/guilds.json` across restarts

## Local dev

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Slash commands may take up to ~1 hour to appear globally on first deploy; re-invite with `applications.commands` scope if missing.
