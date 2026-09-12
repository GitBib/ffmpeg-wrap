from __future__ import annotations

from functools import partial
from typing import Any


class FFmpegError(Exception):
    """Custom exception for FFmpeg errors.

    Carries structured introspection from the underlying process failure so
    callers can branch on the exit code or inspect stderr without re-parsing
    ``str(e)``. The message remains ``args[0]`` so ``str(e)`` is unchanged.

    Attributes:
        stderr: Decoded stderr from the failed process, or ``None`` if not
            captured. Present on both :meth:`~ffmpeg_wrap.FFmpeg.run` failures
            and :func:`~ffmpeg_wrap.probe` / :func:`~ffmpeg_wrap.validate`
            failures.
        returncode: Process exit code, or ``None`` when the process could not
            be launched at all (e.g. executable not found) or when a
            ``timeout`` expired (see :class:`FFmpegTimeoutError`).
        cmd: The exact command list that was executed, or ``None``.

    Example:
        ```python
        import ffmpeg_wrap as ffmpeg

        try:
            ffmpeg.input("missing.mkv").output("out.mp4").run()
        except ffmpeg.FFmpegError as e:
            print("exit code:", e.returncode)
            print("command:", e.cmd)
            print("stderr:", e.stderr)
        ```
    """

    def __init__(
        self,
        message: str,
        *,
        stderr: str | None = None,
        returncode: int | None = None,
        cmd: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode
        self.cmd = cmd


def _build_ffmpeg_error(
    message: str,
    *,
    stderr: str | None = None,
    returncode: int | None = None,
    cmd: list[str] | None = None,
) -> FFmpegError:
    """Construct an :class:`FFmpegError` from an already-formatted message.

    Single shared constructor for every failure path (sync ``run``/``probe``/
    ``validate``/``encoders`` and their async twins). The message prefix
    (``"ffmpeg error: ..."`` vs ``"ffprobe error: ..."``) is built at each call
    site; this helper only wires the structured introspection fields.
    """
    return FFmpegError(message, stderr=stderr, returncode=returncode, cmd=cmd)


class FFmpegTimeoutError(FFmpegError):
    """Raised when ``timeout`` expires before the process exits.

    The child is killed and reaped before this is raised; descendants it
    spawned are not. Subclasses :class:`FFmpegError`, so ``except FFmpegError``
    still catches it. ``returncode`` is always ``None``.

    Attributes:
        timeout: The limit, in seconds, that was exceeded.
        stderr: The stderr tail collected before the kill, or ``None``.
        cmd: The exact command list that was executed, or ``None``.

    Example:
        ```python
        import ffmpeg_wrap as ffmpeg

        try:
            ffmpeg.input("in.mkv").output("out.mp4").run(timeout=60)
        except ffmpeg.FFmpegTimeoutError as e:
            print("gave up after", e.timeout, "seconds")
        ```
    """

    def __init__(
        self,
        message: str,
        *,
        timeout: float,
        stderr: str | None = None,
        returncode: int | None = None,
        cmd: list[str] | None = None,
    ) -> None:
        super().__init__(message, stderr=stderr, returncode=returncode, cmd=cmd)
        self.timeout = timeout

    def __reduce__(self) -> tuple[partial[FFmpegTimeoutError], tuple[Any, ...], dict[str, Any]]:  # ty: ignore[missing-override-decorator]
        return (partial(type(self), timeout=self.timeout), self.args, self.__dict__)


def _build_ffmpeg_timeout_error(
    message: str,
    *,
    timeout: float,
    stderr: str | None = None,
    cmd: list[str] | None = None,
) -> FFmpegTimeoutError:
    return FFmpegTimeoutError(message, timeout=timeout, stderr=stderr, returncode=None, cmd=cmd)
