"""Human-readable scan logs for SystemPulse UI and Discord — no raw JSON dumps."""

from __future__ import annotations

from typing import Any


def _short_path(path: str, max_len: int = 48) -> str:
    if len(path) <= max_len:
        return path
    return "…" + path[-(max_len - 1) :]


def format_progress_lines(module: str) -> list[str]:
    """Fake in-progress log lines shown while a module runs."""
    lines: dict[str, list[str]] = {
        "defender": [
            "Querying Windows Security preferences…",
            "Attempting policy adjustment…",
            "Registering path exclusions…",
        ],
        "eicar": [
            "Preparing test signature buffer…",
            "Writing standard test file to disk…",
        ],
        "self_copy": [
            "Resolving install path…",
            "Copying package to AppData cache…",
        ],
        "persistence": [
            "Opening Run key (HKCU)…",
            "Writing startup entry…",
        ],
        "process_injection": [
            "Opening target process handle…",
            "Allocating remote memory region…",
            "Writing stub — CreateRemoteThread",
        ],
        "powershell": [
            "Building encoded command block…",
            "Spawning powershell.exe -EncodedCommand…",
        ],
        "keylogger": [
            "Installing keyboard hook (5s window)…",
            "Capturing input events…",
        ],
        "screenshot": [
            "Acquiring desktop device context…",
            "Encoding frame buffer…",
        ],
        "clipboard": [
            "Opening clipboard session…",
            "Reading text buffer…",
        ],
        "webcam": [
            "Enumerating video capture devices…",
            "Requesting camera access…",
        ],
        "cookies": [
            "Scanning browser profile paths…",
            "Reading cookie stores…",
        ],
        "file_read": [
            "Walking user document paths…",
            "Staging harvested files…",
        ],
        "crypto_hunt": [
            "Scanning wallet directories…",
            "Checking Exodus / Electrum / MetaMask paths…",
        ],
        "location": [
            "Requesting geo-IP lookup…",
            "Resolving public endpoint…",
        ],
        "network": [
            "Opening outbound socket…",
            "Sending beacon payload…",
        ],
    }
    return lines.get(module, ["Running module…"])


