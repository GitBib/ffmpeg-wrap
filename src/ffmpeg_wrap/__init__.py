from importlib.metadata import version
from typing import TYPE_CHECKING

from ffmpeg_wrap._builder import FFmpeg, input
from ffmpeg_wrap._encoders import encoders, has_encoder
from ffmpeg_wrap._errors import FFmpegError
from ffmpeg_wrap._filters import filter_arg_escape
from ffmpeg_wrap._probe import CodecType, Format, ProbeResult, Stream, probe, validate

if TYPE_CHECKING:
    from ffmpeg_wrap import aio as aio

__version__ = version("ffmpeg-wrap")

# ``aio`` is intentionally NOT eagerly imported above: it pulls in the optional
# ``anyio`` dependency, and the core package must stay dependency-free. It is
# resolved lazily via the PEP 562 module ``__getattr__`` below, so ``from
# ffmpeg_wrap import aio`` (and ``import ffmpeg_wrap.aio``) works without making
# ``import ffmpeg_wrap`` import anyio. ``aio`` is deliberately ABSENT from
# ``__all__``: a name in ``__all__`` makes ``from ffmpeg_wrap import *`` bind it,
# which would eagerly trigger ``__getattr__`` -> import anyio and break wildcard
# import when the optional ``[async]`` extra is not installed.
__all__ = [
    "CodecType",
    "FFmpeg",
    "FFmpegError",
    "Format",
    "ProbeResult",
    "Stream",
    "encoders",
    "filter_arg_escape",
    "has_encoder",
    "input",
    "probe",
    "validate",
]


def __getattr__(name: str) -> object:
    """Lazily resolve the optional ``aio`` submodule (PEP 562).

    Accessing ``ffmpeg_wrap.aio`` imports the anyio-backed submodule on first
    use and caches it in module globals, so subsequent attribute accesses skip
    this hook entirely. Importing ``ffmpeg_wrap`` itself never imports anyio.
    """
    if name == "aio":
        import importlib

        mod = importlib.import_module("ffmpeg_wrap.aio")
        globals()["aio"] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
