# ffmpeg-wrap

A typed Python wrapper for FFmpeg and ffprobe. Build FFmpeg commands with a
fluent API and parse ffprobe output into typed [msgspec](https://msgspec.dev/)
models. The core package is dependency-light (just `msgspec`) and ships both a
synchronous API and an optional [AnyIO](https://anyio.readthedocs.io/)-backed
async mirror.

## Requirements

- Python 3.10+
- FFmpeg and ffprobe installed and available on `PATH`

## Installation

With uv:

```
uv add ffmpeg-wrap
```

With pip:

```
pip install ffmpeg-wrap
```

The async API lives behind an optional extra:

```
uv add "ffmpeg-wrap[async]"
```

Or with pip:

```
pip install "ffmpeg-wrap[async]"
```

## Sync example

```python
import ffmpeg_wrap as ffmpeg

result = ffmpeg.probe("video.mkv")
for stream in result.streams:
    print(stream.codec_name, stream.codec_type)

ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().run()
```

## Async example

```python
import anyio

from ffmpeg_wrap import aio, input


async def main():
    await input("input.mkv").output("output.mp4", c="copy").overwrite_output().arun()
    result = await aio.probe("output.mp4")
    print(result.format.format_name if result.format else None)


anyio.run(main)
```

## Where to next

- [Guide](guide.md) — sync usage: the builder chain, probing, validation, encoder discovery.
- [Async API](async.md) — the AnyIO-backed mirror, backend choice, and bounding concurrency.
- [API Reference — Sync](reference/sync.md) — auto-generated reference for the public sync surface.
- [API Reference — Async](reference/async.md) — auto-generated reference for `ffmpeg_wrap.aio`.
