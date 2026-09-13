from __future__ import annotations

import inspect
import logging
import math
import time
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock

import anyio
import pytest
from conftest import FakeChild

import ffmpeg_wrap as ffmpeg
import ffmpeg_wrap.aio as aio
from ffmpeg_wrap._builder import FFmpeg
from ffmpeg_wrap._errors import FFmpegError, FFmpegTimeoutError
from ffmpeg_wrap._probe import _TIMEOUT_MAX_SECONDS, _build_probe_cmd, _build_validate_cmd, probe

WATCHDOG = 20


@pytest.fixture
def lavfi_wav(ffmpeg_available: None, tmp_path: Path) -> Path:
    out = tmp_path / "tone.wav"
    ffmpeg.input("anullsrc=r=48000:cl=mono", f="lavfi", t=0.2).output(str(out)).overwrite_output().run(
        capture_stderr=True
    )
    return out


@pytest.mark.timeout(30)
@pytest.mark.parametrize("text", [False, True])
async def test_capture_path_times_out_and_kills_child(fake_child: FakeChild, text: bool):
    builder = fake_child.make(FAKE_CHILD_STDERR="partial")
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.run(builder, capture_stderr=True, text=text, timeout=0.5)
    error = exc_info.value
    assert error.timeout == 0.5
    assert error.cmd == builder.compile()
    assert error.returncode is None
    assert error.stderr is None
    assert str(error) == "ffmpeg timed out after 0.5s"
    assert isinstance(error.__cause__, TimeoutError)
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
async def test_tee_path_silent_child_capture_stdout_times_out(fake_child: FakeChild):
    builder = fake_child.make()
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.run(builder, capture_stdout=True, timeout=0.5)
    error = exc_info.value
    assert error.timeout == 0.5
    assert error.cmd == builder.compile()
    assert error.returncode is None
    assert error.stderr is None
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
async def test_tee_path_producing_child_capture_stdout_times_out(fake_child: FakeChild):
    builder = fake_child.make(FAKE_CHILD_STDOUT_BYTES=2_000_000)
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.run(builder, capture_stdout=True, timeout=0.5)
    assert exc_info.value.timeout == 0.5
    assert exc_info.value.returncode is None
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("text", [False, True])
async def test_tee_path_without_capture_stdout_times_out(fake_child: FakeChild, text: bool):
    builder = fake_child.make(FAKE_CHILD_STDERR="partial\r\nx")
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.run(builder, text=text, timeout=0.5)
    error = exc_info.value
    assert error.stderr == ("partial\nx" if text else "partial\r\nx")
    assert error.returncode is None
    assert error.cmd == builder.compile()
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
async def test_tee_path_raises_while_grandchild_still_holds_stderr(fake_child: FakeChild):
    builder = fake_child.make(FAKE_CHILD_SPAWN=1, FAKE_CHILD_SLEEP=3)
    started = time.monotonic()
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.run(builder, timeout=0.5)
    assert time.monotonic() - started < 2
    assert exc_info.value.timeout == 0.5
    assert fake_child.alive()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stdout", [False, True])
async def test_tee_path_raises_when_child_exits_but_detached_grandchild_holds_pipes(
    fake_child: FakeChild, capture_stdout: bool
):
    builder = fake_child.make(FAKE_CHILD_SPAWN=1, FAKE_CHILD_DETACH=1, FAKE_CHILD_SLEEP=3)
    started = time.monotonic()
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.run(builder, capture_stdout=capture_stdout, timeout=0.5)
    assert time.monotonic() - started < 2.5
    assert exc_info.value.timeout == 0.5
    assert fake_child.alive()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stdout", [False, True])
async def test_tee_path_without_timeout_waits_for_detached_grandchild(fake_child: FakeChild, capture_stdout: bool):
    builder = fake_child.make(FAKE_CHILD_SPAWN=1, FAKE_CHILD_DETACH=1, FAKE_CHILD_SLEEP=1, FAKE_CHILD_STDOUT_BYTES=7)
    with anyio.fail_after(WATCHDOG):
        result = await aio.run(builder, capture_stdout=capture_stdout)
    assert result == ((b"x" * 7 if capture_stdout else None), None)
    assert not fake_child.alive()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stdout", [False, True])
