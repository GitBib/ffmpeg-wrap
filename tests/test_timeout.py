import io
import logging
import math
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest
from conftest import FakeChild

import ffmpeg_wrap as ffmpeg
from ffmpeg_wrap import _builder, _probe
from ffmpeg_wrap._builder import FFmpeg
from ffmpeg_wrap._errors import FFmpegError, FFmpegTimeoutError
from ffmpeg_wrap._probe import _TIMEOUT_MAX_SECONDS, _build_probe_cmd, _build_validate_cmd, probe, validate


@pytest.fixture
def lavfi_wav(ffmpeg_available: None, tmp_path: Path) -> Path:
    out = tmp_path / "tone.wav"
    ffmpeg.input("anullsrc=r=48000:cl=mono", f="lavfi", t=0.2).output(str(out)).overwrite_output().run(
        capture_stderr=True
    )
    return out


def _fake_popen(monkeypatch: pytest.MonkeyPatch, process: MagicMock) -> MagicMock:
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(_builder.subprocess, "Popen", popen)
    return popen


def _fake_process(*, stderr: bytes = b"", stdout: object = None, returncode: int = 0) -> MagicMock:
    process = MagicMock()
    process.stderr = io.BytesIO(stderr)
    process.stdout = stdout
    process.returncode = returncode
    process.wait = MagicMock()
    return process


