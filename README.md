# SystemPulse Bot

Discord bot for live **SystemPulse** AV research telemetry — one linked channel per server.

## Setup

1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application → **Bot** → copy token
2. OAuth2 URL Generator → scopes: `bot` + `applications.commands`
3. Permissions: `Send Messages`, `Embed Links`
4. Deploy on Railway with:
   - `DISCORD_TOKEN`
   - `API_URL` — your Railway API URL
   - `BOT_API_KEY` — same as API `BOT_API_KEY`

## Commands (`/pulse …`)

| Command | Description |
|---------|-------------|
| `/pulse link` | Post live scan events to this channel |
| `/pulse unlink` | Stop logging for this server |
| `/pulse live` | Skip backlog — only events from the next scan |
| `/pulse alert` | Ping a role on scan start / blocked checks |
| `/pulse alert-off` | Disable role pings |
| `/pulse stats` | API health, totals, top modules |
| `/pulse host` | Latest endpoint / session summary |
| `/pulse recent` | Last 1–10 events (embeds) |
| `/pulse guide` | Quick setup help |

### Typical flow

1. `/pulse link` in your log channel
2. `/pulse alert @YourRole` — optional pings + auto scan report card
3. `/pulse live` before each scan
4. Run **SystemPulse.exe** as Administrator → **Run Health Scan**
5. Events appear live; a **scan report card** posts when the scan finishes

## Notes

- Linked channels persist in the **API** (`PUT/GET /api/bot/watches`) — survives bot restarts on Railway
- `data/guilds.json` is a local backup only
- Old commands (`/watch`, `/reset`, etc.) are replaced — re-sync may take up to ~1 hour globally
- Slash-only — no prefix commands

## Local dev

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```
