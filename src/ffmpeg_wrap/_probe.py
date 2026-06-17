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

from ffmpeg_wrap._errors import _build_ffmpeg_error

logger = logging.getLogger("ffmpeg_wrap")


class CodecType(StrEnum):
    """The known ffprobe ``codec_type`` values.

    Used for the typed predicates on :class:`Stream` (``is_video`` etc.).
    Note that :attr:`Stream.codec_type` stays typed ``str | None`` rather than
    this enum: ffmpeg can report ``codec_type`` values outside this set, and
    decoding into a strict enum would raise ``msgspec.ValidationError`` on such
    files. Compare against these members instead of retyping the field.

    Attributes:
        VIDEO: Represents a video stream (``"video"``).
        AUDIO: Represents an audio stream (``"audio"``).
        SUBTITLE: Represents a subtitle stream (``"subtitle"``).
        DATA: Represents a data stream (``"data"``).
        ATTACHMENT: Represents an attachment stream (``"attachment"``).

    Example:
        ```python
        from ffmpeg_wrap import CodecType, probe

        result = probe("video.mkv")
        videos = [s for s in result.streams if s.codec_type == CodecType.VIDEO]
        ```
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
    """A single stream entry from ffprobe JSON output.

    Decoded by ``msgspec`` from the ffprobe ``streams`` array. All fields
    except :attr:`index` are optional because ffprobe omits irrelevant fields
    per stream type (e.g. ``width``/``height`` are absent on audio streams).

    Attributes:
        index: Absolute stream index within the file (0-based).
        codec_name: Short codec name as reported by ffprobe (e.g.
            ``"h264"``, ``"aac"``, ``"subrip"``). ``None`` if not reported.
        codec_type: Stream type string (e.g. ``"video"``, ``"audio"``,
            ``"subtitle"``). ``None`` if not reported. Compare against
            :class:`CodecType` members rather than raw strings.
        width: Video frame width in pixels. ``None`` for non-video streams.
        height: Video frame height in pixels. ``None`` for non-video streams.
        channels: Number of audio channels. ``None`` for non-audio streams.
        sample_rate: Audio sample rate as a string (e.g. ``"48000"``).
            ``None`` for non-audio streams.
        duration: Stream duration as a decimal-seconds string (e.g.
            ``"3600.123000"``). ``None`` or ``"N/A"`` when unavailable.
        bit_rate: Stream bit rate as a string (e.g. ``"128000"``).
            ``None`` when not reported.
        tags: Key/value metadata tags from the stream header (e.g.
            ``{"language": "eng", "title": "Commentary"}``). ``None`` when
            no tags are present.
        disposition: ffprobe disposition flags as a ``dict[str, int]``
            (e.g. ``{"default": 1, "forced": 0}``). ``None`` when absent.
        type_index: Zero-based ordinal of this stream within its
            :attr:`codec_type` group (the ``N`` in the ffmpeg specifier
            ``0:<type>:N``). Set by :func:`probe`; defaults to ``0`` when a
            ``Stream`` is constructed outside of :func:`probe`.

    Example:
        ```python
        from ffmpeg_wrap import probe

        result = probe("video.mkv")
        for stream in result.streams:
            print(stream.index, stream.codec_type, stream.codec_name)
        ```
    """

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

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            sub = next(s for s in result.streams if s.is_subtitle)
            print(sub.map_specifier())   # e.g. "0:s:0"
            print(sub.map_specifier(1))  # e.g. "1:s:0"
            ```
        """
        letter = _STREAM_TYPE_LETTERS.get(self.codec_type or "")
        if letter is None:
            return f"{input_index}:{self.index}"
        return f"{input_index}:{letter}:{self.type_index}"

    def duration_seconds(self) -> float | None:
        """Return this stream's ``duration`` as ``float`` seconds.

        Returns ``None`` when ``duration`` is missing or non-numeric
        (e.g. ``"N/A"``); the raw ``duration`` string is preserved.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            for stream in result.streams:
                secs = stream.duration_seconds()
                if secs is not None:
                    print(f"stream {stream.index}: {secs:.1f}s")
            ```
        """
        return _parse_duration(self.duration)

    @property
    def is_video(self) -> bool:
        """True iff ``codec_type`` is ``"video"``.

        Returns:
            ``True`` for video streams, ``False`` otherwise.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            videos = [s for s in result.streams if s.is_video]
            audios = [s for s in result.streams if s.is_audio]
            subs = [s for s in result.streams if s.is_subtitle]
            ```
        """
        return self.codec_type == CodecType.VIDEO

    @property
    def is_audio(self) -> bool:
        """True iff ``codec_type`` is ``"audio"``.

        Returns:
            ``True`` for audio streams, ``False`` otherwise.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            audio_streams = [s for s in result.streams if s.is_audio]
            ```
        """
        return self.codec_type == CodecType.AUDIO

    @property
    def is_subtitle(self) -> bool:
        """True iff ``codec_type`` is ``"subtitle"``.

        Returns:
            ``True`` for subtitle streams, ``False`` otherwise.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            subtitle_streams = [s for s in result.streams if s.is_subtitle]
            ```
        """
        return self.codec_type == CodecType.SUBTITLE

    @property
    def is_data(self) -> bool:
        """True iff ``codec_type`` is ``"data"``.

        Returns:
            ``True`` for data streams, ``False`` otherwise.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            data_streams = [s for s in result.streams if s.is_data]
            ```
        """
        return self.codec_type == CodecType.DATA

    @property
    def is_attachment(self) -> bool:
        """True iff ``codec_type`` is ``"attachment"``.

        Returns:
            ``True`` for attachment streams, ``False`` otherwise.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            attachments = [s for s in result.streams if s.is_attachment]
            ```
        """
        return self.codec_type == CodecType.ATTACHMENT

    @property
    def is_text_subtitle(self) -> bool:
        """True iff this is a subtitle stream with a known text-based codec.

        Best-effort: driven by a documented codec-name set (``subrip``,
        ``ass``, ``ssa``, ...). Unknown subtitle codecs return ``False``.

        Returns:
            ``True`` for text-based subtitle streams (SRT, ASS, WebVTT, etc.),
            ``False`` otherwise.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            text_subs = [s for s in result.streams if s.is_text_subtitle]
            ```
        """
        return self.is_subtitle and self.codec_name in _TEXT_SUBTITLE_CODECS

    @property
    def is_image_subtitle(self) -> bool:
        """True iff this is a subtitle stream with a known image-based codec.

        Best-effort: driven by a documented codec-name set
        (``hdmv_pgs_subtitle``, ``dvd_subtitle``, ``dvb_subtitle``, ...).
        Unknown subtitle codecs return ``False``.

        Returns:
            ``True`` for bitmap/image subtitle streams (PGS, DVD, DVB, etc.),
            ``False`` otherwise.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            image_subs = [s for s in result.streams if s.is_image_subtitle]
            ```
        """
        return self.is_subtitle and self.codec_name in _IMAGE_SUBTITLE_CODECS


