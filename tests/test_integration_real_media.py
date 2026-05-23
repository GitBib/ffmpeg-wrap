"""End-to-end tests against real media files.

These exercise the full path — build a command, actually run ffmpeg/ffprobe,
then re-probe the output to confirm the result — rather than asserting argv.
They cover the 0.3.0 surface (repeatable/typed ``map()``, codec helpers,
``filter_complex``, structured ``FFmpegError``, ``run(text=True)``, encoder
introspection) on genuine streams.

Source files (`real_file`/`real_file_two` fixtures) are both Matroska with a
single h264 video stream and a single Vorbis audio stream. Re-encoding tests
trim with ``t=`` to stay fast.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ffmpeg_wrap as ffmpeg

pytestmark = pytest.mark.integration


def test_probe_real_file_format_and_streams(real_file: Path) -> None:
    result = ffmpeg.probe(real_file)

    assert result.format is not None
    assert "matroska" in (result.format.format_name or "")
    assert result.duration_seconds() == pytest.approx(183.129, abs=0.5)
    assert result.format.duration_seconds() == pytest.approx(183.129, abs=0.5)

    assert len(result.streams) == 2
    video = next(s for s in result.streams if s.is_video)
    audio = next(s for s in result.streams if s.is_audio)
    assert video.codec_name == "h264"
    assert audio.codec_name == "vorbis"
    assert audio.channels == 2


def test_probe_predicates_are_mutually_consistent(real_file: Path) -> None:
    for stream in ffmpeg.probe(real_file).streams:
        assert stream.is_video is (stream.codec_type == "video")
        assert stream.is_audio is (stream.codec_type == "audio")
        assert stream.is_text_subtitle is False
        assert stream.is_image_subtitle is False


def test_validate_real_file_is_clean(real_file: Path) -> None:
    ok, stderr = ffmpeg.validate(real_file)
    assert ok is True, f"expected clean validation, got stderr: {stderr!r}"


def test_copy_remux_preserves_streams(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "copy.mkv"
    ffmpeg.input(real_file, t=2).output(str(out), c="copy").overwrite_output().run(capture_stderr=True)

    streams = ffmpeg.probe(out).streams
    kinds = sorted(s.codec_type for s in streams)
    assert kinds == ["audio", "video"]


def test_extract_audio_with_map_and_codec(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "audio.wav"
    (
        ffmpeg.input(real_file, t=2)
        .output(str(out))
        .map("0:a")
        .codec("a", "pcm_s16le")
        .overwrite_output()
        .run(capture_stderr=True)
    )

    streams = ffmpeg.probe(out).streams
    assert all(s.is_audio for s in streams)
    assert streams[0].codec_name == "pcm_s16le"


def test_repeatable_map_chained_combines_two_inputs(real_file: Path, real_file_two: Path, tmp_path: Path) -> None:
    """Video from input 0, audio from input 1 via two chained .map() calls."""
    out = tmp_path / "mux_chained.mkv"
    (
        ffmpeg.input(real_file)
        .input(real_file_two)
        .output(str(out))
        .map("0:v")
        .map("1:a")
        .codec("v", "copy")
        .codec("a", "copy")
        .overwrite_output()
        .run(capture_stderr=True)
    )

    streams = ffmpeg.probe(out).streams
    assert [(s.codec_type, s.codec_name) for s in streams] == [("video", "h264"), ("audio", "vorbis")]


def test_repeatable_map_list_value_combines_two_inputs(real_file: Path, real_file_two: Path, tmp_path: Path) -> None:
    """Same result via a list-valued map= kwarg instead of chained calls."""
    out = tmp_path / "mux_list.mkv"
    (
        ffmpeg.input(real_file)
        .input(real_file_two)
        .output(str(out), map=["0:v", "1:a"], c="copy")
        .overwrite_output()
        .run(capture_stderr=True)
    )

    streams = ffmpeg.probe(out).streams
    assert sorted(s.codec_type for s in streams) == ["audio", "video"]


def test_map_stream_object_emits_per_type_specifier(real_file: Path, tmp_path: Path) -> None:
    """A probed Stream maps to its per-type specifier (0:a:0), not its absolute index."""
    audio = next(s for s in ffmpeg.probe(real_file).streams if s.is_audio)
    out = tmp_path / "by_stream.mkv"
    (
        ffmpeg.input(real_file, t=2)
        .output(str(out))
        .map(audio)
        .codec("a", "copy")
        .overwrite_output()
        .run(capture_stderr=True)
    )

    streams = ffmpeg.probe(out).streams
    assert all(s.is_audio for s in streams)


def test_filter_complex_scales_video(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "scaled.mkv"
    (
        ffmpeg.input(real_file, t=1)
        .filter_complex("[0:v]scale=320:-2[v]")
        .output(str(out))
        .map("[v]")
        .map("0:a")
        .codec("a", "copy")
        .overwrite_output()
        .run(capture_stderr=True)
    )

    video = next(s for s in ffmpeg.probe(out).streams if s.is_video)
    assert video.width == 320


def test_failed_run_raises_structured_error(ffmpeg_available: None, tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.mkv"
    out = tmp_path / "out.mkv"

    with pytest.raises(ffmpeg.FFmpegError) as excinfo:
        ffmpeg.input(str(missing)).output(str(out)).overwrite_output().run()

    err = excinfo.value
    assert err.returncode is not None and err.returncode != 0
    assert err.cmd is not None and err.cmd[0] == "ffmpeg"
    assert err.stderr and "does_not_exist" in err.stderr


def test_run_text_mode_returns_str_stderr(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "text.mkv"
    _stdout, stderr = (
        ffmpeg.input(real_file, t=1).output(str(out), c="copy").overwrite_output().run(capture_stderr=True, text=True)
    )
    assert isinstance(stderr, str)


def test_encoders_introspection_reflects_real_build(ffmpeg_available: None) -> None:
    available = ffmpeg.encoders()
    assert isinstance(available, frozenset)
    assert len(available) > 0
    assert "aac" in available
    assert ffmpeg.has_encoder("aac") is True
    assert ffmpeg.has_encoder("definitely_not_a_real_encoder") is False


def test_subtitle_stream_predicates(mkv_with_subs: Path) -> None:
    streams = ffmpeg.probe(mkv_with_subs).streams
    subtitle = next(s for s in streams if s.is_subtitle)
    assert subtitle.codec_name == "subrip"
    assert subtitle.is_text_subtitle is True
    assert subtitle.is_image_subtitle is False
    assert sum(s.is_subtitle for s in streams) == 1


def test_stream_map_specifier_and_type_index(mkv_with_subs: Path) -> None:
    by_kind = {s.codec_type: s for s in ffmpeg.probe(mkv_with_subs).streams}
    assert by_kind["video"].map_specifier() == "0:v:0"
    assert by_kind["audio"].map_specifier() == "0:a:0"
    assert by_kind["subtitle"].map_specifier() == "0:s:0"
    assert by_kind["subtitle"].map_specifier(input_index=1) == "1:s:0"
    assert all(s.type_index == 0 for s in by_kind.values())


def test_codec_type_enum_matches_probe_strings(mkv_with_subs: Path) -> None:
    kinds = {s.codec_type for s in ffmpeg.probe(mkv_with_subs).streams}
    assert ffmpeg.CodecType.VIDEO in kinds
    assert ffmpeg.CodecType.AUDIO in kinds
    assert ffmpeg.CodecType.SUBTITLE in kinds


def test_validate_rejects_bad_media(bad_media: Path, ffmpeg_available: None) -> None:
    ok, stderr = ffmpeg.validate(bad_media)
    assert ok is False
    assert stderr.strip() != ""


def test_validate_invalid_loglevel_raises(real_file: Path) -> None:
    with pytest.raises(ValueError, match="invalid loglevel"):
        ffmpeg.validate(real_file, loglevel="definitely-not-a-level")


def test_validate_forwards_extra_args(real_file: Path) -> None:
    ok, _stderr = ffmpeg.validate(real_file, extra_args=("-show_format",))
    assert ok is True


def test_probe_bad_media_raises_structured_error(bad_media: Path, ffmpeg_available: None) -> None:
    with pytest.raises(ffmpeg.FFmpegError) as excinfo:
        ffmpeg.probe(bad_media)
    assert excinfo.value.returncode is not None and excinfo.value.returncode != 0


def test_map_stream_selects_audio(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "by_map_stream.mkv"
    (
        ffmpeg.input(real_file, t=2)
        .output(str(out))
        .map_stream("a", 0)
        .codec("a", "copy")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    assert all(s.is_audio for s in ffmpeg.probe(out).streams)


def test_bitrate_helper_sets_audio_bitrate(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "bitrate.m4a"
    (
        ffmpeg.input(real_file, t=1)
        .output(str(out))
        .map("0:a")
        .codec("a", "aac")
        .bitrate("a", "96k")
        .flag("vn")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    streams = ffmpeg.probe(out).streams
    assert [s.codec_name for s in streams] == ["aac"]


def test_quality_helper_encodes_audio(real_file: Path, tmp_path: Path) -> None:
    if not ffmpeg.has_encoder("libvorbis"):
        pytest.skip("libvorbis not available in this ffmpeg build")
    out = tmp_path / "quality.ogg"
    (
        ffmpeg.input(real_file, t=1)
        .output(str(out))
        .map("0:a")
        .codec("a", "libvorbis")
        .quality("a", 3)
        .overwrite_output()
        .run(capture_stderr=True)
    )
    assert [s.codec_name for s in ffmpeg.probe(out).streams] == ["vorbis"]


def test_audio_filter_changes_duration(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "atempo.wav"
    (
        ffmpeg.input(real_file, t=2)
        .output(str(out))
        .map("0:a")
        .audio_filter("atempo=2.0")
        .codec("a", "pcm_s16le")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    assert ffmpeg.probe(out).duration_seconds() == pytest.approx(1.0, abs=0.2)


def test_video_filter_scales_video(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "vf.mkv"
    (
        ffmpeg.input(real_file, t=1)
        .output(str(out))
        .video_filter("scale=176:-2")
        .map("0:v")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    video = next(s for s in ffmpeg.probe(out).streams if s.is_video)
    assert video.width == 176


def test_flag_vn_drops_video(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "audio_only.mkv"
    (
        ffmpeg.input(real_file, t=1)
        .output(str(out))
        .flag("vn")
        .codec("a", "copy")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    streams = ffmpeg.probe(out).streams
    assert all(not s.is_video for s in streams)
    assert any(s.is_audio for s in streams)


def test_loglevel_hide_banner_and_global_args(real_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "quiet.mkv"
    (
        ffmpeg.input(real_file, t=1)
        .output(str(out), c="copy")
        .loglevel("error")
        .hide_banner()
        .global_args("-nostats")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    assert len(ffmpeg.probe(out).streams) == 2


def test_filter_complex_script_scales_video(real_file: Path, tmp_path: Path) -> None:
    script = tmp_path / "graph.txt"
    script.write_text("[0:v]scale=160:-2[v]", encoding="utf-8")
    out = tmp_path / "fcs.mkv"
    (
        ffmpeg.input(real_file, t=1)
        .filter_complex_script(str(script))
        .output(str(out))
        .map("[v]")
        .map("0:a")
        .codec("a", "copy")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    video = next(s for s in ffmpeg.probe(out).streams if s.is_video)
    assert video.width == 160


def test_capture_stdout_pipe(real_file: Path) -> None:
    stdout, _stderr = (
        ffmpeg.input(real_file, t=1)
        .output("pipe:1", f="wav", acodec="pcm_s16le")
        .map("0:a")
        .overwrite_output()
        .run(capture_stdout=True)
    )
    assert isinstance(stdout, bytes)
    assert stdout[:4] == b"RIFF"


def test_hwaccel_input_flag_runs(real_file: Path, tmp_path: Path) -> None:
    """`.hwaccel("auto")` emits the input-side flag; ffmpeg falls back to CPU when no GPU."""
    out = tmp_path / "hwaccel.mkv"
    (
        ffmpeg.input(real_file, t=1)
        .hwaccel("auto")
        .output(str(out), c="copy")
        .map("0:v")
        .map("0:a")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    assert len(ffmpeg.probe(out).streams) == 2


def test_lavfi_only_input_generates_silence(ffmpeg_available: None, tmp_path: Path) -> None:
    """The documented lavfi idiom: a source with no -i input file."""
    out = tmp_path / "silence.wav"
    (
        ffmpeg.input("anullsrc=channel_layout=stereo:sample_rate=48000", f="lavfi", t=1)
        .output(str(out))
        .overwrite_output()
        .run(capture_stderr=True)
    )
    result = ffmpeg.probe(out)
    assert result.duration_seconds() == pytest.approx(1.0, abs=0.1)
    assert any(s.is_audio for s in result.streams)


def test_filter_arg_escape_burn_in(
    real_file: Path, srt_file: Path, subtitles_filter_available: None, tmp_path: Path
) -> None:
    """filter_arg_escape feeds a path into the subtitles filter, burned into video."""
    out = tmp_path / "burned.mkv"
    (
        ffmpeg.input(real_file, t=1)
        .output(str(out))
        .video_filter(f"subtitles={ffmpeg.filter_arg_escape(str(srt_file))}")
        .map("0:v")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    assert any(s.is_video for s in ffmpeg.probe(out).streams)