@pytest.mark.parametrize("capture_stderr", [False, True])
async def test_child_failure_within_timeout_is_not_a_timeout(
    fake_child: FakeChild, capture_stdout: bool, capture_stderr: bool
):
    builder = fake_child.make(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDERR="boom", FAKE_CHILD_EXIT=3)
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegError) as exc_info:
        await aio.run(builder, capture_stdout=capture_stdout, capture_stderr=capture_stderr, text=True, timeout=30)
    error = exc_info.value
    assert not isinstance(error, FFmpegTimeoutError)
    assert error.returncode == 3
    assert error.stderr == "boom"
    assert error.cmd == builder.compile()
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stderr", [False, True])
async def test_message_matches_sync_error(fake_child: FakeChild, capture_stderr: bool):
    builder = fake_child.make(FAKE_CHILD_STDERR="partial")
    with pytest.raises(FFmpegTimeoutError) as sync_info:
        builder.run(capture_stderr=capture_stderr, timeout=0.5)
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as async_info:
        await aio.run(builder, capture_stderr=capture_stderr, timeout=0.5)
    assert str(async_info.value) == str(sync_info.value)
    assert async_info.value.cmd == sync_info.value.cmd
    assert async_info.value.timeout == sync_info.value.timeout
    fake_child.assert_gone()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stderr", [False, True])
async def test_arun_forwards_timeout(fake_child: FakeChild, capture_stderr: bool):
    builder = fake_child.make()
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await builder.arun(capture_stderr=capture_stderr, timeout=0.5)
    assert exc_info.value.timeout == 0.5
    assert exc_info.value.cmd == builder.compile()
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stderr", [False, True])
async def test_caller_cancellation_kills_child_without_timeout(fake_child: FakeChild, capture_stderr: bool):
    builder = fake_child.make()
    with anyio.fail_after(WATCHDOG), anyio.move_on_after(0.3) as scope:
        await aio.run(builder, capture_stdout=True, capture_stderr=capture_stderr, timeout=None)
    assert scope.cancelled_caught
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("capture_stderr", [False, True])
async def test_stalled_launch_is_reported_as_timeout(monkeypatch: pytest.MonkeyPatch, capture_stderr: bool):
    name = "run_process" if capture_stderr else "open_process"
    real = getattr(anyio, name)

    async def stalled(*args, **kwargs):
        await anyio.sleep(5)
        return await real(*args, **kwargs)

    monkeypatch.setattr(anyio, name, stalled)
    builder = FFmpeg().input("in.mkv").output("out.mp4")
    started = time.monotonic()
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.run(builder, capture_stdout=True, capture_stderr=capture_stderr, timeout=0.2)
    assert time.monotonic() - started < 3
    error = exc_info.value
    assert error.timeout == 0.2
    assert error.stderr is None
    assert error.cmd == ["ffmpeg", "-i", "in.mkv", "out.mp4"]
    assert str(error) == "ffmpeg timed out after 0.2s"
    assert isinstance(error.__cause__, TimeoutError)