@pytest.mark.timeout(30)
@pytest.mark.parametrize("text", [False, True])
def test_capture_path_times_out_and_kills_child(fake_child: FakeChild, text: bool):
    builder = fake_child.make(FAKE_CHILD_STDERR="partial")
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(capture_stderr=True, text=text, timeout=0.5)
    error = exc_info.value
    assert error.timeout == 0.5
    assert error.cmd == builder.compile()
    assert error.returncode is None
    assert isinstance(error.stderr, str)
    assert "partial" in error.stderr
    assert str(error) == "ffmpeg timed out after 0.5s"
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
def test_tee_path_silent_child_capture_stdout_times_out(fake_child: FakeChild):
    builder = fake_child.make()
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(capture_stdout=True, timeout=0.5)
    error = exc_info.value
    assert error.timeout == 0.5
    assert error.cmd == builder.compile()
    assert error.returncode is None
    assert error.stderr is None
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
def test_tee_path_producing_child_capture_stdout_times_out(fake_child: FakeChild):
    builder = fake_child.make(FAKE_CHILD_STDOUT_BYTES=2_000_000)
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(capture_stdout=True, timeout=0.5)
    assert exc_info.value.timeout == 0.5
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("text", [False, True])
def test_tee_path_without_capture_stdout_times_out(fake_child: FakeChild, text: bool):
    builder = fake_child.make(FAKE_CHILD_STDERR="partial")
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(text=text, timeout=0.5)
    error = exc_info.value
    assert isinstance(error.stderr, str)
    assert "partial" in error.stderr
    assert error.returncode is None
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
def test_tee_path_raises_while_grandchild_still_holds_stderr(fake_child: FakeChild, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(FFmpeg, "_TIMEOUT_JOIN_SECONDS", 0.2)
    builder = fake_child.make(FAKE_CHILD_SPAWN=1, FAKE_CHILD_SLEEP=3)
    started = time.monotonic()
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(timeout=0.5)
    assert time.monotonic() - started < 2
    assert exc_info.value.timeout == 0.5
    assert fake_child.alive()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stdout", [False, True])
def test_tee_path_raises_when_child_exits_but_detached_grandchild_holds_pipes(
    fake_child: FakeChild, capture_stdout: bool
):
    builder = fake_child.make(FAKE_CHILD_SPAWN=1, FAKE_CHILD_DETACH=1, FAKE_CHILD_SLEEP=3)
    started = time.monotonic()
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(capture_stdout=capture_stdout, timeout=0.5)
    assert time.monotonic() - started < 2.5
    assert exc_info.value.timeout == 0.5
    assert fake_child.alive()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stdout", [False, True])
def test_tee_path_without_timeout_waits_for_detached_grandchild(fake_child: FakeChild, capture_stdout: bool):
    builder = fake_child.make(FAKE_CHILD_SPAWN=1, FAKE_CHILD_DETACH=1, FAKE_CHILD_SLEEP=1, FAKE_CHILD_STDOUT_BYTES=7)
    result = builder.run(capture_stdout=capture_stdout)
    assert result == ((b"x" * 7 if capture_stdout else None), None)
    assert not fake_child.alive()


def test_tee_path_timeout_raises_after_bounded_join_when_drain_never_finishes(monkeypatch: pytest.MonkeyPatch):
    release = threading.Event()

    class _StuckStderr:
        def read1(self, _size):
            release.wait()
            return b""

        def close(self):
            pass

    process = _fake_process()
    process.stderr = _StuckStderr()
    process.wait = MagicMock(side_effect=[subprocess.TimeoutExpired(["ffmpeg"], 0.5), None])
    _fake_popen(monkeypatch, process)
    monkeypatch.setattr(FFmpeg, "_TIMEOUT_JOIN_SECONDS", 0.2)
    started = time.monotonic()
    try:
        with pytest.raises(FFmpegTimeoutError) as exc_info:
            FFmpeg().input("in.mkv").output("out.mp4").run(timeout=0.5)
    finally:
        release.set()
    assert time.monotonic() - started < 2
    assert exc_info.value.stderr is None
    process.kill.assert_called_once_with()


def test_tee_path_timeout_joins_both_drains_within_one_bound(monkeypatch: pytest.MonkeyPatch):
    release = threading.Event()

    class _StuckPipe:
        def read1(self, _size):
            release.wait()
            return b""

        def close(self):
            pass

    process = _fake_process(stdout=_StuckPipe())
    process.stderr = _StuckPipe()
    process.wait = MagicMock(side_effect=[subprocess.TimeoutExpired(["ffmpeg"], 0.5), None])
    _fake_popen(monkeypatch, process)
    monkeypatch.setattr(FFmpeg, "_TIMEOUT_JOIN_SECONDS", 0.6)
    started = time.monotonic()
    try:
        with pytest.raises(FFmpegTimeoutError):
            FFmpeg().input("in.mkv").output("pipe:").run(capture_stdout=True, timeout=0.5)
    finally:
        release.set()
    assert time.monotonic() - started < 1.0
    process.kill.assert_called_once_with()


def test_tee_path_drain_failure_is_raised_even_when_the_timeout_expired(monkeypatch: pytest.MonkeyPatch):
    class _FailingStdout:
        def read1(self, _size):
            raise MemoryError("simulated allocation failure")

        def close(self):
            pass

    process = _fake_process(stderr=b"tail", stdout=_FailingStdout())
    process.wait = MagicMock(side_effect=[subprocess.TimeoutExpired(["ffmpeg"], 0.5), None])
    _fake_popen(monkeypatch, process)
    with pytest.raises(MemoryError, match="simulated allocation failure"):
        FFmpeg().input("in.mkv").output("pipe:").run(capture_stdout=True, timeout=0.5)
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stdout", [False, True])
@pytest.mark.parametrize("capture_stderr", [False, True])
def test_child_failure_within_timeout_is_not_a_timeout(
    fake_child: FakeChild, capture_stdout: bool, capture_stderr: bool
):
    builder = fake_child.make(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDERR="boom", FAKE_CHILD_EXIT=3)
    with pytest.raises(FFmpegError) as exc_info:
        builder.run(capture_stdout=capture_stdout, capture_stderr=capture_stderr, text=True, timeout=30)
    error = exc_info.value
    assert not isinstance(error, FFmpegTimeoutError)
    assert error.returncode == 3
    assert error.stderr == "boom"
    assert error.cmd == builder.compile()
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [None, 30])
@pytest.mark.parametrize("text", [False, True])
def test_tee_path_success_with_real_pipe_returns_all_stdout(fake_child: FakeChild, timeout, text: bool):
    builder = fake_child.make(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDOUT_BYTES=2_000_000)
    stdout, stderr = builder.run(capture_stdout=True, text=text, timeout=timeout)
    assert stderr is None
    assert isinstance(stdout, str if text else bytes)
    assert len(stdout) == 2_000_000
    assert fake_child.marker.exists()


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, RuntimeError])
def test_tee_path_kills_child_when_wait_is_interrupted(monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]):
    process = _fake_process(stderr=b"tail")
    process.wait = MagicMock(side_effect=[interrupt(), None])
    _fake_popen(monkeypatch, process)
    with pytest.raises(interrupt):
        FFmpeg().input("in.mkv").output("out.mp4").run(timeout=5)
    process.kill.assert_called_once_with()
    assert process.wait.call_args_list == [call(timeout=5), call()]


