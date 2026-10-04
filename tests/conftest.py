"""Shared fixtures, including real-media fixtures for integration tests.

Real ``.mkv`` files are downloaded into this directory by the Makefile
(``make download``) from the same host pymkv uses for its fixtures. They are
git-ignored. Integration tests skip automatically when the files — or the
ffmpeg/ffprobe binaries — are absent, so the plain unit suite still runs
anywhere with no setup.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import psutil
import pytest

import ffmpeg_wrap as ffmpeg

TESTS_DIR = Path(__file__).parent
REAL_FILE = TESTS_DIR / "file.mkv"
REAL_FILE_TWO = TESTS_DIR / "file_2.mkv"
FAKE_CHILD = TESTS_DIR / "fake_child.py"
FAKE_SITE = TESTS_DIR / "fake_site"
FAKE_CHILD_ENV = (
    "FAKE_CHILD_PIDFILE",
    "FAKE_CHILD_MARKER",
    "FAKE_CHILD_STDERR",
    "FAKE_CHILD_STDOUT_BYTES",
    "FAKE_CHILD_SLEEP",
    "FAKE_CHILD_SPAWN",
    "FAKE_CHILD_DETACH",
    "FAKE_CHILD_EXIT",
)
CHILD_GONE_DEADLINE = 5.0
PID_REUSE_SLACK = 2.0


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: real-media tests that shell out to ffmpeg/ffprobe and need downloaded fixtures",
    )


@pytest.fixture(params=["asyncio", "trio"], scope="module")
def anyio_backend(request: pytest.FixtureRequest) -> str:
    """Parametrize async tests over both AnyIO backends (asyncio and trio).

    Module-scoped per AnyIO's requirement; async fixtures depending on it must
    therefore be function- or module-scoped (never session-scoped). The trio
    leg is skipped when trio is not installed so the suite still runs without
    the ``[async]`` extra / dev group.
    """
    if request.param == "trio":
        pytest.importorskip("trio")
    return request.param


@pytest.fixture(scope="session")
def ffmpeg_available() -> None:
    """Skip the test unless both ffmpeg and ffprobe are on PATH."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")


@pytest.fixture(scope="session")
def ffmpeg_major(ffmpeg_available: None) -> int | None:
    banner = subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-version"], capture_output=True, text=True, check=False
    ).stdout
    match = re.match(r"ffmpeg version n?(\d+)\.", banner)
    return int(match.group(1)) if match else None


def skip_unless_filter_script_form_supported(ffmpeg_major: int | None, *, legacy: bool) -> None:
    if ffmpeg_major is None:
        return
    if legacy and ffmpeg_major >= 9:
        pytest.skip("-filter_complex_script was removed in ffmpeg 9.0")
    if not legacy and ffmpeg_major < 7:
        pytest.skip("-/filter_complex needs ffmpeg 7.0+")


def write_large_filtergraph(path: Path, branches: int = 1800) -> Path:
    split = f"[0:a]asplit={branches}" + "".join(f"[split_{i:05d}]" for i in range(branches))
    chains = ";\n".join(f"[split_{i:05d}]adelay={i % 500}|{i % 500},volume=0.5[mixed_{i:05d}]" for i in range(branches))
    mix = "".join(f"[mixed_{i:05d}]" for i in range(branches)) + f"amix=inputs={branches}:normalize=0[a]"
    graph = f"{split};\n{chains};\n{mix}\n"
    assert len(graph.encode()) > 128 * 1024
    path.write_text(graph, encoding="utf-8")
    return path


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


_LAVFI_AUDIO_SUFFIXES = {"flac": ".flac", "libmp3lame": ".mp3", "libopus": ".opus"}


@pytest.fixture
def lavfi_audio(ffmpeg_available: None, tmp_path: Path) -> Callable[..., Path]:
    def make(codec: str, sample_fmt: str | None = None, sample_rate: int = 48000) -> Path:
        if not ffmpeg.has_encoder(codec):
            pytest.skip(f"ffmpeg built without the {codec} encoder")
        out = tmp_path / f"{codec}-{sample_fmt or 'default'}{_LAVFI_AUDIO_SUFFIXES.get(codec, '.wav')}"
        (
            ffmpeg.input(f"anullsrc=r={sample_rate}:cl=mono", f="lavfi", t=1)
            .output(str(out), ar=sample_rate, sample_fmt=sample_fmt)
            .codec("a", codec)
            .overwrite_output()
            .run(capture_stderr=True)
        )
        return out

    return make


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


def started_child_pid(pidfile: Path, *, deadline_s: float = CHILD_GONE_DEADLINE) -> int | None:
    deadline = time.monotonic() + deadline_s
    while True:
        text = pidfile.read_text(encoding="utf-8").strip() if pidfile.exists() else ""
        if text:
            return int(text.split()[0])
        if time.monotonic() > deadline:
            return None
        time.sleep(0.01)


