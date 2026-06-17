"""Task 4 tests for ``ffmpeg_wrap.aio.run`` and ``FFmpeg.arun``.

These exercise the real ffmpeg binary against the downloaded media fixtures.
Coroutine tests are auto-promoted by ``anyio_mode = "auto"`` and run on both
AnyIO backends via the module-scoped ``anyio_backend`` fixture (asyncio + trio)
defined in ``conftest.py``.

The whole module is skipped cleanly when ``anyio`` is not installed and when
ffmpeg/ffprobe are unavailable (via the ``ffmpeg_available``-backed fixtures).
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("anyio")

import anyio

import ffmpeg_wrap as ffmpeg
import ffmpeg_wrap.aio as aio
from ffmpeg_wrap import FFmpegError


def _failing_chain(real_file: Path) -> ffmpeg.FFmpeg:
    """A command guaranteed to fail (bogus codec) on the given input."""
    return ffmpeg.input(real_file, t=1).output("-", f="null").codec("v", "definitely-not-a-codec").overwrite_output()


def _transcode_chain(real_file: Path, out: Path) -> ffmpeg.FFmpeg:
    """A short, real transcode that succeeds and prints progress to stderr."""
    return ffmpeg.input(real_file, t=1).output(str(out)).codec("v", "libx264").codec("a", "copy").overwrite_output()


class TestAioRunCapture:
    async def test_capture_stdout_and_stderr(self, real_file: Path) -> None:
        # ``-f null -`` writes nothing to stdout but emits progress to stderr.
        chain = ffmpeg.input(real_file, t=1).output("-", f="null").overwrite_output()
        stdout, stderr = await aio.run(chain, capture_stdout=True, capture_stderr=True)
        assert isinstance(stdout, bytes)
        assert isinstance(stderr, bytes)

    async def test_capture_stderr_only_returns_none_stdout(self, real_file: Path) -> None:
        chain = ffmpeg.input(real_file, t=1).output("-", f="null").overwrite_output()
        stdout, stderr = await aio.run(chain, capture_stderr=True)
        assert stdout is None
        assert isinstance(stderr, bytes)

    async def test_capture_text_decodes(self, real_file: Path) -> None:
        chain = ffmpeg.input(real_file, t=1).output("-", f="null").overwrite_output()
        stdout, stderr = await aio.run(chain, capture_stdout=True, capture_stderr=True, text=True)
        assert isinstance(stdout, str)
        assert isinstance(stderr, str)

    async def test_capture_error_raises_ffmpeg_error(self, real_file: Path) -> None:
        with pytest.raises(FFmpegError) as exc:
            await aio.run(_failing_chain(real_file), capture_stdout=True, capture_stderr=True)
        assert exc.value.returncode not in (None, 0)
        assert exc.value.stderr
        assert exc.value.cmd is not None

    async def test_capture_missing_binary_raises(self, real_file: Path) -> None:
        chain = ffmpeg.FFmpeg(ffmpeg_path="ffmpeg-does-not-exist").input(real_file).output("-", f="null")
        with pytest.raises(FFmpegError):
            await aio.run(chain, capture_stderr=True)


class TestAioRunTee:
    async def test_tee_success(self, real_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out.mp4"
        stdout, stderr = await aio.run(_transcode_chain(real_file, out))
        assert stdout is None
        assert stderr is None
        assert out.exists() and out.stat().st_size > 0

    async def test_tee_capture_stdout(self, real_file: Path) -> None:
        # Pipe a tiny stream to stdout while teeing stderr.
        chain = ffmpeg.input(real_file, t=1).output("-", f="matroska", c="copy").map("0:v").overwrite_output()
        stdout, stderr = await aio.run(chain, capture_stdout=True)
        assert isinstance(stdout, bytes)
        assert len(stdout) > 0
        assert stderr is None

    async def test_tee_error_raises_ffmpeg_error(self, real_file: Path) -> None:
        with pytest.raises(FFmpegError) as exc:
            await aio.run(_failing_chain(real_file))
        assert exc.value.returncode not in (None, 0)
        assert exc.value.stderr  # populated from the bounded tail
        assert exc.value.cmd is not None


class TestArun:
    async def test_arun_delegates(self, real_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out.mp4"
        stdout, stderr = await _transcode_chain(real_file, out).arun()
        assert stdout is None
        assert stderr is None
        assert out.exists() and out.stat().st_size > 0

    async def test_arun_capture(self, real_file: Path) -> None:
        chain = ffmpeg.input(real_file, t=1).output("-", f="null").overwrite_output()
        stdout, stderr = await chain.arun(capture_stdout=True, capture_stderr=True)
        assert isinstance(stdout, bytes)
        assert isinstance(stderr, bytes)


class TestFFmpegErrorParity:
    async def test_error_parity_with_sync(self, real_file: Path) -> None:
        # The same failing command should produce equivalent FFmpegError fields
        # from both the sync and async capture paths.
        with pytest.raises(FFmpegError) as sync_exc:
            _failing_chain(real_file).run(capture_stdout=True, capture_stderr=True, text=True)
        with pytest.raises(FFmpegError) as async_exc:
            await aio.run(_failing_chain(real_file), capture_stdout=True, capture_stderr=True, text=True)

        assert async_exc.value.returncode == sync_exc.value.returncode
        assert async_exc.value.cmd == sync_exc.value.cmd
        # The unknown-encoder diagnostic is deterministic for this command.
        assert "definitely-not-a-codec" in (sync_exc.value.stderr or "")
        assert "definitely-not-a-codec" in (async_exc.value.stderr or "")

    async def test_error_parity_with_sync_text_false(self, real_file: Path) -> None:
        # text=False parity: sync decodes the captured error bytes as
        # UTF-8/replace with NO newline translation; async must produce the
        # IDENTICAL FFmpegError.stderr (the plan's byte-for-byte parity claim).
        with pytest.raises(FFmpegError) as sync_exc:
            _failing_chain(real_file).run(capture_stdout=True, capture_stderr=True, text=False)
        with pytest.raises(FFmpegError) as async_exc:
            await aio.run(_failing_chain(real_file), capture_stdout=True, capture_stderr=True, text=False)

        assert async_exc.value.returncode == sync_exc.value.returncode
        assert async_exc.value.cmd == sync_exc.value.cmd
        # Exact stderr equality after the SAME decode strategy on both paths.
        # ffmpeg embeds a nondeterministic heap pointer per run (``@ 0x...`` on
        # Unix, ``@ 000002...`` without the ``0x`` prefix on Windows), so
        # normalise it before comparing — what we assert is that the decode
        # (UTF-8/replace, no newline translation) is identical, not the pointer.
        ptr = re.compile(r"@ (?:0x)?[0-9a-fA-F]+")
        assert ptr.sub("@ 0xPTR", async_exc.value.stderr or "") == ptr.sub("@ 0xPTR", sync_exc.value.stderr or "")

    async def test_empty_stderr_fallback_parity_with_sync(self, real_file: Path) -> None:
        # Empty-stderr fallback parity (capture path): a command that fails with
        # NO captured stderr must produce an IDENTICAL ``str(FFmpegError)`` on
        # both paths. Sync uses ``stderr_text or str(e)`` (the CalledProcessError)
        # → "Command '[...]' returned non-zero exit status N."; async must build
        # the SAME message via ``str(CalledProcessError(...))`` — NOT a divergent
        # "ffmpeg exited with code N".
        def _silent_failing_chain() -> ffmpeg.FFmpeg:
            # ``-loglevel quiet`` suppresses all stderr; the bogus codec still
            # makes ffmpeg exit non-zero, so the capture path sees empty stderr.
            return (
                ffmpeg.input(real_file, t=1)
                .global_args("-loglevel", "quiet")
                .output("-", f="null")
                .codec("v", "definitely-not-a-codec")
                .overwrite_output()
            )

        with pytest.raises(FFmpegError) as sync_exc:
            _silent_failing_chain().run(capture_stdout=True, capture_stderr=True)
        with pytest.raises(FFmpegError) as async_exc:
            await aio.run(_silent_failing_chain(), capture_stdout=True, capture_stderr=True)

        assert async_exc.value.returncode == sync_exc.value.returncode
        assert async_exc.value.cmd == sync_exc.value.cmd
        # No stderr was captured on either path, so the fallback message drives
        # ``str(FFmpegError)`` — assert it is byte-for-byte identical.
        assert not sync_exc.value.stderr
        assert not async_exc.value.stderr
        assert str(async_exc.value) == str(sync_exc.value)
        assert "returned non-zero exit status" in str(async_exc.value)

    async def test_capture_text_decodes_leniently_on_non_utf8(self) -> None:
        # Documented lenient-vs-strict edge: on undecodable bytes the async
        # capture path decodes leniently (errors="replace") and never raises
        # UnicodeDecodeError. Pipe raw non-UTF-8 bytes through a tiny shim that
        # emits them on stdout, then capture with text=True.
        chain = ffmpeg.FFmpeg(ffmpeg_path=sys.executable).global_args(
            "-c",
            "import sys; sys.stdout.buffer.write(bytes([255, 254, 128]) + b'abc')",
        )
        # This shim just writes raw non-UTF-8 bytes to stdout and exits 0.
        # The key assertion is that capture+decode does NOT raise
        # UnicodeDecodeError where strict sync decoding would.
        stdout, _ = await aio.run(chain, capture_stdout=True, capture_stderr=True, text=True)
        assert isinstance(stdout, str)
        assert "abc" in stdout


def test_no_per_process_reaping_thread_on_trio(real_file: Path, tmp_path: Path) -> None:
    """trio reaps children WITHOUT a dedicated reaping thread.

    The plan's claim is specifically about *child reaping*: on the asyncio
    backend CPython adds a single per-process ``ThreadedChildWatcher`` thread,
    while on trio reaping is handled by signal/``waitpid`` machinery with no
    dedicated thread. (AnyIO's trio backend separately uses a bounded *reused*
    worker-thread pool named ``Trio thread N`` for blocking pipe-FD I/O — that
    is NOT a reaping thread; its scaling is asserted in the test below.)

    Runs directly via ``anyio.run(..., backend="trio")`` (not the parametrized
    fixture) so the assertion is trio-specific.
    """
    pytest.importorskip("trio")

    out = tmp_path / "trio_out.mp4"
    before = {t.name for t in threading.enumerate()}

    async def _job() -> None:
        await aio.run(_transcode_chain(real_file, out))

    anyio.run(_job, backend="trio")

    after = {t.name for t in threading.enumerate()}
    new_threads = after - before
    # No NEW persistent thread whose name suggests dedicated child reaping.
    # NOTE: AnyIO's pooled "Trio thread N" workers are pipe-I/O workers, not
    # reaping threads, so they are deliberately not in this token set.
    suspicious = {
        name
        for name in new_threads
        if any(token in name.lower() for token in ("waitpid", "child", "ffmpeg", "reap", "watcher"))
    }
    assert not suspicious, f"unexpected child-reaping thread(s) on trio: {suspicious}"
    assert out.exists()


def test_trio_worker_threads_bounded_by_concurrency_not_job_count(real_file: Path) -> None:
    """trio's pipe-I/O worker pool scales with concurrency, not total jobs.

    This guards the real thread-free claim: there is NO per-process thread that
    grows one-per-job. AnyIO's trio backend uses a bounded, *reused* worker pool
    for the blocking pipe-FD reads, so when we run many jobs through a small
    ``CapacityLimiter`` the peak ``Trio thread N`` count tracks the limiter bound
    (a small constant), not the (much larger) total job count.

    Runs trio-only and directly via ``anyio.run`` so the assertion is specific
    to the trio backend.
    """
    pytest.importorskip("trio")

    n_jobs = 24
    limit = 3

    def _trio_worker_count() -> int:
        return sum(1 for t in threading.enumerate() if t.name.startswith("Trio thread"))

    peak = {"v": 0}
    stop = threading.Event()

    def _watch() -> None:
        while not stop.is_set():
            peak["v"] = max(peak["v"], _trio_worker_count())
            stop.wait(0.002)

    async def _job() -> None:
        limiter = anyio.CapacityLimiter(limit)

        async def _one() -> None:
            async with limiter:
                chain = ffmpeg.input(real_file, t=1).output("-", f="null").overwrite_output()
                await aio.run(chain, capture_stderr=True)

        async with anyio.create_task_group() as tg:
            for _ in range(n_jobs):
                tg.start_soon(_one)

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    try:
        anyio.run(_job, backend="trio")
    finally:
        stop.set()
        watcher.join()

    # Sublinear/bounded: peak worker threads stay near the concurrency bound, NOT
    # near the job count. A generous ceiling (limit + small slack) still proves
    # there is no per-job thread (which would push the peak toward ``n_jobs``).
    assert peak["v"] <= limit + 2, (
        f"trio worker-thread peak {peak['v']} grew with job count "
        f"(n_jobs={n_jobs}, limit={limit}); expected it bounded by concurrency"
    )


class TestDeadlockAndFallback:
    async def test_concurrent_read_no_deadlock(self, real_file: Path) -> None:
        # Large output to BOTH stdout (piped matroska) and stderr (verbose
        # progress). If the reader tasks did not run concurrently, the pipe
        # buffers would fill and ffmpeg would deadlock — fail_after turns a hang
        # into a fast failure instead of stalling the suite.
        chain = (
            ffmpeg.input(real_file)
            .global_args("-loglevel", "verbose")
            .output("-", f="matroska", c="copy")
            .map("0:v")
            .map("0:a")
            .overwrite_output()
        )
        with anyio.fail_after(120):
            stdout, stderr = await aio.run(chain, capture_stdout=True)
        assert isinstance(stdout, bytes)
        assert len(stdout) > 0
        assert stderr is None

    async def test_tee_error_propagates_real_error_not_closed_resource(self, real_file: Path) -> None:
        # The failing tee run must surface the genuine FFmpegError, not a bare
        # ClosedResourceError leaking from the cancelled stderr reader task.
        with pytest.raises(FFmpegError):
            await aio.run(_failing_chain(real_file))

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "asyncio's ProactorEventLoop cannot cancel a blocked subprocess pipe read on "
            "Windows, so cancelling a tee mid-receive hangs instead of unwinding. Normal "
            "aio.run — including the large-pipe deadlock case — works on Windows; only this "
            "forced mid-read cancellation is unreliable there (an asyncio limitation, not a "
            "library defect). Verified on Linux and macOS for both backends."
        ),
    )
    async def test_tee_cancelled_midread_raises_cancel_not_closed_resource(self, real_file: Path) -> None:
        # Cancel a long-running tee run WHILE its reader tasks are mid-receive
        # (a timeout fires before ffmpeg finishes). This is the path that
        # actually exercises aio.py's ClosedResourceError catch in the readers:
        # the task group cancels the receivers and the process scope terminates
        # the child. The surfaced outcome must be the cancellation/timeout, NOT
        # a bare anyio.ClosedResourceError leaking out of a reader.
        # A full-length libx264 *re-encode* (no -t, slow preset) is guaranteed to
        # still be running when the short timeout fires, unlike a fast stream
        # copy which finishes in milliseconds. ``-re`` is not needed — encoding
        # the whole ~3 min input takes far longer than the timeout.
        chain = (
            ffmpeg.input(real_file)
            .global_args("-loglevel", "verbose")
            .output("-", f="matroska")
            .codec("v", "libx264")
            .codec("a", "copy")
            .map("0:v")
            .map("0:a")
            .overwrite_output()
        )
        with pytest.raises(TimeoutError):
            with anyio.fail_after(0.5):
                await aio.run(chain, capture_stdout=True)
        # Reaching here (TimeoutError, not ClosedResourceError) proves the catch
        # works; fail_after raises TimeoutError on both AnyIO backends.

    async def test_tee_failing_run_text_true_builds_str_stderr(self, real_file: Path) -> None:
        # The text=True FAILING-tee path builds FFmpegError from
        # pump.tail_bytes()+decode (aio.py ~346-356). Only text=False was
        # covered before; assert the str stderr carries the diagnostic.
        with pytest.raises(FFmpegError) as exc:
            await aio.run(_failing_chain(real_file), text=True)
        assert isinstance(exc.value.stderr, str)
        assert "definitely-not-a-codec" in (exc.value.stderr or "")
        assert exc.value.returncode not in (None, 0)

    async def test_stderr_buffer_none_fallback(
        self,
        real_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Replace sys.stderr with a text-only sink lacking ``.buffer`` so the
        # TeePump takes the per-chunk decode fallback path. The tee must still
        # forward and the run must still complete.
        class _TextOnlyStderr:
            def __init__(self) -> None:
                self.written: list[str] = []

            def write(self, s: str) -> int:
                self.written.append(s)
                return len(s)

            def flush(self) -> None:
                pass

        sink = _TextOnlyStderr()
        monkeypatch.setattr(sys, "stderr", sink)

        out = tmp_path / "fallback_out.mp4"
        stdout, stderr = await aio.run(_transcode_chain(real_file, out))
        assert stdout is None
        assert stderr is None
        assert out.exists() and out.stat().st_size > 0
        # The text-only sink should have received the actual forwarded (decoded)
        # ffmpeg diagnostics, not just an empty write — assert recognisable
        # progress/banner markers appear in the joined output.
        joined = "".join(sink.written)
        assert joined.strip()
        assert any(marker in joined for marker in ("frame", "size", "time", "ffmpeg version", "Stream"))
