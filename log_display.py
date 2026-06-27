"""Readable activity logs from real module payloads — shows what was actually collected."""

from __future__ import annotations

from typing import Any

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


def module_label(module: str) -> str:
    return MODULE_LABELS.get(module, module.replace("_", " ").title())


def _short_path(path: str, max_len: int = 56) -> str:
    s = str(path)
    if len(s) <= max_len:
        return s
    return "…" + s[-(max_len - 1) :]


def format_progress_lines(module: str) -> list[str]:
    """Short status lines while a module runs."""
    return [f"Running {module.replace('_', ' ')}…"]


def format_event_display(
    module: str,
    action: str,
    status: str,
    payload: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> tuple[list[str], str]:
    p = {
        k: v
        for k, v in (payload or {}).items()
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

    if error_message:
        return _format_module(module, action, status, p), f"FAILED — {error_message[:200]}"

    if status == "blocked":
        return _format_module(module, action, status, p), "BLOCKED — interrupted by security software"

    lines = _format_module(module, action, status, p)
    return lines, _result_line(module, status, p)


def _result_line(module: str, status: str, p: dict) -> str:
    if status == "success":
        return _success_result(module, p)
    if status == "failed":
        return "FAILED — module did not complete"
    if status == "blocked":
        return "BLOCKED"
    return f"{status.upper()}"


def _format_module(module: str, action: str, status: str, p: dict) -> list[str]:
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
    lines = [f"Action: {action.replace('_', ' ')}"]
    lines.extend(fn(p))
    return lines


def _success_result(module: str, p: dict) -> str:
    results = {
        "screenshot": lambda: (
            f"OK — desktop captured ({p.get('width', '?')}×{p.get('height', '?')}, "
            f"{p.get('size_bytes', 0)} bytes)"
            if p.get("captured")
            else "OK — screenshot saved"
        ),
        "clipboard": lambda: f"OK — {p.get('chars_captured', 0)} chars from clipboard",
        "location": lambda: f"OK — {p.get('city', '?')}, {p.get('country', '?')} ({p.get('ip', '?')})",
        "crypto_hunt": lambda: f"OK — {p.get('wallets_found', 0)} wallet path(s) found",
        "keylogger": lambda: f"OK — {p.get('keys_captured', 0)} keys in {p.get('duration_seconds', '?')}s",
        "cookies": lambda: f"OK — {p.get('cookies_found', p.get('count', 0))} cookies read",
        "webcam": lambda: f"OK — webcam accessed via {p.get('method', p.get('backend', 'camera'))}",
    }
    if module in results:
        return results[module]()
    return "OK — completed"


def _defender(p: dict) -> list[str]:
    lines = [f"Elevated: {'yes' if p.get('elevated') else 'no'}"]
    for step in (p.get("attempts") or [])[:5]:
        lines.append(f"{step.get('step', '?')}: exit {step.get('code', '?')}")
    if p.get("warning"):
        lines.append(str(p["warning"])[:120])
    return lines


def _eicar(p: dict) -> list[str]:
    lines = []
    for f in p.get("files_written") or []:
        lines.append(f"Dropped: {_short_path(str(f))}")
    return lines or ["EICAR test file write attempted"]


def _self_copy(p: dict) -> list[str]:
    return [
        f"From: {_short_path(str(p.get('source', '?')))}",
        f"To: {_short_path(str(p.get('destination', '?')))}",
        f"Copy exists: {p.get('exists', False)}",
    ]


def _persistence(p: dict) -> list[str]:
    lines = [
        f"Hive: {p.get('hive', 'HKCU')}",
        f"Key: {p.get('key', 'Run')}",
        f"Value: {p.get('value_name', '?')} → {p.get('value_set', '?')}",
    ]
    if "cleaned_up" in p:
        lines.append(f"Cleaned up: {p['cleaned_up']}")
    return lines


def _process_injection(p: dict) -> list[str]:
    lines = []
    for step in (p.get("steps") or [])[:5]:
        lines.append(f"{step.get('stage', '?')}: {step}")
    return lines or ["Win32 injection APIs invoked"]


def _powershell(p: dict) -> list[str]:
    lines = [
        f"Encoded length: {p.get('encoded_command_length', '?')}",
        f"Exit code: {p.get('returncode', '?')}",
    ]
    if p.get("stdout_preview"):
        lines.append(f"Output: {str(p['stdout_preview'])[:100]}")
    return lines


def _keylogger(p: dict) -> list[str]:
    return [
        f"Method: {p.get('method', '?')}",
        f"Duration: {p.get('duration_seconds', '?')}s",
        f"Keys captured: {p.get('keys_captured', 0)}",
        f"Log: {_short_path(str(p.get('log_file', '?')))}",
    ]


def _screenshot(p: dict) -> list[str]:
    lines = [
        f"Method: {p.get('method', '?')}",
        f"File: {_short_path(str(p.get('file', '?')))}",
    ]
    if p.get("width") and p.get("height"):
        lines.append(f"Resolution: {p['width']}×{p['height']}")
    if p.get("size_bytes"):
        lines.append(f"Size: {p['size_bytes']} bytes")
    if p.get("image_base64"):
        lines.append("Image attached to Discord embed")
    return lines


def _clipboard(p: dict) -> list[str]:
    preview = p.get("preview") or "(empty)"
    if len(preview) > 100:
        preview = preview[:97] + "…"
    return [
        f"Chars: {p.get('chars_captured', 0)}",
        f"Preview: {preview!r}",
        f"Saved: {_short_path(str(p.get('file', '?')))}",
    ]


def _webcam(p: dict) -> list[str]:
    lines = [f"Method: {p.get('method', p.get('backend', '?'))}"]
    if p.get("device"):
        lines.append(f"Device: {p['device']}")
    if p.get("frames"):
        lines.append(f"Frames: {p['frames']}")
    if p.get("file"):
        lines.append(f"Output: {_short_path(str(p['file']))}")
    return lines


def _cookies(p: dict) -> list[str]:
    lines = [
        f"Browser: {p.get('browser', '?')}",
        f"Cookies: {p.get('cookies_found', p.get('count', 0))}",
    ]
    for c in (p.get("sample") or p.get("cookies") or [])[:5]:
        if isinstance(c, dict):
            lines.append(f"  · {c.get('name', '?')} @ {c.get('domain', '?')}")
    logins = p.get("login_databases") or []
    if logins:
        lines.append(f"Password DBs: {len(logins)}")
        for ldb in logins[:4]:
            if isinstance(ldb, dict):
                path = _short_path(str(ldb.get("path", "?")))
                lines.append(f"  · {ldb.get('browser', '?')}: {path}")
    return lines


def _file_read(p: dict) -> list[str]:
    files = p.get("files") or p.get("paths") or []
    lines = [f"Files read: {p.get('count', len(files))}"]
    for f in files[:4]:
        lines.append(f"  · {_short_path(str(f))}")
    return lines


def _crypto_hunt(p: dict) -> list[str]:
    lines = [f"Wallets found: {p.get('wallets_found', 0)}"]
    for hit in (p.get("hits") or [])[:5]:
        if isinstance(hit, dict):
            path = _short_path(str(hit.get("path", "?")))
            extra = f" ({hit['files']} items)" if hit.get("files") is not None else ""
            if hit.get("size") is not None:
                extra = f" ({hit['size']} bytes)"
            lines.append(f"  · {path}{extra}")
    return lines


def _location(p: dict) -> list[str]:
    return [
        f"IP: {p.get('ip', '?')}",
        f"City: {p.get('city', '?')}",
        f"Region: {p.get('region', '?')}",
        f"Country: {p.get('country', '?')}",
        f"Coords: {p.get('latitude', '?')}, {p.get('longitude', '?')}",
    ]


def _network(p: dict) -> list[str]:
    return [
        f"Host: {p.get('host', p.get('endpoint', '?'))}",
        f"Port: {p.get('port', '?')}",
        f"Bytes sent: {p.get('bytes_sent', 0)}",
        f"Response: {str(p.get('response_preview', '—'))[:80]}",
    ]


def _generic(p: dict) -> list[str]:
    if not p:
        return ["No payload details"]
    lines = []
    for k, v in list(p.items())[:6]:
        if isinstance(v, (list, dict)):
            lines.append(f"{k}: {str(v)[:80]}")
        else:
            lines.append(f"{k}: {v}")
    return lines
