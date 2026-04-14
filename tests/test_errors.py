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
