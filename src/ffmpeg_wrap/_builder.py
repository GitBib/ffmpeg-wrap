from __future__ import annotations

import locale
import logging
import subprocess
import sys  # noqa: F401  # re-exported so tests can patch ``_builder.sys.stderr`` (the shared TeePump reads ``sys.stderr``)
import threading
import time
from collections.abc import Callable
from os import PathLike
from pathlib import Path
from typing import Any, Literal, overload

from ffmpeg_wrap import _textio
from ffmpeg_wrap._errors import FFmpegError, _build_ffmpeg_error, _build_ffmpeg_timeout_error
from ffmpeg_wrap._probe import Stream, _validate_timeout
from ffmpeg_wrap._textio import TeePump, decode_error_stderr, decode_text

logger = logging.getLogger("ffmpeg_wrap")


class FFmpeg:
    """A fluent interface wrapper for ffmpeg.

    Build an ffmpeg command incrementally via method chaining and execute it
    with :meth:`run` (sync) or :meth:`arun` (async). Every builder method
    returns ``self`` so calls can be chained in a single expression.

    Args:
        ffmpeg_path: Path to the ffmpeg executable. Defaults to ``"ffmpeg"``,
            which relies on the executable being present on ``PATH``. Pass an
            absolute path to pin a specific build.

    Example:
        ```python
        from ffmpeg_wrap import input

        out, _ = (
            input("in.mkv")
            .output("out.mp4")
            .codec("v", "libx264")
            .codec("a", "aac")
            .overwrite_output()
            .run(capture_stderr=True, text=True)
        )
        ```
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        """Initialise the FFmpeg command builder.

        Args:
            ffmpeg_path: Path to the ffmpeg executable. Defaults to
                ``"ffmpeg"`` (resolved from ``PATH``). Pass an absolute path
                to target a specific ffmpeg build, e.g.
                ``"/usr/local/bin/ffmpeg"``.

        Example:
            ```python
            from ffmpeg_wrap import FFmpeg

            ff = FFmpeg(ffmpeg_path="/usr/local/bin/ffmpeg")
            ff.input("in.mkv").output("out.mp4").run()
            ```
        """
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

        Example:
            ```python
            from ffmpeg_wrap import input

            cmd = input("in.mkv").output("out.mp4").codec("v", "copy").compile()
            # ['ffmpeg', '-i', '/abs/path/in.mkv', '-c:v', 'copy', 'out.mp4']
            print(cmd)
            ```
        """
        cmd = [self._ffmpeg_path]

        if self._overwrite:
            cmd.append("-y")

        cmd.extend(self._global_args)

        cmd.extend(self._filter_graph_args)

        for inp in self._inputs:
            for k, v in inp["kwargs"].items():
                cmd.extend(_convert_arg(k, v))
            cmd.extend(["-i", str(inp["filename"])])

        for out in self._outputs:
            for k, v in out["kwargs"].items():
                cmd.extend(_convert_arg(k, v))
            cmd.append(str(out["filename"]))

        return cmd

    def input(self, filename: str | PathLike[str], **kwargs: Any) -> FFmpeg:
        """Add an input file with optional arguments.

        Args:
            filename: Input filesystem path or special ffmpeg input string.
            **kwargs: FFmpeg input options (e.g., t=10).

        Returns:
            Self for chaining.

        Example:
            ```python
            from ffmpeg_wrap import FFmpeg

            ff = FFmpeg().input("clip.mkv", ss=30, t=60)
            # Emits: -ss 30 -t 60 -i clip.mkv
            ```
        """
        filename_str = filename if isinstance(filename, str) else str(Path(filename))
        self._inputs.append({"filename": filename_str, "kwargs": kwargs})
        return self

    def output(self, filename: str | PathLike[str], **kwargs: Any) -> FFmpeg:
        """Add an output file with optional arguments.

        Args:
            filename: Output filesystem path or special ffmpeg output string.
            **kwargs: FFmpeg output options (e.g., c="copy", ac=2).

        Returns:
            Self for chaining.

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp4", c="copy", ac=2)
            ```
        """
        filename_str = filename if isinstance(filename, str) else str(Path(filename))
        self._outputs.append({"filename": filename_str, "kwargs": kwargs})
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

        Example:
            ```python
            from ffmpeg_wrap import input, probe

            result = probe("in.mkv")
            video = next(s for s in result.streams if s.is_video)
            input("in.mkv").output("out.mkv").map("0:a", video)
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            # Map first video and second audio stream from input 0
            input("in.mkv").output("out.mkv").map_stream("v", 0).map_stream("a", 1)
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import FFmpeg

            ff = FFmpeg().input("in.mkv").hwaccel("cuda").output("out.mp4")
            # Emits: -hwaccel cuda -i in.mkv out.mp4
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp4").codec("v", "libx264").codec("a", "aac")
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp4").codec("v", "libx264").bitrate("v", "2M")
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp3").codec("a", "libmp3lame").quality("a", 2)
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mkv").audio_filter("loudnorm")
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp4").video_filter("scale=1280:-2")
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            # Extract audio only: suppress video and subtitle streams
            input("in.mkv").output("out.aac").flag("vn", "sn")
            ```
        """
        for name in names:
            self._set_output_option(name, True)
        return self

    def overwrite_output(self) -> FFmpeg:
        """Add the ``-y`` flag to overwrite output files without prompting.

        Returns:
            Self for chaining.

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp4").overwrite_output().run()
            ```
        """
        self._overwrite = True
        return self

    def global_args(self, *args: str) -> FFmpeg:
        """Add raw global arguments emitted before input options.

        Args:
            *args: Global arguments (e.g. ``"-nostdin"``, ``"-benchmark"``).

        Returns:
            Self for chaining.

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp4").global_args("-nostdin", "-benchmark")
            ```
        """
        self._global_args.extend(args)
        return self

    def hide_banner(self) -> FFmpeg:
        """Add the ``-hide_banner`` global flag.

        Thin sugar over :meth:`global_args`; emitted in the global slot.

        Returns:
            Self for chaining.

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp4").hide_banner().run()
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").output("out.mp4").loglevel("error").run()
            ```
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

        Example:
            ```python
            from ffmpeg_wrap import input

            (
                input("in.mkv")
                .filter_complex("[0:v]split=2[a][b];[a]scale=640:-2[out]")
                .output("out.mp4", map="[out]")
            )
            ```
        """
        self._filter_graph_args = ["-filter_complex", graph_str]
        return self

    def filter_complex_script(self, path: str | PathLike[str]) -> FFmpeg:
        """Read a graph-level filtergraph from a UTF-8 file.

        Reads the file when this method is called and emits its contents via
        ``-filter_complex`` in the same dedicated slot as :meth:`filter_complex`
        (after global args, before inputs). Mutually exclusive with
        :meth:`filter_complex` at runtime (not enforced here).

        Args:
            path: Path to a file containing the filtergraph.

        Returns:
            Self for chaining.

        Example:
            ```python
            from ffmpeg_wrap import input

            input("in.mkv").filter_complex_script("graph.txt").output("out.mp4").run()
            ```
        """
        graph_str = Path(path).read_text(encoding="utf-8")
        self._filter_graph_args = ["-filter_complex", graph_str]
        return self

    @overload
    def run(
        self,
        capture_stdout: bool = ...,
        capture_stderr: bool = ...,
        *,
        text: Literal[False] = ...,
        timeout: float | None = ...,
    ) -> tuple[bytes | None, bytes | None]: ...

    @overload
    def run(
        self,
        capture_stdout: bool = ...,
        capture_stderr: bool = ...,
        *,
        text: Literal[True],
        timeout: float | None = ...,
    ) -> tuple[str | None, str | None]: ...

    def run(
        self,
        capture_stdout: bool = False,
        capture_stderr: bool = False,
        *,
        text: bool = False,
        timeout: float | None = None,
    ) -> tuple[bytes | None, bytes | None] | tuple[str | None, str | None]:
        """Build and execute the FFmpeg command.

        Args:
            capture_stdout: Whether to capture stdout.
            capture_stderr: Whether to capture stderr.
            text: When True, decode stdout/stderr (and ``FFmpegError.stderr``)
                as text using the platform default encoding; otherwise return
                raw bytes.
            timeout: Wall-clock limit in seconds, or ``None`` (the default) for
                no limit. When it expires the child is killed and reaped, then
                :class:`~ffmpeg_wrap.FFmpegTimeoutError` is raised.

        Returns:
            Tuple of (stdout, stderr). Values are ``str`` when ``text=True``
            else ``bytes`` when the corresponding capture flag is True, or
            None otherwise.

        Raises:
            FFmpegError: If ffmpeg fails.
            FFmpegTimeoutError: If ``timeout`` expires before ffmpeg exits.
            ValueError: If ``timeout`` is not a positive finite number of at most 2147483.647 seconds.

        Example:
            ```python
            import ffmpeg_wrap as ffmpeg

            try:
                _, stderr = (
                    ffmpeg.input("in.mkv")
                    .output("out.mp4")
                    .overwrite_output()
                    .run(capture_stderr=True, text=True)
                )
            except ffmpeg.FFmpegError as e:
                print(e.returncode, e.stderr)
            ```
        """
        _validate_timeout(timeout)

        cmd = self.compile()

        stdout_dest = subprocess.PIPE if capture_stdout else None

        logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")

        try:
            if capture_stderr:
                # Caller wants stderr returned: capture it directly.
                if text:
                    process_text = subprocess.run(
                        cmd,
                        stdout=stdout_dest,
                        stderr=subprocess.PIPE,
                        check=True,
                        text=True,
                        timeout=timeout,
                    )
                    return process_text.stdout, process_text.stderr
                process_bytes = subprocess.run(
                    cmd,
                    stdout=stdout_dest,
                    stderr=subprocess.PIPE,
                    check=True,
                    text=False,
                    timeout=timeout,
                )
                return process_bytes.stdout, process_bytes.stderr
            # Caller does not capture stderr: tee ffmpeg's stderr to the
            # inherited stderr stream in real time (preserving the live
            # progress output of a bare ``run()``) while collecting it, so the
            # failure path can still populate ``FFmpegError.stderr`` without
            # silently buffering an entire successful run in memory.
            stdout_data = self._run_tee(cmd, stdout_dest, text, timeout)
            if isinstance(stdout_data, str):
                return stdout_data, None
            return stdout_data, None
        except subprocess.TimeoutExpired as e:
            timeout_stderr = decode_error_stderr(e.stderr, text=text, encoding=locale.getpreferredencoding(False))
            logger.error(f"FFmpeg command timed out after {e.timeout}s: {timeout_stderr or str(e)}")
            raise _build_ffmpeg_timeout_error(
                f"ffmpeg timed out after {e.timeout}s",
                timeout=e.timeout,
                stderr=timeout_stderr,
                cmd=cmd,
            ) from e
        except subprocess.CalledProcessError as e:
            if e.stderr is None:
                stderr_text: str | None = None
            elif isinstance(e.stderr, str):
                stderr_text = e.stderr
            else:
                stderr_text = e.stderr.decode("utf-8", errors="replace")
            err_msg = stderr_text or str(e)
            logger.error(f"FFmpeg command failed: {err_msg}")
            raise _build_ffmpeg_error(
                f"ffmpeg error: {err_msg}",
                stderr=stderr_text,
                returncode=e.returncode,
                cmd=cmd,
            ) from e
        except OSError as e:
            logger.error(f"ffmpeg could not be executed: {e}")
            raise _build_ffmpeg_error(f"ffmpeg could not be executed: {e}", cmd=cmd) from e

    @overload
    async def arun(
        self,
        capture_stdout: bool = ...,
        capture_stderr: bool = ...,
        *,
        text: Literal[False] = ...,
        timeout: float | None = ...,
    ) -> tuple[bytes | None, bytes | None]: ...

    @overload
    async def arun(
        self,
        capture_stdout: bool = ...,
        capture_stderr: bool = ...,
        *,
        text: Literal[True],
        timeout: float | None = ...,
    ) -> tuple[str | None, str | None]: ...

    async def arun(
        self,
        capture_stdout: bool = False,
        capture_stderr: bool = False,
        *,
        text: bool = False,
        timeout: float | None = None,
    ) -> tuple[bytes | None, bytes | None] | tuple[str | None, str | None]:
        """Build and execute the FFmpeg command asynchronously.

        Async mirror of :meth:`run`, powered by AnyIO via the optional
        ``[async]`` extra. The return contract is identical to :meth:`run`.
        Importing this module never imports anyio — the dependency is pulled in
        lazily here, so ``ffmpeg_wrap.aio`` (and thus anyio) is only loaded when
        ``arun`` is actually awaited.

        Args:
            capture_stdout: Whether to capture stdout.
            capture_stderr: Whether to capture stderr.
            text: When True, decode stdout/stderr (and ``FFmpegError.stderr``)
                leniently as text using the platform default encoding.
            timeout: Wall-clock limit in seconds, or ``None`` (the default) for
                no limit; see :func:`ffmpeg_wrap.aio.run`.

        Returns:
            Tuple of (stdout, stderr); see :meth:`run`.

        Raises:
            FFmpegError: If ffmpeg fails or cannot be executed.
            FFmpegTimeoutError: If ``timeout`` expires before ffmpeg exits.
            ValueError: If ``timeout`` is not a positive finite number of at most 2147483.647 seconds.
            ImportError: If the optional ``[async]`` extra is not installed.

        Example:
            ```python
            import anyio
            from ffmpeg_wrap import input

            async def main():
                _, stderr = await (
                    input("in.mkv")
                    .output("out.mp4")
                    .overwrite_output()
                    .arun(capture_stderr=True, text=True)
                )

            anyio.run(main)
            ```
        """
        from ffmpeg_wrap import aio

        return await aio.run(self, capture_stdout, capture_stderr, text=text, timeout=timeout)

    # Thin re-exports of the ``_textio`` versions so existing call sites and
    # any tests referencing ``FFmpeg._decode_text`` / ``FFmpeg._STDERR_TAIL_BYTES``
    # keep working. The single source of truth lives in ``_textio``.
    _TIMEOUT_JOIN_SECONDS = 5.0
    _DRAIN_GRACE_SECONDS = 1.0
    _STDERR_TAIL_BYTES = _textio.STDERR_TAIL_BYTES
    _decode_text = staticmethod(decode_text)

    @staticmethod
    def _run_tee(
        cmd: list[str],
        stdout_dest: int | None,
        text: bool,
        timeout: float | None = None,
    ) -> bytes | str | None:
        """Run ``cmd`` forwarding stderr to the terminal while collecting it.

        The child runs in binary mode so the pump can forward stderr in fixed
        chunks (``read1``) as bytes arrive. ffmpeg writes its progress as
        carriage-return-terminated updates (``frame=...\\r``) rather than
        newline-terminated lines, so a line-oriented read would withhold all of
        it until EOF; chunked reads preserve the live progress of a bare
        ``run()``. Only a bounded tail of stderr is retained (in the shared
        :class:`~ffmpeg_wrap._textio.TeePump`), and it is decoded and joined
        solely on the failure path — successful runs keep memory flat. A piped
        stdout is drained by a second thread so the calling thread can wait on
        the child with ``timeout``; on expiry the child is killed and reaped,
        the drain threads get a bounded join, and ``TimeoutExpired`` is raised
        with the collected stderr tail. When the child exits in time the drains
        are joined until the same deadline plus a short grace, so the last
        pipe-buffered chunks of a child that finished just before the limit are
        still collected, while a descendant that inherited a pipe and outlives
        the child is reported as expiry: ``timeout`` bounds the whole call
        rather than just the child's lifetime. Each pipe is closed only by the
        thread that reads it, so a drain still blocked on such a grandchild, or
        on a stuck stderr sink, cannot stall the raise; a drain that fails
        closes its pipe regardless and its exception is re-raised here once the
        child has been reaped. On a non-zero exit raises ``CalledProcessError``
        carrying the collected stderr (and stdout) so the caller can build
        ``FFmpegError``.
        """
        encoding = locale.getpreferredencoding(False)
        pump_state = TeePump(encoding)
        stdout_buffer = bytearray()

        drain_errors: list[BaseException] = []

        def _drain(stream: Any, sink: Callable[[bytes], object]) -> None:
            # Only the read loop lives here; per-chunk forwarding + bounded-tail
            # bookkeeping are owned by the shared ``TeePump`` (same code the
            # async tee task feeds), and a piped stdout accumulates in
            # ``stdout_buffer``.
            reader = stream.read1 if hasattr(stream, "read1") else stream.read
            try:
                while True:
                    chunk = reader(65536)
                    if not chunk:
                        break
                    sink(chunk)
            except BaseException as e:
                drain_errors.append(e)
            finally:
                stream.close()

        timed_out_after: float | None = None
        process = subprocess.Popen(cmd, stdout=stdout_dest, stderr=subprocess.PIPE)
        targets: list[tuple[Any, Callable[[bytes], object]]] = [(process.stderr, pump_state.feed)]
        if process.stdout is not None:
            targets.append((process.stdout, stdout_buffer.extend))
        drains: list[threading.Thread] = []
        started = time.monotonic()
        try:
            for stream, sink in targets:
                drain = threading.Thread(target=_drain, args=(stream, sink), daemon=True)
                drain.start()
                drains.append(drain)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            timed_out_after = e.timeout
            process.kill()
            process.wait()
            join_deadline = time.monotonic() + FFmpeg._TIMEOUT_JOIN_SECONDS
            for drain in drains:
                drain.join(timeout=max(0.0, join_deadline - time.monotonic()))
        except BaseException:
            process.kill()
            process.wait()
            for stream, _ in targets[len(drains) :]:
                stream.close()
            raise
        else:
            if timeout is None:
                for drain in drains:
                    drain.join()
            else:
                join_deadline = max(started + timeout, time.monotonic() + FFmpeg._DRAIN_GRACE_SECONDS)
                for drain in drains:
                    drain.join(timeout=max(0.0, join_deadline - time.monotonic()))
                    if drain.is_alive():
                        timed_out_after = timeout
                        break

        if drain_errors:
            raise drain_errors[0]
        stdout_data: bytes | str | None = bytes(stdout_buffer) if process.stdout is not None else None
        # The returned stdout and ``FFmpegError.stderr`` mirror
        # ``subprocess.run(text=True)`` (see ``decode_text``): platform-default
        # encoding with universal-newline translation, leniently decoded.
        if text and isinstance(stdout_data, bytes):
            stdout_data = decode_text(stdout_data, encoding)
        if timed_out_after is not None or process.returncode:
            stderr_bytes = pump_state.tail_bytes()
            stderr_data: bytes | str = decode_text(stderr_bytes, encoding) if text else stderr_bytes
            if timed_out_after is not None:
                raise subprocess.TimeoutExpired(cmd, timed_out_after, output=stdout_data, stderr=stderr_data or None)
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


def input(filename: str | PathLike[str], ffmpeg_path: str = "ffmpeg", **kwargs: Any) -> FFmpeg:
    """Start a new FFmpeg chain with an input file.

    This is the recommended entry point for building a fluent chain. It creates
    a fresh :class:`FFmpeg` instance and calls :meth:`~FFmpeg.input` on it.

    Args:
        filename: Input filesystem path or special ffmpeg input string.
        ffmpeg_path: Path to the ffmpeg executable. Defaults to ``"ffmpeg"``
            (resolved from ``PATH``).
        **kwargs: FFmpeg input options passed through to the input slot
            (e.g. ``ss=30``, ``t=60``).

    Returns:
        A new :class:`FFmpeg` instance with the input added, ready for further
        chaining.

    Example:
        ```python
        from ffmpeg_wrap import input

        input("in.mkv", ss=30, t=60).output("clip.mp4").codec("v", "copy").run()
        ```
    """
    instance = FFmpeg(ffmpeg_path=ffmpeg_path)
    return instance.input(filename, **kwargs)
