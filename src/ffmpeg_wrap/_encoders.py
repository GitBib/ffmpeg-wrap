from __future__ import annotations

import logging
import subprocess

from ._errors import FFmpegError

logger = logging.getLogger("ffmpeg_wrap")

# Cache of parsed encoder sets, keyed on the ffmpeg binary path so a process
# can introspect more than one build. Cleared in tests via
# ``_clear_encoders_cache``.
_ENCODERS_CACHE: dict[str, frozenset[str]] = {}


def _parse_encoders(output: str) -> frozenset[str]:
    """Extract encoder names from ``ffmpeg -encoders`` output.

    The listing has a flags legend, a ``------`` separator, then one encoder
    per line as ``<flags> <name> <description>``. Only lines after the
    separator are parsed, taking the second whitespace-delimited token as the
    encoder name.
    """
    names: set[str] = set()
    seen_separator = False
    for line in output.splitlines():
        stripped = line.strip()
        if not seen_separator:
            if stripped and set(stripped) == {"-"}:
                seen_separator = True
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            names.add(parts[1])
    return frozenset(names)


def encoders(ffmpeg_path: str = "ffmpeg") -> frozenset[str]:
    """Return the set of encoder names reported by ``ffmpeg -encoders``.

    The result is cached per ``ffmpeg_path`` for the lifetime of the process.
    Use :func:`has_encoder` for a single-name membership check.

    Args:
        ffmpeg_path: Path to the ffmpeg executable.

    Returns:
        A frozenset of encoder names (e.g. ``"libx264"``, ``"h264_nvenc"``).

    Raises:
        FFmpegError: If ffmpeg cannot be run or exits non-zero.
    """
    cached = _ENCODERS_CACHE.get(ffmpeg_path)
    if cached is not None:
        return cached

    cmd = [ffmpeg_path, "-hide_banner", "-encoders"]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, text=True)
    except subprocess.CalledProcessError as e:
        stderr_text = e.stderr if isinstance(e.stderr, str) else None
        err_msg = stderr_text or str(e)
        logger.error(f"ffmpeg -encoders failed: {err_msg}")
        raise FFmpegError(
            f"ffmpeg -encoders error: {err_msg}",
            stderr=stderr_text,
            returncode=e.returncode,
            cmd=cmd,
        ) from e
    except OSError as e:
        logger.error(f"ffmpeg could not be executed: {e}")
        raise FFmpegError(f"ffmpeg could not be executed: {e}", cmd=cmd) from e

    parsed = _parse_encoders(result.stdout)
    _ENCODERS_CACHE[ffmpeg_path] = parsed
    return parsed


def has_encoder(name: str, ffmpeg_path: str = "ffmpeg") -> bool:
    """Return whether ``name`` is an available ffmpeg encoder.

    Args:
        name: Encoder name to check (e.g. ``"h264_nvenc"``).
        ffmpeg_path: Path to the ffmpeg executable.

    Returns:
        True iff ``name`` appears in :func:`encoders`.

    Raises:
        FFmpegError: If ffmpeg cannot be run or exits non-zero.
    """
    return name in encoders(ffmpeg_path)


def _clear_encoders_cache() -> None:
    """Clear the per-path encoder cache (test-only hook)."""
    _ENCODERS_CACHE.clear()
