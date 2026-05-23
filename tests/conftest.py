"""Shared fixtures, including real-media fixtures for integration tests.

Real ``.mkv`` files are downloaded into this directory by the Makefile
(``make download``) from the same host pymkv uses for its fixtures. They are
git-ignored. Integration tests skip automatically when the files — or the
ffmpeg/ffprobe binaries — are absent, so the plain unit suite still runs
anywhere with no setup.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import ffmpeg_wrap as ffmpeg

TESTS_DIR = Path(__file__).parent
REAL_FILE = TESTS_DIR / "file.mkv"
REAL_FILE_TWO = TESTS_DIR / "file_2.mkv"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: real-media tests that shell out to ffmpeg/ffprobe and need downloaded fixtures",
    )


@pytest.fixture(scope="session")
def ffmpeg_available() -> None:
    """Skip the test unless both ffmpeg and ffprobe are on PATH."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")


@pytest.fixture
def real_file(ffmpeg_available: None) -> Path:
    """Path to the primary real test file (video h264 + audio vorbis)."""
    if not REAL_FILE.exists():
        pytest.skip(f"{REAL_FILE} missing — run `make download` to fetch test media")
    return REAL_FILE


@pytest.fixture
def real_file_two(ffmpeg_available: None) -> Path:
    """Path to the secondary real test file (video h264 + audio vorbis)."""
    if not REAL_FILE_TWO.exists():
        pytest.skip(f"{REAL_FILE_TWO} missing — run `make download` to fetch test media")
    return REAL_FILE_TWO


@pytest.fixture
def srt_file(tmp_path: Path) -> Path:
    """A small valid SRT subtitle file for filter/burn-in tests."""
    path = tmp_path / "subs.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello world\n\n2\n00:00:01,000 --> 00:00:02,000\nSecond line\n\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def bad_media(tmp_path: Path) -> Path:
    """A file with a media extension but garbage content (invalid to ffprobe)."""
    path = tmp_path / "bad.mkv"
    path.write_bytes(b"this is not a media file at all")
    return path


@pytest.fixture
def mkv_with_subs(real_file: Path, srt_file: Path, tmp_path: Path) -> Path:
    """A short Matroska muxed with a video, audio and a (text) subtitle stream."""
    out = tmp_path / "with_subs.mkv"
    (
        ffmpeg.input(real_file, t=2)
        .input(str(srt_file))
        .output(str(out))
        .map("0:v")
        .map("0:a")
        .map("1:s")
        .codec("v", "copy")
        .codec("a", "copy")
        .codec("s", "srt")
        .overwrite_output()
        .run(capture_stderr=True)
    )
    return out


@pytest.fixture(scope="session")
def subtitles_filter_available(ffmpeg_available: None) -> None:
    """Skip unless this ffmpeg build has the ``subtitles`` filter (needs libass)."""
    result = subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=False,
    )
    if " subtitles " not in result.stdout:
        pytest.skip("ffmpeg built without the subtitles filter (no libass)")
