"""Verify the README usage snippets compile against the current public API.

Each test reconstructs a documented chain and asserts the exact ``compile()``
argv (or escaped string) shown in the README, so the docs cannot drift away
from the shipped behavior.
"""

import ffmpeg_wrap as ffmpeg


def test_lavfi_synthetic_source_snippet():
    """The lavfi-source entry-point snippet compiles as documented."""
    cmd = (
        ffmpeg.input("anullsrc=channel_layout=stereo:sample_rate=48000", f="lavfi", t=5)
        .output("silence.wav")
        .overwrite_output()
        .compile()
    )
    assert cmd == [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-t",
        "5",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "silence.wav",
    ]


def test_filter_complex_fanout_snippet():
    """filter_complex graph fanned to two mapped outputs."""
    cmd = (
        ffmpeg.input("input.mkv")
        .filter_complex("[0:v]split=2[full][thumb];[thumb]scale=320:-2[thumb]")
        .output("full.mp4")
        .map("[full]")
        .output("thumb.mp4")
        .map("[thumb]")
        .overwrite_output()
        .compile()
    )
    assert cmd == [
        "ffmpeg",
        "-y",
        "-filter_complex",
        "[0:v]split=2[full][thumb];[thumb]scale=320:-2[thumb]",
        "-i",
        "input.mkv",
        "-map",
        "[full]",
        "full.mp4",
        "-map",
        "[thumb]",
        "thumb.mp4",
    ]


def test_filter_complex_script_snippet():
    """filter_complex_script reads the graph from a file."""
    cmd = ffmpeg.input("input.mkv").filter_complex_script("graph.txt").output("output.mp4").compile()
    assert cmd == [
        "ffmpeg",
        "-filter_complex_script",
        "graph.txt",
        "-i",
        "input.mkv",
        "output.mp4",
    ]


def test_remux_repeated_map_snippet():
    """Stream-preserving remux emits one -map per stream type."""
    cmd = (
        ffmpeg.input("input.mkv")
        .output("output.mkv", c="copy")
        .map("0:v")
        .map("0:a")
        .map("0:s")
        .overwrite_output()
        .compile()
    )
    assert cmd == [
        "ffmpeg",
        "-y",
        "-i",
        "input.mkv",
        "-c",
        "copy",
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-map",
        "0:s",
        "output.mkv",
    ]


def test_map_list_form_matches_repeated_map():
    """Passing map=[...] expands to the same argv as repeated .map()."""
    cmd = ffmpeg.input("input.mkv").output("output.mkv", c="copy", map=["0:v", "0:a", "0:s"]).compile()
    assert cmd == [
        "ffmpeg",
        "-i",
        "input.mkv",
        "-c",
        "copy",
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-map",
        "0:s",
        "output.mkv",
    ]


def test_filter_arg_escape_subtitles_snippet():
    """filter_arg_escape produces the documented escaped subtitles= value."""
    path = r"C:\videos\clip.srt"
    graph = f"subtitles={ffmpeg.filter_arg_escape(path)}"
    assert graph == r"subtitles='C\:\\videos\\clip.srt'"


def test_hwaccel_method_snippet():
    """The .hwaccel() sugar lands -hwaccel before -i, with -c:v on the output."""
    cmd = (
        ffmpeg.input("input.mkv")
        .hwaccel("cuda")
        .output("output.mp4")
        .codec("v", "h264_nvenc")
        .overwrite_output()
        .compile()
    )
    assert cmd == [
        "ffmpeg",
        "-y",
        "-hwaccel",
        "cuda",
        "-i",
        "input.mkv",
        "-c:v",
        "h264_nvenc",
        "output.mp4",
    ]


def test_hwaccel_input_kwarg_equivalent():
    """input(..., hwaccel="cuda") is equivalent to the .hwaccel() form."""
    cmd = ffmpeg.input("input.mkv", hwaccel="cuda").output("output.mp4").codec("v", "h264_nvenc").compile()
    assert cmd == [
        "ffmpeg",
        "-hwaccel",
        "cuda",
        "-i",
        "input.mkv",
        "-c:v",
        "h264_nvenc",
        "output.mp4",
    ]


def test_error_handling_attributes_documented():
    """FFmpegError exposes returncode/stderr/cmd as the README recipe relies on."""
    err = ffmpeg.FFmpegError("ffmpeg error: boom", stderr="boom", returncode=1, cmd=["ffmpeg"])
    assert str(err) == "ffmpeg error: boom"
    assert err.returncode == 1
    assert err.stderr == "boom"
    assert err.cmd == ["ffmpeg"]