class _LateStdout:
    def __init__(self, delay: float):
        self._chunks = [(0.0, b"early "), (delay, b"late")]

    def read1(self, _size):
        if not self._chunks:
            return b""
        delay, chunk = self._chunks.pop(0)
        time.sleep(delay)
        return chunk

    def close(self):
        pass


def test_tee_success_path_joins_stdout_drain_before_returning(monkeypatch: pytest.MonkeyPatch):
    process = _fake_process(stdout=_LateStdout(0.3))
    _fake_popen(monkeypatch, process)
    stdout, stderr = FFmpeg().input("in.mkv").output("pipe:").run(capture_stdout=True)
    assert stdout == b"early late"
    assert stderr is None
    process.wait.assert_called_once_with(timeout=None)


def test_tee_success_path_collects_late_chunk_after_deadline_within_grace(monkeypatch: pytest.MonkeyPatch):
    process = _fake_process(stdout=_LateStdout(0.3))
    _fake_popen(monkeypatch, process)
    stdout, stderr = FFmpeg().input("in.mkv").output("pipe:").run(capture_stdout=True, timeout=0.05)
    assert stdout == b"early late"
    assert stderr is None
    process.wait.assert_called_once_with(timeout=0.05)
    process.kill.assert_not_called()


def test_tee_success_path_raises_timeout_when_drain_outlives_deadline_and_grace(monkeypatch: pytest.MonkeyPatch):
    process = _fake_process(stdout=_LateStdout(1.0))
    _fake_popen(monkeypatch, process)
    monkeypatch.setattr(FFmpeg, "_DRAIN_GRACE_SECONDS", 0.1)
    started = time.monotonic()
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        FFmpeg().input("in.mkv").output("pipe:").run(capture_stdout=True, timeout=0.05)
    assert time.monotonic() - started < 0.9
    assert exc_info.value.timeout == 0.05
    assert exc_info.value.stderr is None


@pytest.mark.timeout(30)
@pytest.mark.parametrize("failing_start", [1, 2])
def test_tee_path_kills_child_when_drain_thread_fails_to_start(
    fake_child: FakeChild, monkeypatch: pytest.MonkeyPatch, failing_start: int
):
    real_popen = subprocess.Popen
    real_start = threading.Thread.start
    spawned: list[subprocess.Popen[bytes]] = []
    starts = 0

    def _popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    def _start(self):
        nonlocal starts
        starts += 1
        if starts == failing_start:
            raise RuntimeError("can't start new thread")
        real_start(self)

    monkeypatch.setattr(_builder.subprocess, "Popen", _popen)
    monkeypatch.setattr(threading.Thread, "start", _start)
    builder = fake_child.make(FAKE_CHILD_SLEEP=5, FAKE_CHILD_STDOUT_BYTES=10)
    with pytest.raises(RuntimeError, match="can't start new thread"):
        builder.run(capture_stdout=True)
    (process,) = spawned
    assert process.returncode is not None
    assert process.poll() is not None
    assert process.stdout is not None and process.stdout.closed
    deadline = time.monotonic() + 5
    while not process.stderr.closed and time.monotonic() < deadline:
        time.sleep(0.05)
    assert process.stderr.closed


