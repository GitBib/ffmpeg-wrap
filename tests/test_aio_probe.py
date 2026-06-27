"""Task 3 tests for the async ``ffmpeg_wrap.aio`` probe/validate/encoders API.

These exercise the real ffmpeg/ffprobe binaries against the downloaded media
fixtures. Every coroutine test is auto-promoted by ``anyio_mode = "auto"`` and
runs on both AnyIO backends via the module-scoped ``anyio_backend`` fixture
(asyncio + trio) defined in ``conftest.py``.

The whole module is skipped cleanly when ``anyio`` is not installed (so the
suite still passes without the ``[async]`` extra) and when ffmpeg/ffprobe are
unavailable (via the existing ``ffmpeg_available``-backed fixtures).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("anyio")

import ffmpeg_wrap as ffmpeg
import ffmpeg_wrap.aio as aio
from ffmpeg_wrap import FFmpegError
from ffmpeg_wrap._encoders import _clear_encoders_cache


@pytest.fixture(autouse=True)
def _reset_encoders_cache() -> None:
    """Keep the shared encoder cache clean across tests."""
    _clear_encoders_cache()
    yield
    _clear_encoders_cache()


class TestAioProbe:
    async def test_probe_success(self, real_file: Path) -> None:
        result = await aio.probe(real_file)
        assert isinstance(result, aio.ProbeResult)
        assert result.streams

    async def test_probe_bad_path_raises(self, ffmpeg_available: None) -> None:
        with pytest.raises(FFmpegError):
            await aio.probe("/nonexistent/file/does-not-exist.mkv")

    async def test_probe_missing_binary_raises(self, real_file: Path) -> None:
        with pytest.raises(FFmpegError):
            await aio.probe(real_file, ffprobe_path="ffprobe-does-not-exist")

    async def test_probe_parity_with_sync(self, real_file: Path) -> None:
        assert await aio.probe(real_file) == ffmpeg.probe(real_file)


class TestAioValidate:
    async def test_validate_success(self, real_file: Path) -> None:
        ok, stderr = await aio.validate(real_file)
        assert ok is True
        assert stderr == ""

    async def test_validate_bad_media_returns_false(self, bad_media: Path) -> None:
        ok, stderr = await aio.validate(bad_media)
        assert ok is False
        assert isinstance(stderr, str)

    async def test_validate_invalid_loglevel_raises(self) -> None:
        # Pure-logic path: ValueError is raised by _build_validate_cmd before any
        # subprocess, so this does not depend on ffmpeg being installed.
        with pytest.raises(ValueError, match="invalid loglevel"):
            await aio.validate("whatever.mkv", loglevel="bogus")

    async def test_validate_missing_binary_raises(self, real_file: Path) -> None:
        with pytest.raises(FFmpegError):
            await aio.validate(real_file, ffprobe_path="ffprobe-does-not-exist")

    async def test_validate_parity_with_sync(self, real_file: Path) -> None:
        assert await aio.validate(real_file) == ffmpeg.validate(real_file)

    async def test_validate_bad_media_parity_with_sync(self, bad_media: Path) -> None:
        # ffmpeg embeds a nondeterministic context pointer in its diagnostics
        # (``@ 0x811020000`` on Unix, ``@ 000002e074235680`` without the ``0x``
        # prefix on Windows); normalise it before comparing so we assert on the
        # meaningful stderr content, not the per-run heap address.
        async_ok, async_err = await aio.validate(bad_media)
        sync_ok, sync_err = ffmpeg.validate(bad_media)
        ptr = re.compile(r"@ (?:0x)?[0-9a-fA-F]+")
        assert async_ok == sync_ok
        assert ptr.sub("@ 0xPTR", async_err) == ptr.sub("@ 0xPTR", sync_err)


class TestAioEncoders:
    async def test_encoders_success(self, ffmpeg_available: None) -> None:
        result = await aio.encoders()
        assert isinstance(result, frozenset)
        assert result
        assert all(isinstance(name, str) for name in result)

    async def test_encoders_missing_binary_raises(self, ffmpeg_available: None) -> None:
        with pytest.raises(FFmpegError):
            await aio.encoders(ffmpeg_path="ffmpeg-does-not-exist")

    async def test_has_encoder_true(self, ffmpeg_available: None) -> None:
        # Pull a real encoder name from the listing, then assert membership.
        names = await aio.encoders()
        any_name = next(iter(names))
        assert await aio.has_encoder(any_name) is True

    async def test_has_encoder_false(self, ffmpeg_available: None) -> None:
        assert await aio.has_encoder("definitely-not-a-real-encoder") is False

    async def test_encoders_parity_with_sync(self, ffmpeg_available: None) -> None:
        async_result = await aio.encoders()
        _clear_encoders_cache()
        sync_result = ffmpeg.encoders()
        assert async_result == sync_result
        assert all(isinstance(name, str) for name in async_result)
