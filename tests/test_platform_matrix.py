from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import anyio
import psutil
import pytest
from conftest import CHILD_GONE_DEADLINE, FAKE_CHILD, FAKE_SITE, TESTS_DIR, FakeChild, started_child_pid

import ffmpeg_wrap.aio as aio
from ffmpeg_wrap._errors import FFmpegTimeoutError
from ffmpeg_wrap._probe import _build_probe_cmd, _build_validate_cmd, probe, validate

REPO_ROOT = TESTS_DIR.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "python-tests.yml"
MAKEFILE = REPO_ROOT / "Makefile"
WATCHDOG = 20
TIMEOUT = 0.5

SKIP_FREE_FILES = ("test_timeout.py", "test_aio_timeout.py", "test_platform_matrix.py")
FORBIDDEN_SKIP_CALLS = ("pytest.mark.skipif", "pytest.mark.skip", "pytest.skip", "pytest.importorskip")
FORBIDDEN_SKIP_IMPORTS = ("skip", "importorskip")

SYNC_PATHS = ("capture", "tee-stdout", "tee-no-stdout", "probe", "validate")
ASYNC_PATHS = ("capture", "tee-stdout", "tee-no-stdout", "arun", "probe", "validate")
ASYNC_TAIL_PATHS = ("tee-stdout", "tee-no-stdout", "arun")
FFPROBE_PATHS = ("probe", "validate")


def _hook_ffprobe(fake_child: FakeChild) -> str:
    fake_child.set_env(PYTHONPATH=str(FAKE_SITE))
    return sys.executable


def _expected(path: str, builder, ffprobe: str) -> tuple[list[str], str]:
    if path == "probe":
        return _build_probe_cmd("x.wav", ffprobe), "ffprobe"
    if path == "validate":
        return _build_validate_cmd("x.wav", ffprobe), "ffprobe"
    return builder.compile(), "ffmpeg"


def _assert_timeout_error(error: FFmpegTimeoutError, cmd: list[str], tool: str, *, tail: bool) -> None:
    assert error.timeout == TIMEOUT
    assert error.cmd == cmd
    assert error.returncode is None
    if tail:
        assert isinstance(error.stderr, str)
        assert "partial" in error.stderr
    else:
        assert error.stderr is None
    assert str(error) == f"{tool} timed out after {TIMEOUT}s"


def _run_sync(path: str, builder, ffprobe: str) -> None:
    if path == "capture":
        builder.run(capture_stderr=True, timeout=TIMEOUT)
    elif path == "tee-stdout":
        builder.run(capture_stdout=True, timeout=TIMEOUT)
    elif path == "tee-no-stdout":
        builder.run(timeout=TIMEOUT)
    elif path == "probe":
        probe("x.wav", ffprobe_path=ffprobe, timeout=TIMEOUT)
    else:
        validate("x.wav", ffprobe_path=ffprobe, timeout=TIMEOUT)


async def _run_async(path: str, builder, ffprobe: str) -> None:
    if path == "capture":
        await aio.run(builder, capture_stderr=True, timeout=TIMEOUT)
    elif path == "tee-stdout":
        await aio.run(builder, capture_stdout=True, timeout=TIMEOUT)
    elif path == "tee-no-stdout":
        await aio.run(builder, timeout=TIMEOUT)
    elif path == "arun":
        await builder.arun(timeout=TIMEOUT)
    elif path == "probe":
        await aio.probe("x.wav", ffprobe_path=ffprobe, timeout=TIMEOUT)
    else:
        await aio.validate("x.wav", ffprobe_path=ffprobe, timeout=TIMEOUT)