@pytest.mark.timeout(30)
def test_tee_path_drain_failure_closes_pipe_and_reraises(fake_child: FakeChild, monkeypatch: pytest.MonkeyPatch):
    real_popen = subprocess.Popen
    closed = threading.Event()

    class _FailingStdout:
        def __init__(self, stream):
            self._stream = stream
            self._reads = 0

        def read1(self, size):
            self._reads += 1
            if self._reads > 1:
                raise MemoryError("simulated allocation failure")
            return self._stream.read1(size)

        def close(self):
            self._stream.close()
            closed.set()

    def _popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        process.stdout = _FailingStdout(process.stdout)
        return process

    monkeypatch.setattr(_builder.subprocess, "Popen", _popen)
    builder = fake_child.make(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDOUT_BYTES=2_000_000)
    with pytest.raises(MemoryError, match="simulated allocation failure"):
        builder.run(capture_stdout=True)
    assert closed.is_set()
    fake_child.assert_gone()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [None, 30, _TIMEOUT_MAX_SECONDS])
def test_capture_path_short_child_succeeds_with_timeout(fake_child: FakeChild, timeout):
    builder = fake_child.make(FAKE_CHILD_SLEEP=0.1, FAKE_CHILD_STDERR="ok")
    assert builder.run(capture_stderr=True, timeout=timeout) == (None, b"ok")
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [None, 30, _TIMEOUT_MAX_SECONDS])
def test_tee_path_short_child_succeeds_with_timeout(fake_child: FakeChild, timeout):
    builder = fake_child.make(FAKE_CHILD_SLEEP=0.1, FAKE_CHILD_STDOUT_BYTES=10)
    assert builder.run(timeout=timeout) == (None, None)
    assert builder.run(capture_stdout=True, timeout=timeout) == (b"x" * 10, None)
    assert fake_child.marker.exists()


@pytest.mark.parametrize(
    "timeout", [0, -1, 0.0, -0.5, math.nan, math.inf, -math.inf, _TIMEOUT_MAX_SECONDS + 1, 1e20, 10**400]
)
@pytest.mark.parametrize("capture_stderr", [False, True])
def test_invalid_timeout_raises_before_spawn(timeout, capture_stderr: bool):
    builder = FFmpeg(ffmpeg_path="/nonexistent/ffmpeg-for-timeout-test").input("in.mkv").output("out.mp4")
    with pytest.raises(ValueError, match="timeout must be"):
        builder.run(capture_stderr=capture_stderr, timeout=timeout)


def _run_raising_timeout(monkeypatch: pytest.MonkeyPatch, stderr) -> subprocess.TimeoutExpired:
    expired = subprocess.TimeoutExpired(["ffmpeg"], 0.5, stderr=stderr)
    monkeypatch.setattr(_builder.subprocess, "run", MagicMock(side_effect=expired))
    return expired


@pytest.mark.parametrize(
    ("text", "raw", "expected"),
    [
        (True, b"tail\r\nx", "tail\nx"),
        (False, b"tail\r\nx", "tail\r\nx"),
        (True, "already text", "already text"),
        (False, "already text", "already text"),
        (True, None, None),
        (False, None, None),
    ],
)
def test_capture_path_maps_timeout_expired(monkeypatch: pytest.MonkeyPatch, text: bool, raw, expected):
    expired = _run_raising_timeout(monkeypatch, raw)
    builder = FFmpeg().input("in.mkv").output("out.mp4")
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(capture_stderr=True, text=text, timeout=0.5)
    error = exc_info.value
    assert error.stderr == expected
    assert error.timeout == 0.5
    assert error.returncode is None
    assert error.cmd == ["ffmpeg", "-i", "in.mkv", "out.mp4"]
    assert error.__cause__ is expired


@pytest.mark.parametrize(("text", "expected"), [(True, "tail\nx"), (False, "tail\r\nx")])
def test_tee_path_maps_timeout_expired(monkeypatch: pytest.MonkeyPatch, text: bool, expected: str):
    expired = subprocess.TimeoutExpired(["ffmpeg"], 0.5)
    process = _fake_process(stderr=b"tail\r\nx", stdout=io.BytesIO(b"partial out"))
    process.wait = MagicMock(side_effect=[expired, None])
    _fake_popen(monkeypatch, process)
    builder = FFmpeg().input("in.mkv").output("pipe:")
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(capture_stdout=True, text=text, timeout=0.5)
    error = exc_info.value
    assert error.stderr == expected
    assert error.timeout == 0.5
    assert error.returncode is None
    assert error.cmd == ["ffmpeg", "-i", "in.mkv", "pipe:"]
    assert isinstance(error.__cause__, subprocess.TimeoutExpired)
    process.kill.assert_called_once_with()
    assert process.wait.call_args_list[0].kwargs == {"timeout": 0.5}
    assert process.wait.call_count == 2


