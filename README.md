# ffmpeg-wrap

[![Tests](https://github.com/GitBib/ffmpeg-wrap/actions/workflows/python-tests.yml/badge.svg)](https://github.com/GitBib/ffmpeg-wrap/actions/workflows/python-tests.yml)
[![Prek checks](https://github.com/GitBib/ffmpeg-wrap/actions/workflows/prek.yml/badge.svg)](https://github.com/GitBib/ffmpeg-wrap/actions/workflows/prek.yml)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/ffmpeg-wrap?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/ffmpeg-wrap)

A typed Python wrapper for FFmpeg and ffprobe CLI tools. Build FFmpeg commands with a fluent API and parse ffprobe output into typed data structures.

## Requirements

- Python 3.10+
- FFmpeg and ffprobe installed and available on PATH

## Installation

With uv:

```
uv add ffmpeg-wrap
```

With pip:

```
pip install ffmpeg-wrap
```

## Quick start

```python
import ffmpeg_wrap as ffmpeg

# Probe a file into typed structures
result = ffmpeg.probe("video.mkv")
for stream in result.streams:
    print(stream.codec_name, stream.codec_type)

# Build and run a command with the fluent builder
ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().run()
```

### Async

The asynchronous mirror of the API ships as an optional extra. With uv:

```
uv add "ffmpeg-wrap[async]"
```

With pip:

```
pip install "ffmpeg-wrap[async]"
```

```python
import anyio

from ffmpeg_wrap import aio, input


async def main():
    await input("input.mkv").output("output.mp4", c="copy").overwrite_output().arun()
    result = await aio.probe("output.mp4")
    print(result.format.format_name if result.format else None)


anyio.run(main)
```

## Documentation

Full documentation, including guides and the complete API reference, is hosted at
[gitbib.github.io/ffmpeg-wrap](https://gitbib.github.io/ffmpeg-wrap/):

- **[Guide](https://gitbib.github.io/ffmpeg-wrap/guide/)** — a walkthrough of the
  synchronous API: probing files, building and running commands, mapping streams,
  complex filtergraphs, validation, encoder discovery, and error handling.
- **[Async API](https://gitbib.github.io/ffmpeg-wrap/async/)** — the AnyIO-backed
  asynchronous mirror, backend choice (asyncio or trio), and bounding concurrency.
- **[API Reference](https://gitbib.github.io/ffmpeg-wrap/reference/sync/)** — the
  auto-generated reference for every public function, builder method, and model.
