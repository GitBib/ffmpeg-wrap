"""Verify the README usage snippets compile against the current public API.

Each test reconstructs a documented chain and asserts the exact ``compile()``
argv (or escaped string) shown in the README, so the docs cannot drift away
from the shipped behavior.
"""

import inspect

import pytest

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


# --- Async API snippets ---------------------------------------------------
#
# The README "Async API" examples spawn real ffmpeg/ffprobe inside ``async def
# main()`` bodies driven by ``anyio.run(...)``. We do NOT execute them here (no
# event loop, no ffmpeg in the unit harness). Instead, matching this file's
# convention, we validate the *static* contract the snippets rely on: the
# documented builder chain still compiles to the expected argv, and the
# ``aio``/``arun`` API surface exists with the documented signatures. The
# anyio-dependent checks are guarded with ``importorskip`` so the suite still
# passes without the optional ``[async]`` extra installed.


def test_async_builder_chain_snippet_compiles():
    """The README ``arun()`` example chain compiles to the documented argv.

    The README awaits this exact chain; here we assert its synchronous
    ``compile()`` argv so the documented command cannot drift.
    """
    cmd = ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().compile()
    assert cmd == [
        "ffmpeg",
        "-y",
        "-i",
        "input.mkv",
        "-c",
        "copy",
        "output.mp4",
    ]


def test_readme_async_imports_are_importable():
    """``from ffmpeg_wrap import input`` and ``aio`` resolve as the README shows."""
    pytest.importorskip("anyio")
    from ffmpeg_wrap import aio

    # README documents ``from ffmpeg_wrap import input`` for the async chain;
    # access via the module to avoid shadowing the builtin in this test file.
    assert callable(ffmpeg.input)
    # Documented coroutine mirrors exist on the aio submodule.
    for name in ("probe", "validate", "run", "encoders", "has_encoder"):
        fn = getattr(aio, name)
        assert inspect.iscoroutinefunction(fn), name


def test_readme_arun_is_documented_coroutine():
    """``FFmpeg.arun`` exists as a coroutine with the documented kwargs."""
    pytest.importorskip("anyio")
    builder = ffmpeg.input("input.mkv").output("output.mp4", c="copy")
    assert inspect.iscoroutinefunction(builder.arun)
    params = inspect.signature(builder.arun).parameters
    assert set(params) == {"capture_stdout", "capture_stderr", "text"}


def test_readme_capacity_limiter_pattern_available():
    """The highload snippet's anyio primitives exist as documented."""
    anyio = pytest.importorskip("anyio")
    assert hasattr(anyio, "CapacityLimiter")
    assert hasattr(anyio, "create_task_group")
    assert hasattr(anyio, "run")
