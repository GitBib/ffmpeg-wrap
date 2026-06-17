"""Tests for the extracted shared pure core (Task 2 refactor).

These prove the shared helpers behave identically to the former inline logic and
that ``FFmpegError`` objects produced by the sync paths are byte-for-byte
unchanged (stderr/returncode/cmd).
"""

import collections
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ffmpeg_wrap._builder import FFmpeg
from ffmpeg_wrap._encoders import _build_encoders_cmd
from ffmpeg_wrap._errors import FFmpegError, _build_ffmpeg_error
from ffmpeg_wrap._probe import (
    _build_probe_cmd,
    _build_validate_cmd,
    _interpret_validate,
    _parse_probe_output,
    probe,
    validate,
)
from ffmpeg_wrap._textio import STDERR_TAIL_BYTES, TeePump, decode_text


class TestBuildFFmpegError:
    def test_wires_all_fields(self):
        err = _build_ffmpeg_error("ffmpeg error: boom", stderr="boom", returncode=2, cmd=["ffmpeg"])
        assert isinstance(err, FFmpegError)
        assert str(err) == "ffmpeg error: boom"
        assert err.stderr == "boom"
        assert err.returncode == 2
        assert err.cmd == ["ffmpeg"]

    def test_defaults_none(self):
        err = _build_ffmpeg_error("ffprobe could not be executed: x", cmd=["ffprobe"])
        assert err.stderr is None
        assert err.returncode is None
        assert err.cmd == ["ffprobe"]


class TestDecodeText:
    def test_matches_subprocess_universal_newlines(self):
        assert decode_text(b"a\r\nb\rc\n", "utf-8") == "a\nb\nc\n"

    def test_lenient_decode(self):
        # Undecodable byte never raises; uses the replacement char.
        assert decode_text(b"\xff", "utf-8") == "�"

    def test_reexport_alias_identical(self):
        assert FFmpeg._decode_text(b"x\r\ny", "utf-8") == decode_text(b"x\r\ny", "utf-8")
        assert FFmpeg._STDERR_TAIL_BYTES == STDERR_TAIL_BYTES == 256 * 1024


class TestTeePump:
    def test_forwards_raw_bytes_to_buffer_sink(self):
        fake_stderr = MagicMock()
        with patch("ffmpeg_wrap._textio.sys.stderr", fake_stderr):
            pump = TeePump("utf-8")
            pump.feed(b"frame= 1\r")
            pump.feed(b"frame= 2\n")
        written = b"".join(c.args[0] for c in fake_stderr.buffer.write.call_args_list)
        assert written == b"frame= 1\rframe= 2\n"
        assert pump.tail_bytes() == b"frame= 1\rframe= 2\n"

    def test_text_only_sink_decodes_per_chunk(self):
        class _TextOnly:
            buffer = None

            def __init__(self):
                self.written = []

            def write(self, data):
                if not isinstance(data, str):
                    raise TypeError("must be str")
                self.written.append(data)

            def flush(self):
                pass

        fake = _TextOnly()
        with patch("ffmpeg_wrap._textio.sys.stderr", fake):
            pump = TeePump("utf-8")
            pump.feed(b"frame= 10\r")
        assert "".join(fake.written) == "frame= 10\r"

    def test_bounded_tail_keeps_only_limit(self):
        fake_stderr = MagicMock()
        with patch("ffmpeg_wrap._textio.sys.stderr", fake_stderr):
            pump = TeePump("utf-8", tail_limit=10)
            pump.feed(b"aaaaa")
            pump.feed(b"bbbbb")
            pump.feed(b"ccccc")
        tail = pump.tail_bytes()
        # Bounded: total retained never grossly exceeds the limit; always keeps
        # the most recent chunk (mirrors the former deque logic: pop while
        # len > limit and more than one chunk remains).
        assert tail.endswith(b"ccccc")
        assert len(tail) <= 15  # at most limit + last chunk size

    def test_tail_matches_former_inline_logic(self):
        # Reproduce the old inline deque bookkeeping and assert equivalence.
        limit = 12
        chunks = [b"123", b"4567", b"89ab", b"cdef"]
        tail: collections.deque[bytes] = collections.deque()
        tail_len = 0
        for chunk in chunks:
            tail.append(chunk)
            tail_len += len(chunk)
            while tail_len > limit and len(tail) > 1:
                tail_len -= len(tail.popleft())
        expected = b"".join(tail)

        fake_stderr = MagicMock()
        with patch("ffmpeg_wrap._textio.sys.stderr", fake_stderr):
            pump = TeePump("utf-8", tail_limit=limit)
            for chunk in chunks:
                pump.feed(chunk)
        assert pump.tail_bytes() == expected

    def test_feed_swallows_sink_write_errors(self):
        fake_stderr = MagicMock()
        fake_stderr.buffer.write.side_effect = OSError("pipe closed")
        with patch("ffmpeg_wrap._textio.sys.stderr", fake_stderr):
            pump = TeePump("utf-8")
            pump.feed(b"data")  # must not raise
        # Tail still retained even when the live write failed.
        assert pump.tail_bytes() == b"data"