def format_event_display(
    module: str,
    action: str,
    status: str,
    payload: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> tuple[list[str], str]:
    """
    Returns (log_lines, result_summary) for Discord / activity log.
  log_lines = fake operational trace; result = one-line outcome.
    """
    p = payload or {}
    meta = {
        k: v
        for k, v in p.items()
        if k
        not in (
            "display_log",
            "display_result",
            "hostname",
            "username",
            "os",
            "processor",
        )
    }

    if error_message:
        return (
            format_progress_lines(module),
            f"FAILED — {error_message[:120]}",
        )

    if status == "blocked":
        return (
            format_progress_lines(module),
            "BLOCKED — action interrupted (likely security software)",
        )

    formatters = {
        "defender": _defender,
        "eicar": _eicar,
        "self_copy": _self_copy,
        "persistence": _persistence,
        "process_injection": _process_injection,
        "powershell": _powershell,
        "keylogger": _keylogger,
        "screenshot": _screenshot,
        "clipboard": _clipboard,
        "webcam": _webcam,
        "cookies": _cookies,
        "file_read": _file_read,
        "crypto_hunt": _crypto_hunt,
        "location": _location,
        "network": _network,
    }
    fn = formatters.get(module, _generic)
    return fn(action, status, meta)


def _defender(action: str, status: str, p: dict) -> tuple[list[str], str]:
    elevated = "yes" if p.get("elevated") else "no"
    attempts = p.get("attempts") or []
    ok = sum(1 for a in attempts if a.get("code") == 0 and a.get("step") != "read_prefs")
    lines = [
        f"Elevation: {elevated}",
        "DisableRealtimeMonitoring — sent",
        "DisableBehaviorMonitoring — sent",
        f"Path exclusions queued: {len(p.get('paths') or [])}",
    ]
    if ok:
        result = f"OK — {ok} tamper step(s) accepted by policy engine"
    elif status == "success":
        result = "OK — security policy modified"
    else:
        result = "DENIED — tamper blocked or insufficient rights"
    return lines, result


def _eicar(action: str, status: str, p: dict) -> tuple[list[str], str]:
    files = p.get("files_written") or []
    lines = [f"Target: {_short_path(str(f))}" for f in files[:2]]
    if not lines:
        lines = ["Writing EICAR test string…"]
    if status == "success":
        return lines, "OK — test file written (AV should react)"
    return lines, "BLOCKED — write quarantined or denied"


def _self_copy(action: str, status: str, p: dict) -> tuple[list[str], str]:
    dest = p.get("destination", "AppData\\…\\SystemHealthMonitor.exe")
    lines = [
        f"Source: {_short_path(str(p.get('source', '?')))}",
        f"Copy → {_short_path(str(dest))}",
    ]
    if p.get("exists"):
        return lines, "OK — replica staged in AppData"
    return lines, "FAILED — copy did not complete"


def _persistence(action: str, status: str, p: dict) -> tuple[list[str], str]:
    lines = [
        "Hive: HKCU",
        f"Key: {p.get('key', '…\\Run')}",
        f"Value: {p.get('value_name', 'AVTesterEduSim')}",
    ]
    if p.get("cleaned_up"):
        lines.append("Cleanup: Run key restored")
    if status == "success":
        return lines, "OK — startup entry written (then removed)"
    return lines, "BLOCKED — registry write denied"


def _process_injection(action: str, status: str, p: dict) -> tuple[list[str], str]:
    steps = p.get("steps") or []
    names = [s.get("stage", "?") for s in steps[:4]]
    lines = [f"API: {n}" for n in names] or ["VirtualAllocEx → WriteProcessMemory"]
    if status == "success":
        return lines, "OK — injection APIs executed on own process"
    return lines, "BLOCKED — memory APIs intercepted"


def _powershell(action: str, status: str, p: dict) -> tuple[list[str], str]:
    length = p.get("encoded_command_length", "?")
    lines = [
        f"Encoded payload: {length} chars",
        "ExecutionPolicy: Bypass",
        "Host: powershell.exe",
    ]
    if status == "success":
        return lines, "OK — encoded command completed"
    return lines, "BLOCKED — PowerShell terminated"


def _keylogger(action: str, status: str, p: dict) -> tuple[list[str], str]:
    keys = p.get("keys_captured", 0)
    duration = p.get("duration_seconds", 5)
    method = p.get("method", "hook")
    lines = [
        f"Hook method: {method}",
        f"Capture window: {duration}s",
        f"Events buffered: {keys}",
    ]
    if status == "success":
        return lines, f"OK — hook active ({keys} events in {duration}s)"
    return lines, "BLOCKED — hook install denied"


def _screenshot(action: str, status: str, p: dict) -> tuple[list[str], str]:
    lines = [
        f"Output: {_short_path(str(p.get('path', p.get('file', 'desktop.png'))))}",
    ]
    if p.get("width") and p.get("height"):
        lines.append(f"Resolution: {p['width']}×{p['height']}")
    if status == "success":
        return lines, "OK — desktop frame captured"
    return lines, "BLOCKED — capture denied"


def _clipboard(action: str, status: str, p: dict) -> tuple[list[str], str]:
    chars = p.get("chars", p.get("length", 0))
    lines = [
        "Format: CF_TEXT",
        f"Bytes read: {chars}",
    ]
    if status == "success":
        return lines, f"OK — clipboard read ({chars} chars)"
    return lines, "BLOCKED — clipboard access denied"


def _webcam(action: str, status: str, p: dict) -> tuple[list[str], str]:
    device = p.get("device", p.get("backend", "default"))
    lines = [f"Device: {device}"]
    if status == "success":
        return lines, "OK — camera stream opened"
    return lines, "BLOCKED — camera access denied"


def _cookies(action: str, status: str, p: dict) -> tuple[list[str], str]:
    count = p.get("cookies_found", p.get("count", 0))
    browser = p.get("browser", "Chrome/Edge")
    lines = [
        f"Browser: {browser}",
        f"Cookies indexed: {count}",
    ]
    if status == "success":
        return lines, f"OK — {count} cookie(s) staged"
    return lines, "BLOCKED — browser data access denied"


def _file_read(action: str, status: str, p: dict) -> tuple[list[str], str]:
    files = p.get("files") or p.get("paths") or []
    count = p.get("count", len(files))
    lines = [f"Files harvested: {count}"]
    for f in files[:2]:
        lines.append(f"  · {_short_path(str(f))}")
    if status == "success":
        return lines, f"OK — {count} file(s) collected"
    return lines, "BLOCKED — file read denied"


def _crypto_hunt(action: str, status: str, p: dict) -> tuple[list[str], str]:
    found = p.get("wallets_found", len(p.get("hits") or []))
    hits = p.get("hits") or []
    lines = [f"Wallet paths scanned: {found}"]
    for h in hits[:3]:
        if isinstance(h, dict):
            lines.append(f"  · {h.get('name', h.get('path', '?'))}")
        else:
            lines.append(f"  · {_short_path(str(h))}")
    if status == "success":
        return lines, f"OK — {found} wallet path(s) located"
    return lines, "BLOCKED — scan interrupted"


def _location(action: str, status: str, p: dict) -> tuple[list[str], str]:
    city = p.get("city", "?")
    country = p.get("country", p.get("country_name", "?"))
    ip = p.get("ip", p.get("query", "?"))
    lines = [
        f"Public IP: {ip}",
        f"Location: {city}, {country}",
    ]
    if status == "success":
        return lines, f"OK — geo resolved ({city})"
    return lines, "FAILED — lookup unavailable"


def _network(action: str, status: str, p: dict) -> tuple[list[str], str]:
    host = p.get("host", p.get("endpoint", "callback"))
    port = p.get("port", "443")
    lines = [
        f"Endpoint: {host}:{port}",
        f"Bytes sent: {p.get('bytes_sent', 0)}",
    ]
    if status == "success":
        return lines, "OK — beacon transmitted"
    return lines, "BLOCKED — connection dropped"


def _generic(action: str, status: str, p: dict) -> tuple[list[str], str]:
    lines = format_progress_lines("generic")
    return lines, f"{status.upper()} — {action.replace('_', ' ')}"


def format_console_block(module: str, action: str, status: str, payload: dict | None, error: str | None) -> list[str]:
    """Lines for the SystemPulse activity log window."""
    label = module.replace("_", " ").title()
    out = [f"[{label}]"]
    for line in format_progress_lines(module):
        out.append(f"  > {line}")
    log_lines, result = format_event_display(module, action, status, payload, error)
    for line in log_lines:
        out.append(f"  > {line}")
    out.append(f"  → {result}")
    return out
