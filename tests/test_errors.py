import copy
import pickle

import pytest

from ffmpeg_wrap._errors import FFmpegError, FFmpegTimeoutError, _build_ffmpeg_timeout_error
from ffmpeg_wrap._textio import decode_error_stderr


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


def test_timeout_error_is_ffmpeg_error_subclass():
    assert issubclass(FFmpegTimeoutError, FFmpegError)
    assert issubclass(FFmpegTimeoutError, Exception)


def test_timeout_error_caught_by_except_ffmpeg_error():
    with pytest.raises(FFmpegError):
        raise FFmpegTimeoutError("ffmpeg timed out after 0.5s", timeout=0.5)


def test_timeout_error_round_trips_attrs():
    cmd = ["ffmpeg", "-i", "in.mkv", "out.mp4"]
    err = FFmpegTimeoutError("ffmpeg timed out after 2.5s", timeout=2.5, stderr="partial", cmd=cmd)
    assert err.timeout == 2.5
    assert err.cmd == cmd
    assert err.stderr == "partial"
    assert err.returncode is None


def test_timeout_error_str_is_message():
    err = FFmpegTimeoutError("ffmpeg timed out after 1s", timeout=1, stderr="x", cmd=["ffmpeg"])
    assert str(err) == "ffmpeg timed out after 1s"
    assert err.args[0] == "ffmpeg timed out after 1s"


def test_timeout_error_requires_timeout_keyword():
    with pytest.raises(TypeError):
        FFmpegTimeoutError("boom")


@pytest.mark.parametrize(
    "round_trip",
    [
        pytest.param(lambda err: pickle.loads(pickle.dumps(err)), id="pickle"),
        pytest.param(copy.copy, id="copy"),
        pytest.param(copy.deepcopy, id="deepcopy"),
    ],
)
def test_timeout_error_round_trips_through_pickle_and_copy(round_trip):
    err = FFmpegTimeoutError(
        "ffmpeg timed out after 2.5s", timeout=2.5, stderr="partial", cmd=["ffmpeg", "-i", "in.mkv"]
    )
    restored = round_trip(err)
    assert type(restored) is FFmpegTimeoutError
    assert str(restored) == "ffmpeg timed out after 2.5s"
    assert restored.timeout == 2.5
    assert restored.stderr == "partial"
    assert restored.cmd == ["ffmpeg", "-i", "in.mkv"]
    assert restored.returncode is None


def test_ffmpeg_error_round_trips_through_pickle():
    err = FFmpegError("ffmpeg error: boom", stderr="tail", returncode=1, cmd=["ffmpeg"])
    restored = pickle.loads(pickle.dumps(err))
    assert type(restored) is FFmpegError
    assert str(restored) == "ffmpeg error: boom"
    assert restored.stderr == "tail"
    assert restored.returncode == 1
    assert restored.cmd == ["ffmpeg"]


def test_timeout_error_defaults_are_none():
    err = FFmpegTimeoutError("boom", timeout=3.0)
    assert err.stderr is None
    assert err.cmd is None
    assert err.returncode is None


def test_timeout_error_preserves_cause():
    cause = RuntimeError("expired")
    try:
        raise FFmpegTimeoutError("wrapper", timeout=1.0) from cause
    except FFmpegTimeoutError as exc:
        assert exc.__cause__ is cause


def test_build_ffmpeg_timeout_error_always_returncode_none():
    err = _build_ffmpeg_timeout_error("ffprobe timed out after 4s", timeout=4, stderr="tail", cmd=["ffprobe"])
    assert isinstance(err, FFmpegTimeoutError)
    assert err.timeout == 4
    assert err.stderr == "tail"
    assert err.cmd == ["ffprobe"]
    assert err.returncode is None
    assert str(err) == "ffprobe timed out after 4s"


def test_build_ffmpeg_timeout_error_defaults():
    err = _build_ffmpeg_timeout_error("ffmpeg timed out after 1s", timeout=1)
    assert err.stderr is None
    assert err.cmd is None
    assert err.returncode is None


@pytest.mark.parametrize("text", [True, False])
def test_decode_error_stderr_none_stays_none(text):
    assert decode_error_stderr(None, text=text, encoding="utf-8") is None


@pytest.mark.parametrize("text", [True, False])
def test_decode_error_stderr_str_is_unchanged(text):
    assert decode_error_stderr("already\r\ntext", text=text, encoding="utf-8") == "already\r\ntext"


def test_decode_error_stderr_bytes_text_mode_translates_newlines():
    assert decode_error_stderr(b"tail\r\nx", text=True, encoding="utf-8") == "tail\nx"


def test_decode_error_stderr_bytes_binary_mode_keeps_newlines():
    assert decode_error_stderr(b"tail\r\nx", text=False, encoding="utf-8") == "tail\r\nx"


def test_decode_error_stderr_bytes_text_mode_uses_given_encoding():
    assert decode_error_stderr("caf\u00e9".encode("cp1252"), text=True, encoding="cp1252") == "caf\u00e9"


def test_decode_error_stderr_bytes_binary_mode_is_utf8_with_replacement():
    assert decode_error_stderr(bytes([255, 254]) + b"ok", text=False, encoding="cp1252") == "\ufffd\ufffdok"
