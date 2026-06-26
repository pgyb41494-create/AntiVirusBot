# AntiVirusBot

Discord bot that logs every AV simulation event to a channel. Deploy on **Railway**.

## Setup Discord bot

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** → **Reset Token** → copy `DISCORD_TOKEN`
3. Enable **Message Content Intent** under Bot settings
4. **OAuth2 → URL Generator** → scopes: `bot` → permissions: `Send Messages`, `Embed Links`
5. Invite bot to your server
6. Enable Developer Mode in Discord → right-click channel → **Copy Channel ID** → `DISCORD_CHANNEL_ID`

## Railway deploy

1. Push to GitHub (`pgyb41494-create/AntiVirusBot`)
2. Railway → **New Project** → **Deploy from GitHub**
3. Set variables:
   - `DISCORD_TOKEN`
   - `DISCORD_CHANNEL_ID`
   - `API_URL` — your AntiVirusAPI Railway URL
   - `BOT_API_KEY` — same as API's `BOT_API_KEY`

## Commands

| Command | Description |
|---------|-------------|
| `!status` | Bot + API connection status |
| `!latest` | Show most recent event embed |

## Behavior

- Polls `GET /api/events?since_id=N` every 3 seconds
- Posts a rich embed for each new event
- On startup, skips historical events (only logs new ones after boot)

## Local dev

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```