@pytest.mark.timeout(30)
@pytest.mark.parametrize("anyio_backend", ["trio"])
async def test_trio_exhausted_thread_limiter_does_not_delay_deadline(fake_child: FakeChild):
    import trio

    limiter = trio.to_thread.current_default_thread_limiter()
    original_tokens = limiter.total_tokens
    limiter.total_tokens = 1
    builder = fake_child.make()
    try:
        with anyio.fail_after(WATCHDOG):
            async with trio.open_nursery() as nursery:
                nursery.start_soon(partial(trio.to_thread.run_sync, time.sleep, 2.0))
                await trio.sleep(0.05)
                assert limiter.borrowed_tokens == 1
                started = time.monotonic()
                with pytest.raises(FFmpegTimeoutError) as exc_info:
                    await aio.run(builder, capture_stdout=True, timeout=0.2)
                assert time.monotonic() - started < 1.5
                assert exc_info.value.timeout == 0.2
    finally:
        limiter.total_tokens = original_tokens


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [None, 30])
@pytest.mark.parametrize("text", [False, True])
async def test_tee_path_success_with_real_pipe_returns_all_stdout(fake_child: FakeChild, timeout, text: bool):
    builder = fake_child.make(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDOUT_BYTES=2_000_000)
    with anyio.fail_after(WATCHDOG):
        stdout, stderr = await aio.run(builder, capture_stdout=True, text=text, timeout=timeout)
    assert stderr is None
    assert isinstance(stdout, str if text else bytes)
    assert len(stdout) == 2_000_000
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [None, 30, _TIMEOUT_MAX_SECONDS])
async def test_capture_path_short_child_succeeds_with_timeout(fake_child: FakeChild, timeout):
    builder = fake_child.make(FAKE_CHILD_SLEEP=0.1, FAKE_CHILD_STDERR="ok")
    with anyio.fail_after(WATCHDOG):
        assert await aio.run(builder, capture_stderr=True, timeout=timeout) == (None, b"ok")
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [None, 30, _TIMEOUT_MAX_SECONDS])
async def test_tee_path_short_child_succeeds_with_timeout(fake_child: FakeChild, timeout):
    builder = fake_child.make(FAKE_CHILD_SLEEP=0.1, FAKE_CHILD_STDOUT_BYTES=10)
    with anyio.fail_after(WATCHDOG):
        assert await aio.run(builder, timeout=timeout) == (None, None)
        assert await aio.run(builder, capture_stdout=True, timeout=timeout) == (b"x" * 10, None)
    assert fake_child.marker.exists()


@pytest.mark.parametrize(
    "timeout", [0, -1, 0.0, -0.5, math.nan, math.inf, -math.inf, _TIMEOUT_MAX_SECONDS + 1, 1e20, 10**400]
)
@pytest.mark.parametrize("capture_stderr", [False, True])
async def test_invalid_timeout_raises_before_spawn(timeout, capture_stderr: bool):
    builder = FFmpeg(ffmpeg_path="/nonexistent/ffmpeg-for-timeout-test").input("in.mkv").output("out.mp4")
    with pytest.raises(ValueError, match="timeout must be"):
        await aio.run(builder, capture_stderr=capture_stderr, timeout=timeout)
    with pytest.raises(ValueError, match="timeout must be"):
        await builder.arun(capture_stderr=capture_stderr, timeout=timeout)


@pytest.mark.parametrize("capture_stderr", [False, True])
async def test_missing_binary_still_reports_launch_failure(capture_stderr: bool):
    builder = FFmpeg(ffmpeg_path="/nonexistent/ffmpeg-for-timeout-test").input("in.mkv").output("out.mp4")
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegError) as exc_info:
        await aio.run(builder, capture_stderr=capture_stderr, timeout=5)
    assert not isinstance(exc_info.value, FFmpegTimeoutError)
    assert str(exc_info.value).startswith("ffmpeg could not be executed: ")
    assert exc_info.value.returncode is None


@pytest.mark.parametrize("timeout", [None, 5])
@pytest.mark.parametrize("capture_stderr", [False, True])
async def test_os_level_timeout_error_is_a_launch_failure(
    monkeypatch: pytest.MonkeyPatch, timeout, capture_stderr: bool
):
    name = "run_process" if capture_stderr else "open_process"

    async def etimedout(*args, **kwargs):
        raise TimeoutError("[Errno 60] Operation timed out")

    monkeypatch.setattr(anyio, name, etimedout)
    builder = FFmpeg().input("in.mkv").output("out.mp4")
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegError) as exc_info:
        await aio.run(builder, capture_stderr=capture_stderr, timeout=timeout)
    assert not isinstance(exc_info.value, FFmpegTimeoutError)
    assert str(exc_info.value) == "ffmpeg could not be executed: [Errno 60] Operation timed out"
    assert exc_info.value.cmd == ["ffmpeg", "-i", "in.mkv", "out.mp4"]


@pytest.mark.timeout(30)
async def test_timeout_is_caught_by_except_ffmpeg_error(fake_child: FakeChild):
    builder = fake_child.make()
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegError) as exc_info:
        await aio.run(builder, capture_stderr=True, timeout=0.5)
    assert isinstance(exc_info.value, FFmpegTimeoutError)
    fake_child.assert_gone()


