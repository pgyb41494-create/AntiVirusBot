#!/usr/bin/env python3
"""Discord bot that logs every AV simulation event from the Railway API."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import discord
import httpx
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
API_URL = os.getenv("API_URL", "").rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "3"))

STATUS_COLORS = {
    "success": 0x22C55E,
    "failed": 0xEF4444,
    "blocked": 0xF59E0B,
    "simulated": 0xA78BFA,
}

MODULE_EMOJI = {
    "eicar": "🧪",
    "location": "📍",
    "cookies": "🍪",
    "webcam": "📷",
    "file_read": "📁",
    "network": "🌐",
    "process_injection": "💉",
    "keylogger": "⌨️",
    "powershell": "⚡",
    "persistence": "📌",
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_event_id = 0
http_client: httpx.AsyncClient | None = None


def _headers() -> dict[str, str]:
    h: dict[str, str] = {}
    if BOT_API_KEY:
        h["x-api-key"] = BOT_API_KEY
    return h


def _embed_for_event(event: dict) -> discord.Embed:
    module = event.get("module", "unknown")
    emoji = MODULE_EMOJI.get(module, "🔔")
    status = event.get("status", "unknown")
    color = STATUS_COLORS.get(status, 0x3B82F6)

    embed = discord.Embed(
        title=f"{emoji} {module} — {event.get('action', '')}",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Status", value=status.upper(), inline=True)
    embed.add_field(name="Detected", value="✅ Yes" if event.get("detected") else "❌ No", inline=True)
    embed.add_field(name="Blocked", value="🛑 Yes" if event.get("blocked") else "— No", inline=True)

    if event.get("session_id"):
        embed.add_field(name="Session", value=f"`{event['session_id'][:8]}…`", inline=True)
    embed.add_field(name="Event ID", value=str(event.get("id", "?")), inline=True)

    if event.get("error_message"):
        embed.add_field(name="Error", value=event["error_message"][:500], inline=False)

    payload = event.get("payload")
    if payload and isinstance(payload, dict):
        preview = str(payload)[:400]
        embed.add_field(name="Payload", value=f"```json\n{preview}\n```", inline=False)

    embed.set_footer(text="AntiVirusBot · AV Research Logger")
    return embed


async def poll_events() -> None:
    global last_event_id
    await bot.wait_until_ready()

    if not API_URL:
        print("[bot] API_URL not set — polling disabled")
        return

    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if not channel:
        print(f"[bot] Channel {DISCORD_CHANNEL_ID} not found")
        return

    print(f"[bot] Polling {API_URL} every {POLL_INTERVAL}s → #{channel}")

    while not bot.is_closed():
        try:
            assert http_client is not None
            response = await http_client.get(
                f"{API_URL}/api/events",
                params={"since_id": last_event_id, "limit": 50},
                headers=_headers(),
            )
            response.raise_for_status()
            events = response.json()

            for event in events:
                eid = event.get("id", 0)
                if eid > last_event_id:
                    last_event_id = eid
                await channel.send(embed=_embed_for_event(event))

            if events:
                print(f"[bot] Posted {len(events)} event(s), last_id={last_event_id}")

        except httpx.HTTPError as exc:
            print(f"[bot] Poll error: {exc}")

        await asyncio.sleep(POLL_INTERVAL)


@bot.event
async def on_ready():
    print(f"[bot] Logged in as {bot.user}")
    asyncio.create_task(poll_events())


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    """Show bot and API connection status."""
    try:
        assert http_client is not None
        res = await http_client.get(f"{API_URL}/api/health")
        api_ok = res.status_code == 200
        stats = await http_client.get(f"{API_URL}/api/stats")
        data = stats.json() if stats.ok else {}
    except httpx.HTTPError:
        api_ok = False
        data = {}

    await ctx.send(
        f"**AntiVirusBot Status**\n"
        f"API: {'🟢' if api_ok else '🔴'} `{API_URL}`\n"
        f"Last event ID: `{last_event_id}`\n"
        f"Total events: `{data.get('total', '?')}`\n"
        f"Poll interval: `{POLL_INTERVAL}s`"
    )


@bot.command(name="latest")
async def cmd_latest(ctx: commands.Context):
    """Post the most recent simulation event."""
    try:
        assert http_client is not None
        res = await http_client.get(f"{API_URL}/api/events?limit=1")
        events = res.json()
        if not events:
            await ctx.send("No events yet.")
            return
        await ctx.send(embed=_embed_for_event(events[0]))
    except httpx.HTTPError as exc:
        await ctx.send(f"API error: {exc}")


async def main():
    global http_client, last_event_id

    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is required")
    if not DISCORD_CHANNEL_ID:
        raise SystemExit("DISCORD_CHANNEL_ID is required")
    if not API_URL:
        raise SystemExit("API_URL is required")

    http_client = httpx.AsyncClient(timeout=15.0)

    # Seed last_event_id so we only log NEW events after bot starts
    try:
        res = await http_client.get(f"{API_URL}/api/events?limit=1")
        if res.ok:
            events = res.json()
            if events:
                last_event_id = events[0]["id"]
                print(f"[bot] Starting from event id {last_event_id} (older events skipped)")
    except httpx.HTTPError:
        pass

    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
