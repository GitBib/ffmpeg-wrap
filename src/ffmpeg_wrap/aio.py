"""Asynchronous mirror of the public :mod:`ffmpeg_wrap` API, powered by AnyIO.

Importing this module requires the optional ``[async]`` extra::

    pip install ffmpeg-wrap[async]

The coroutine mirrors live here so the core package stays dependency-free:
``import ffmpeg_wrap`` never imports :mod:`anyio`. Each coroutine is a thin
imperative shell over the *same* pure helpers the sync API uses — command
building, output parsing, decoding and error construction are written once in
the private modules and reused here; the only async-specific line is the
``await anyio.run_process(...)`` exec call.
"""

from __future__ import annotations

import locale
import logging
import os
from subprocess import PIPE, CalledProcessError
from typing import TYPE_CHECKING, Any, Literal, overload

try:
    import anyio
except ImportError as exc:  # pragma: no cover - exercised via subprocess in tests
    raise ImportError("install ffmpeg-wrap[async] to use ffmpeg_wrap.aio") from exc

from ffmpeg_wrap._encoders import _ENCODERS_CACHE, _build_encoders_cmd, _parse_encoders
from ffmpeg_wrap._errors import _build_ffmpeg_error
from ffmpeg_wrap._probe import (
    ProbeResult,
    _build_probe_cmd,
    _build_validate_cmd,
    _interpret_validate,
    _parse_probe_output,
)
from ffmpeg_wrap._textio import TeePump, decode_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from ffmpeg_wrap._builder import FFmpeg

logger = logging.getLogger("ffmpeg_wrap")

__all__ = ["encoders", "has_encoder", "probe", "run", "validate"]


def _decode_error_stderr(stderr: bytes | None, encoding: str, *, text: bool) -> str | None:
    """Decode captured error stderr to match the sync ``run`` error path.

    Parity rules (see ``_builder.run`` lines 411-414 and ``_builder._run_tee``):

    * ``text=False`` — sync receives raw ``bytes`` and decodes them as UTF-8
      with ``errors="replace"`` and NO universal-newline translation. We do the
      identical thing so ``FFmpegError.stderr`` is byte-for-byte the same even
      when the locale is not UTF-8 or stderr contains ffmpeg's ``\\r`` progress.
    * ``text=True`` — sync's value already went through locale-decode plus
      universal-newline translation; we mirror that via the lenient
      :func:`decode_text` (the documented lenient-vs-strict edge for undecodable
      bytes).
    """
    if stderr is None:
        return None
    if text:
        return decode_text(stderr, encoding)
    return stderr.decode("utf-8", errors="replace")


async def probe(filename: str | os.PathLike[str], ffprobe_path: str = "ffprobe") -> ProbeResult:
    """Run ffprobe asynchronously and return typed output.

    Async mirror of :func:`ffmpeg_wrap.probe`. Reuses the shared command builder
    and output parser; only the exec call is async.

    Args:
        filename: Path to the file to probe.
        ffprobe_path: Path to the ffprobe executable.

    Returns:
        Parsed and typed output from ffprobe.

    Raises:
        FFmpegError: If ffprobe fails or output cannot be parsed.

    Example:
        ```python
        import anyio
        from ffmpeg_wrap import aio

        async def main():
            result = await aio.probe("video.mkv")
            print(result.format.format_name if result.format else None)
            for stream in result.streams:
                print(stream.index, stream.codec_type, stream.codec_name)

        anyio.run(main)
        ```
    """
    cmd = _build_probe_cmd(filename, ffprobe_path)
    try:
        result = await anyio.run_process(cmd, check=False)
    except OSError as e:
        logger.error(f"ffprobe could not be executed: {e}")
        raise _build_ffmpeg_error(f"ffprobe could not be executed: {e}", cmd=cmd) from e
    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else None
        # Empty-stderr fallback mirrors sync ``probe`` (``stderr_text or str(e)``
        # where ``e`` is the ``CalledProcessError``) so ``str(FFmpegError)`` is
        # identical across sync/async.
        err_msg = stderr_text or str(CalledProcessError(result.returncode, cmd))
        logger.error(f"ffprobe failed: {err_msg}")
        raise _build_ffmpeg_error(
            f"ffprobe error: {err_msg}",
            stderr=stderr_text,
            returncode=result.returncode,
            cmd=cmd,
        )
    return _parse_probe_output(result.stdout, cmd)


