import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ffmpeg_wrap._encoders import (
    _clear_encoders_cache,
    _parse_encoders,
    encoders,
    has_encoder,
)
from ffmpeg_wrap._errors import FFmpegError

# A trimmed but faithful capture of ``ffmpeg -hide_banner -encoders`` output.
ENCODERS_FIXTURE = """\
Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 V..... h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V..... hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)
 A....D aac                  AAC (Advanced Audio Coding)
 A..... libmp3lame           libmp3lame MP3 (MPEG audio layer 3) (codec mp3)
 S..... srt                  SubRip subtitle (codec subrip)
"""


@pytest.fixture(autouse=True)
def _reset_cache():
    _clear_encoders_cache()
    yield
    _clear_encoders_cache()


class TestParseEncoders:
    def test_extracts_names_after_separator(self):
        names = _parse_encoders(ENCODERS_FIXTURE)
        assert names == frozenset({"libx264", "libx265", "h264_nvenc", "hevc_nvenc", "aac", "libmp3lame", "srt"})

    def test_ignores_legend_before_separator(self):
        # "Video"/"Audio" legend descriptions must not leak in as encoder names.
        names = _parse_encoders(ENCODERS_FIXTURE)
        assert "Video" not in names
        assert "=" not in names

    def test_empty_output(self):
        assert _parse_encoders("") == frozenset()


class TestEncoders:
    @patch("ffmpeg_wrap._encoders.subprocess.run")
    def test_encoders_parses_fixture(self, mock_run):
        mock_run.return_value = MagicMock(stdout=ENCODERS_FIXTURE)
        result = encoders()
        assert "h264_nvenc" in result
        assert "libx264" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["ffmpeg", "-hide_banner", "-encoders"]
        assert mock_run.call_args[1]["text"] is True

    @patch("ffmpeg_wrap._encoders.subprocess.run")
    def test_result_is_cached_per_path(self, mock_run):
        mock_run.return_value = MagicMock(stdout=ENCODERS_FIXTURE)
        encoders("ffmpeg")
        encoders("ffmpeg")
        assert mock_run.call_count == 1
        # A different path triggers a fresh probe (separate cache key).
        encoders("/opt/ffmpeg/bin/ffmpeg")
        assert mock_run.call_count == 2

    @patch("ffmpeg_wrap._encoders.subprocess.run")
    def test_clear_cache_forces_reprobe(self, mock_run):
        mock_run.return_value = MagicMock(stdout=ENCODERS_FIXTURE)
        encoders()
        _clear_encoders_cache()
        encoders()
        assert mock_run.call_count == 2

    @patch("ffmpeg_wrap._encoders.subprocess.run")
    def test_raises_on_called_process_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "ffmpeg", stderr="boom")
        with pytest.raises(FFmpegError) as exc:
            encoders()
        assert exc.value.returncode == 1
        assert exc.value.stderr == "boom"

    @patch("ffmpeg_wrap._encoders.subprocess.run")
    def test_raises_on_missing_binary(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no ffmpeg")
        with pytest.raises(FFmpegError):
            encoders("/nonexistent/ffmpeg")


class TestHasEncoder:
    @patch("ffmpeg_wrap._encoders.subprocess.run")
    def test_true_for_present_encoder(self, mock_run):
        mock_run.return_value = MagicMock(stdout=ENCODERS_FIXTURE)
        assert has_encoder("h264_nvenc") is True

    @patch("ffmpeg_wrap._encoders.subprocess.run")
    def test_false_for_absent_encoder(self, mock_run):
        mock_run.return_value = MagicMock(stdout=ENCODERS_FIXTURE)
        assert has_encoder("nonexistent_codec") is False

    @patch("ffmpeg_wrap._encoders.subprocess.run")
    def test_uses_cache(self, mock_run):
        mock_run.return_value = MagicMock(stdout=ENCODERS_FIXTURE)
        has_encoder("libx264")
        has_encoder("aac")
        assert mock_run.call_count == 1