class Format(msgspec.Struct):
    """The format/container section from ffprobe JSON output.

    Decoded from the top-level ``"format"`` key of ffprobe's JSON output.
    All fields are optional because ffprobe may omit them for certain inputs.

    Attributes:
        filename: Resolved path of the probed file as reported by ffprobe.
        nb_streams: Total number of streams in the container.
        nb_programs: Number of programs (relevant for MPEG-TS and similar).
        format_name: Short format/muxer name (e.g. ``"matroska,webm"``).
        format_long_name: Human-readable format name (e.g.
            ``"Matroska / WebM"``).
        start_time: Container start time in decimal seconds (e.g.
            ``"0.000000"``). ``None`` when not reported.
        duration: Container duration in decimal seconds (e.g.
            ``"3600.123000"``). ``None`` or ``"N/A"`` when unavailable.
        size: File size in bytes as a string (e.g. ``"1234567890"``).
        bit_rate: Overall container bit rate in bits/s as a string.
        probe_score: ffprobe format-detection confidence score (0-100).
        tags: Container-level metadata tags (e.g.
            ``{"title": "My Movie", "encoder": "Lavf58.76.100"}``).

    Example:
        ```python
        from ffmpeg_wrap import probe

        result = probe("video.mkv")
        fmt = result.format
        if fmt:
            print(fmt.format_name, fmt.duration_seconds())
        ```
    """

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

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            if result.format:
                secs = result.format.duration_seconds()
                print(f"Duration: {secs:.1f}s" if secs else "Unknown duration")
            ```
        """
        return _parse_duration(self.duration)


class ProbeResult(msgspec.Struct):
    """Typed result of running :func:`probe` on a media file.

    Top-level container decoded from ffprobe's ``-print_format json`` output.
    Provides convenient access to all streams and the container format
    information in a single structured object.

    Attributes:
        streams: All streams found in the file, in index order. Each entry is
            a :class:`Stream` with ``type_index`` populated by :func:`probe`.
        format: Container-level format information, or ``None`` when ffprobe
            did not emit a ``"format"`` section (rare for well-formed files).

    Example:
        ```python
        from ffmpeg_wrap import probe

        result = probe("video.mkv")
        print(result.format.format_name if result.format else None)
        print([s.codec_name for s in result.streams if s.is_video])
        ```
    """

    streams: list[Stream]
    format: Format | None = None

    def duration_seconds(self) -> float | None:
        """Return the container ``duration`` as ``float`` seconds.

        Delegates to ``self.format``; returns ``None`` when ``format`` is
        absent or its ``duration`` is missing/non-numeric.

        Example:
            ```python
            from ffmpeg_wrap import probe

            result = probe("video.mkv")
            secs = result.duration_seconds()
            print(f"Duration: {secs:.1f}s" if secs else "Duration unknown")
            ```
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


def _build_validate_cmd(
    filename: str | os.PathLike[str],
    ffprobe_path: str = "ffprobe",
    loglevel: str = "warning",
    extra_args: tuple[str, ...] = (),
) -> list[str]:
    """Build the ffprobe validation command (pure; shared by sync/async).

    The loglevel precondition lives HERE so it is written once and both shells
    let the ``ValueError`` propagate.

    Raises:
        ValueError: If ``loglevel`` is not a known ffprobe loglevel keyword.
    """
    if loglevel not in _FFPROBE_LOGLEVELS:
        msg = f"invalid loglevel {loglevel!r}, must be one of: {', '.join(sorted(_FFPROBE_LOGLEVELS))}"
        raise ValueError(msg)
    filename_str = _resolve_input(filename)
    return [ffprobe_path, "-v", loglevel, *[str(a) for a in extra_args], filename_str]


def _interpret_validate(returncode: int, stderr_bytes: bytes) -> tuple[bool, str]:
    """Interpret a completed ffprobe validation run (pure; shared by sync/async).

    Returns ``(ok, stderr_text)`` where ``ok`` is True iff the exit code is 0
    AND the decoded stderr is empty after stripping.
    """
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    ok = returncode == 0 and not stderr_text.strip()
    return (ok, stderr_text)


def _build_probe_cmd(filename: str | os.PathLike[str], ffprobe_path: str = "ffprobe") -> list[str]:
    """Build the ffprobe JSON-introspection command (pure; shared by sync/async)."""
    filename_str = _resolve_input(filename)
    return [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        filename_str,
    ]


def _parse_probe_output(stdout: bytes, cmd: list[str]) -> ProbeResult:
    """Decode ffprobe JSON stdout into a ``ProbeResult`` (pure; shared by sync/async).

    Performs the msgspec decode and per-type ordinal assignment. Raises
    ``FFmpegError`` on a decode/validation failure; the OSError and
    CalledProcessError handling stays in each I/O shell.

    Raises:
        FFmpegError: If the output cannot be parsed into a ``ProbeResult``.
    """
    try:
        parsed = msgspec.json.decode(stdout, type=ProbeResult)
    except (msgspec.DecodeError, msgspec.ValidationError) as e:
        logger.error(f"Failed to parse ffprobe output: {e}")
        raise _build_ffmpeg_error(f"ffprobe output parsing error: {e}", cmd=cmd) from e
    _assign_type_indices(parsed.streams)
    return parsed


def validate(
    filename: str | os.PathLike[str],
    ffprobe_path: str = "ffprobe",
    loglevel: str = "warning",
    extra_args: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """Run ffprobe in validation mode and report diagnostics.

    Does **not** raise on bad media — a non-zero exit or non-empty stderr is a
    normal outcome (returned as ``ok=False``). This makes it safe to use as a
    boolean health-check without wrapping in ``try/except``.

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
        ``(ok, stderr_text)`` where ``ok`` is ``True`` iff the ffprobe exit
        code is 0 *and* ``stderr.strip()`` is empty. ``stderr_text`` is the
        raw decoded stderr (never ``None``, possibly an empty string).

    Raises:
        FFmpegError: Only when the ffprobe executable itself cannot be run
            (e.g. not found on ``PATH``). A corrupt or unreadable media file
            does *not* raise — it returns ``(False, stderr_text)``.
        ValueError: If ``loglevel`` is not a recognised ffprobe loglevel
            keyword.

    Example:
        ```python
        import ffmpeg_wrap as ffmpeg

        ok, diag = ffmpeg.validate("recording.mkv")
        if not ok:
            print("Validation failed:", diag)
        ```
    """
    cmd = _build_validate_cmd(filename, ffprobe_path, loglevel, extra_args)
    try:
        result = subprocess.run(cmd, capture_output=True, check=False, text=False)
    except OSError as e:
        logger.error(f"ffprobe could not be executed: {e}")
        raise _build_ffmpeg_error(f"ffprobe could not be executed: {e}", cmd=cmd) from e
    return _interpret_validate(result.returncode, result.stderr)


def probe(filename: str | os.PathLike[str], ffprobe_path: str = "ffprobe") -> ProbeResult:
    """Run ffprobe on the specified file and return typed output.

    Args:
        filename: Path to the file to probe.
        ffprobe_path: Path to the ffprobe executable.

    Returns:
        Parsed and typed :class:`ProbeResult` containing all streams and
        container format information.

    Raises:
        FFmpegError: If ffprobe fails or the output cannot be parsed.

    Example:
        ```python
        import ffmpeg_wrap as ffmpeg

        result = ffmpeg.probe("video.mkv")
        print(result.format.format_name if result.format else None)
        for stream in result.streams:
            print(stream.index, stream.codec_type, stream.codec_name)
        ```
    """
    cmd = _build_probe_cmd(filename, ffprobe_path)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            text=False,
        )
    except subprocess.CalledProcessError as e:
        stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else None
        err_msg = stderr_text or str(e)
        logger.error(f"ffprobe failed: {err_msg}")
        raise _build_ffmpeg_error(
            f"ffprobe error: {err_msg}",
            stderr=stderr_text,
            returncode=e.returncode,
            cmd=cmd,
        ) from e
    except OSError as e:
        logger.error(f"ffprobe could not be executed: {e}")
        raise _build_ffmpeg_error(f"ffprobe could not be executed: {e}", cmd=cmd) from e
    return _parse_probe_output(result.stdout, cmd)
