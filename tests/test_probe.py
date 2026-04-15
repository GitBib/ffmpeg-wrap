import contextlib
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import msgspec
import pytest

from ffmpeg_wrap._errors import FFmpegError
from ffmpeg_wrap._probe import Format, ProbeResult, Stream, _resolve_input, probe, validate


class TestResolveInput:
    def test_regular_string_path(self):
        result = _resolve_input("video.mkv")
        assert result == str(Path("video.mkv").resolve())

    def test_path_object(self):
        result = _resolve_input(Path("video.mkv"))
        assert result == str(Path("video.mkv").resolve())

    def test_pipe_protocol(self):
        assert _resolve_input("pipe:") == "pipe:"
        assert _resolve_input("pipe:0") == "pipe:0"

    def test_url(self):
        assert _resolve_input("http://example.com/video.mp4") == "http://example.com/video.mp4"
        assert _resolve_input("rtmp://stream.example.com/live") == "rtmp://stream.example.com/live"

    def test_bytes_pathlike(self):
        class BytesPath(os.PathLike):
            def __fspath__(self):
                return b"video.mkv"

        result = _resolve_input(BytesPath())
        assert result == str(Path("video.mkv").resolve())

    def test_protocol_looking_filename_md5(self):
        result = _resolve_input("md5:clip.mkv")
        assert result == str(Path("md5:clip.mkv").resolve())

    def test_protocol_looking_filename_tee(self):
        result = _resolve_input("tee:output.mkv")
        assert result == str(Path("tee:output.mkv").resolve())

    def test_dash_stdin(self):
        assert _resolve_input("-") == "-"

    def test_posix_colon_in_filename(self):
        result = _resolve_input("foo:bar.mkv")
        assert result == str(Path("foo:bar.mkv").resolve())


class TestStream:
    def test_create_with_all_fields(self):
        stream = Stream(
            index=0,
            codec_name="h264",
            codec_type="video",
            width=1920,
            height=1080,
            channels=None,
            sample_rate=None,
            duration="120.5",
            bit_rate="5000000",
            tags={"language": "eng"},
            disposition={"default": 1, "forced": 0},
        )
        assert stream.index == 0
        assert stream.codec_name == "h264"
        assert stream.codec_type == "video"
        assert stream.width == 1920
        assert stream.height == 1080
        assert stream.channels is None
        assert stream.sample_rate is None
        assert stream.duration == "120.5"
        assert stream.bit_rate == "5000000"
        assert stream.tags == {"language": "eng"}
        assert stream.disposition == {"default": 1, "forced": 0}

    def test_create_with_defaults(self):
        stream = Stream(index=1)
        assert stream.index == 1
        assert stream.codec_name is None
        assert stream.codec_type is None
        assert stream.width is None
        assert stream.height is None
        assert stream.tags is None


class TestFormat:
    def test_create_with_all_fields(self):
        fmt = Format(
            filename="video.mkv",
            nb_streams=2,
            nb_programs=0,
            format_name="matroska,webm",
            format_long_name="Matroska / WebM",
            start_time="0.000000",
            duration="120.500000",
            size="75000000",
            bit_rate="4979253",
            probe_score=100,
            tags={"title": "My Video"},
        )
        assert fmt.filename == "video.mkv"
        assert fmt.nb_streams == 2
        assert fmt.nb_programs == 0
        assert fmt.format_name == "matroska,webm"
        assert fmt.format_long_name == "Matroska / WebM"
        assert fmt.start_time == "0.000000"
        assert fmt.duration == "120.500000"
        assert fmt.size == "75000000"
        assert fmt.bit_rate == "4979253"
        assert fmt.probe_score == 100
        assert fmt.tags == {"title": "My Video"}

    def test_create_with_defaults(self):
        fmt = Format()
        assert fmt.filename is None
        assert fmt.nb_streams is None
        assert fmt.duration is None
        assert fmt.tags is None


SAMPLE_FFPROBE_JSON = msgspec.json.encode(
    {
        "streams": [
            {
                "index": 0,
                "codec_name": "h264",
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            },
            {
                "index": 1,
                "codec_name": "aac",
                "codec_type": "audio",
                "channels": 2,
                "sample_rate": "48000",
            },
        ],
        "format": {
            "filename": "video.mkv",
            "nb_streams": 2,
            "format_name": "matroska,webm",
            "duration": "120.500000",
            "size": "75000000",
        },
    }
)


