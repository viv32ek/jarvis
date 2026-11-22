"""100% free local Linux homelab status & weather helpers (zero external deps)."""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request


def get_system_status() -> str:
    """Return a concise summary of CPU load, disk space, and running Docker containers."""
    lines: list[str] = []

    try:
        load1, load5, load15 = os.getloadavg()
        lines.append(f"- Load avg: {load1:.2f}, {load5:.2f}, {load15:.2f}")
    except OSError:
        pass

    usage = shutil.disk_usage("/")
    used_gb = usage.used / (1024**3)
    total_gb = usage.total / (1024**3)
    pct = (usage.used / usage.total) * 100
    lines.append(f"- Disk (/): {used_gb:.1f} / {total_gb:.1f} GB ({pct:.0f}% used)")

    if shutil.which("docker"):
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}} ({{.Status}})"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            containers = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if containers:
                lines.append(f"- Docker ({len(containers)} running): " + ", ".join(containers[:10]))
            else:
                lines.append("- Docker: 0 running containers")

    return "\n".join(lines)


def get_weather(location: str = "") -> str:
    """Fetch a concise free weather string from wttr.in (zero API key)."""
    loc = location.strip().replace(" ", "+")
    url = f"https://wttr.in/{loc}?format=3"
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-homelab/0.1"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read().decode("utf-8", errors="replace").strip()