def test_tee_path_timeout_with_empty_stderr_tail_gives_none(monkeypatch: pytest.MonkeyPatch):
    process = _fake_process()
    process.wait = MagicMock(side_effect=[subprocess.TimeoutExpired(["ffmpeg"], 1), None])
    _fake_popen(monkeypatch, process)
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        FFmpeg().input("in.mkv").output("out.mp4").run(timeout=1)
    assert exc_info.value.stderr is None


def test_timeout_is_caught_by_except_ffmpeg_error(monkeypatch: pytest.MonkeyPatch):
    _run_raising_timeout(monkeypatch, b"x")
    builder = FFmpeg().input("in.mkv").output("out.mp4")
    with pytest.raises(FFmpegError) as exc_info:
        builder.run(capture_stderr=True, timeout=0.5)
    assert isinstance(exc_info.value, ffmpeg.FFmpegTimeoutError)


def test_timeout_is_logged_at_error_level(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    _run_raising_timeout(monkeypatch, b"boom")
    builder = FFmpeg().input("in.mkv").output("out.mp4")
    with caplog.at_level(logging.ERROR, logger="ffmpeg_wrap"), pytest.raises(FFmpegTimeoutError):
        builder.run(capture_stderr=True, timeout=0.5)
    records = [r for r in caplog.records if r.name == "ffmpeg_wrap" and r.levelno == logging.ERROR]
    assert len(records) == 1
    assert records[0].getMessage() == "FFmpeg command timed out after 0.5s: boom"


def test_run_signature_has_keyword_only_timeout():
    import inspect

    param = inspect.signature(FFmpeg.run).parameters["timeout"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


@pytest.mark.timeout(30)
def test_probe_times_out_and_kills_fake_ffprobe(fake_child: FakeChild, fake_ffprobe: str):
    fake_child.set_env(FAKE_CHILD_STDERR="partial")
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        probe("x.wav", ffprobe_path=fake_ffprobe, timeout=0.5)
    error = exc_info.value
    assert error.timeout == 0.5
    assert error.cmd == _build_probe_cmd("x.wav", fake_ffprobe)
    assert error.returncode is None
    assert isinstance(error.stderr, str)
    assert "partial" in error.stderr
    assert str(error) == "ffprobe timed out after 0.5s"
    assert isinstance(error.__cause__, subprocess.TimeoutExpired)
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
def test_validate_times_out_and_kills_fake_ffprobe(fake_child: FakeChild, fake_ffprobe: str):
    fake_child.set_env(FAKE_CHILD_STDERR="partial")
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        validate("x.wav", ffprobe_path=fake_ffprobe, timeout=0.5)
    error = exc_info.value
    assert error.timeout == 0.5
    assert error.cmd == _build_validate_cmd("x.wav", fake_ffprobe)
    assert error.returncode is None
    assert isinstance(error.stderr, str)
    assert "partial" in error.stderr
    assert str(error) == "ffprobe timed out after 0.5s"
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [None, 30])
def test_fake_ffprobe_short_child_is_not_killed(fake_child: FakeChild, fake_ffprobe: str, timeout):
    fake_child.set_env(FAKE_CHILD_SLEEP=0.1)
    ok, stderr = validate("x.wav", ffprobe_path=fake_ffprobe, timeout=timeout)
    assert ok is False
    assert isinstance(stderr, str)
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
def test_probe_child_failure_within_timeout_is_not_a_timeout(fake_child: FakeChild, fake_ffprobe: str):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDERR="boom", FAKE_CHILD_EXIT=3)
    with pytest.raises(FFmpegError) as exc_info:
        probe("x.wav", ffprobe_path=fake_ffprobe, timeout=30)
    error = exc_info.value
    assert not isinstance(error, FFmpegTimeoutError)
    assert error.returncode == 3
    assert error.stderr is not None
    assert "boom" in error.stderr
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
def test_validate_child_failure_within_timeout_is_not_a_timeout(fake_child: FakeChild, fake_ffprobe: str):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDERR="boom", FAKE_CHILD_EXIT=3)
    ok, stderr = validate("x.wav", ffprobe_path=fake_ffprobe, timeout=30)
    assert ok is False
    assert "boom" in stderr
    assert fake_child.marker.exists()


