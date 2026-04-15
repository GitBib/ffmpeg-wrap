import logging
import os
import subprocess
from pathlib import Path

import msgspec

from ._errors import FFmpegError

logger = logging.getLogger("ffmpeg_wrap")

_FFPROBE_LOGLEVELS = frozenset({"quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"})

# Known ffmpeg protocols that use "name:" or "name:arg" syntax (no "://").
# Kept as a whitelist to avoid matching POSIX filenames containing colons.
_FFMPEG_SIMPLE_PROTOCOLS = frozenset(
    {
        "amovie",
        "async",
        "bluray",
        "cache",
        "concat",
        "concatf",
        "crypto",
        "data",
        "fd",
        "file",
        "lavfi",
        "movie",
        "pipe",
        "subfile",
    }
)


def _is_special_input(filename: str) -> bool:
    """Check if filename is an ffmpeg special input (not a filesystem path)."""
    if filename == "-":
        return True
    if "://" in filename:
        return True
    # Known ffmpeg protocol prefix like pipe:, concat:, etc.
    colon_idx = filename.find(":")
    if colon_idx > 0:
        prefix = filename[:colon_idx].lower()
        if prefix in _FFMPEG_SIMPLE_PROTOCOLS:
            return True
        # Handle protocol+protocol nesting (e.g. crypto+file:video.mkv)
        # All parts must be known protocols to avoid matching filenames like pipe+notes:clip.mkv
        if "+" in prefix:
            parts = prefix.split("+")
            if all(p in _FFMPEG_SIMPLE_PROTOCOLS for p in parts):
                return True
        # Handle protocol,,options,,: syntax (e.g. subfile,,start,0,end,1024,,:/path)
        # Must use double-comma delimiter; single comma is not valid protocol syntax
        dcomma_idx = prefix.find(",,")
        if dcomma_idx > 0 and prefix[:dcomma_idx] in _FFMPEG_SIMPLE_PROTOCOLS:
            return True
    return False


def _resolve_input(filename: str | os.PathLike[str]) -> str:
    """Resolve a filename argument to the string passed to ffprobe/ffmpeg.

    PathLike objects are always treated as filesystem paths and resolved to
    absolute form.  Plain strings are checked for ffmpeg special-input syntax
    (protocols, pipe:, URLs, "-") and passed through unchanged when detected;
    otherwise they are resolved as filesystem paths.
    """
    filename_str = os.fsdecode(filename)
    if isinstance(filename, os.PathLike) or not _is_special_input(filename_str):
        return str(Path(filename_str).resolve())
    return filename_str


class Stream(msgspec.Struct):
    """A single stream from ffprobe output."""

    index: int
    codec_name: str | None = None
    codec_type: str | None = None
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    sample_rate: str | None = None
    duration: str | None = None
    bit_rate: str | None = None
    tags: dict[str, str] | None = None
    disposition: dict[str, int] | None = None


class Format(msgspec.Struct):
    """The format section from ffprobe output."""

    filename: str | None = None
    nb_streams: int | None = None
    nb_programs: int | None = None
    format_name: str | None = None
    format_long_name: str | None = None
    start_time: str | None = None
    duration: str | None = None
    size: str | None = None
    bit_rate: str | None = None
    probe_score: int | None = None
    tags: dict[str, str] | None = None


class ProbeResult(msgspec.Struct):
    """Typed result of running ffprobe on a file."""

    streams: list[Stream]
    format: Format | None = None


def validate(
    filename: str | os.PathLike[str],
    ffprobe_path: str = "ffprobe",
    loglevel: str = "warning",
    extra_args: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """Run ffprobe in validation mode and report diagnostics.

    Args:
        filename: Path to the media file (str or PathLike).
        ffprobe_path: Path to the ffprobe executable.
        loglevel: Value passed to ffprobe's ``-v`` flag. Defaults to
            ``"warning"`` so DTS/codec warnings surface in stderr.
            Use ``"error"`` for a stricter check that ignores warnings,
            ``"fatal"``/``"panic"`` for only unrecoverable failures,
            or any other ffprobe loglevel keyword. See ``ffprobe -loglevel help``.
        extra_args: Additional raw arguments forwarded to ffprobe before the
            filename, e.g. ``("-show_format",)``. Use with care; no validation
            is performed on these args.

    Returns:
        (ok, stderr_text). ok is True iff ffprobe exit code == 0 AND
        stderr.strip() is empty. stderr_text is the raw decoded stderr
        (never None, possibly empty string).

    Does NOT raise on bad media — that is a normal outcome for a validator.
    Raises FFmpegError only when the ffprobe executable cannot be run.
    Raises ValueError on invalid loglevel.
    """
    if loglevel not in _FFPROBE_LOGLEVELS:
        msg = f"invalid loglevel {loglevel!r}, must be one of: {', '.join(sorted(_FFPROBE_LOGLEVELS))}"
        raise ValueError(msg)
    filename_str = _resolve_input(filename)
    cmd = [ffprobe_path, "-v", loglevel, *[str(a) for a in extra_args], filename_str]
    try:
        result = subprocess.run(cmd, capture_output=True, check=False, text=False)
    except OSError as e:
        logger.error(f"ffprobe could not be executed: {e}")
        raise FFmpegError(f"ffprobe could not be executed: {e}") from e
    stderr_text = result.stderr.decode("utf-8", errors="replace")
    ok = result.returncode == 0 and not stderr_text.strip()
    return (ok, stderr_text)


def probe(filename: str | os.PathLike[str], ffprobe_path: str = "ffprobe") -> ProbeResult:
    """Run ffprobe on the specified file and return typed output.

    Args:
        filename: Path to the file to probe.
        ffprobe_path: Path to the ffprobe executable.

    Returns:
        Parsed and typed output from ffprobe.

    Raises:
        FFmpegError: If ffprobe fails or output cannot be parsed.
    """
    filename_str = _resolve_input(filename)

    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        filename_str,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            text=False,
        )
        return msgspec.json.decode(result.stdout, type=ProbeResult)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        logger.error(f"ffprobe failed: {err_msg}")
        raise FFmpegError(f"ffprobe error: {err_msg}") from e
    except OSError as e:
        logger.error(f"ffprobe could not be executed: {e}")
        raise FFmpegError(f"ffprobe could not be executed: {e}") from e
    except (msgspec.DecodeError, msgspec.ValidationError) as e:
        logger.error(f"Failed to parse ffprobe output: {e}")
        raise FFmpegError(f"ffprobe output parsing error: {e}") from e
