#!/usr/bin/env python3
"""Egress watchdog for Modal Sandboxes.

Run this as the Sandbox entrypoint with a budget in MiB (2048 MiB = 2 GiB):

    python3 /opt/in_sandbox_watchdog.py 2048

Every 2 seconds it sums the bytes sent on every non-loopback interface (from
/proc/net/dev). Once that exceeds the budget it exits with code 86. The Sandbox
finishes when its entrypoint exits, so the orchestrator sees
``sb.returncode == 86``.

Stdlib only, Python >= 3.9.
"""

from __future__ import annotations

import os
import sys
import time

EXIT_BUDGET_EXCEEDED = 86
INTERVAL_S = 2.0
PROC_NET_DEV = "/proc/net/dev"
MiB = 1024**2


def sum_tx_bytes(proc_net_dev_text: str) -> int:
    """Total bytes transmitted on every non-loopback interface listed in /proc/net/dev."""
    total = 0
    for line in proc_net_dev_text.splitlines()[2:]:  # two header lines
        name, _, data = line.partition(":")
        fields = data.split()
        if name.strip() != "lo" and len(fields) >= 9:  # 8 receive fields, then transmit bytes
            total += int(fields[8])
    return total


class NetDev:
    """Reads /proc/net/dev through a descriptor opened once at startup.

    Re-reading the same descriptor returns fresh counters, and a file later
    mounted over the path (e.g. by root inside the Sandbox) can't replace it.
    """

    def __init__(self, path: str = PROC_NET_DEV):
        self.fd = os.open(path, os.O_RDONLY)

    def tx_bytes(self) -> int:
        os.lseek(self.fd, 0, os.SEEK_SET)
        chunks = []
        while chunk := os.read(self.fd, 65536):
            chunks.append(chunk)
        return sum_tx_bytes(b"".join(chunks).decode())


def run(budget: int, proc_net_dev: str = PROC_NET_DEV, interval: float = INTERVAL_S) -> int:
    net_dev = NetDev(proc_net_dev)
    # The counters are cumulative and we start before any traffic, so they are the egress so far.
    while net_dev.tx_bytes() <= budget:
        time.sleep(interval)
    return EXIT_BUDGET_EXCEEDED


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].isdigit():
        sys.exit("usage: in_sandbox_watchdog.py BUDGET_MIB   (e.g. 2048 for 2 GiB)")
    return run(int(argv[0]) * MiB)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
