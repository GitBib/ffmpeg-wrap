"""Tests for the public API of ffmpeg_wrap."""

import ffmpeg_wrap as ffmpeg


def test_all_names_accessible():
    """All public names are accessible via the package."""
    assert hasattr(ffmpeg, "FFmpegError")
    assert hasattr(ffmpeg, "Stream")
    assert hasattr(ffmpeg, "Format")
    assert hasattr(ffmpeg, "ProbeResult")
    assert hasattr(ffmpeg, "probe")
    assert hasattr(ffmpeg, "FFmpeg")
    assert hasattr(ffmpeg, "input")
    assert hasattr(ffmpeg, "validate")


def test_input_returns_ffmpeg_instance():
    """ffmpeg.input(...) returns an FFmpeg instance."""
    result = ffmpeg.input("test.mkv")
    assert isinstance(result, ffmpeg.FFmpeg)


def test_probe_is_callable():
    """ffmpeg.probe is callable."""
    assert callable(ffmpeg.probe)


def test_ffmpeg_error_is_exception_subclass():
    """ffmpeg.FFmpegError is an Exception subclass."""
    assert issubclass(ffmpeg.FFmpegError, Exception)


def test_validate_is_callable():
    """ffmpeg.validate is callable."""
    assert callable(ffmpeg.validate)


def test_all_exports_match():
    """__all__ contains all expected public names."""
    expected = {"FFmpegError", "Stream", "Format", "ProbeResult", "probe", "FFmpeg", "input", "validate"}
    assert set(ffmpeg.__all__) == expected
