from __future__ import annotations

import collections
import locale
import logging
import os
import subprocess
import sys
import threading
from typing import Any, Literal, overload

from ._errors import FFmpegError
from ._probe import Stream

logger = logging.getLogger("ffmpeg_wrap")


class FFmpeg:
    """A fluent interface wrapper for ffmpeg."""

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self._inputs: list[dict[str, Any]] = []
        self._outputs: list[dict[str, Any]] = []
        self._global_args: list[str] = []
        self._filter_graph_args: list[str] = []
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

        cmd.extend(self._filter_graph_args)

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

    def _append_output_list(self, key: str, values: list[str]) -> None:
        """Append ``values`` to a repeatable option list on the current output.

        Normalizes any existing scalar value (e.g. from ``output(map="0:v")``)
        into a list so that mixing the kwarg and method forms is consistent.
        """
        if not self._outputs:
            msg = f"call .output(...) before .{key}(...)"
            raise FFmpegError(msg)
        kwargs = self._outputs[-1]["kwargs"]
        existing = kwargs.get(key)
        if existing is None:
            items: list[str] = []
        elif isinstance(existing, (list, tuple)):
            items = list(existing)
        else:
            items = [existing]
        items.extend(values)
        kwargs[key] = items

    def map(self, *specs: str | Stream) -> FFmpeg:
        """Add one or more ``-map`` specifiers to the current output.

        Accepts raw specifier strings (``"0:v"``) and ``Stream`` objects. For a
        ``Stream``, the per-type specifier (e.g. ``0:s:0``) is used, computed
        from probe data rather than the raw absolute index.

        Args:
            *specs: Map specifiers as strings or ``Stream`` objects.

        Returns:
            Self for chaining.
        """
        tokens = [spec.map_specifier() if isinstance(spec, Stream) else str(spec) for spec in specs]
        self._append_output_list("map", tokens)
        return self

    def map_stream(self, kind: str, ordinal: int, input: int = 0) -> FFmpeg:
        """Add a per-type ``-map`` specifier to the current output.

        Args:
            kind: Stream-type letter (e.g. ``"v"``, ``"a"``, ``"s"``).
            ordinal: Zero-based index within that stream type.
            input: Index of the ffmpeg input to map from.

        Returns:
            Self for chaining.
        """
        self._append_output_list("map", [f"{input}:{kind}:{ordinal}"])
        return self

    def _set_input_option(self, key: str, value: Any) -> None:
        """Set a single option on the current input's kwargs.

        Raises ``FFmpegError`` if no input has been added yet.
        """
        if not self._inputs:
            msg = f"call .input(...) before setting option {key!r}"
            raise FFmpegError(msg)
        self._inputs[-1]["kwargs"][key] = value

    def hwaccel(self, name: str) -> FFmpeg:
        """Request a hardware-acceleration backend on the current input.

        Discoverable sugar for ``input(..., hwaccel=name)``: emits
        ``-hwaccel {name}`` before the current input's ``-i`` (e.g.
        ``hwaccel("cuda")`` -> ``-hwaccel cuda -i in.mkv``).

        Args:
            name: Hardware-acceleration backend (e.g. ``"cuda"``, ``"vaapi"``).

        Returns:
            Self for chaining.
        """
        self._set_input_option("hwaccel", name)
        return self

    def _set_output_option(self, key: str, value: Any) -> None:
        """Set a single option on the current output's kwargs.

        Raises ``FFmpegError`` if no output has been added yet.
        """
        if not self._outputs:
            msg = f"call .output(...) before setting option {key!r}"
            raise FFmpegError(msg)
        self._outputs[-1]["kwargs"][key] = value

    def codec(self, kind: str, name: str) -> FFmpeg:
        """Set the codec for a stream type on the current output.

        Emits ``-c:{kind} {name}`` (e.g. ``codec("v", "libx265")`` ->
        ``-c:v libx265``).

        Args:
            kind: Stream-type letter (e.g. ``"v"``, ``"a"``, ``"s"``).
            name: Codec name (e.g. ``"libx265"``, ``"copy"``).

        Returns:
            Self for chaining.
        """
        self._set_output_option(f"c:{kind}", name)
        return self

    def bitrate(self, kind: str, value: str | int) -> FFmpeg:
        """Set the bitrate for a stream type on the current output.

        Emits ``-b:{kind} {value}`` (e.g. ``bitrate("v", "2M")`` ->
        ``-b:v 2M``).

        Args:
            kind: Stream-type letter (e.g. ``"v"``, ``"a"``).
            value: Bitrate value (e.g. ``"2M"``, ``128000``).

        Returns:
            Self for chaining.
        """
        self._set_output_option(f"b:{kind}", value)
        return self

    def quality(self, kind: str, value: str | int) -> FFmpeg:
        """Set the quality scale for a stream type on the current output.

        Emits ``-q:{kind} {value}`` (e.g. ``quality("a", 2)`` -> ``-q:a 2``).

        Args:
            kind: Stream-type letter (e.g. ``"v"``, ``"a"``).
            value: Quality value.

        Returns:
            Self for chaining.
        """
        self._set_output_option(f"q:{kind}", value)
        return self

    def audio_filter(self, chain: str) -> FFmpeg:
        """Set the audio filter chain on the current output.

        Emits ``-filter:a {chain}``.

        Args:
            chain: Filter chain string (e.g. ``"loudnorm"``).

        Returns:
            Self for chaining.
        """
        self._set_output_option("filter:a", chain)
        return self

    def video_filter(self, chain: str) -> FFmpeg:
        """Set the video filter chain on the current output.

        Emits ``-filter:v {chain}``.

        Args:
            chain: Filter chain string (e.g. ``"scale=1280:-2"``).

        Returns:
            Self for chaining.
        """
        self._set_output_option("filter:v", chain)
        return self

    def flag(self, *names: str) -> FFmpeg:
        """Add one or more valueless switches to the current output.

        Emits a bare ``-{name}`` token per name (e.g. ``flag("vn", "sn")`` ->
        ``-vn -sn``). This is the intentional way to request a switch; note the
        contrast with ``output(..., vn=None)``, which *omits* the flag entirely.

        Args:
            *names: Switch names without the leading dash (e.g. ``"vn"``).

        Returns:
            Self for chaining.
        """
        for name in names:
            self._set_output_option(name, True)
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

    def hide_banner(self) -> FFmpeg:
        """Add the ``-hide_banner`` global flag.

        Thin sugar over :meth:`global_args`; emitted in the global slot.

        Returns:
            Self for chaining.
        """
        self._global_args.append("-hide_banner")
        return self

    def loglevel(self, level: str) -> FFmpeg:
        """Set the ffmpeg log level via ``-loglevel {level}``.

        Thin sugar over :meth:`global_args`; emitted in the global slot.

        Args:
            level: Log level (e.g. ``"error"``, ``"warning"``, ``"verbose"``).

        Returns:
            Self for chaining.
        """
        self._global_args.extend(["-loglevel", level])
        return self

    def filter_complex(self, graph_str: str) -> FFmpeg:
        """Set a graph-level ``-filter_complex`` filtergraph.

        Emits ``-filter_complex <graph_str>`` in a dedicated slot after global
        args and before inputs, so placement no longer depends on parking the
        option on an output. Mutually exclusive with
        :meth:`filter_complex_script` at runtime (not enforced here).

        Args:
            graph_str: The filtergraph string (e.g.
                ``"[0:v]split=2[a][b]"``).

        Returns:
            Self for chaining.
        """
        self._filter_graph_args = ["-filter_complex", graph_str]
        return self

    def filter_complex_script(self, path: str | os.PathLike[str]) -> FFmpeg:
        """Read a graph-level filtergraph from a file via ``-filter_complex_script``.

        Emits ``-filter_complex_script <path>`` in the same dedicated slot as
        :meth:`filter_complex` (after global args, before inputs). Mutually
        exclusive with :meth:`filter_complex` at runtime (not enforced here).

        Args:
            path: Path to a file containing the filtergraph.

        Returns:
            Self for chaining.
        """
        self._filter_graph_args = ["-filter_complex_script", os.fsdecode(path)]
        return self

    @overload
    def run(
        self,
        capture_stdout: bool = ...,
        capture_stderr: bool = ...,
        *,
        text: Literal[False] = ...,
    ) -> tuple[bytes | None, bytes | None]: ...

    @overload
    def run(
        self,
        capture_stdout: bool = ...,
        capture_stderr: bool = ...,
        *,
        text: Literal[True],
    ) -> tuple[str | None, str | None]: ...

    def run(
        self,
        capture_stdout: bool = False,
        capture_stderr: bool = False,
        *,
        text: bool = False,
    ) -> tuple[bytes | None, bytes | None] | tuple[str | None, str | None]:
        """Build and execute the FFmpeg command.

        Args:
            capture_stdout: Whether to capture stdout.
            capture_stderr: Whether to capture stderr.
            text: When True, decode stdout/stderr (and ``FFmpegError.stderr``)
                as text using the platform default encoding; otherwise return
                raw bytes.

        Returns:
            Tuple of (stdout, stderr). Values are ``str`` when ``text=True``
            else ``bytes`` when the corresponding capture flag is True, or
            None otherwise.

        Raises:
            FFmpegError: If ffmpeg fails.
        """
        cmd = self.compile()

        stdout_dest = subprocess.PIPE if capture_stdout else None

        logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")

        try:
            if capture_stderr:
                # Caller wants stderr returned: capture it directly.
                process = subprocess.run(
                    cmd,
                    stdout=stdout_dest,
                    stderr=subprocess.PIPE,
                    check=True,
                    text=text,
                )
                return process.stdout, process.stderr
            # Caller does not capture stderr: tee ffmpeg's stderr to the
            # inherited stderr stream in real time (preserving the live
            # progress output of a bare ``run()``) while collecting it, so the
            # failure path can still populate ``FFmpegError.stderr`` without
            # silently buffering an entire successful run in memory.
            stdout_data = self._run_tee(cmd, stdout_dest, text)
            return stdout_data, None
        except subprocess.CalledProcessError as e:
            if e.stderr is None:
                stderr_text: str | None = None
            elif isinstance(e.stderr, str):
                stderr_text = e.stderr
            else:
                stderr_text = e.stderr.decode("utf-8", errors="replace")
            err_msg = stderr_text or str(e)
            logger.error(f"FFmpeg command failed: {err_msg}")
            raise FFmpegError(
                f"ffmpeg error: {err_msg}",
                stderr=stderr_text,
                returncode=e.returncode,
                cmd=cmd,
            ) from e
        except OSError as e:
            logger.error(f"ffmpeg could not be executed: {e}")
            raise FFmpegError(f"ffmpeg could not be executed: {e}", cmd=cmd) from e

    # Maximum stderr tail retained for ``FFmpegError`` (ffmpeg's diagnostics
    # land at the end of the stream, so a bounded tail keeps memory flat on
    # long jobs while still capturing the actionable error text).
    _STDERR_TAIL_BYTES = 256 * 1024

    @staticmethod
    def _decode_text(data: bytes, encoding: str) -> str:
        """Decode tee-path bytes the way ``subprocess.run(text=True)`` returns text.

        Mirrors subprocess's universal-newline translation (``\\r\\n`` and
        ``\\r`` collapse to ``\\n``) so the returned stdout and
        ``FFmpegError.stderr`` have the same shape regardless of which ``run()``
        path produced them. Decoding stays lenient (``errors="replace"``)
        rather than subprocess's strict default: a stray byte must never turn a
        finished run — or the error report for a failed one — into a
        ``UnicodeDecodeError``.
        """
        return data.decode(encoding, errors="replace").replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _run_tee(cmd: list[str], stdout_dest: int | None, text: bool) -> bytes | str | None:
        """Run ``cmd`` forwarding stderr to the terminal while collecting it.

        The child runs in binary mode so the pump can forward stderr in fixed
        chunks (``read1``) as bytes arrive. ffmpeg writes its progress as
        carriage-return-terminated updates (``frame=...\\r``) rather than
        newline-terminated lines, so a line-oriented read would withhold all of
        it until EOF; chunked reads preserve the live progress of a bare
        ``run()``. Only a bounded tail of stderr is retained, and it is decoded
        and joined solely on the failure path — successful runs keep memory
        flat. On a non-zero exit raises ``CalledProcessError`` carrying the
        collected stderr (and stdout) so the caller can build ``FFmpegError``.
        """
        encoding = locale.getpreferredencoding(False)
        # ``sys.stderr`` is normally a text wrapper over a binary ``.buffer``,
        # which takes the raw byte chunks directly. When the active
        # ``sys.stderr`` is text-only (no ``.buffer`` — e.g. a capturing
        # wrapper) or ``None``, fall back and decode per chunk so the live tee
        # is preserved instead of silently dropped on a bytes-to-text write.
        buffer = getattr(sys.stderr, "buffer", None)
        if buffer is not None:
            sink, sink_is_text = buffer, False
        else:
            sink, sink_is_text = sys.stderr, True
        tail: collections.deque[bytes] = collections.deque()
        tail_len = 0

        def _pump(stream: Any) -> None:
            nonlocal tail_len
            reader = stream.read1 if hasattr(stream, "read1") else stream.read
            while True:
                chunk = reader(65536)
                if not chunk:
                    break
                try:
                    # Forward raw bytes (or a lenient per-chunk decode for a
                    # text-only sink) WITHOUT newline translation, so ffmpeg's
                    # ``\r`` progress updates still overwrite in place on the
                    # terminal rather than scrolling.
                    sink.write(chunk.decode(encoding, errors="replace") if sink_is_text else chunk)
                    sink.flush()
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
                tail.append(chunk)
                tail_len += len(chunk)
                while tail_len > FFmpeg._STDERR_TAIL_BYTES and len(tail) > 1:
                    tail_len -= len(tail.popleft())
            stream.close()

        with subprocess.Popen(cmd, stdout=stdout_dest, stderr=subprocess.PIPE) as process:
            pump = threading.Thread(target=_pump, args=(process.stderr,), daemon=True)
            pump.start()
            stdout_data = process.stdout.read() if process.stdout is not None else None
            process.wait()
            pump.join()

        # The returned stdout and ``FFmpegError.stderr`` mirror
        # ``subprocess.run(text=True)`` (see ``_decode_text``): platform-default
        # encoding with universal-newline translation, leniently decoded.
        if text and isinstance(stdout_data, bytes):
            stdout_data = FFmpeg._decode_text(stdout_data, encoding)
        if process.returncode:
            stderr_bytes = b"".join(tail)
            stderr_data: bytes | str = FFmpeg._decode_text(stderr_bytes, encoding) if text else stderr_bytes
            raise subprocess.CalledProcessError(process.returncode, cmd, output=stdout_data, stderr=stderr_data)
        return stdout_data


def _convert_arg(key: str, value: Any) -> list[str]:
    """Convert a kwarg pair to command-line arguments."""
    if value is None or value is False:
        return []
    if isinstance(value, (list, tuple)):
        # Repeatable flag: emit it once per element (``map=["0:v", "1:a"]`` ->
        # ``-map 0:v -map 1:a``). Each element reuses the scalar rules above.
        args: list[str] = []
        for item in value:
            args.extend(_convert_arg(key, item))
        return args
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