class TestProbeBuildersAndParse:
    def test_build_probe_cmd_shape(self):
        cmd = _build_probe_cmd("in.mkv", "ffprobe")
        assert cmd[0] == "ffprobe"
        assert cmd[1:7] == ["-v", "quiet", "-print_format", "json", "-show_format", "-show_streams"]
        assert cmd[-1].endswith("in.mkv")

    def test_parse_probe_output_assigns_type_indices(self):
        data = (
            b'{"streams": [{"index": 0, "codec_type": "video"}, '
            b'{"index": 1, "codec_type": "audio"}, '
            b'{"index": 2, "codec_type": "audio"}]}'
        )
        result = _parse_probe_output(data, ["ffprobe"])
        assert [s.type_index for s in result.streams] == [0, 0, 1]

    def test_parse_probe_output_raises_ffmpegerror_on_bad_json(self):
        with pytest.raises(FFmpegError) as exc:
            _parse_probe_output(b"not json", ["ffprobe", "x"])
        assert "ffprobe output parsing error" in str(exc.value)
        assert exc.value.cmd == ["ffprobe", "x"]

    def test_build_validate_cmd_loglevel_precondition(self):
        with pytest.raises(ValueError, match="invalid loglevel"):
            _build_validate_cmd("in.mkv", loglevel="nope")

    def test_build_validate_cmd_shape(self):
        cmd = _build_validate_cmd("in.mkv", "ffprobe", "warning", ("-show_format",))
        assert cmd[:4] == ["ffprobe", "-v", "warning", "-show_format"]
        assert cmd[-1].endswith("in.mkv")

    def test_interpret_validate_ok(self):
        assert _interpret_validate(0, b"") == (True, "")
        assert _interpret_validate(0, b"   ") == (True, "   ")

    def test_interpret_validate_not_ok(self):
        ok, text = _interpret_validate(1, b"boom")
        assert ok is False
        assert text == "boom"
        ok2, _ = _interpret_validate(0, b"warning: dts")
        assert ok2 is False


class TestEncodersBuilder:
    def test_build_encoders_cmd(self):
        assert _build_encoders_cmd("ffmpeg") == ["ffmpeg", "-hide_banner", "-encoders"]
        assert _build_encoders_cmd("/usr/bin/ffmpeg")[0] == "/usr/bin/ffmpeg"


class TestSyncErrorParity:
    """The sync failure paths must still build identical FFmpegError objects."""

    @patch("ffmpeg_wrap._builder.subprocess.Popen")
    def test_run_tee_failure_error_unchanged(self, mock_popen):
        process = MagicMock()
        process.stderr = MagicMock()
        process.stderr.read1.side_effect = [b"boom stderr", b""]
        process.stdout = None
        process.returncode = 3
        cm = MagicMock()
        cm.__enter__.return_value = process
        cm.__exit__.return_value = False
        mock_popen.return_value = cm
        ff = FFmpeg().input("in.mkv").output("out.mp4")
        with patch("ffmpeg_wrap._builder.sys.stderr", MagicMock()), pytest.raises(FFmpegError) as exc:
            ff.run()
        err = exc.value
        assert err.returncode == 3
        assert err.cmd == ["ffmpeg", "-i", "in.mkv", "out.mp4"]
        # The run() catch decodes the byte stderr from the tee (text=False).
        assert err.stderr == "boom stderr"
        assert str(err) == "ffmpeg error: boom stderr"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_called_process_error_unchanged(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"], stderr=b"bad media")
        with pytest.raises(FFmpegError) as exc:
            probe("in.mkv")
        err = exc.value
        assert err.stderr == "bad media"
        assert err.returncode == 1
        assert str(err) == "ffprobe error: bad media"

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_probe_oserror_unchanged(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no ffprobe")
        with pytest.raises(FFmpegError) as exc:
            probe("in.mkv")
        assert exc.value.returncode is None
        assert exc.value.stderr is None
        assert "ffprobe could not be executed" in str(exc.value)

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_oserror_unchanged(self, mock_run):
        mock_run.side_effect = OSError("boom")
        with pytest.raises(FFmpegError) as exc:
            validate("in.mkv")
        assert "ffprobe could not be executed" in str(exc.value)

    @patch("ffmpeg_wrap._probe.subprocess.run")
    def test_validate_success_path(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        assert validate("in.mkv") == (True, "")
