#!/usr/bin/env python3
"""SystemPulse Discord bot — live AV research telemetry per server."""

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

from log_display import decoy_label, format_event_display

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
API_URL = os.getenv("API_URL", "").rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2"))
CONFIG_PATH = Path(__file__).parent / "data" / "guilds.json"

PULSE_COLOR = 0x3D6FA8

STATUS_COLORS = {
    "success": 0x3D9A6E,
    "failed": 0xC45C5C,
    "blocked": 0xC49A3A,
    "simulated": 0x5B8FD4,
}

MODULE_LABELS = {
    "eicar": "EICAR Drop",
    "self_copy": "Self Replication",
    "persistence": "Registry Persistence",
    "process_injection": "Process Injection",
    "powershell": "Encoded PowerShell",
    "defender": "Defender Tamper",
    "keylogger": "Keylogger Hook",
    "screenshot": "Screenshot Capture",
    "clipboard": "Clipboard Steal",
    "webcam": "Webcam Access",
    "cookies": "Browser Cookies",
    "file_read": "File Harvest",
    "crypto_hunt": "Crypto Wallets",
    "location": "Geo Location",
    "network": "C2 Callback",
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
    "screenshot": "🖥️",
    "clipboard": "📋",
    "defender": "🛡️",
    "crypto_hunt": "₿",
    "self_copy": "📎",
}

intents = discord.Intents.default()
intents.guilds = True

http_client: httpx.AsyncClient | None = None
_commands_synced = False
_poll_task: asyncio.Task | None = None

pulse = app_commands.Group(name="pulse", description="SystemPulse AV research telemetry")


@dataclass
class GuildWatch:
    channel_id: int
    last_event_id: int = 0


@dataclass
class GuildStore:
    watches: dict[str, GuildWatch] = field(default_factory=dict)

    def _to_payload(self) -> dict[str, dict]:
        return {
            gid: {"channel_id": w.channel_id, "last_event_id": w.last_event_id}
            for gid, w in self.watches.items()
        }

    def _write_local_backup(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self._to_payload(), indent=2), encoding="utf-8")

    @classmethod
    def _from_payload(cls, payload: dict) -> GuildStore:
        watches = {
            gid: GuildWatch(
                channel_id=int(data["channel_id"]),
                last_event_id=int(data.get("last_event_id", 0)),
            )
            for gid, data in payload.items()
        }
        return cls(watches=watches)

    @classmethod
    def load_local(cls) -> GuildStore:
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return cls._from_payload(raw)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return cls()

    async def load_from_api(self) -> None:
        assert http_client is not None
        try:
            res = await http_client.get(f"{API_URL}/api/bot/watches", headers=_headers())
            if res.status_code == 404:
                raise httpx.HTTPStatusError("not found", request=res.request, response=res)
            res.raise_for_status()
            data = res.json().get("watches") or {}
            self.watches = GuildStore._from_payload(data).watches
            print(f"[bot] Loaded {len(self.watches)} linked guild(s) from API")
            if not self.watches:
                local = self.load_local()
                if local.watches:
                    self.watches = local.watches
                    print(f"[bot] Migrated {len(self.watches)} guild(s) from local file to API")
                    await self.persist()
            else:
                self._write_local_backup()
            return
        except httpx.HTTPError as exc:
            print(f"[bot] API watch load failed ({exc}) — using local backup")
            self.watches = self.load_local().watches

    async def persist(self) -> None:
        self._write_local_backup()
        assert http_client is not None
        try:
            body = {
                "watches": {
                    gid: {
                        "channel_id": w.channel_id,
                        "last_event_id": w.last_event_id,
                    }
                    for gid, w in self.watches.items()
                }
            }
            res = await http_client.put(
                f"{API_URL}/api/bot/watches",
                json=body,
                headers=_headers(),
            )
            if res.status_code == 404:
                return
            res.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"[bot] API watch save failed ({exc}) — local backup only")


store = GuildStore()


class SystemPulseBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix=None, intents=intents)

    async def setup_hook(self) -> None:
        self.tree.add_command(pulse)
        self.add_listener(self._on_ready_sync, "on_ready")

    async def _on_ready_sync(self) -> None:
        global _commands_synced
        if _commands_synced:
            return
        synced = await self.tree.sync()
        _commands_synced = True
        print(f"[bot] Synced {len(synced)} slash command(s) globally")


bot = SystemPulseBot()


def _headers() -> dict[str, str]:
    h: dict[str, str] = {}
    if BOT_API_KEY:
        h["x-api-key"] = BOT_API_KEY
    return h


def _guild_id(interaction: discord.Interaction) -> str | None:
    if interaction.guild is None:
        return None
    return str(interaction.guild.id)


def _module_label(module: str) -> str:
    return decoy_label(module)


def _event_host(event: dict) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        host = payload.get("hostname")
        if host and isinstance(host, str):
            return host
    return "Unknown"


def _event_user(event: dict) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        user = payload.get("username")
        if user and isinstance(user, str):
            return user
    return "—"


def _parse_ts(iso: str | None) -> datetime:
    if not iso:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _embed_for_event(event: dict) -> discord.Embed:
    module = event.get("module", "unknown")
    emoji = MODULE_EMOJI.get(module, "📡")
    label = _module_label(module)
    status = event.get("status", "unknown")
    color = STATUS_COLORS.get(status, PULSE_COLOR)
    hostname = _event_host(event)
    username = _event_user(event)
    action = event.get("action", "—")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

    clean = {
        k: v
        for k, v in payload.items()
        if k not in ("display_log", "display_result", "hostname", "username", "os", "processor")
    }
    log_lines = payload.get("display_log")
    result = payload.get("display_result")
    if not log_lines or not result:
        log_lines, result = format_event_display(
            module,
            action,
            status,
            clean,
            event.get("error_message"),
        )

    embed = discord.Embed(
        title=f"{emoji} {label}",
        description=f"`{action.replace('_', ' ')}`",
        color=color,
        timestamp=_parse_ts(event.get("created_at")),
    )
    embed.add_field(name="Host", value=f"`{hostname}`", inline=True)
    embed.add_field(name="User", value=f"`{username}`", inline=True)
    embed.add_field(name="Status", value=f"`{status.upper()}`", inline=True)
    embed.add_field(
        name="Detected",
        value="Yes" if event.get("detected") else "No",
        inline=True,
    )
    embed.add_field(
        name="Blocked",
        value="Yes" if event.get("blocked") else "No",
        inline=True,
    )
    embed.add_field(name="Event ID", value=str(event.get("id", "?")), inline=True)

    activity = "\n".join(f"> {line}" for line in log_lines[:8])
    if len(activity) > 900:
        activity = activity[:897] + "…"
    embed.add_field(name="Activity log", value=activity or "—", inline=False)
    embed.add_field(name="Result", value=result[:256], inline=False)

    if event.get("error_message"):
        embed.add_field(name="Error", value=str(event["error_message"])[:300], inline=False)

    embed.set_footer(text="SystemPulse · Live Telemetry")
    return embed


async def _fetch_latest_event_id() -> int:
    assert http_client is not None
    res = await http_client.get(f"{API_URL}/api/events?limit=1")
    res.raise_for_status()
    events = res.json()
    if not events:
        return 0
    return int(events[0]["id"])


async def _fetch_events(limit: int = 50, since_id: int | None = None) -> list[dict]:
    assert http_client is not None
    params: dict[str, int] = {"limit": limit}
    if since_id is not None:
        params["since_id"] = since_id
    res = await http_client.get(
        f"{API_URL}/api/events",
        params=params,
        headers=_headers() if since_id is not None else None,
    )
    res.raise_for_status()
    events: list[dict] = res.json()
    events.sort(key=lambda e: int(e.get("id", 0)))
    return events