@pytest.mark.timeout(30)
def test_timeout_expired_stderr_shape(fake_child: FakeChild):
    fake_child.set_env(FAKE_CHILD_STDERR="partial")
    with pytest.raises(subprocess.TimeoutExpired) as raw_info:
        subprocess.run(
            [sys.executable, str(FAKE_CHILD)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    raw = raw_info.value.stderr
    if sys.platform == "win32":
        assert isinstance(raw, str)
    else:
        assert isinstance(raw, bytes)
    assert "partial" in (raw if isinstance(raw, str) else raw.decode())
    fake_child.assert_gone()

    fake_child.pidfile.unlink()
    builder = fake_child.make()
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        builder.run(capture_stderr=True, text=True, timeout=TIMEOUT)
    assert isinstance(exc_info.value.stderr, str)
    assert "partial" in exc_info.value.stderr
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("path", SYNC_PATHS)
def test_every_sync_path_times_out_here(fake_child: FakeChild, path: str):
    builder = fake_child.make(FAKE_CHILD_STDERR="partial")
    ffprobe = _hook_ffprobe(fake_child) if path in FFPROBE_PATHS else ""
    cmd, tool = _expected(path, builder, ffprobe)
    with pytest.raises(FFmpegTimeoutError) as exc_info:
        _run_sync(path, builder, ffprobe)
    _assert_timeout_error(exc_info.value, cmd, tool, tail=True)
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("path", ASYNC_PATHS)
async def test_every_async_path_times_out_here(fake_child: FakeChild, path: str):
    builder = fake_child.make(FAKE_CHILD_STDERR="partial")
    ffprobe = _hook_ffprobe(fake_child) if path in FFPROBE_PATHS else ""
    cmd, tool = _expected(path, builder, ffprobe)
    with anyio.fail_after(WATCHDOG), pytest.raises(FFmpegTimeoutError) as exc_info:
        await _run_async(path, builder, ffprobe)
    _assert_timeout_error(exc_info.value, cmd, tool, tail=path in ASYNC_TAIL_PATHS)
    fake_child.assert_gone()
    fake_child.assert_reaped()


@pytest.mark.timeout(30)
def test_kill_is_final(fake_child: FakeChild):
    builder = fake_child.make()
    with pytest.raises(FFmpegTimeoutError):
        builder.run(capture_stderr=True, timeout=TIMEOUT)
    pid = started_child_pid(fake_child.pidfile)
    if pid is None:
        assert not fake_child.marker.exists()
        return
    deadline = time.monotonic() + CHILD_GONE_DEADLINE
    while psutil.pid_exists(pid):
        assert time.monotonic() < deadline, f"pid {pid} still exists {CHILD_GONE_DEADLINE}s after the timeout"
        time.sleep(0.05)
    assert not psutil.pid_exists(pid)
    if sys.platform == "win32":
        assert not fake_child.alive()
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert not fake_child.marker.exists()


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _skip_constructs(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            name = _dotted_name(node)
            if name in FORBIDDEN_SKIP_CALLS:
                found.append((node.lineno, name))
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            found.extend(
                (node.lineno, f"from pytest import {alias.name}")
                for alias in node.names
                if alias.name in FORBIDDEN_SKIP_IMPORTS
            )
    return [f"{path.name}:{lineno}: {name}" for lineno, name in sorted(found)]


def test_timeout_suites_have_no_inline_skips():
    for name in SKIP_FREE_FILES:
        path = TESTS_DIR / name
        assert path.is_file(), f"{path} is missing"
        assert _skip_constructs(path) == []


def test_inline_skip_scan_detects_every_forbidden_construct(tmp_path: Path):
    source = """\
import pytest
from pytest import importorskip
@pytest.mark.skipif(True, reason='x')
def test_a(): pytest.skip('x')
@pytest.mark.skip
def test_b(): pytest.importorskip('x')
"""
    path = tmp_path / "test_skippy.py"
    path.write_text(source, encoding="utf-8")
    found = _skip_constructs(path)
    assert [line.split(": ", 1)[1] for line in found] == [
        "from pytest import importorskip",
        "pytest.mark.skipif",
        "pytest.skip",
        "pytest.mark.skip",
        "pytest.importorskip",
    ]


def _section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    stop = text.find(end, begin + len(start))
    return text[begin:] if stop == -1 else text[begin:stop]


def test_ci_runs_the_platform_gate():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    run_job = _section(workflow, "\n  run:\n", "\n  runtime-import:\n")
    assert "os: [ubuntu-latest, windows-latest, macos-latest]" in run_job
    assert "ffmpeg -version && ffprobe -version" in run_job
    assert "make test-platform" in run_job

    makefile = MAKEFILE.read_text(encoding="utf-8")
    target = _section(makefile, "\ntest-platform:", "\n\n")
    for name in SKIP_FREE_FILES:
        assert f"tests/{name}" in target
    assert "-rs" in target
    assert "skipped" in target
    assert "-ra" in _section(makefile, "\ntest: download\n", "\n\n")
