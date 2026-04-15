from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from ._errors import FFmpegError

logger = logging.getLogger("ffmpeg_wrap")


class FFmpeg:
    """A fluent interface wrapper for ffmpeg."""

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self._inputs: list[dict[str, Any]] = []
        self._outputs: list[dict[str, Any]] = []
        self._global_args: list[str] = []
        self._overwrite: bool = False
        self._ffmpeg_path: str = ffmpeg_path

    def compile(self) -> list[str]:
        """Build the FFmpeg command as a list of arguments without executing it.

        Returns:
            The command as a list of strings.
        """
        cmd = [self._ffmpeg_path]

        if self._overwrite:
            cmd.append("-y")

        cmd.extend(self._global_args)

        for inp in self._inputs:
            for k, v in inp["kwargs"].items():
                cmd.extend(_convert_arg(k, v))
            cmd.extend(["-i", inp["filename"]])

        for out in self._outputs:
            for k, v in out["kwargs"].items():
                cmd.extend(_convert_arg(k, v))
            cmd.append(out["filename"])

        return cmd

    def input(self, filename: str | os.PathLike[str], **kwargs: Any) -> FFmpeg:
        """Add an input file with optional arguments.

        Args:
            filename: Input file path or PathLike object.
            **kwargs: FFmpeg input options (e.g., t=10).

        Returns:
            Self for chaining.
        """
        self._inputs.append({"filename": os.fsdecode(filename), "kwargs": kwargs})
        return self

    def output(self, filename: str | os.PathLike[str], **kwargs: Any) -> FFmpeg:
        """Add an output file with optional arguments.

        Args:
            filename: Output file path or PathLike object.
            **kwargs: FFmpeg output options (e.g., c="copy", ac=2).

        Returns:
            Self for chaining.
        """
        self._outputs.append({"filename": os.fsdecode(filename), "kwargs": kwargs})
        return self

    def overwrite_output(self) -> FFmpeg:
        """Add the -y flag to overwrite output files.

        Returns:
            Self for chaining.
        """
        self._overwrite = True
        return self

    def global_args(self, *args: str) -> FFmpeg:
        """Add global arguments.

        Args:
            *args: Global arguments.

        Returns:
            Self for chaining.
        """
        self._global_args.extend(args)
        return self

    def run(
        self,
        capture_stdout: bool = False,
        capture_stderr: bool = False,
    ) -> tuple[bytes | None, bytes | None]:
        """Build and execute the FFmpeg command.

        Args:
            capture_stdout: Whether to capture stdout.
            capture_stderr: Whether to capture stderr.

        Returns:
            Tuple of (stdout, stderr). Values are bytes when the
            corresponding capture flag is True, or None otherwise.

        Raises:
            FFmpegError: If ffmpeg fails.
        """
        cmd = self.compile()

        stdout_dest = subprocess.PIPE if capture_stdout else None
        stderr_dest = subprocess.PIPE if capture_stderr else None

        logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")

        try:
            process = subprocess.run(
                cmd,
                stdout=stdout_dest,
                stderr=stderr_dest,
                check=True,
                text=False,
            )
            return process.stdout, process.stderr
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
            logger.error(f"FFmpeg command failed: {err_msg}")
            raise FFmpegError(f"ffmpeg error: {err_msg}") from e
        except OSError as e:
            logger.error(f"ffmpeg could not be executed: {e}")
            raise FFmpegError(f"ffmpeg could not be executed: {e}") from e


def _convert_arg(key: str, value: Any) -> list[str]:
    """Convert a kwarg pair to command-line arguments."""
    if value is None or value is False:
        return []
    flag = f"-{key}"
    if value is True:
        return [flag]
    return [flag, str(value)]


def input(filename: str | os.PathLike[str], ffmpeg_path: str = "ffmpeg", **kwargs: Any) -> FFmpeg:
    """Start a new FFmpeg chain with an input file.

    Args:
        filename: Input file path or PathLike object.
        ffmpeg_path: Path to the ffmpeg executable.
        **kwargs: Input arguments.

    Returns:
        A new FFmpeg wrapper instance with the input added.
    """
    instance = FFmpeg(ffmpeg_path=ffmpeg_path)
    return instance.input(filename, **kwargs)
