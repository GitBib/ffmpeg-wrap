import io
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ffmpeg_wrap._builder import FFmpeg, _convert_arg
from ffmpeg_wrap._builder import input as ffmpeg_input
from ffmpeg_wrap._errors import FFmpegError
from ffmpeg_wrap._probe import Stream


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


class TestGlobalSugar:
    def test_hide_banner_emits_flag_in_global_slot(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").hide_banner()
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-hide_banner", "-i", "in.mkv", "out.mp4"]

    def test_loglevel_emits_flag_and_value(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").loglevel("error")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-loglevel", "error", "-i", "in.mkv", "out.mp4"]

    def test_hide_banner_and_loglevel_compose_before_input(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").hide_banner().loglevel("verbose")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-hide_banner", "-loglevel", "verbose", "-i", "in.mkv", "out.mp4"]
        assert cmd.index("-hide_banner") < cmd.index("-i")
        assert cmd.index("-loglevel") < cmd.index("-i")

    def test_global_sugar_returns_self(self):
        ff = FFmpeg()
        assert ff.hide_banner() is ff
        assert ff.loglevel("error") is ff


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

    def test_list_value_repeats_flag_per_element(self):
        assert _convert_arg("map", ["0:v", "1:a"]) == ["-map", "0:v", "-map", "1:a"]

    def test_tuple_value_repeats_flag_per_element(self):
        assert _convert_arg("metadata", ("title=x", "comment=y")) == [
            "-metadata",
            "title=x",
            "-metadata",
            "comment=y",
        ]

    def test_empty_list_produces_nothing(self):
        assert _convert_arg("map", []) == []


class TestListValuedFlags:
    def test_map_list_kwarg_repeats_flag(self):
        ff = FFmpeg()
        ff.input("a.mkv").input("b.mkv").output("out.mkv", map=["0:v", "1:a"], c="copy")
        assert ff.compile() == [
            "ffmpeg",
            "-i",
            "a.mkv",
            "-i",
            "b.mkv",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c",
            "copy",
            "out.mkv",
        ]

    def test_metadata_list_kwarg_repeats_flag(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mkv", metadata=["title=Movie", "year=2026"])
        assert ff.compile() == [
            "ffmpeg",
            "-i",
            "in.mkv",
            "-metadata",
            "title=Movie",
            "-metadata",
            "year=2026",
            "out.mkv",
        ]

    def test_mixing_list_and_scalar_kwargs_preserves_order(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mkv", map=["0:v", "0:a"], c="copy", crf=18)
        cmd = ff.compile()
        # list flag expands inline, scalar flags follow in insertion order
        assert cmd == [
            "ffmpeg",
            "-i",
            "in.mkv",
            "-map",
            "0:v",
            "-map",
            "0:a",
            "-c",
            "copy",
            "-crf",
            "18",
            "out.mkv",
        ]


class TestMap:
    def test_map_string_chains_to_repeated_flag(self):
        ff = FFmpeg()
        ff.input("a.mkv").input("b.mkv").output("out.mkv").map("0:v").map("1:a")
        assert ff.compile() == [
            "ffmpeg",
            "-i",
            "a.mkv",
            "-i",
            "b.mkv",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "out.mkv",
        ]

    def test_map_variadic(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mkv").map("0:v", "0:a")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-map", "0:v", "-map", "0:a", "out.mkv"]

    def test_map_merges_with_existing_kwarg(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mkv", map="0:v").map("0:a")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-map", "0:v", "-map", "0:a", "out.mkv"]

    def test_map_stream_object_uses_per_type_specifier(self):
        subtitle = Stream(index=2, codec_type="subtitle")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.srt").map(subtitle)
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-map", "0:s:0", "out.srt"]

    def test_map_stream_object_respects_type_index(self):
        second_audio = Stream(index=3, codec_type="audio", type_index=1)
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mka").map(second_audio)
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-map", "0:a:1", "out.mka"]

    def test_map_stream_unknown_codec_type_falls_back_to_absolute(self):
        unknown = Stream(index=4, codec_type=None)
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mkv").map(unknown)
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-map", "0:4", "out.mkv"]

    def test_map_mixes_stream_and_string(self):
        video = Stream(index=0, codec_type="video")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mkv").map(video, "0:a")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-map", "0:v:0", "-map", "0:a", "out.mkv"]

    def test_map_stream_method(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.srt").map_stream("s", 0)
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-map", "0:s:0", "out.srt"]

    def test_map_stream_method_custom_input(self):
        ff = FFmpeg()
        ff.input("a.mkv").input("b.mkv").output("out.mka").map_stream("a", 1, input=1)
        assert ff.compile() == ["ffmpeg", "-i", "a.mkv", "-i", "b.mkv", "-map", "1:a:1", "out.mka"]

    def test_map_before_output_raises(self):
        ff = FFmpeg()
        ff.input("in.mkv")
        with pytest.raises(FFmpegError, match=r"call \.output"):
            ff.map("0:v")


class TestStreamSpecifiers:
    def test_codec_emits_colon_flag(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").codec("v", "libx265")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-c:v", "libx265", "out.mp4"]

    def test_bitrate_emits_colon_flag(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").bitrate("v", "2M")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-b:v", "2M", "out.mp4"]

    def test_bitrate_accepts_int(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").bitrate("a", 128000)
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-b:a", "128000", "out.mp4"]

    def test_quality_emits_colon_flag(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").quality("a", 2)
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-q:a", "2", "out.mp4"]

    def test_audio_filter_emits_colon_flag(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").audio_filter("loudnorm")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-filter:a", "loudnorm", "out.mp4"]

    def test_video_filter_emits_colon_flag(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").video_filter("scale=1280:-2")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-filter:v", "scale=1280:-2", "out.mp4"]

    def test_multiple_kinds_coexist(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").codec("v", "libx265").codec("a", "aac").bitrate("v", "2M")
        assert ff.compile() == [
            "ffmpeg",
            "-i",
            "in.mkv",
            "-c:v",
            "libx265",
            "-c:a",
            "aac",
            "-b:v",
            "2M",
            "out.mp4",
        ]

    def test_composes_with_repeatable_map(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").map("0:v").map("0:a").codec("v", "libx265")
        assert ff.compile() == [
            "ffmpeg",
            "-i",
            "in.mkv",
            "-map",
            "0:v",
            "-map",
            "0:a",
            "-c:v",
            "libx265",
            "out.mp4",
        ]

    def test_dict_unpack_escape_hatch_still_works(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4", **{"c:v:0": "libx264"})
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-c:v:0", "libx264", "out.mp4"]

    def test_codec_before_output_raises(self):
        ff = FFmpeg()
        ff.input("in.mkv")
        with pytest.raises(FFmpegError, match=r"call \.output"):
            ff.codec("v", "libx265")


class TestFlag:
    def test_single_flag_emits_bare_token(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").flag("vn")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-vn", "out.mp4"]

    def test_multiple_flags_in_one_call(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").flag("vn", "sn")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-vn", "-sn", "out.mp4"]

    def test_chained_flag_calls(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").flag("vn").flag("sn")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-vn", "-sn", "out.mp4"]

    def test_flag_ordering_relative_to_other_kwargs(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4", c="copy").flag("vn")
        assert ff.compile() == ["ffmpeg", "-i", "in.mkv", "-c", "copy", "-vn", "out.mp4"]

    def test_flag_composes_with_codec_and_map(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").map("0:a").codec("a", "aac").flag("vn")
        assert ff.compile() == [
            "ffmpeg",
            "-i",
            "in.mkv",
            "-map",
            "0:a",
            "-c:a",
            "aac",
            "-vn",
            "out.mp4",
        ]

    def test_flag_before_output_raises(self):
        ff = FFmpeg()
        ff.input("in.mkv")
        with pytest.raises(FFmpegError, match=r"call \.output"):
            ff.flag("vn")

    def test_contrast_none_kwarg_omits_but_flag_emits(self):
        # output(..., vn=None) omits the switch entirely (no regression)...
        omitted = FFmpeg()
        omitted.input("in.mkv").output("out.mp4", vn=None)
        assert omitted.compile() == ["ffmpeg", "-i", "in.mkv", "out.mp4"]
        # ...while .flag("vn") is the intentional form that emits it.
        emitted = FFmpeg()
        emitted.input("in.mkv").output("out.mp4").flag("vn")
        assert emitted.compile() == ["ffmpeg", "-i", "in.mkv", "-vn", "out.mp4"]


class TestFilterComplex:
    def test_filter_complex_after_global_before_input(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").hide_banner().filter_complex("[0:v]scale=1280:-2[v]")
        cmd = ff.compile()
        assert cmd == [
            "ffmpeg",
            "-hide_banner",
            "-filter_complex",
            "[0:v]scale=1280:-2[v]",
            "-i",
            "in.mkv",
            "out.mp4",
        ]
        assert cmd.index("-hide_banner") < cmd.index("-filter_complex")
        assert cmd.index("-filter_complex") < cmd.index("-i")

    def test_filter_complex_without_global_args(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").filter_complex("[0:v]null[v]")
        cmd = ff.compile()
        assert cmd == ["ffmpeg", "-filter_complex", "[0:v]null[v]", "-i", "in.mkv", "out.mp4"]

    def test_filter_complex_multi_output_with_map(self):
        # Batch-trim shape: one graph fanned to multiple mapped outputs.
        ff = FFmpeg()
        ff.input("in.mkv").filter_complex("[0:v]split=2[a][b]")
        ff.output("a.mp4").map("[a]")
        ff.output("b.mp4").map("[b]")
        assert ff.compile() == [
            "ffmpeg",
            "-filter_complex",
            "[0:v]split=2[a][b]",
            "-i",
            "in.mkv",
            "-map",
            "[a]",
            "a.mp4",
            "-map",
            "[b]",
            "b.mp4",
        ]

    def test_filter_complex_script_emits_script_form(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").filter_complex_script("graph.txt")
        cmd = ff.compile()
        assert cmd == [
            "ffmpeg",
            "-filter_complex_script",
            "graph.txt",
            "-i",
            "in.mkv",
            "out.mp4",
        ]

    def test_filter_complex_script_accepts_path_object(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").filter_complex_script(Path("graph.txt"))
        cmd = ff.compile()
        assert cmd[1] == "-filter_complex_script"
        assert cmd[2] == "graph.txt"

    def test_filter_complex_returns_self(self):
        ff = FFmpeg()
        assert ff.filter_complex("[0:v]null[v]") is ff
        assert ff.filter_complex_script("graph.txt") is ff

    def test_filter_complex_last_call_wins(self):
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4").filter_complex("[0:v]a[v]").filter_complex_script("g.txt")
        cmd = ff.compile()
        assert "-filter_complex" not in cmd
        assert cmd[1] == "-filter_complex_script"
        assert cmd[2] == "g.txt"


class TestHwaccel:
    def test_hwaccel_lands_before_input(self):
        ff = FFmpeg()
        ff.input("in.mkv").hwaccel("cuda").output("out.mp4", c="copy")
        cmd = ff.compile()
        assert cmd == [
            "ffmpeg",
            "-hwaccel",
            "cuda",
            "-i",
            "in.mkv",
            "-c",
            "copy",
            "out.mp4",
        ]
        assert cmd.index("-hwaccel") < cmd.index("-i")

    def test_hwaccel_equivalent_to_input_kwarg(self):
        sugar = FFmpeg().input("in.mkv").hwaccel("cuda").output("out.mp4")
        kwarg = FFmpeg().input("in.mkv", hwaccel="cuda").output("out.mp4")
        assert sugar.compile() == kwarg.compile()

    def test_hwaccel_targets_current_input(self):
        ff = FFmpeg()
        ff.input("a.mkv").hwaccel("cuda").input("b.mkv").hwaccel("vaapi")
        ff.output("out.mp4")
        assert ff.compile() == [
            "ffmpeg",
            "-hwaccel",
            "cuda",
            "-i",
            "a.mkv",
            "-hwaccel",
            "vaapi",
            "-i",
            "b.mkv",
            "out.mp4",
        ]

    def test_hwaccel_returns_self(self):
        ff = FFmpeg().input("in.mkv")
        assert ff.hwaccel("cuda") is ff

    def test_hwaccel_before_input_raises(self):
        ff = FFmpeg()
        with pytest.raises(FFmpegError):
            ff.hwaccel("cuda")


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


def _popen_cm(returncode=0, stderr=b"", stdout=None):
    """Build a MagicMock standing in for ``subprocess.Popen`` used as a
    context manager by ``FFmpeg._run_tee`` (the non-capture-stderr path).

    The child always runs in binary mode, so stderr/stdout are byte streams."""
    process = MagicMock()
    process.stderr = io.BytesIO(stderr)
    process.stdout = io.BytesIO(stdout) if stdout is not None else None
    process.returncode = returncode
    process.wait = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = process
    cm.__exit__.return_value = False
    return cm


class TestRun:
    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_executes_command(self, mock_popen):
        mock_popen.return_value = _popen_cm()
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4", c="copy").run()
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["ffmpeg", "-i", "in.mkv", "-c", "copy", "out.mp4"]

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_raises_ffmpeg_error_on_failure(self, mock_popen):
        mock_popen.return_value = _popen_cm(returncode=1, stderr=b"encoding failed")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError, match="ffmpeg error"):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_capture_stdout(self, mock_popen):
        mock_popen.return_value = _popen_cm(stdout=b"output data")
        ff = FFmpeg()
        ff.input("in.mkv").output("pipe:", f="rawvideo")
        ff.run(capture_stdout=True)
        call_args = mock_popen.call_args
        assert call_args[1]["stdout"] == subprocess.PIPE
        # stderr is piped internally (and teed to the terminal) so the failure
        # path can populate FFmpegError.stderr even without capture.
        assert call_args[1]["stderr"] == subprocess.PIPE

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_capture_stderr(self, mock_run):
        mock_run.return_value = MagicMock(stdout=None, stderr=b"progress info")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        ff.run(capture_stderr=True)
        call_args = mock_run.call_args
        assert call_args[1]["stdout"] is None
        assert call_args[1]["stderr"] == subprocess.PIPE

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_error_without_stderr(self, mock_popen):
        mock_popen.return_value = _popen_cm(returncode=1, stderr=b"")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_returns_none_when_not_capturing(self, mock_popen):
        mock_popen.return_value = _popen_cm()
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

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_raises_ffmpeg_error_on_missing_binary(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError("No such file or directory: 'ffmpeg'")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError, match="ffmpeg could not be executed"):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_raises_ffmpeg_error_on_bad_ffmpeg_path(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError("No such file or directory: '/bad/path/ffmpeg'")
        ff = FFmpeg(ffmpeg_path="/bad/path/ffmpeg")
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError, match="ffmpeg could not be executed"):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_raises_ffmpeg_error_on_permission_error(self, mock_popen):
        mock_popen.side_effect = PermissionError("[Errno 13] Permission denied: '/usr/bin/ffmpeg'")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError, match="ffmpeg could not be executed"):
            ff.run()

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_failure_populates_structured_error(self, mock_popen):
        mock_popen.return_value = _popen_cm(returncode=2, stderr=b"encoding failed")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError) as exc_info:
            ff.run()
        err = exc_info.value
        assert err.returncode == 2
        assert err.stderr == "encoding failed"
        assert err.cmd == ["ffmpeg", "-i", "in.mkv", "out.mp4"]

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_failure_populates_stderr_even_without_capture(self, mock_popen):
        mock_popen.return_value = _popen_cm(returncode=1, stderr=b"boom")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError) as exc_info:
            ff.run(capture_stderr=False)
        assert exc_info.value.stderr == "boom"

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_failure_without_stderr_leaves_attr_none(self, mock_run):
        # Defensive guard: when CalledProcessError.stderr is None (e.g. stderr
        # was not piped), FFmpegError.stderr stays None. Exercised via the
        # capture path where subprocess.run surfaces the exception directly.
        mock_run.side_effect = subprocess.CalledProcessError(1, "ffmpeg", stderr=None)
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError) as exc_info:
            ff.run(capture_stderr=True)
        assert exc_info.value.stderr is None
        assert exc_info.value.returncode == 1

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_oserror_populates_cmd_only(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError("No such file or directory: 'ffmpeg'")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError) as exc_info:
            ff.run()
        err = exc_info.value
        assert err.cmd == ["ffmpeg", "-i", "in.mkv", "out.mp4"]
        assert err.returncode is None
        assert err.stderr is None

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_text_false_by_default_passes_text_false(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"out", stderr=b"err")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        stdout, stderr = ff.run(capture_stdout=True, capture_stderr=True)
        assert mock_run.call_args[1]["text"] is False
        assert stdout == b"out"
        assert stderr == b"err"

    @patch("ffmpeg_wrap._builder.subprocess.run")
    def test_run_text_true_returns_str(self, mock_run):
        mock_run.return_value = MagicMock(stdout="out", stderr="err")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        stdout, stderr = ff.run(capture_stdout=True, capture_stderr=True, text=True)
        assert mock_run.call_args[1]["text"] is True
        assert stdout == "out"
        assert stderr == "err"

    @patch("ffmpeg_wrap._builder.sys.stderr")
    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_tees_stderr_to_terminal_on_success(self, mock_popen, mock_stderr):
        # On a successful bare run(), ffmpeg's stderr must still reach the
        # terminal (live progress) while not being returned to the caller.
        mock_popen.return_value = _popen_cm(returncode=0, stderr=b"frame= 10\n")
        sink = mock_stderr.buffer
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        stdout, stderr = ff.run()
        assert stdout is None
        assert stderr is None
        # stderr content was forwarded to the inherited stderr stream.
        written = b"".join(call.args[0] for call in sink.write.call_args_list)
        assert written == b"frame= 10\n"

    @patch("ffmpeg_wrap._builder.sys.stderr")
    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_streams_carriage_return_progress_live(self, mock_popen, mock_stderr):
        # ffmpeg writes progress as `\r`-terminated updates with no newline
        # until the very end. A line-oriented read would withhold them until
        # EOF; chunked reads must forward each update as it arrives.
        progress = [b"frame=  1 q=28.0 \r", b"frame=  2 q=27.0 \r", b"frame=  3\n"]

        class _ChunkStream:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            def read1(self, _size):
                return self._chunks.pop(0) if self._chunks else b""

            def close(self):
                pass

        process = MagicMock()
        process.stderr = _ChunkStream(progress)
        process.stdout = None
        process.returncode = 0
        cm = MagicMock()
        cm.__enter__.return_value = process
        cm.__exit__.return_value = False
        mock_popen.return_value = cm
        sink = mock_stderr.buffer

        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        ff.run()

        writes = [call.args[0] for call in sink.write.call_args_list]
        # Each `\r` update reached the terminal as its own write (live), and
        # the full content was forwarded.
        assert writes == progress
        assert b"".join(writes) == b"".join(progress)

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_text_true_failure_keeps_str_stderr(self, mock_popen):
        # text=True decodes the captured byte stderr to str for FFmpegError.
        mock_popen.return_value = _popen_cm(returncode=1, stderr=b"boom text")
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with pytest.raises(FFmpegError) as exc_info:
            ff.run(text=True)
        assert exc_info.value.stderr == "boom text"

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_text_true_normalizes_newlines_like_subprocess(self, mock_popen):
        # text=True must mirror subprocess.run(text=True) universal-newline
        # translation: CRLF and bare CR collapse to LF in returned stdout and
        # in FFmpegError.stderr (the tee path decodes manually).
        mock_popen.return_value = _popen_cm(returncode=1, stderr=b"line1\r\nline2\rline3\n", stdout=b"a\r\nb\rc")
        ff = FFmpeg()
        ff.input("in.mkv").output("pipe:", f="rawvideo")
        with pytest.raises(FFmpegError) as exc_info:
            ff.run(capture_stdout=True, text=True)
        assert exc_info.value.stderr == "line1\nline2\nline3\n"
        assert exc_info.value.cmd is not None

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_text_true_success_normalizes_stdout_newlines(self, mock_popen):
        mock_popen.return_value = _popen_cm(returncode=0, stdout=b"a\r\nb\rc")
        ff = FFmpeg()
        ff.input("in.mkv").output("pipe:", f="rawvideo")
        stdout, stderr = ff.run(capture_stdout=True, text=True)
        assert stdout == "a\nb\nc"
        assert stderr is None

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_tees_to_text_only_stderr_without_buffer(self, mock_popen):
        # Some environments replace sys.stderr with a text-only wrapper that
        # has no .buffer (and rejects bytes). The tee must still forward the
        # live progress by decoding per chunk instead of silently dropping it.
        mock_popen.return_value = _popen_cm(returncode=0, stderr=b"frame= 10\r")

        class _TextOnlyStderr:
            def __init__(self):
                self.written = []

            def write(self, data):
                if not isinstance(data, str):
                    raise TypeError("write() argument must be str, not bytes")
                self.written.append(data)

            def flush(self):
                pass

        fake_stderr = _TextOnlyStderr()
        ff = FFmpeg()
        ff.input("in.mkv").output("out.mp4")
        with patch("ffmpeg_wrap._builder.sys.stderr", fake_stderr):
            stdout, stderr = ff.run()
        assert stdout is None
        assert stderr is None
        # Live progress reached the text-only sink as decoded str (CR preserved
        # so the terminal can overwrite in place).
        assert "".join(fake_stderr.written) == "frame= 10\r"


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

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_with_path_objects_does_not_crash(self, mock_popen):
        mock_popen.return_value = _popen_cm()
        ff = FFmpeg()
        ff.input(Path("in.mkv")).output(Path("out.mp4"))
        ff.run()
        mock_popen.assert_called_once()

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
