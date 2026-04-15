import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ffmpeg_wrap._builder import FFmpeg, _convert_arg
from ffmpeg_wrap._builder import input as ffmpeg_input
from ffmpeg_wrap._errors import FFmpegError


class TestFluentChain:
    def test_input_output_builds_correct_command(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4", c="copy")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-c", "copy", "out.mp4"]

    def test_multiple_inputs_and_outputs(self):
        ff = FFmpeg()
        ff.input("a.mkv").input("b.mkv").output("out.mp4", c="copy")
        assert ff.compile() == [
            "ffmpeg",
            "-i",
            "a.mkv",
            "-i",
            "b.mkv",
            "-c",
            "copy",
            "out.mp4",
        ]

    def test_input_with_kwargs(self):
        ff = FFmpeg()
        ff.input("in.mkv", t=10, ss=30).output("out.mp4")
        cmd = ff.compile()
        assert "-t" in cmd
        assert "10" in cmd
        assert "-ss" in cmd
        assert "30" in cmd
        assert cmd.index("-t") < cmd.index("-i")
        assert cmd.index("-ss") < cmd.index("-i")

    def test_multiple_outputs(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out1.mp4", c="copy").output("out2.mkv", c="libx265")
        cmd = ff.compile()
        assert cmd == [
            "ffmpeg",
            "-i",
            "in.mkv",
            "-c",
            "copy",
            "out1.mp4",
            "-c",
            "libx265",
            "out2.mkv",
        ]


class TestOverwriteOutput:
    def test_overwrite_output_adds_y_flag(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").overwrite_output()
        cmd = ff.compile()
        assert "-y" in cmd
        assert cmd[1] == "-y"

    def test_no_y_flag_by_default(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        assert "-y" not in ff.compile()


class TestGlobalArgs:
    def test_global_args_positioning(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").global_args("-hide_banner", "-loglevel", "error")
        cmd = ff.compile()
        hide_idx = cmd.index("-hide_banner")
        i_idx = cmd.index("-i")
        assert hide_idx < i_idx
        assert cmd[hide_idx + 1] == "-loglevel"
        assert cmd[hide_idx + 2] == "error"

    def test_global_args_after_overwrite(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").overwrite_output().global_args("-hide_banner")
        cmd = ff.compile()
        assert cmd[1] == "-y"
        assert cmd[2] == "-hide_banner"


class TestConvertArg:
    def test_true_value_produces_flag_only(self):
        assert _convert_arg("an", True) == ["-an"]

    def test_false_value_produces_nothing(self):
        assert _convert_arg("an", False) == []

    def test_none_value_produces_nothing(self):
        assert _convert_arg("key", None) == []

    def test_string_value_produces_flag_and_value(self):
        assert _convert_arg("c", "copy") == ["-c", "copy"]

    def test_int_value_produces_flag_and_str_value(self):
        assert _convert_arg("t", 10) == ["-t", "10"]


class TestInputHelper:
    def test_input_helper_creates_ffmpeg_instance(self):
        result = ffmpeg_input("video.mkv")
        assert isinstance(result, FFmpeg)

    def test_input_helper_with_kwargs(self):
        result = ffmpeg_input("video.mkv", t=5)
        cmd = result.compile()
        assert "-t" in cmd
        assert "5" in cmd
        assert "-i" in cmd
        assert "video.mkv" in cmd

    def test_input_helper_custom_ffmpeg_path(self):
        result = ffmpeg_input("video.mkv", ffmpeg_path="/usr/local/bin/ffmpeg")
        assert result.compile()[0] == "/usr/local/bin/ffmpeg"


class TestRun:
    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_executes_command(self, mock_run):
        mock_run.return_value = MagicMock(stdout=None, stderr=None)
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4", c="copy").run()
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd == ["ffmpeg", "-i", "in.mkv", "-c", "copy", "out.mp4"]

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_raises_ffmpeg_error_on_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "ffmpeg", stderr=b"encoding failed")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError, match="ffmpeg error"):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_capture_stdout(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"output data", stderr=None)
        ff = FFmpeg()
        ff.input("in.mkv").output("pipe:", f="rawvideo")
        ff.run(capture_stdout=True)
        call_args = mock_run.call_args
        assert call_args[1]["stdout"] == subprocess.PIPE
        assert call_args[1]["stderr"] is None

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_capture_stderr(self, mock_run):
        mock_run.return_value = MagicMock(stdout=None, stderr=b"progress info")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        ff.run(capture_stderr=True)
        call_args = mock_run.call_args
        assert call_args[1]["stdout"] is None
        assert call_args[1]["stderr"] == subprocess.PIPE

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_error_without_stderr(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "ffmpeg", stderr=None)
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_returns_none_when_not_capturing(self, mock_run):
        mock_run.return_value = MagicMock(stdout=None, stderr=None)
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        stdout, stderr = ff.run()
        assert stdout is None
        assert stderr is None

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_returns_bytes_when_capturing(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"out", stderr=b"err")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        stdout, stderr = ff.run(capture_stdout=True, capture_stderr=True)
        assert stdout == b"out"
        assert stderr == b"err"

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_capture_both_passes_pipe(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"", stderr=b"")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        ff.run(capture_stdout=True, capture_stderr=True)
        call_args = mock_run.call_args
        assert call_args[1]["stdout"] == subprocess.PIPE
        assert call_args[1]["stderr"] == subprocess.PIPE

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_raises_ffmpeg_error_on_missing_binary(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'ffmpeg'")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError, match="ffmpeg could not be executed"):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_raises_ffmpeg_error_on_bad_ffmpeg_path(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: '/bad/path/ffmpeg'")
        ff = FFmpeg(ffmpeg_path="/bad/path/ffmpeg")
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError, match="ffmpeg could not be executed"):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_raises_ffmpeg_error_on_permission_error(self, mock_run):
        mock_run.side_effect = PermissionError("[Errno 13] Permission denied: '/usr/bin/ffmpeg'")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError, match="ffmpeg could not be executed"):
            ff.run()


class TestPathLikeSupport:
    def test_input_accepts_path_object(self):
        ff = FFmpeg()
        ff.input(Path("in.mkv")).output("out.mp4")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-i", "in.mkv", "out.mp4"]

    def test_output_accepts_path_object(self):
        ff = FFmpeg()
        ff.input("in.mkv").output(Path("out.mp4"), c="copy")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-i", "in.mkv", "-c", "copy", "out.mp4"]

    def test_both_input_and_output_accept_path_objects(self):
        ff = FFmpeg()
        ff.input(Path("in.mkv")).output(Path("out.mp4"), c="copy")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-i", "in.mkv", "-c", "copy", "out.mp4"]

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_with_path_objects_does_not_crash(self, mock_run):
        mock_run.return_value = MagicMock(stdout=None, stderr=None)
        ff = FFmpeg()
        ff.input(Path("in.mkv")).output(Path("out.mp4"))
        ff.run()
        mock_run.assert_called_once()

    def test_input_accepts_custom_pathlike(self):
        class MyPath(os.PathLike):
            def __fspath__(self):
                return "/tmp/custom.mkv"

        ff = FFmpeg()
        ff.input(MyPath()).output("out.mp4")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-i", "/tmp/custom.mkv", "out.mp4"]

    def test_output_accepts_custom_pathlike(self):
        class MyPath(os.PathLike):
            def __fspath__(self):
                return "/tmp/custom_out.mp4"

        ff = FFmpeg()
        ff.input("in.mkv").output(MyPath(), c="copy")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-i", "in.mkv", "-c", "copy", "/tmp/custom_out.mp4"]

    def test_input_accepts_bytes_pathlike(self):
        class BytesPath(os.PathLike):
            def __fspath__(self):
                return b"/tmp/bytes_input.mkv"

        ff = FFmpeg()
        ff.input(BytesPath()).output("out.mp4")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-i", "/tmp/bytes_input.mkv", "out.mp4"]

    def test_output_accepts_bytes_pathlike(self):
        class BytesPath(os.PathLike):
            def __fspath__(self):
                return b"/tmp/bytes_output.mp4"

        ff = FFmpeg()
        ff.input("in.mkv").output(BytesPath(), c="copy")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-i", "in.mkv", "-c", "copy", "/tmp/bytes_output.mp4"]

    def test_input_helper_accepts_path_object(self):
        result = ffmpeg_input(Path("video.mkv"))
        assert isinstance(result, FFmpeg)
        cmd = result.compile()
        assert "video.mkv" in cmd
