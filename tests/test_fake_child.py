import os
import subprocess
import sys
import time

import pytest
from conftest import (
    FAKE_CHILD,
    FAKE_SITE,
    FakeChild,
    assert_child_gone,
    assert_child_reaped,
    assert_spawned_by,
    child_pid,
    kill_leftover_child,
)


@pytest.mark.timeout(30)
def test_shim_writes_pidfile_stderr_and_marker(fake_child: FakeChild):
    fake_child.set_env(FAKE_CHILD_SLEEP=0.2, FAKE_CHILD_STDERR="partial")
    proc = subprocess.Popen(
        [sys.executable, str(FAKE_CHILD), "-i", "in.mkv", "out.mp4"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert stdout == ""
    assert stderr == "partial"
    assert_spawned_by(fake_child.pidfile, proc)
    assert fake_child.marker.read_text(encoding="utf-8") == "done"


@pytest.mark.timeout(30)
def test_shim_stdout_bytes(fake_child: FakeChild):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDOUT_BYTES=70_000)
    result = subprocess.run(
        [sys.executable, str(FAKE_CHILD)],
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert result.stdout == b"x" * 70_000
    assert result.stderr == b""


@pytest.mark.timeout(30)
def test_shim_exit_code(fake_child: FakeChild):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDERR="boom", FAKE_CHILD_EXIT=3)
    result = subprocess.run([sys.executable, str(FAKE_CHILD)], capture_output=True, check=False, timeout=10)
    assert result.returncode == 3
    assert result.stderr == b"boom"
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
def test_sitecustomize_propagates_the_shim_exit_code(fake_child: FakeChild):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_EXIT=3, PYTHONPATH=str(FAKE_SITE))
    result = subprocess.run(
        [sys.executable, "-c", "print('unreachable')"], capture_output=True, check=False, timeout=10
    )
    assert result.returncode == 3
    assert result.stdout == b""


@pytest.mark.timeout(30)
def test_sitecustomize_runs_the_shim_before_the_script(fake_child: FakeChild):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDERR="hooked", PYTHONPATH=str(FAKE_SITE))
    proc = subprocess.Popen(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert stderr == "hooked"
    assert int(stdout) == assert_spawned_by(fake_child.pidfile, proc)
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
def test_liveness_helper_sees_pipe_blocked_child_and_teardown_kills_it(fake_child: FakeChild):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDOUT_BYTES=2_000_000)
    proc = subprocess.Popen(
        [sys.executable, str(FAKE_CHILD)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        pid = assert_spawned_by(fake_child.pidfile, proc)
        time.sleep(0.5)
        assert proc.poll() is None
        assert fake_child.alive()
        with pytest.raises(AssertionError, match="still alive"):
            assert_child_gone(fake_child.pidfile, fake_child.marker, 0, deadline_s=0.5)
        assert not fake_child.marker.exists()

        kill_leftover_child(fake_child.pidfile)
        proc.wait(timeout=10)
        assert_child_gone(fake_child.pidfile, fake_child.marker, 0)
        assert not fake_child.alive()
        if sys.platform != "win32":
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        proc.kill()
        proc.wait(timeout=10)
        assert proc.stdout is not None
        proc.stdout.close()


def test_assert_child_gone_fails_when_marker_was_written(fake_child: FakeChild):
    fake_child.set_env(FAKE_CHILD_SLEEP=0)
    subprocess.run([sys.executable, str(FAKE_CHILD)], check=True, capture_output=True, timeout=10)
    assert fake_child.marker.exists()
    with pytest.raises(AssertionError, match="on its own"):
        fake_child.assert_gone(sleep_s=0)


def test_child_killed_before_writing_its_pid_counts_as_gone(fake_child: FakeChild):
    assert_child_gone(fake_child.pidfile, fake_child.marker, 0, deadline_s=0.2)
    assert_child_reaped(fake_child.pidfile, deadline_s=0.2)
    fake_child.marker.write_text("done", encoding="utf-8")
    with pytest.raises(AssertionError, match="without ever writing its pid"):
        assert_child_gone(fake_child.pidfile, fake_child.marker, 0, deadline_s=0.2)


def test_child_pid_fails_without_pidfile(fake_child: FakeChild):
    with pytest.raises(AssertionError, match="never wrote"):
        child_pid(fake_child.pidfile, deadline_s=0.2)