async def _fetch_stats() -> dict | None:
    assert http_client is not None
    res = await http_client.get(f"{API_URL}/api/stats")
    if not res.is_success:
        return None
    return res.json()


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
            events = await _fetch_events(limit=100, since_id=since_id)

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

            await store.persist()
            if events:
                print(f"[bot] Distributed {len(events)} event(s) across guilds")

        except httpx.HTTPError as exc:
            print(f"[bot] Poll error: {exc}")

        await asyncio.sleep(POLL_INTERVAL)


@pulse.command(name="link", description="Start posting live scan events to this channel")
@app_commands.guild_only()
async def pulse_link(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if gid is None or interaction.channel is None:
        await interaction.response.send_message("Use this in a server text channel.", ephemeral=True)
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
    await store.persist()

    embed = discord.Embed(
        title="SystemPulse linked",
        description=(
            f"Live telemetry will post in {interaction.channel.mention}.\n\n"
            f"**Cursor:** event `{latest}` — only newer events are sent.\n"
            f"Run `/pulse live` before a scan to skip old backlog."
        ),
        color=PULSE_COLOR,
    )
    embed.set_footer(text="SystemPulse")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@pulse.command(name="unlink", description="Stop live event posts for this server")
@app_commands.guild_only()
async def pulse_unlink(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if gid is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return

    if gid in store.watches:
        del store.watches[gid]
        await store.persist()
        await interaction.response.send_message("Unlinked — this server will no longer receive events.", ephemeral=True)
    else:
        await interaction.response.send_message("This server is not linked. Use `/pulse link` first.", ephemeral=True)


@pulse.command(name="live", description="Jump to now — only post events from the next scan onward")
@app_commands.guild_only()
async def pulse_live(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if gid is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return

    watch = store.watches.get(gid)
    if not watch:
        await interaction.response.send_message(
            "Link a channel first with `/pulse link`.", ephemeral=True
        )
        return

    try:
        latest = await _fetch_latest_event_id()
    except httpx.HTTPError as exc:
        await interaction.response.send_message(f"API error: {exc}", ephemeral=True)
        return

    old_id = watch.last_event_id
    watch.last_event_id = latest
    await store.persist()

    await interaction.response.send_message(
        f"Live mode updated. Cursor `{old_id}` → `{latest}`.\n"
        "Run **SystemPulse.exe** now — new events will appear immediately.",
        ephemeral=True,
    )


@pulse.command(name="stats", description="Show API health and scan totals")
@app_commands.guild_only()
async def pulse_stats(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    watch = store.watches.get(gid or "")

    api_ok = False
    storage = "?"
    stats: dict | None = None
    try:
        assert http_client is not None
        health = await http_client.get(f"{API_URL}/api/health")
        api_ok = health.status_code == 200
        if health.ok:
            storage = health.json().get("storage", "?")
        stats = await _fetch_stats()
    except httpx.HTTPError:
        pass

    embed = discord.Embed(title="SystemPulse Stats", color=PULSE_COLOR if api_ok else 0xC45C5C)
    embed.add_field(name="API", value="Online" if api_ok else "Offline", inline=True)
    embed.add_field(name="Storage", value=f"`{storage}`", inline=True)
    embed.add_field(name="Poll", value=f"`{POLL_INTERVAL}s`", inline=True)

    if stats:
        embed.add_field(name="Total events", value=str(stats.get("total", 0)), inline=True)
        embed.add_field(name="Detected", value=str(stats.get("detected", 0)), inline=True)
        embed.add_field(name="Blocked", value=str(stats.get("blocked", 0)), inline=True)

        modules = stats.get("by_module") or []
        if modules:
            lines = []
            for row in sorted(modules, key=lambda m: m.get("count", 0), reverse=True)[:8]:
                mid = row.get("module", "?")
                lines.append(f"`{row.get('count', 0)}` {_module_label(mid)}")
            embed.add_field(name="Top modules", value="\n".join(lines) or "—", inline=False)

    if watch:
        channel = bot.get_channel(watch.channel_id)
        ch = channel.mention if channel else f"`{watch.channel_id}`"
        embed.add_field(
            name="This server",
            value=f"Linked to {ch}\nCursor: `{watch.last_event_id}`",
            inline=False,
        )
    else:
        embed.add_field(name="This server", value="Not linked — use `/pulse link`", inline=False)

    embed.set_footer(text=f"SystemPulse · {len(store.watches)} server(s) linked")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@pulse.command(name="host", description="Show the most recent endpoint that ran a scan")
@app_commands.guild_only()
async def pulse_host(interaction: discord.Interaction) -> None:
    try:
        events = await _fetch_events(limit=100)
    except httpx.HTTPError as exc:
        await interaction.response.send_message(f"API error: {exc}", ephemeral=True)
        return

    if not events:
        await interaction.response.send_message("No scans recorded yet.", ephemeral=True)
        return

    latest = events[0]
    session_id = latest.get("session_id")
    session_events = [e for e in events if e.get("session_id") == session_id] if session_id else [latest]
    session_events.sort(key=lambda e: int(e.get("id", 0)))

    hostname = _event_host(latest)
    username = _event_user(latest)
    detected = sum(1 for e in session_events if e.get("detected"))
    blocked = sum(1 for e in session_events if e.get("blocked"))

    embed = discord.Embed(
        title=f"Latest endpoint — {hostname}",
        color=PULSE_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="User", value=f"`{username}`", inline=True)
    embed.add_field(name="Modules run", value=str(len(session_events)), inline=True)
    embed.add_field(name="Detected / Blocked", value=f"`{detected}` / `{blocked}`", inline=True)

    lines = []
    for e in session_events[-12:]:
        mod = e.get("module", "?")
        emoji = MODULE_EMOJI.get(mod, "•")
        payload = e.get("payload") or {}
        result = payload.get("display_result") or e.get("status", "?")
        lines.append(f"{emoji} {_module_label(mod)} — {result}")
    embed.add_field(name="Session log", value="\n".join(lines) or "—", inline=False)

    if session_id:
        embed.set_footer(text=f"Session {str(session_id)[:8]}… · SystemPulse")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@pulse.command(name="recent", description="Show the last few scan events")
@app_commands.describe(count="How many events to show (1–10)")
@app_commands.guild_only()
async def pulse_recent(interaction: discord.Interaction, count: app_commands.Range[int, 1, 10] = 5) -> None:
    try:
        events = await _fetch_events(limit=count)
    except httpx.HTTPError as exc:
        await interaction.response.send_message(f"API error: {exc}", ephemeral=True)
        return

    if not events:
        await interaction.response.send_message("No events yet.", ephemeral=True)
        return

    await interaction.response.send_message(
        embeds=[_embed_for_event(e) for e in events[:count]],
        ephemeral=True,
    )


@pulse.command(name="guide", description="Quick setup guide for SystemPulse Discord logging")
@app_commands.guild_only()
async def pulse_guide(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="SystemPulse — Discord setup",
        description="Log AV research scans from SystemPulse.exe into this server.",
        color=PULSE_COLOR,
    )
    embed.add_field(
        name="1. Link channel",
        value="`/pulse link` in the channel where you want live events.",
        inline=False,
    )
    embed.add_field(
        name="2. Before each scan",
        value="`/pulse live` — skips old events, only new ones post.",
        inline=False,
    )
    embed.add_field(
        name="3. Run simulator",
        value="Right-click **SystemPulse.exe** → Run as administrator → **Run Health Scan**.",
        inline=False,
    )
    embed.add_field(
        name="Commands",
        value=(
            "`/pulse link` — start logging here\n"
            "`/pulse unlink` — stop logging\n"
            "`/pulse live` — jump to now\n"
            "`/pulse stats` — totals & API health\n"
            "`/pulse host` — latest PC scan summary\n"
            "`/pulse recent` — last N events\n"
            "`/pulse guide` — this help"
        ),
        inline=False,
    )
    embed.set_footer(text="SystemPulse AV Research")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_ready() -> None:
    global _poll_task
    if http_client is not None:
        await store.load_from_api()
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
    await store.load_from_api()
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
