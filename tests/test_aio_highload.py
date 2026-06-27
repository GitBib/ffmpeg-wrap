"""Task 5 tests: highload concurrency + lazy public ``aio`` export.

The highload test launches many concurrent ``aio.probe()`` calls bounded by an
``anyio.CapacityLimiter`` and asserts both that every call succeeds (matching the
sync probe) and that the observed concurrency never exceeds the limiter bound.

The export tests prove ``from ffmpeg_wrap import aio`` works via the PEP 562
package ``__getattr__`` and that a bare ``import ffmpeg_wrap`` does NOT pull in
anyio (checked in a fresh subprocess so an already-imported anyio in the test
process can't mask a leak — mirrors ``tests/test_aio_packaging.py``).

The whole module is skipped when ``anyio`` is not installed (so the suite still
passes without the ``[async]`` extra).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("anyio")

import anyio

import ffmpeg_wrap as ffmpeg
import ffmpeg_wrap.aio as aio


class _ConcurrencyTracker:
    """Track the peak number of simultaneously-active limited sections.

    Robustness note: this mutates shared (``active``/``peak``) state without a
    lock. That is safe here only because AnyIO runs the tasks cooperatively on a
    single thread and there is NO ``await`` between :meth:`enter` and the peak
    read (nor in :meth:`exit`), so no task switch can interleave a partial
    update. Do not add an ``await`` inside these methods.
    """

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    def enter(self) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)

    def exit(self) -> None:
        self.active -= 1


class TestAioHighload:
    async def test_bounded_concurrent_probe(self, real_file: Path) -> None:
        n_jobs = 20
        limit = 4
        limiter = anyio.CapacityLimiter(limit)
        tracker = _ConcurrencyTracker()
        results: list[aio.ProbeResult] = []
        expected = ffmpeg.probe(real_file)

        async def one_probe() -> None:
            async with limiter:
                tracker.enter()
                try:
                    result = await aio.probe(real_file)
                finally:
                    tracker.exit()
                results.append(result)

        with anyio.fail_after(120):
            async with anyio.create_task_group() as tg:
                for _ in range(n_jobs):
                    tg.start_soon(one_probe)

        assert len(results) == n_jobs
        assert all(r == expected for r in results)
        # The CapacityLimiter must have kept observed concurrency within bound.
        assert tracker.peak <= limit
        assert tracker.peak >= 1


def test_from_ffmpeg_wrap_import_aio() -> None:
    """The PEP 562 ``__getattr__`` exposes ``aio`` as a package attribute."""
    from ffmpeg_wrap import aio as exported

    assert exported is aio


def test_unknown_attribute_raises_attribute_error() -> None:
    import ffmpeg_wrap

    with pytest.raises(AttributeError):
        _ = ffmpeg_wrap.does_not_exist


def test_core_import_does_not_import_anyio() -> None:
    """``import ffmpeg_wrap`` must not import anyio until ``aio`` is accessed.

    Run in a fresh subprocess so anyio already imported in this test process
    can't mask a leak. After ``import ffmpeg_wrap`` anyio must be absent; after
    accessing ``ffmpeg_wrap.aio`` it must be present.
    """
    code = (
        "import sys\n"
        "import ffmpeg_wrap\n"
        "assert 'anyio' not in sys.modules, sorted(m for m in sys.modules if 'anyio' in m)\n"
        "mod = ffmpeg_wrap.aio\n"
        "assert 'anyio' in sys.modules\n"
        "assert mod is ffmpeg_wrap.aio\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_wildcard_import_does_not_import_anyio() -> None:
    """``from ffmpeg_wrap import *`` must not import anyio.

    Regression guard for listing ``aio`` in ``__all__``: that would make the
    wildcard import bind ``aio``, eagerly triggering ``__getattr__`` -> import
    anyio and breaking ``import *`` when the optional ``[async]`` extra is not
    installed. Run in a fresh subprocess so a pre-imported anyio can't mask a
    leak.
    """
    code = (
        "import sys\n"
        "from ffmpeg_wrap import *\n"
        "assert 'anyio' not in sys.modules, sorted(m for m in sys.modules if 'anyio' in m)\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