async def validate(
    filename: str | os.PathLike[str],
    ffprobe_path: str = "ffprobe",
    loglevel: str = "warning",
    extra_args: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """Run ffprobe in validation mode asynchronously and report diagnostics.

    Async mirror of :func:`ffmpeg_wrap.validate`. The loglevel precondition
    lives in the shared command builder, so an invalid ``loglevel`` raises
    ``ValueError`` exactly as in the sync path.

    Args:
        filename: Path to the media file (str or PathLike).
        ffprobe_path: Path to the ffprobe executable.
        loglevel: Value passed to ffprobe's ``-v`` flag (default ``"warning"``).
        extra_args: Additional raw arguments forwarded to ffprobe before the
            filename.

    Returns:
        ``(ok, stderr_text)`` — see :func:`ffmpeg_wrap.validate`. Does NOT raise
        on bad media — that is a normal outcome for a validator.

    Raises:
        FFmpegError: Only when the ffprobe executable cannot be run.
        ValueError: On an invalid ``loglevel``.

    Example:
        ```python
        import anyio
        from ffmpeg_wrap import aio

        async def main():
            ok, diag = await aio.validate("recording.mkv")
            if not ok:
                print("Validation failed:", diag)

        anyio.run(main)
        ```
    """
    cmd = _build_validate_cmd(filename, ffprobe_path, loglevel, extra_args)
    try:
        result = await anyio.run_process(cmd, check=False)
    except OSError as e:
        logger.error(f"ffprobe could not be executed: {e}")
        raise _build_ffmpeg_error(f"ffprobe could not be executed: {e}", cmd=cmd) from e
    return _interpret_validate(result.returncode, result.stderr)


async def encoders(ffmpeg_path: str = "ffmpeg") -> frozenset[str]:
    """Return the set of encoder names reported by ``ffmpeg -encoders``.

    Async mirror of :func:`ffmpeg_wrap.encoders`. Shares the same per-path cache
    (``_ENCODERS_CACHE``) as the sync API, so a value resolved by either path
    populates one cache.

    AnyIO's ``run_process`` always returns raw ``bytes``; we decode stdout as
    UTF-8 before parsing so the result is ``frozenset[str]`` identical to sync
    (``ffmpeg -encoders`` names are ASCII-only). The redundant-spawn race under
    concurrent first-use is benign: the only ``await`` is the subprocess itself,
    and the dict write is atomic.

    Args:
        ffmpeg_path: Path to the ffmpeg executable.

    Returns:
        A frozenset of encoder names (e.g. ``"libx264"``, ``"h264_nvenc"``).

    Raises:
        FFmpegError: If ffmpeg cannot be run or exits non-zero.

    Example:
        ```python
        import anyio
        from ffmpeg_wrap import aio

        async def main():
            available = await aio.encoders()
            print("libx264" in available)

        anyio.run(main)
        ```
    """
    cached = _ENCODERS_CACHE.get(ffmpeg_path)
    if cached is not None:
        return cached

    cmd = _build_encoders_cmd(ffmpeg_path)
    try:
        result = await anyio.run_process(cmd, check=False)
    except OSError as e:
        logger.error(f"ffmpeg could not be executed: {e}")
        raise _build_ffmpeg_error(f"ffmpeg could not be executed: {e}", cmd=cmd) from e
    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else None
        # Empty-stderr fallback mirrors sync ``encoders`` (``stderr_text or str(e)``).
        err_msg = stderr_text or str(CalledProcessError(result.returncode, cmd))
        logger.error(f"ffmpeg -encoders failed: {err_msg}")
        raise _build_ffmpeg_error(
            f"ffmpeg -encoders error: {err_msg}",
            stderr=stderr_text,
            returncode=result.returncode,
            cmd=cmd,
        )

    parsed = _parse_encoders(result.stdout.decode("utf-8", errors="replace"))
    _ENCODERS_CACHE[ffmpeg_path] = parsed
    return parsed


async def has_encoder(name: str, ffmpeg_path: str = "ffmpeg") -> bool:
    """Return whether ``name`` is an available ffmpeg encoder.

    Async mirror of :func:`ffmpeg_wrap.has_encoder`.

    Args:
        name: Encoder name to check (e.g. ``"h264_nvenc"``).
        ffmpeg_path: Path to the ffmpeg executable.

    Returns:
        ``True`` iff ``name`` appears in :func:`encoders`.

    Raises:
        FFmpegError: If ffmpeg cannot be run or exits non-zero.

    Example:
        ```python
        import anyio
        from ffmpeg_wrap import aio

        async def main():
            if await aio.has_encoder("h264_nvenc"):
                print("NVENC is available")

        anyio.run(main)
        ```
    """
    return name in await encoders(ffmpeg_path)


@overload
async def run(
    ffmpeg: FFmpeg,
    capture_stdout: bool = ...,
    capture_stderr: bool = ...,
    *,
    text: Literal[False] = ...,
) -> tuple[bytes | None, bytes | None]: ...


@overload
async def run(
    ffmpeg: FFmpeg,
    capture_stdout: bool = ...,
    capture_stderr: bool = ...,
    *,
    text: Literal[True],
) -> tuple[str | None, str | None]: ...


async def run(
    ffmpeg: FFmpeg,
    capture_stdout: bool = False,
    capture_stderr: bool = False,
    *,
    text: bool = False,
) -> tuple[bytes | None, bytes | None] | tuple[str | None, str | None]:
    """Build and execute an :class:`~ffmpeg_wrap.FFmpeg` command asynchronously.

    Async mirror of :meth:`ffmpeg_wrap.FFmpeg.run`, powered by AnyIO (runs on
    both the asyncio and trio backends). The return contract is identical to the
    sync ``run``: a ``(stdout, stderr)`` tuple of ``bytes`` (or ``str`` when
    ``text=True``), with ``None`` for any stream that was not captured.

    Two execution paths mirror sync:

    * **Capture path** (``capture_stderr=True``): ``anyio.run_process`` captures
      stderr (and stdout when ``capture_stdout`` is set), building ``FFmpegError``
      on a non-zero exit.
    * **Tee path** (``capture_stderr=False``): ffmpeg's stderr is forwarded live
      to the inherited terminal via the shared :class:`~ffmpeg_wrap._textio.TeePump`
      while only a bounded tail is retained for the failure path. This uses an
      AnyIO task group instead of the sync version's OS pump thread — so on the
      trio backend no per-process reaping thread is created either.

    Args:
        ffmpeg: The built :class:`~ffmpeg_wrap.FFmpeg` instance to execute.
        capture_stdout: Whether to capture stdout (else inherit to terminal).
        capture_stderr: Whether to capture stderr (else tee it live).
        text: When True, decode stdout/stderr (and ``FFmpegError.stderr``) as
            text leniently using the platform default encoding.

    Returns:
        Tuple of (stdout, stderr); see :meth:`ffmpeg_wrap.FFmpeg.run`.

    Raises:
        FFmpegError: If ffmpeg fails or cannot be executed.

    Example:
        ```python
        import anyio
        from ffmpeg_wrap import aio, input

        async def main():
            _, stderr = await aio.run(
                input("in.mkv").output("out.mp4").overwrite_output(),
                capture_stderr=True,
                text=True,
            )
            print(stderr)

        anyio.run(main)
        ```
    """
    cmd = ffmpeg.compile()
    stdout_dest = PIPE if capture_stdout else None

    logger.debug(f"Running ffmpeg command (async): {' '.join(cmd)}")

    if capture_stderr:
        return await _run_capture(cmd, stdout_dest, text=text)
    return await _run_tee(cmd, stdout_dest, text=text)


async def _run_capture(
    cmd: list[str],
    stdout_dest: int | None,
    *,
    text: bool,
) -> tuple[bytes | None, bytes | None] | tuple[str | None, str | None]:
    """Capture-path exec: collect stderr (and optionally stdout) via run_process."""
    encoding = locale.getpreferredencoding(False)
    try:
        result = await anyio.run_process(cmd, stdout=stdout_dest, stderr=PIPE, check=False)
    except OSError as e:
        logger.error(f"ffmpeg could not be executed: {e}")
        raise _build_ffmpeg_error(f"ffmpeg could not be executed: {e}", cmd=cmd) from e

    if result.returncode:
        # ``run_process`` always returns raw bytes. Decode the error stderr the
        # SAME way sync does for parity: on the ``text=False`` path sync receives
        # bytes from ``subprocess.run`` and decodes them as UTF-8 with
        # ``errors="replace"`` and NO newline translation (``_builder.run``
        # lines 411-414); on the ``text=True`` path sync's value already went
        # through locale-decode + universal newlines (mirrored by ``decode_text``).
        stderr_text = _decode_error_stderr(result.stderr, encoding, text=text)
        # Empty-stderr fallback mirrors sync ``run`` (``stderr_text or str(e)``
        # where ``e`` is the ``CalledProcessError``) so ``str(FFmpegError)`` is
        # byte-for-byte identical across sync/async.
        err_msg = stderr_text or str(CalledProcessError(result.returncode, cmd))
        logger.error(f"FFmpeg command failed: {err_msg}")
        raise _build_ffmpeg_error(
            f"ffmpeg error: {err_msg}",
            stderr=stderr_text,
            returncode=result.returncode,
            cmd=cmd,
        )

    if text:
        stdout = decode_text(result.stdout, encoding) if result.stdout is not None else None
        stderr = decode_text(result.stderr, encoding) if result.stderr is not None else None
        return stdout, stderr
    # Guard: when stdout is not piped, ``result.stdout`` is None — never decode it.
    return result.stdout, result.stderr


async def _run_tee(
    cmd: list[str],
    stdout_dest: int | None,
    *,
    text: bool,
) -> tuple[bytes | None, bytes | None] | tuple[str | None, str | None]:
    """Tee-path exec: forward stderr live, accumulate stdout, no sync pump thread.

    Mirrors the sync :meth:`ffmpeg_wrap.FFmpeg._run_tee` exactly except the read
    loop is an AnyIO task instead of a daemon thread. This removes the dedicated
    synchronous stderr-pump OS thread; this code does not itself spawn a
    per-process thread, though the event-loop backend's child reaping may
    (asyncio non-pidfd path) or may not (trio) use threads outside this code's
    control. On the trio backend, the actual pipe-FD reads may still run on
    AnyIO's bounded, shared worker-thread pool (not a thread per process). The shared
    :class:`~ffmpeg_wrap._textio.TeePump` owns the per-chunk forward-to-sink and
    bounded-tail bookkeeping (including the ``sys.stderr.buffer is None``
    fallback), so the tee behavior is identical to sync.
    """
    encoding = locale.getpreferredencoding(False)
    pump = TeePump(encoding)
    stdout_buf = bytearray()

    try:
        process = await anyio.open_process(cmd, stdout=stdout_dest, stderr=PIPE, stdin=None)
    except OSError as e:
        logger.error(f"ffmpeg could not be executed: {e}")
        raise _build_ffmpeg_error(f"ffmpeg could not be executed: {e}", cmd=cmd) from e

    async def _drain(stream: Any, on_chunk: Callable[[bytes], object]) -> None:
        # Shared read loop for both pipes — only the per-chunk action differs.
        # ``ClosedResourceError`` is caught alongside ``EndOfStream`` so a read
        # cancelled by task-group teardown never masks the original error.
        if stream is None:
            return
        while True:
            try:
                chunk = await stream.receive(65536)
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                break
            on_chunk(chunk)

    async with process, anyio.create_task_group() as tg:
        tg.start_soon(_drain, process.stderr, pump.feed)
        if stdout_dest is not None:
            tg.start_soon(_drain, process.stdout, stdout_buf.extend)
        tg.start_soon(process.wait)

    stdout_data: bytes | None = bytes(stdout_buf) if stdout_dest is not None else None

    stdout_result: bytes | str | None
    if text and stdout_data is not None:
        stdout_result = decode_text(stdout_data, encoding)
    else:
        stdout_result = stdout_data

    if process.returncode:
        # Match sync ``_run_tee`` + ``run`` error handling: text=False decodes
        # the bounded tail as UTF-8/replace (no newline translation), text=True
        # uses the locale ``decode_text``. See ``_decode_error_stderr``.
        stderr_bytes = pump.tail_bytes()
        stderr_text = _decode_error_stderr(stderr_bytes, encoding, text=text)
        # Empty-stderr fallback mirrors sync ``_run_tee`` (which raises
        # ``CalledProcessError`` that ``run`` formats via ``str(e)``) so
        # ``str(FFmpegError)`` is byte-for-byte identical across sync/async.
        err_msg = stderr_text or str(CalledProcessError(process.returncode, cmd))
        logger.error(f"FFmpeg command failed: {err_msg}")
        raise _build_ffmpeg_error(
            f"ffmpeg error: {err_msg}",
            stderr=stderr_text,
            returncode=process.returncode,
            cmd=cmd,
        )

    return stdout_result, None