@pytest.mark.timeout(30)
async def test_timeout_is_logged_at_error_level(fake_child: FakeChild, caplog: pytest.LogCaptureFixture):
    builder = fake_child.make(FAKE_CHILD_STDERR="boom")
    with (
        caplog.at_level(logging.ERROR, logger="ffmpeg_wrap"),
        anyio.fail_after(WATCHDOG),
        pytest.raises(FFmpegTimeoutError),
    ):
        await aio.run(builder, timeout=0.5)
    records = [r for r in caplog.records if r.name == "ffmpeg_wrap" and r.levelno == logging.ERROR]
    assert len(records) == 1
    assert records[0].getMessage() == "FFmpeg command timed out after 0.5s: boom"
    fake_child.assert_gone()


def test_run_signatures_have_keyword_only_timeout():
    for fn in (aio.run, FFmpeg.arun):
        param = inspect.signature(fn).parameters["timeout"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None


@pytest.mark.timeout(30)
async def test_probe_times_out_and_kills_fake_ffprobe(fake_child: FakeChild, fake_ffprobe: str):
    fake_child.set_env(FAKE_CHILD_STDERR="partial")
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.probe("x.wav", ffprobe_path=fake_ffprobe, timeout=0.5)
    error = exc_info.value
    assert error.timeout == 0.5
    assert error.cmd == _build_probe_cmd("x.wav", fake_ffprobe)
    assert error.returncode is None
    assert error.stderr is None
    assert str(error) == "ffprobe timed out after 0.5s"
    assert isinstance(error.__cause__, TimeoutError)
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
async def test_validate_times_out_and_kills_fake_ffprobe(fake_child: FakeChild, fake_ffprobe: str):
    fake_child.set_env(FAKE_CHILD_STDERR="partial")
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await aio.validate("x.wav", ffprobe_path=fake_ffprobe, timeout=0.5)
    error = exc_info.value
    assert error.timeout == 0.5
    assert error.cmd == _build_validate_cmd("x.wav", fake_ffprobe)
    assert error.returncode is None
    assert error.stderr is None
    assert str(error) == "ffprobe timed out after 0.5s"
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("timeout", [None, 30])
async def test_fake_ffprobe_short_child_is_not_killed(fake_child: FakeChild, fake_ffprobe: str, timeout):
    fake_child.set_env(FAKE_CHILD_SLEEP=0.1)
    with anyio.fail_after(WATCHDOG):
        ok, stderr = await aio.validate("x.wav", ffprobe_path=fake_ffprobe, timeout=timeout)
    assert ok is False
    assert isinstance(stderr, str)
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
async def test_probe_child_failure_within_timeout_is_not_a_timeout(fake_child: FakeChild, fake_ffprobe: str):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDERR="boom", FAKE_CHILD_EXIT=3)
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegError) as exc_info:
        await aio.probe("x.wav", ffprobe_path=fake_ffprobe, timeout=30)
    error = exc_info.value
    assert not isinstance(error, FFmpegTimeoutError)
    assert error.returncode == 3
    assert error.stderr is not None
    assert "boom" in error.stderr
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
async def test_validate_child_failure_within_timeout_is_not_a_timeout(fake_child: FakeChild, fake_ffprobe: str):
    fake_child.set_env(FAKE_CHILD_SLEEP=0, FAKE_CHILD_STDERR="boom", FAKE_CHILD_EXIT=3)
    with anyio.fail_after(WATCHDOG):
        ok, stderr = await aio.validate("x.wav", ffprobe_path=fake_ffprobe, timeout=30)
    assert ok is False
    assert "boom" in stderr
    assert fake_child.marker.exists()


@pytest.mark.timeout(30)
async def test_ffprobe_message_matches_sync_error(fake_child: FakeChild, fake_ffprobe: str):
    with pytest.raises(FFmpegTimeoutError) as sync_info:
        probe("x.wav", ffprobe_path=fake_ffprobe, timeout=0.5)
    fake_child.assert_gone()
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as async_info:
        await aio.probe("x.wav", ffprobe_path=fake_ffprobe, timeout=0.5)
    assert str(async_info.value) == str(sync_info.value)
    assert async_info.value.cmd == sync_info.value.cmd
    assert async_info.value.timeout == sync_info.value.timeout
    fake_child.assert_gone()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("call", [aio.probe, aio.validate])
