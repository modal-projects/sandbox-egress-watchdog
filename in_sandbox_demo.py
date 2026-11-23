"""Run a Modal Sandbox with the egress watchdog as its entrypoint.

What it does:
  1. Builds an image with the watchdog at /opt/in_sandbox_watchdog.py and a non-root
     user `app` (uid 1000).
  2. Starts a "sink" Sandbox that accepts TCP on a public tunnel and counts the
     bytes it receives (stands in for any internet destination).
  3. Starts the watched Sandbox with a small budget, then execs an upload
     workload *as uid 1000* that streams to the sink without stopping.
  4. Waits for the watched Sandbox and reports why it ended.

    pip install modal
    python in_sandbox_demo.py --budget-mib 200

Exit code 86 means the watchdog ended the Sandbox for exceeding its budget.
"""

from __future__ import annotations

import argparse
import pathlib
import time

import modal

HERE = pathlib.Path(__file__).parent
WATCHDOG_PATH = "/opt/in_sandbox_watchdog.py"
EXIT_BUDGET_EXCEEDED = 86

# Exec workloads through this prefix so they run as uid 1000 with no way to regain privileges.
AS_APP_USER = ["setpriv", "--reuid=1000", "--regid=1000", "--init-groups", "--no-new-privs", "--"]

# Counts every byte received on port 9000 and keeps the running total in /tmp/rx_total.
SINK_CODE = r"""
import os, socket, threading, time
total = 0
def handle(conn):
    global total
    while b := conn.recv(1 << 20):
        total += len(b)
def flush():
    while True:
        with open("/tmp/rx_total.tmp", "w") as f:
            f.write(str(total))
        os.replace("/tmp/rx_total.tmp", "/tmp/rx_total")
        time.sleep(0.5)
threading.Thread(target=flush, daemon=True).start()
s = socket.create_server(("0.0.0.0", 9000))
while True:
    threading.Thread(target=handle, args=(s.accept()[0],), daemon=True).start()
"""

# Sends incompressible data to host:port until the Sandbox is stopped.
UPLOAD_CODE = r"""
import os, socket, sys
chunk = os.urandom(1 << 20)
s = socket.create_connection((sys.argv[1], int(sys.argv[2])))
while True:
    s.sendall(chunk)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-mib", type=int, default=200, help="Lifetime egress budget in MiB (default 200)")
    args = ap.parse_args()

    app = modal.App.lookup("egress-watchdog-demo", create_if_missing=True)
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .run_commands("useradd --uid 1000 --create-home app")
        .add_local_file(HERE / "in_sandbox_watchdog.py", WATCHDOG_PATH, copy=True)
    )

    sink = modal.Sandbox.create("python3", "-c", SINK_CODE, app=app, image=image, unencrypted_ports=[9000],
                                timeout=1800)
    host, port = sink.tunnels()[9000].tcp_socket
    print(f"sink:     {sink.object_id} at {host}:{port}")

    sb = None
    try:
        # The watchdog is the entrypoint: when it exits, the Sandbox ends with its exit code.
        sb = modal.Sandbox.create("python3", WATCHDOG_PATH, str(args.budget_mib), app=app, image=image,
                                  timeout=1800)
        print(f"watched:  {sb.object_id} (budget {args.budget_mib} MiB)")

        # Untrusted workload, as uid 1000. It would run forever; the watchdog stops it.
        sb.exec(*AS_APP_USER, "python3", "-c", UPLOAD_CODE, host, str(port))
        t0 = time.monotonic()
        sb.wait(raise_on_termination=False)
        print(f"returncode: {sb.returncode} after {time.monotonic() - t0:.1f}s")

        if sb.returncode == EXIT_BUDGET_EXCEEDED:
            print("-> egress budget exceeded")
        else:
            print("-> sandbox ended for another reason")

        time.sleep(1.5)  # let the sink flush its counter
        p = sink.exec("cat", "/tmp/rx_total")
        print(f"sink received {int(p.stdout.read().strip() or 0):,} bytes")
    finally:
        # Also stop the watched Sandbox in case something failed before the watchdog ended it.
        if sb is not None:
            sb.terminate()
        sink.terminate()


if __name__ == "__main__":
    main()
