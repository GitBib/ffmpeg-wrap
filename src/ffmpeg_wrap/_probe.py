import logging
import os
import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # Python 3.10: StrEnum was added in 3.11
    from enum import Enum

    class StrEnum(str, Enum):
        """Minimal ``enum.StrEnum`` backport for Python 3.10."""

        __str__ = str.__str__


import msgspec

from ._errors import FFmpegError

logger = logging.getLogger("ffmpeg_wrap")


class CodecType(StrEnum):
    """The known ffprobe ``codec_type`` values.

    Used for the typed predicates on :class:`Stream` (``is_video`` etc.).
    Note that :attr:`Stream.codec_type` stays typed ``str | None`` rather than
    this enum: ffmpeg can report ``codec_type`` values outside this set, and
    decoding into a strict enum would raise ``msgspec.ValidationError`` on such
    files. Compare against these members instead of retyping the field.
    """

    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    DATA = "data"
    ATTACHMENT = "attachment"


# Best-effort classification of subtitle ``codec_name`` values into text-based
# vs image-based (bitmap) subtitles. Not exhaustive: ffmpeg ships many subtitle
# codecs, and an unrecognised name yields ``False`` from both predicates.
_TEXT_SUBTITLE_CODECS = frozenset(
    {
        "subrip",
        "srt",
        "ass",
        "ssa",
        "webvtt",
        "mov_text",
        "text",
        "eia_608",
        "subviewer",
        "microdvd",
    }
)
_IMAGE_SUBTITLE_CODECS = frozenset(
    {
        "hdmv_pgs_subtitle",
        "dvd_subtitle",
        "dvb_subtitle",
        "xsub",
        "dvb_teletext",
    }
)

_FFPROBE_LOGLEVELS = frozenset({"quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"})

# Maps an ffprobe ``codec_type`` to its ffmpeg stream-specifier letter
# (e.g. ``"subtitle"`` -> ``"s"`` so the 1st subtitle stream is ``0:s:0``).
_STREAM_TYPE_LETTERS = {
    "video": "v",
    "audio": "a",
    "subtitle": "s",
    "data": "d",
    "attachment": "t",
}

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


def _parse_duration(value: str | None) -> float | None:
    """Parse an ffprobe duration string to seconds.

    Returns ``None`` for ``None`` or non-numeric values (e.g. ``"N/A"``).
    """
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


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
    # Per-type ordinal within this stream's ``codec_type`` (the ``N`` in the
    # ffmpeg specifier ``0:s:N``). Populated by ``probe()``; defaults to 0 when
    # a ``Stream`` is constructed or decoded outside of ``probe()``.
    type_index: int = 0

    def map_specifier(self, input_index: int = 0) -> str:
        """Return the ffmpeg ``-map`` specifier for this stream.

        Emits the per-type form ``{input}:{letter}:{ordinal}`` (e.g. ``0:s:0``)
        derived from ``codec_type`` and ``type_index``. When ``codec_type`` is
        unknown or ``None``, falls back to the unambiguous absolute-index form
        ``{input}:{index}``.

        Args:
            input_index: Index of the ffmpeg input the stream belongs to.

        Returns:
            The map specifier string.
        """
        letter = _STREAM_TYPE_LETTERS.get(self.codec_type or "")
        if letter is None:
            return f"{input_index}:{self.index}"
        return f"{input_index}:{letter}:{self.type_index}"

    def duration_seconds(self) -> float | None:
        """Return this stream's ``duration`` as ``float`` seconds.

        Returns ``None`` when ``duration`` is missing or non-numeric
        (e.g. ``"N/A"``); the raw ``duration`` string is preserved.
        """
        return _parse_duration(self.duration)

    @property
    def is_video(self) -> bool:
        """True iff ``codec_type`` is ``"video"``."""
        return self.codec_type == CodecType.VIDEO

    @property
    def is_audio(self) -> bool:
        """True iff ``codec_type`` is ``"audio"``."""
        return self.codec_type == CodecType.AUDIO

    @property
    def is_subtitle(self) -> bool:
        """True iff ``codec_type`` is ``"subtitle"``."""
        return self.codec_type == CodecType.SUBTITLE

    @property
    def is_data(self) -> bool:
        """True iff ``codec_type`` is ``"data"``."""
        return self.codec_type == CodecType.DATA

    @property
    def is_attachment(self) -> bool:
        """True iff ``codec_type`` is ``"attachment"``."""
        return self.codec_type == CodecType.ATTACHMENT

    @property
    def is_text_subtitle(self) -> bool:
        """True iff this is a subtitle stream with a known text-based codec.

        Best-effort: driven by a documented codec-name set (``subrip``,
        ``ass``, ``ssa``, ...). Unknown subtitle codecs return ``False``.
        """
        return self.is_subtitle and self.codec_name in _TEXT_SUBTITLE_CODECS

    @property
    def is_image_subtitle(self) -> bool:
        """True iff this is a subtitle stream with a known image-based codec.

        Best-effort: driven by a documented codec-name set
        (``hdmv_pgs_subtitle``, ``dvd_subtitle``, ``dvb_subtitle``, ...).
        Unknown subtitle codecs return ``False``.
        """
        return self.is_subtitle and self.codec_name in _IMAGE_SUBTITLE_CODECS


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

    def duration_seconds(self) -> float | None:
        """Return the container ``duration`` as ``float`` seconds.

        Returns ``None`` when ``duration`` is missing or non-numeric
        (e.g. ``"N/A"``); the raw ``duration`` string is preserved.
        """
        return _parse_duration(self.duration)


class ProbeResult(msgspec.Struct):
    """Typed result of running ffprobe on a file."""

    streams: list[Stream]
    format: Format | None = None

    def duration_seconds(self) -> float | None:
        """Return the container ``duration`` as ``float`` seconds.

        Delegates to ``self.format``; returns ``None`` when ``format`` is
        absent or its ``duration`` is missing/non-numeric.
        """
        if self.format is None:
            return None
        return self.format.duration_seconds()


def _assign_type_indices(streams: list[Stream]) -> None:
    """Set each stream's per-type ordinal (the ``N`` in ``0:<type>:N``)."""
    counters: dict[str | None, int] = {}
    for stream in streams:
        ordinal = counters.get(stream.codec_type, 0)
        stream.type_index = ordinal
        counters[stream.codec_type] = ordinal + 1


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
        raise FFmpegError(f"ffprobe could not be executed: {e}", cmd=cmd) from e
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
        parsed = msgspec.json.decode(result.stdout, type=ProbeResult)
        _assign_type_indices(parsed.streams)
        return parsed
    except subprocess.CalledProcessError as e:
        stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else None
        err_msg = stderr_text or str(e)
        logger.error(f"ffprobe failed: {err_msg}")
        raise FFmpegError(
            f"ffprobe error: {err_msg}",
            stderr=stderr_text,
            returncode=e.returncode,
            cmd=cmd,
        ) from e
    except OSError as e:
        logger.error(f"ffprobe could not be executed: {e}")
        raise FFmpegError(f"ffprobe could not be executed: {e}", cmd=cmd) from e
    except (msgspec.DecodeError, msgspec.ValidationError) as e:
        logger.error(f"Failed to parse ffprobe output: {e}")
        raise FFmpegError(f"ffprobe output parsing error: {e}", cmd=cmd) from e
