import pytest

from ffmpeg_wrap._errors import FFmpegError


def test_ffmpeg_error_is_exception():
    assert issubclass(FFmpegError, Exception)


def test_ffmpeg_error_can_be_raised_and_caught():
    with pytest.raises(FFmpegError, match="something went wrong"):
        raise FFmpegError("something went wrong")


def test_ffmpeg_error_preserves_message():
    err = FFmpegError("test message")
    assert str(err) == "test message"


def test_ffmpeg_error_preserves_cause():
    cause = RuntimeError("root cause")
    try:
        raise FFmpegError("wrapper error") from cause
    except FFmpegError as exc:
        assert exc.__cause__ is cause


def test_ffmpeg_error_default_attrs_are_none():
    err = FFmpegError("boom")
    assert err.stderr is None
    assert err.returncode is None
    assert err.cmd is None


def test_ffmpeg_error_carries_structured_attrs():
    err = FFmpegError(
        "ffmpeg error: failed",
        stderr="failed",
        returncode=1,
        cmd=["ffmpeg", "-i", "in.mkv", "out.mp4"],
    )
    assert err.stderr == "failed"
    assert err.returncode == 1
    assert err.cmd == ["ffmpeg", "-i", "in.mkv", "out.mp4"]


def test_ffmpeg_error_str_unchanged_with_structured_attrs():
    err = FFmpegError("ffmpeg error: failed", stderr="failed", returncode=1)
    assert str(err) == "ffmpeg error: failed"
    assert err.args[0] == "ffmpeg error: failed"
