"""Local tests for in_sandbox_watchdog.py. Run: python3 -m unittest discover -s tests -v"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG = os.path.join(HERE, "..", "in_sandbox_watchdog.py")
sys.path.insert(0, os.path.join(HERE, ".."))

import in_sandbox_watchdog as wd

HEADER = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
)


def net_dev(eth0, lo=0, tunl0=0):
    """A /proc/net/dev-shaped text. Fixed-width, so every version has the same length."""
    return HEADER + "".join(
        f"{name:>6}: 999 1 0 0 0 0 0 0 {tx:>20} 1 0 0 0 0 0 0\n"
        for name, tx in (("lo", lo), ("eth0", eth0), ("tunl0", tunl0))
    )


def write_net_dev(path, eth0, lo=0):
    """Overwrite the file in place with one write, as /proc does: a pinned descriptor sees it."""
    mode = "r+" if os.path.exists(path) else "w"
    with open(path, mode) as f:
        f.write(net_dev(eth0, lo))


class ParseTests(unittest.TestCase):
    def test_sums_tx_column_of_non_loopback_interfaces(self):
        self.assertEqual(wd.sum_tx_bytes(net_dev(eth0=1000, lo=5, tunl0=20)), 1020)


class NetDevTests(unittest.TestCase):
    def test_pinned_fd_sees_in_place_updates_but_not_path_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "dev")
            write_net_dev(p, eth0=1)
            src = wd.NetDev(p)
            self.assertEqual(src.tx_bytes(), 1)
            write_net_dev(p, eth0=7)
            self.assertEqual(src.tx_bytes(), 7)
            with open(p + ".new", "w") as f:  # a different file at the path (like a bind mount over it)
                f.write(net_dev(eth0=0))
            os.replace(p + ".new", p)
            self.assertEqual(src.tx_bytes(), 7)

    @unittest.skipUnless(os.path.exists("/proc/net/dev"), "Linux only")
    def test_real_proc_net_dev_rereads(self):
        src = wd.NetDev()
        a = src.tx_bytes()
        self.assertGreaterEqual(src.tx_bytes(), a)


class RunTests(unittest.TestCase):
    """Runs the watchdog loop against a fake /proc/net/dev with a short interval."""

    def test_breach_returns_86(self):
        with tempfile.TemporaryDirectory() as d:
            dev = os.path.join(d, "dev")
            write_net_dev(dev, eth0=0)
            result = {}

            def target():
                result["code"] = wd.run(1_000_000, dev, interval=0.05)

            t = threading.Thread(target=target, daemon=True)
            t.start()
            write_net_dev(dev, eth0=999_999, lo=10**9)  # loopback ignored, at budget
            time.sleep(0.3)
            self.assertTrue(t.is_alive())
            write_net_dev(dev, eth0=1_000_001, lo=10**9)
            t.join(timeout=5)

        self.assertEqual(result["code"], 86)

    def test_cli_rejects_non_integer_budget(self):
        for bad in ([], ["50GiB"], ["1.5"], ["-1"], ["1", "2"]):
            p = subprocess.run([sys.executable, WATCHDOG, *bad], capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            self.assertIn("BUDGET_MIB", p.stderr)


if __name__ == "__main__":
    unittest.main()