async def test_stalled_ffprobe_launch_is_reported_as_timeout(monkeypatch: pytest.MonkeyPatch, call):
    real = anyio.run_process

    async def stalled(*args, **kwargs):
        await anyio.sleep(5)
        return await real(*args, **kwargs)

    monkeypatch.setattr(anyio, "run_process", stalled)
    started = time.monotonic()
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await call("in.wav", timeout=0.2)
    assert time.monotonic() - started < 3
    error = exc_info.value
    assert error.timeout == 0.2
    assert error.stderr is None
    assert error.returncode is None
    assert error.cmd is not None
    assert error.cmd[0] == "ffprobe"
    assert str(error) == "ffprobe timed out after 0.2s"
    assert isinstance(error.__cause__, TimeoutError)


@pytest.mark.parametrize(
    "timeout", [0, -1, 0.0, -0.5, math.nan, math.inf, -math.inf, _TIMEOUT_MAX_SECONDS + 1, 1e20, 10**400]
)
@pytest.mark.parametrize("call", [aio.probe, aio.validate])
async def test_ffprobe_invalid_timeout_raises_before_spawn(monkeypatch: pytest.MonkeyPatch, call, timeout):
    run_process = MagicMock()
    monkeypatch.setattr(anyio, "run_process", run_process)
    with pytest.raises(ValueError, match="timeout must be"):
        await call("in.wav", timeout=timeout)
    run_process.assert_not_called()


@pytest.mark.parametrize("timeout", [None, 5])
@pytest.mark.parametrize("call", [aio.probe, aio.validate])
async def test_ffprobe_os_level_timeout_error_is_a_launch_failure(monkeypatch: pytest.MonkeyPatch, call, timeout):
    async def etimedout(*args, **kwargs):
        raise TimeoutError("[Errno 60] Operation timed out")

    monkeypatch.setattr(anyio, "run_process", etimedout)
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegError) as exc_info:
        await call("in.wav", timeout=timeout)
    assert not isinstance(exc_info.value, FFmpegTimeoutError)
    assert str(exc_info.value) == "ffprobe could not be executed: [Errno 60] Operation timed out"


@pytest.mark.parametrize("call", [aio.probe, aio.validate])
async def test_ffprobe_missing_binary_still_reports_launch_failure(call):
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegError) as exc_info:
        await call("in.wav", ffprobe_path="/nonexistent/ffprobe-for-timeout-test", timeout=5)
    assert not isinstance(exc_info.value, FFmpegTimeoutError)
    assert str(exc_info.value).startswith("ffprobe could not be executed: ")
    assert exc_info.value.returncode is None


@pytest.mark.timeout(30)
async def test_ffprobe_timeout_is_caught_by_except_ffmpeg_error(fake_child: FakeChild, fake_ffprobe: str):
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegError) as exc_info:
        await aio.probe("x.wav", ffprobe_path=fake_ffprobe, timeout=0.5)
    assert isinstance(exc_info.value, FFmpegTimeoutError)
    fake_child.assert_gone()


@pytest.mark.timeout(30)
async def test_ffprobe_timeout_is_logged_at_error_level(
    fake_child: FakeChild, fake_ffprobe: str, caplog: pytest.LogCaptureFixture
):
    with (
        caplog.at_level(logging.ERROR, logger="ffmpeg_wrap"),
        anyio.fail_after(WATCHDOG),
        pytest.raises(FFmpegTimeoutError),
    ):
        await aio.validate("x.wav", ffprobe_path=fake_ffprobe, timeout=0.5)
    records = [r for r in caplog.records if r.name == "ffmpeg_wrap" and r.levelno == logging.ERROR]
    assert len(records) == 1
    assert records[0].getMessage().startswith("ffprobe timed out after 0.5s: ")
    fake_child.assert_gone()


@pytest.mark.timeout(30)
async def test_ffprobe_timeout_succeeds_on_real_file(lavfi_wav: Path):
    with anyio.fail_after(WATCHDOG):
        result = await aio.probe(lavfi_wav, timeout=30)
        assert result.streams
        assert result.streams[0].codec_type == "audio"
        ok, stderr = await aio.validate(lavfi_wav, timeout=30)
    assert ok is True
    assert stderr == ""


def test_probe_signatures_have_keyword_only_timeout():
    for fn in (aio.probe, aio.validate):
        param = inspect.signature(fn).parameters["timeout"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None
