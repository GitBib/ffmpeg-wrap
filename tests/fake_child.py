from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    if os.environ.get("FAKE_CHILD_SPAWN"):
        env = {key: value for key, value in os.environ.items() if key not in ("FAKE_CHILD_SPAWN", "FAKE_CHILD_DETACH")}
        if os.environ.get("FAKE_CHILD_DETACH"):
            subprocess.Popen([sys.executable, __file__], env=env)
            return
        subprocess.run([sys.executable, __file__], env=env, check=False)
        return
    pidfile = os.environ.get("FAKE_CHILD_PIDFILE")
    if pidfile:
        Path(pidfile).write_text(f"{os.getpid()}\n{os.getppid()}", encoding="utf-8")
    stderr_text = os.environ.get("FAKE_CHILD_STDERR")
    if stderr_text:
        sys.stderr.buffer.write(stderr_text.encode("utf-8"))
        sys.stderr.buffer.flush()
    stdout_bytes = int(os.environ.get("FAKE_CHILD_STDOUT_BYTES", "0"))
    if stdout_bytes > 0:
        sys.stdout.buffer.write(b"x" * stdout_bytes)
        sys.stdout.buffer.flush()
    time.sleep(float(os.environ.get("FAKE_CHILD_SLEEP", "5")))
    marker = os.environ.get("FAKE_CHILD_MARKER")
    if marker:
        Path(marker).write_text("done", encoding="utf-8")
    exit_code = os.environ.get("FAKE_CHILD_EXIT")
    if exit_code:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(int(exit_code))


if __name__ == "__main__":
    main()
