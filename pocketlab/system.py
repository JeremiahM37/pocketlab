"""System-stats collection — CPU, memory, disk, temperature, uptime, load.

Local stats come from psutil. Remote stats come from a single portable
``/proc``-based shell probe run over ssh, so the remote host needs nothing
installed beyond a POSIX shell and the standard ``/proc`` + ``/sys`` files
(true on essentially any Linux box). Both paths return the same dict shape so
the frontend renders them identically.
"""

from __future__ import annotations

import os
import shlex
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psutil

from .config import Host
from .ssh import SSHError, run_text

# Pseudo-filesystems we never want to show as "disks".
_SKIP_FSTYPES = {"tmpfs", "devtmpfs", "squashfs", "overlay", "ramfs", "efivarfs"}


def _local_stats() -> dict[str, Any]:
    uname = os.uname()
    vm = psutil.virtual_memory()

    disks = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in _SKIP_FSTYPES or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append(
            {
                "mount": part.mountpoint,
                "total": usage.total,
                "used": usage.used,
                "percent": round(usage.percent, 1),
            }
        )

    temps = []
    try:
        for chip, entries in (psutil.sensors_temperatures() or {}).items():
            for e in entries:
                if e.current:
                    temps.append(
                        {"label": e.label or chip, "celsius": round(e.current, 1)}
                    )
    except (AttributeError, OSError):
        pass

    try:
        load = list(os.getloadavg())
    except OSError:
        load = [0.0, 0.0, 0.0]

    return {
        "online": True,
        "hostname": uname.nodename,
        "uptime": int(time.time() - psutil.boot_time()),
        "load": [round(x, 2) for x in load],
        "cpu_percent": round(psutil.cpu_percent(interval=0.3), 1),
        "cpu_count": psutil.cpu_count() or 0,
        "mem": {"total": vm.total, "used": vm.used, "percent": round(vm.percent, 1)},
        "disks": disks,
        "temps": temps,
    }


# A single shell command emitting labelled blocks we parse below. Kept to
# /proc + /sys so it works on any Linux host without extra tooling.
_PROBE = r"""
echo "===STAT1==="; head -1 /proc/stat
sleep 0.3
echo "===STAT2==="; head -1 /proc/stat
echo "===HOST==="; hostname
echo "===UPTIME==="; cat /proc/uptime
echo "===LOAD==="; cat /proc/loadavg
echo "===NCPU==="; nproc
echo "===MEM==="; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo
echo "===DF==="; df -kP -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null | tail -n +2
echo "===TEMP==="
for z in /sys/class/thermal/thermal_zone*; do
  t=$(cat "$z/type" 2>/dev/null); v=$(cat "$z/temp" 2>/dev/null)
  [ -n "$v" ] && echo "$t:$v"
done
true
"""


def parse_probe(out: str) -> dict[str, Any]:
    """Parse the /proc probe output into the standard stats dict."""
    blocks: dict[str, list[str]] = {}
    current = None
    for line in out.splitlines():
        if line.startswith("===") and line.endswith("==="):
            current = line.strip("=")
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)

    def _cpu_times(line: str) -> list[int]:
        # "cpu  user nice system idle iowait irq softirq steal ..."
        return [int(x) for x in line.split()[1:]]

    cpu_percent = 0.0
    try:
        t1 = _cpu_times(blocks["STAT1"][0])
        t2 = _cpu_times(blocks["STAT2"][0])
        idle1, idle2 = t1[3] + t1[4], t2[3] + t2[4]  # idle + iowait
        total1, total2 = sum(t1), sum(t2)
        dt, di = total2 - total1, idle2 - idle1
        if dt > 0:
            cpu_percent = round(100.0 * (1 - di / dt), 1)
    except (KeyError, IndexError, ValueError):
        pass

    hostname = (blocks.get("HOST") or ["?"])[0].strip()

    try:
        uptime = int(float(blocks["UPTIME"][0].split()[0]))
    except (KeyError, IndexError, ValueError):
        uptime = 0

    try:
        load = [round(float(x), 2) for x in blocks["LOAD"][0].split()[:3]]
    except (KeyError, IndexError, ValueError):
        load = [0.0, 0.0, 0.0]

    try:
        cpu_count = int(blocks["NCPU"][0])
    except (KeyError, IndexError, ValueError):
        cpu_count = 0

    mem_total = mem_avail = 0
    for line in blocks.get("MEM", []):
        if line.startswith("MemTotal:"):
            mem_total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            mem_avail = int(line.split()[1]) * 1024
    mem_used = max(mem_total - mem_avail, 0)
    mem_pct = round(100.0 * mem_used / mem_total, 1) if mem_total else 0.0

    disks = []
    for line in blocks.get("DF", []):
        parts = line.split()
        if len(parts) < 6:
            continue
        # Filesystem 1024-blocks Used Available Capacity Mounted-on
        try:
            total = int(parts[1]) * 1024
            used = int(parts[2]) * 1024
        except ValueError:
            continue
        disks.append(
            {
                "mount": parts[5],
                "total": total,
                "used": used,
                "percent": round(100.0 * used / total, 1) if total else 0.0,
            }
        )

    temps = []
    for line in blocks.get("TEMP", []):
        label, _, raw = line.partition(":")
        try:
            temps.append({"label": label or "temp", "celsius": round(int(raw) / 1000.0, 1)})
        except ValueError:
            continue

    return {
        "online": True,
        "hostname": hostname,
        "uptime": uptime,
        "load": load,
        "cpu_percent": cpu_percent,
        "cpu_count": cpu_count,
        "mem": {"total": mem_total, "used": mem_used, "percent": mem_pct},
        "disks": disks,
        "temps": temps,
    }


def host_stats(host: Host) -> dict[str, Any]:
    """Collect stats for one configured host. Never raises — returns an
    ``{"online": False, "error": ...}`` payload on failure so one dead host
    doesn't blank the whole dashboard."""
    result: dict[str, Any] = {"host": host.name}
    try:
        if host.ssh_target:
            # shlex.quote, not manual '…' wrapping: the probe itself contains
            # single quotes (the meminfo grep), which would otherwise break the
            # remote shell's parse of `sh -c '...'`.
            out = run_text(host.ssh_target, f"sh -c {shlex.quote(_PROBE)}")
            result.update(parse_probe(out))
        else:
            result.update(_local_stats())
    except (SSHError, OSError, ValueError) as exc:
        result.update({"online": False, "error": str(exc)})
    return result


def all_stats(hosts: list[Host]) -> list[dict[str, Any]]:
    """Collect stats for all hosts concurrently.

    Each ssh probe can take seconds (up to ssh.DEFAULT_COMMAND_TIMEOUT); probing hosts in
    parallel makes total latency that of the slowest host, not the sum. Order
    of results matches the configured host order.
    """
    if not hosts:
        return []
    if len(hosts) == 1:
        return [host_stats(hosts[0])]
    with ThreadPoolExecutor(max_workers=min(len(hosts), 16)) as pool:
        return list(pool.map(host_stats, hosts))
