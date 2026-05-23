from importlib.metadata import version

from ._builder import FFmpeg, input
from ._encoders import encoders, has_encoder
from ._errors import FFmpegError
from ._filters import filter_arg_escape
from ._probe import CodecType, Format, ProbeResult, Stream, probe, validate

__version__ = version("ffmpeg-wrap")

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