class TestProbeResult:
    def test_parse_valid_json(self):
        result = msgspec.json.decode(SAMPLE_FFPROBE_JSON, type=ProbeResult)
        assert len(result.streams) == 2
        assert result.streams[0].codec_name == "h264"
        assert result.streams[0].width == 1920
        assert result.streams[1].codec_name == "aac"
        assert result.streams[1].channels == 2
        assert result.format is not None
        assert result.format.filename == "video.mkv"
        assert result.format.duration == "120.500000"


class TestProbeFunction:
    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_returns_result(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=SAMPLE_FFPROBE_JSON, stderr=b""
        )
        result = probe("video.mkv")
        assert isinstance(result, ProbeResult)
        assert len(result.streams) == 2
        assert result.streams[0].codec_name == "h264"
        assert result.format.duration == "120.500000"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_raises_on_subprocess_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd=["ffprobe"], stderr=b"No such file")
        with pytest.raises(FFmpegError, match="ffprobe error"):
            probe("missing.mkv")

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_raises_on_malformed_json(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"not json at all", stderr=b""
        )
        with pytest.raises(FFmpegError, match="parsing error"):
            probe("video.mkv")

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_raises_on_invalid_schema(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=msgspec.json.encode({"streams": "not_a_list"}),
            stderr=b"",
        )
        with pytest.raises(FFmpegError, match="parsing error"):
            probe("video.mkv")

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_builds_correct_command(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=msgspec.json.encode({"streams": []}),
            stderr=b"",
        )
        probe("video.mkv")
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "ffprobe"
        assert "-v" in cmd
        assert "quiet" in cmd
        assert "-print_format" in cmd
        assert "json" in cmd
        assert "-show_format" in cmd
        assert "-show_streams" in cmd

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_custom_ffprobe_path(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=msgspec.json.encode({"streams": []}),
            stderr=b"",
        )
        probe("video.mkv", ffprobe_path="/usr/local/bin/ffprobe")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/ffprobe"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_pathlike(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=msgspec.json.encode({"streams": []}),
            stderr=b"",
        )
        probe(Path("video.mkv"))
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("video.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_url_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=msgspec.json.encode({"streams": []}),
            stderr=b"",
        )
        probe("http://example.com/video.mp4")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "http://example.com/video.mp4"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_pipe_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("pipe:")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "pipe:"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_pipe_fd_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("pipe:0")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "pipe:0"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_dash_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("-")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "-"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_concat_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("concat:file1.ts|file2.ts")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "concat:file1.ts|file2.ts"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_file_protocol_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("file:video.mkv")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "file:video.mkv"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_bluray_protocol_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("bluray:/path/to/disc")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "bluray:/path/to/disc"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_concatf_protocol_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("concatf:filelist.txt")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "concatf:filelist.txt"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_colon_in_filename_resolves_path(self, mock_run):
        """POSIX filenames with colons must not be mistaken for ffmpeg protocols."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("foo:bar.mkv")
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("foo:bar.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_md5_filename_resolves_path(self, mock_run):
        """md5: is an output-only protocol, so md5:clip.mkv must resolve as a path."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("md5:clip.mkv")
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("md5:clip.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_tee_filename_resolves_path(self, mock_run):
        """tee: is an output-only protocol, so tee:output.mkv must resolve as a path."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("tee:output.mkv")
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("tee:output.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_crypto_plus_nesting_skips_resolve(self, mock_run):
        """crypto+file:video.mkv uses protocol nesting and must not be resolved."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("crypto+file:video.mkv")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "crypto+file:video.mkv"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_subfile_options_skips_resolve(self, mock_run):
        """subfile,,start,0,end,1024,,:/path/to/file uses option block syntax."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("subfile,,start,0,end,1024,,:/path/to/file")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "subfile,,start,0,end,1024,,:/path/to/file"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_plus_in_filename_resolves_path(self, mock_run):
        """POSIX filenames like pipe+notes:clip.mkv must not be mistaken for protocol nesting."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("pipe+notes:clip.mkv")
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("pipe+notes:clip.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_comma_in_filename_resolves_path(self, mock_run):
        """POSIX filenames like file,backup:clip.mkv must not be mistaken for option syntax."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe("file,backup:clip.mkv")
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("file,backup:clip.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_pathlike_with_protocol_name_resolves_path(self, mock_run):
        """PathLike inputs must always be resolved, even if the string looks like a protocol."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe(Path("file:video.mkv"))
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("file:video.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_with_bytes_pathlike(self, mock_run):
        """A PathLike returning bytes must be decoded and resolved, not crash."""

        class BytesPath(os.PathLike):
            def __fspath__(self):
                return b"video.mkv"

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=msgspec.json.encode({"streams": []}), stderr=b""
        )
        probe(BytesPath())
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("video.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_raises_ffmpeg_error_on_missing_binary(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'ffprobe'")
        with pytest.raises(FFmpegError, match="ffprobe could not be executed"):
            probe("video.mkv")

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_raises_ffmpeg_error_on_permission_error(self, mock_run):
        mock_run.side_effect = PermissionError("[Errno 13] Permission denied: '/usr/bin/ffprobe'")
        with pytest.raises(FFmpegError, match="ffprobe could not be executed"):
            probe("video.mkv")


class TestValidate:
    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_returns_true_on_clean_file(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        ok, stderr = validate("video.mkv")
        assert ok is True
        assert stderr == ""

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_returns_false_on_nonzero_exit(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"moov atom not found"
        )
        ok, stderr = validate("broken.mkv")
        assert ok is False
        assert stderr == "moov atom not found"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_returns_false_on_warnings_with_zero_exit(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b"non-monotonic DTS"
        )
        ok, stderr = validate("warning.mkv")
        assert ok is False
        assert stderr == "non-monotonic DTS"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_returns_false_on_nonexistent_file(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"No such file")
        ok, stderr = validate("/nonexistent/file.mkv")
        assert ok is False
        assert stderr == "No such file"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_raises_on_missing_binary(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'ffprobe'")
        with pytest.raises(FFmpegError, match="ffprobe could not be executed"):
            validate("video.mkv")

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_builds_correct_command(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("video.mkv")
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("video.mkv").resolve())
        assert cmd == ["ffprobe", "-v", "warning", resolved]

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_custom_loglevel(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("video.mkv", loglevel="error")
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("video.mkv").resolve())
        assert cmd == ["ffprobe", "-v", "error", resolved]

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_extra_args_forwarded_before_filename(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("video.mkv", loglevel="error", extra_args=("-show_format", "-hide_banner"))
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("video.mkv").resolve())
        assert cmd == ["ffprobe", "-v", "error", "-show_format", "-hide_banner", resolved]

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_custom_ffprobe_path(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("video.mkv", ffprobe_path="/usr/local/bin/ffprobe")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/ffprobe"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_with_pathlike(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate(Path("video.mkv"))
        cmd = mock_run.call_args[0][0]
        resolved = str(Path("video.mkv").resolve())
        assert cmd[-1] == resolved

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_with_pipe_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("pipe:")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "pipe:"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_with_url_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("http://example.com/video.mp4")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "http://example.com/video.mp4"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_strips_whitespace_only_stderr(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"   \n")
        ok, stderr = validate("video.mkv")
        assert ok is True
        assert stderr == "   \n"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_preserves_utf8_in_stderr(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr="ошибка кодека".encode()
        )
        ok, stderr = validate("video.mkv")
        assert ok is False
        assert stderr == "ошибка кодека"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_with_dash_skips_resolve(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("-")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "-"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_replaces_invalid_utf8_in_stderr(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"bad codec \xff\xfe data"
        )
        ok, stderr = validate("video.mkv")
        assert ok is False
        assert "\ufffd" in stderr

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_subprocess_kwargs(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("video.mkv")
        kwargs = mock_run.call_args[1]
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_raises_on_permission_error(self, mock_run):
        mock_run.side_effect = PermissionError("[Errno 13] Permission denied: '/usr/bin/ffprobe'")
        with pytest.raises(FFmpegError, match="ffprobe could not be executed"):
            validate("video.mkv")

    def test_validate_raises_on_invalid_loglevel(self):
        with pytest.raises(ValueError, match="invalid loglevel"):
            validate("video.mkv", loglevel="warningg")

    def test_validate_accepts_all_valid_loglevels(self):
        valid = {"quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"}
        for level in valid:
            with contextlib.suppress(FFmpegError, OSError):
                validate("video.mkv", loglevel=level)

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_extra_args_coerced_to_str(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        validate("video.mkv", extra_args=(Path("-hide_banner"),))
        cmd = mock_run.call_args[0][0]
        assert "-hide_banner" in cmd
        assert all(isinstance(a, str) for a in cmd)