def _probe_run_raising_timeout(monkeypatch: pytest.MonkeyPatch, stderr) -> subprocess.TimeoutExpired:
    expired = subprocess.TimeoutExpired(["ffprobe"], 0.5, stderr=stderr)
    monkeypatch.setattr(_probe.subprocess, "run", MagicMock(side_effect=expired))
    return expired


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(b"tail\r\nx", "tail\r\nx"), ("already text", "already text"), (None, None)],
)
@pytest.mark.parametrize("call", [probe, validate])
def test_ffprobe_maps_timeout_expired(monkeypatch: pytest.MonkeyPatch, call, raw, expected):
    expired = _probe_run_raising_timeout(monkeypatch, raw)
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        call("in.wav", timeout=0.5)
    error = exc_info.value
    assert str(error) == "ffprobe timed out after 0.5s"
    assert error.stderr == expected
    assert error.timeout == 0.5
    assert error.returncode is None
    assert error.cmd is not None
    assert error.cmd[0] == "ffprobe"
    assert error.__cause__ is expired


@pytest.mark.parametrize("call", [probe, validate])
def test_ffprobe_passes_timeout_to_subprocess_run(monkeypatch: pytest.MonkeyPatch, call):
    run = MagicMock(
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=b'{"streams": []}', stderr=b"")
    )
    monkeypatch.setattr(_probe.subprocess, "run", run)
    call("in.wav", timeout=2.5)
    assert run.call_args.kwargs["timeout"] == 2.5
    call("in.wav")
    assert run.call_args.kwargs["timeout"] is None


@pytest.mark.parametrize("call", [probe, validate])
def test_ffprobe_timeout_is_caught_by_except_ffmpeg_error(monkeypatch: pytest.MonkeyPatch, call):
    _probe_run_raising_timeout(monkeypatch, b"x")
    with pytest.raises(FFmpegError) as exc_info:
        call("in.wav", timeout=0.5)
    assert isinstance(exc_info.value, FFmpegTimeoutError)


@pytest.mark.parametrize("call", [probe, validate])
def test_ffprobe_timeout_is_logged_at_error_level(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, call
):
    _probe_run_raising_timeout(monkeypatch, b"boom")
    with caplog.at_level(logging.ERROR, logger="ffmpeg_wrap"), pytest.raises(FFmpegTimeoutError):
        call("in.wav", timeout=0.5)
    records = [r for r in caplog.records if r.name == "ffmpeg_wrap" and r.levelno == logging.ERROR]
    assert len(records) == 1
    assert records[0].getMessage() == "ffprobe timed out after 0.5s: boom"


@pytest.mark.parametrize(
    "timeout", [0, -1, 0.0, -0.5, math.nan, math.inf, -math.inf, _TIMEOUT_MAX_SECONDS + 1, 1e20, 10**400]
)
@pytest.mark.parametrize("call", [probe, validate])
def test_ffprobe_invalid_timeout_raises_before_spawn(monkeypatch: pytest.MonkeyPatch, call, timeout):
    run = MagicMock()
    monkeypatch.setattr(_probe.subprocess, "run", run)
    with pytest.raises(ValueError, match="timeout must be"):
        call("in.wav", timeout=timeout)
    run.assert_not_called()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [30, _TIMEOUT_MAX_SECONDS])
def test_ffprobe_timeout_succeeds_on_real_file(lavfi_wav: Path, timeout):
    result = probe(lavfi_wav, timeout=timeout)
    assert result.streams
    assert result.streams[0].codec_type == "audio"
    ok, stderr = validate(lavfi_wav, timeout=timeout)
    assert ok is True
    assert stderr == ""


def test_probe_signatures_have_keyword_only_timeout():
    import inspect

    for fn in (probe, validate):
        param = inspect.signature(fn).parameters["timeout"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None
