# Egress budgets for Modal Sandboxes

A watchdog that ends a Sandbox once it has sent more than an egress budget.
It runs as the Sandbox's entrypoint and every 2 s reads the kernel's transmit
counters from `/proc/net/dev` (every interface except loopback). Over budget,
it exits with code 86, which ends the Sandbox.

- `in_sandbox_watchdog.py`: the watchdog (stdlib only, Python >= 3.9)
- `in_sandbox_demo.py`: an end-to-end demo on Modal

This is a demo of the pattern, not a feature-complete tool.

## Untrusted code must run as a non-root user

The watchdog measures from inside the Sandbox, so root there can defeat it,
for example by freezing it with `kill -STOP` or `ptrace`. As a regular
user, every attempt to stop, edit or bypass the watchdog failed with "operation
not permitted" in our tests on gVisor and the VM runtime.

To do this, create a regular user in the image and start untrusted commands
as that user with `setpriv`:

```python
sb.exec("setpriv", "--reuid=1000", "--regid=1000", "--init-groups", "--no-new-privs", "--",
        "bash", "-c", user_command)
```

If part of your stack needs root, start it as root and drop to the regular user
for anything that runs user-supplied code.

## Using the watchdog

1. Add the script and a regular user to your image (any image with `python3`):

   ```python
   image = (
       modal.Image.debian_slim()
       .run_commands("useradd --uid 1000 --create-home app")
       .add_local_file("in_sandbox_watchdog.py", "/opt/in_sandbox_watchdog.py", copy=True)
   )
   ```

2. Make it the entrypoint, with the budget in MiB:

   ```python
   sb = modal.Sandbox.create("python3", "/opt/in_sandbox_watchdog.py", "2048",  # 2 GiB
                             app=app, image=image,
                             timeout=3600)  # seconds; the default is 5 minutes
   ```

3. Run untrusted work as the regular user (above).

4. Check the exit code:

   ```python
   sb.wait(raise_on_termination=False)
   if sb.returncode == 86:
       ...  # egress budget exceeded
   ```

Data keeps flowing until the next check, so a Sandbox will send somewhat more
than its budget before it ends. For small budgets, lower `INTERVAL_S` in the
script.

## Running the demo

The demo starts a "sink" Sandbox to receive traffic and a Sandbox that uploads
to it without stopping as the regular user, then reports how it was stopped.

```bash
pip install modal && modal setup
python in_sandbox_demo.py --budget-mib 200   # ends with returncode 86
```

Unit tests (no Modal needed): `python3 -m unittest discover -s tests -v`.
