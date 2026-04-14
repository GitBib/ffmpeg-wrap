import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import msgspec
import pytest

from ffmpeg_wrap._errors import FFmpegError
from ffmpeg_wrap._probe import Format, ProbeResult, Stream, probe


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
        with pytest.raises(FFmpegError, match="ffprobe not found"):
            probe("video.mkv")
