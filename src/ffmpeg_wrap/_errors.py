from __future__ import annotations


class FFmpegError(Exception):
    """Custom exception for FFmpeg errors.

    Carries structured introspection from the underlying process failure so
    callers can branch on the exit code or inspect stderr without re-parsing
    ``str(e)``. The message remains ``args[0]`` so ``str(e)`` is unchanged.

    Attributes:
        stderr: Decoded stderr from the failed process, if captured.
        returncode: Process exit code, if available.
        cmd: The command that was executed, if available.
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
