from importlib.metadata import version

from ._builder import FFmpeg, input
from ._errors import FFmpegError
from ._probe import Format, ProbeResult, Stream, probe

__version__ = version("ffmpeg-wrap")

__all__ = [
    "FFmpeg",
    "FFmpegError",
    "Format",
    "ProbeResult",
    "Stream",
    "input",
    "probe",
]
