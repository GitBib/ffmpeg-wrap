import logging
import os
import subprocess
from pathlib import Path

import msgspec

from ._errors import FFmpegError

logger = logging.getLogger("ffmpeg_wrap")

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
    filename_str = os.fsdecode(filename)
    # PathLike objects are always filesystem paths; only check special input for bare strings
    if isinstance(filename, os.PathLike) or not _is_special_input(filename_str):
        filename_str = str(Path(filename_str).resolve())

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
    except FileNotFoundError as e:
        logger.error(f"ffprobe executable not found: {e}")
        raise FFmpegError(f"ffprobe not found: {e}") from e
    except (msgspec.DecodeError, msgspec.ValidationError) as e:
        logger.error(f"Failed to parse ffprobe output: {e}")
        raise FFmpegError(f"ffprobe output parsing error: {e}") from e
