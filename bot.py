#!/usr/bin/env python3
"""Discord bot that logs AV simulation events per-server (no global channel ID)."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
API_URL = os.getenv("API_URL", "").rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2"))
CONFIG_PATH = Path(__file__).parent / "data" / "guilds.json"

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
intents.guilds = True

http_client: httpx.AsyncClient | None = None
_commands_synced = False
_poll_task: asyncio.Task | None = None


@dataclass
class GuildWatch:
    channel_id: int
    last_event_id: int = 0


@dataclass
class GuildStore:
    watches: dict[str, GuildWatch] = field(default_factory=dict)

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            gid: {"channel_id": w.channel_id, "last_event_id": w.last_event_id}
            for gid, w in self.watches.items()
        }
        CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> GuildStore:
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            watches = {
                gid: GuildWatch(
                    channel_id=int(data["channel_id"]),
                    last_event_id=int(data.get("last_event_id", 0)),
                )
                for gid, data in raw.items()
            }
            return cls(watches=watches)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return cls()


store = GuildStore.load()


class AvBot(commands.Bot):
    def __init__(self) -> None:
        # No prefix — slash commands only (avoids duplicate / conflicting text commands).
        super().__init__(command_prefix=None, intents=intents)

    async def setup_hook(self) -> None:
        # Register slash commands once at startup, before connect.
        self.add_listener(self._on_ready_sync, "on_ready")

    async def _on_ready_sync(self) -> None:
        global _commands_synced
        if _commands_synced:
            return
        synced = await self.tree.sync()
        _commands_synced = True
        print(f"[bot] Synced {len(synced)} slash command(s) globally")


bot = AvBot()


def _headers() -> dict[str, str]:
    h: dict[str, str] = {}
    if BOT_API_KEY:
        h["x-api-key"] = BOT_API_KEY
    return h


def _guild_id(interaction: discord.Interaction) -> str | None:
    if interaction.guild is None:
        return None
    return str(interaction.guild.id)


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
        embed.add_field(name="Session", value=f"`{str(event['session_id'])[:8]}…`", inline=True)
    embed.add_field(name="Event ID", value=str(event.get("id", "?")), inline=True)

    if event.get("error_message"):
        embed.add_field(name="Error", value=str(event["error_message"])[:500], inline=False)

    payload = event.get("payload")
    if payload and isinstance(payload, dict):
        preview = str(payload)[:400]
        embed.add_field(name="Payload", value=f"```json\n{preview}\n```", inline=False)

    embed.set_footer(text="AntiVirusBot · AV Research Logger")
    return embed


async def _fetch_latest_event_id() -> int:
    assert http_client is not None
    res = await http_client.get(f"{API_URL}/api/events?limit=1")
    res.raise_for_status()
    events = res.json()
    if not events:
        return 0
    return int(events[0]["id"])


async def _fetch_events_since(since_id: int) -> list[dict]:
    assert http_client is not None
    res = await http_client.get(
        f"{API_URL}/api/events",
        params={"since_id": since_id, "limit": 100},
        headers=_headers(),
    )
    res.raise_for_status()
    events: list[dict] = res.json()
    events.sort(key=lambda e: int(e.get("id", 0)))
    return events


async def poll_events() -> None:
    await bot.wait_until_ready()

    if not API_URL:
        print("[bot] API_URL not set — polling disabled")
        return

    print(f"[bot] Polling {API_URL} every {POLL_INTERVAL}s for {len(store.watches)} guild(s)")

    while not bot.is_closed():
        if not store.watches:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        try:
            since_id = min(w.last_event_id for w in store.watches.values())
            events = await _fetch_events_since(since_id)

            if not events:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            for event in events:
                event_id = int(event.get("id", 0))
                embed = _embed_for_event(event)

                for guild_id, watch in list(store.watches.items()):
                    if event_id <= watch.last_event_id:
                        continue

                    channel = bot.get_channel(watch.channel_id)
                    if channel is None:
                        guild = bot.get_guild(int(guild_id))
                        if guild:
                            channel = guild.get_channel(watch.channel_id)
                    if channel is None:
                        continue

                    try:
                        await channel.send(embed=embed)
                        watch.last_event_id = event_id
                    except discord.HTTPException as exc:
                        print(f"[bot] Send failed guild={guild_id}: {exc}")

            store.save()
            if events:
                print(f"[bot] Distributed {len(events)} event(s) across guilds")

        except httpx.HTTPError as exc:
            print(f"[bot] Poll error: {exc}")

        await asyncio.sleep(POLL_INTERVAL)


@bot.tree.command(name="watch", description="Log AV simulation events in this channel")
@app_commands.guild_only()
async def slash_watch(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if gid is None or interaction.channel is None:
        await interaction.response.send_message("Use this in a server channel.", ephemeral=True)
        return

    latest = 0
    try:
        latest = await _fetch_latest_event_id()
    except httpx.HTTPError:
        pass

    store.watches[gid] = GuildWatch(
        channel_id=interaction.channel.id,
        last_event_id=latest,
    )
    store.save()

    await interaction.response.send_message(
        f"✅ Logging enabled in {interaction.channel.mention}. "
        f"Only **new** events after ID `{latest}` will be posted.\n"
        f"Use `/reset` to skip backlog and catch new events faster.",
        ephemeral=True,
    )


@bot.tree.command(name="unwatch", description="Stop logging events in this server")
@app_commands.guild_only()
async def slash_unwatch(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if gid is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return

    if gid in store.watches:
        del store.watches[gid]
        store.save()
        await interaction.response.send_message("🛑 Logging stopped for this server.", ephemeral=True)
    else:
        await interaction.response.send_message("This server is not being watched.", ephemeral=True)


@bot.tree.command(
    name="reset",
    description="Skip backlog for this server — only new events from now (faster)",
)
@app_commands.guild_only()
async def slash_reset(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if gid is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return

    watch = store.watches.get(gid)
    if not watch:
        await interaction.response.send_message(
            "Run `/watch` in the channel you want first.", ephemeral=True
        )
        return

    try:
        latest = await _fetch_latest_event_id()
    except httpx.HTTPError as exc:
        await interaction.response.send_message(f"API error: {exc}", ephemeral=True)
        return

    old_id = watch.last_event_id
    watch.last_event_id = latest
    store.save()

    await interaction.response.send_message(
        f"⏩ **Reset for this server.** Cursor `{old_id}` → `{latest}`.\n"
        "New simulator events will post immediately — no old backlog.",
        ephemeral=True,
    )


@bot.tree.command(name="status", description="Bot and API status for this server")
@app_commands.guild_only()
async def slash_status(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    watch = store.watches.get(gid or "")

    api_ok = False
    total = "?"
    try:
        assert http_client is not None
        res = await http_client.get(f"{API_URL}/api/health")
        api_ok = res.status_code == 200
        stats = await http_client.get(f"{API_URL}/api/stats")
        if stats.ok:
            total = stats.json().get("total", "?")
    except httpx.HTTPError:
        pass

    if watch:
        channel = bot.get_channel(watch.channel_id)
        ch_name = channel.mention if channel else f"`{watch.channel_id}`"
        guild_line = f"**This server:** watching {ch_name}, cursor `{watch.last_event_id}`"
    else:
        guild_line = "**This server:** not watching — run `/watch` in your log channel"

    await interaction.response.send_message(
        f"**AntiVirusBot Status**\n"
        f"API: {'🟢' if api_ok else '🔴'} `{API_URL}`\n"
        f"Total events: `{total}`\n"
        f"Poll interval: `{POLL_INTERVAL}s`\n"
        f"Watched servers: `{len(store.watches)}`\n"
        f"{guild_line}",
        ephemeral=True,
    )


@bot.tree.command(name="latest", description="Show the most recent simulation event")
@app_commands.guild_only()
async def slash_latest(interaction: discord.Interaction) -> None:
    try:
        assert http_client is not None
        res = await http_client.get(f"{API_URL}/api/events?limit=1")
        res.raise_for_status()
        events = res.json()
        if not events:
            await interaction.response.send_message("No events yet.", ephemeral=True)
            return
        await interaction.response.send_message(embed=_embed_for_event(events[0]))
    except httpx.HTTPError as exc:
        await interaction.response.send_message(f"API error: {exc}", ephemeral=True)


@bot.event
async def on_ready() -> None:
    global _poll_task
    print(f"[bot] Logged in as {bot.user} ({len(store.watches)} guild watch(es))")
    if _poll_task is None or _poll_task.done():
        _poll_task = asyncio.create_task(poll_events())


async def main() -> None:
    global http_client

    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is required")
    if not API_URL:
        raise SystemExit("API_URL is required")

    http_client = httpx.AsyncClient(timeout=15.0)
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