def child_pid(pidfile: Path, *, deadline_s: float = CHILD_GONE_DEADLINE) -> int:
    pid = started_child_pid(pidfile, deadline_s=deadline_s)
    if pid is None:
        raise AssertionError(f"fake child never wrote its pid to {pidfile}")
    return pid


def child_pids(pidfile: Path, *, deadline_s: float = CHILD_GONE_DEADLINE) -> tuple[int, int]:
    child_pid(pidfile, deadline_s=deadline_s)
    pid, ppid = pidfile.read_text(encoding="utf-8").split()
    return int(pid), int(ppid)


def assert_spawned_by(pidfile: Path, proc: subprocess.Popen[bytes] | subprocess.Popen[str]) -> int:
    pid, ppid = child_pids(pidfile)
    assert proc.pid in (pid, ppid), f"fake child pid {pid} (parent {ppid}) was not spawned by Popen pid {proc.pid}"
    return pid


def _live_child(pid: int, pidfile: Path) -> psutil.Process | None:
    try:
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return None
        if proc.create_time() > pidfile.stat().st_mtime + PID_REUSE_SLACK:
            return None
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return None
    return proc


def child_alive(pidfile: Path) -> bool:
    return _live_child(child_pid(pidfile), pidfile) is not None


def assert_child_gone(
    pidfile: Path,
    marker: Path,
    sleep_s: float = 5.0,
    *,
    deadline_s: float = CHILD_GONE_DEADLINE,
) -> None:
    pid = started_child_pid(pidfile, deadline_s=deadline_s)
    if pid is None:
        assert not marker.exists(), f"fake child wrote {marker} without ever writing its pid"
        return
    deadline = time.monotonic() + deadline_s
    while _live_child(pid, pidfile) is not None:
        if time.monotonic() > deadline:
            raise AssertionError(f"fake child pid {pid} is still alive {deadline_s}s after the timeout")
        time.sleep(0.05)
    assert not marker.exists(), f"fake child pid {pid} finished its {sleep_s}s sleep on its own instead of being killed"


def assert_child_reaped(pidfile: Path, *, deadline_s: float = CHILD_GONE_DEADLINE) -> None:
    pid = started_child_pid(pidfile, deadline_s=deadline_s)
    if pid is None:
        return
    if sys.platform == "win32":
        deadline = time.monotonic() + deadline_s
        while psutil.pid_exists(pid):
            assert time.monotonic() < deadline, f"pid {pid} still exists {deadline_s}s after the timeout"
            time.sleep(0.05)
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def kill_leftover_child(pidfile: Path) -> None:
    if not pidfile.exists():
        return
    text = pidfile.read_text(encoding="utf-8").strip()
    if not text:
        return
    proc = _live_child(int(text.split()[0]), pidfile)
    if proc is None:
        return
    with contextlib.suppress(psutil.NoSuchProcess, psutil.TimeoutExpired):
        proc.kill()
        proc.wait(CHILD_GONE_DEADLINE)


@dataclass
class FakeChild:
    pidfile: Path
    marker: Path
    monkeypatch: pytest.MonkeyPatch

    def set_env(self, **env: object) -> None:
        for key, value in env.items():
            self.monkeypatch.setenv(key, str(value))

    def make(self, **env: object) -> ffmpeg.FFmpeg:
        self.set_env(**env)
        return ffmpeg.FFmpeg(ffmpeg_path=sys.executable).global_args(str(FAKE_CHILD))

    def alive(self) -> bool:
        return child_alive(self.pidfile)

    def assert_gone(self, sleep_s: float = 5.0) -> None:
        assert_child_gone(self.pidfile, self.marker, sleep_s)

    def assert_reaped(self) -> None:
        assert_child_reaped(self.pidfile)


@pytest.fixture
def fake_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeChild]:
    for name in FAKE_CHILD_ENV:
        monkeypatch.delenv(name, raising=False)
    pidfile = tmp_path / "child.pid"
    marker = tmp_path / "child.marker"
    monkeypatch.setenv("FAKE_CHILD_PIDFILE", str(pidfile))
    monkeypatch.setenv("FAKE_CHILD_MARKER", str(marker))
    yield FakeChild(pidfile, marker, monkeypatch)
    kill_leftover_child(pidfile)


@pytest.fixture
def fake_ffprobe(fake_child: FakeChild) -> str:
    assert (FAKE_SITE / "sitecustomize.py").is_file()
    fake_child.monkeypatch.setenv("PYTHONPATH", str(FAKE_SITE))
    return sys.executable
