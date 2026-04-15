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

## Usage

```python
import ffmpeg_wrap as ffmpeg
```

### Probe a media file

```python
result = ffmpeg.probe("video.mkv")

for stream in result.streams:
    print(stream.codec_name, stream.codec_type)

if result.format:
    print(result.format.duration)
    print(result.format.format_name)
```

### Build and run an FFmpeg command

```python
ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().run()
```

### Multiple inputs and outputs

```python
(
    ffmpeg.input("input.mkv", ss=10, t=30)
    .output("clip.mp4", vcodec="libx264", acodec="aac")
    .overwrite_output()
    .run()
)
```

### Global arguments

```python
(
    ffmpeg.input("input.mkv")
    .output("output.mp4", c="copy")
    .global_args("-hide_banner", "-loglevel", "error")
    .overwrite_output()
    .run()
)
```

### Capture output

```python
stdout, stderr = (
    ffmpeg.input("input.mkv")
    .output("output.mp4", c="copy")
    .overwrite_output()
    .run(capture_stdout=True, capture_stderr=True)
)
```

### Validate a media file

`validate()` checks whether a file is valid media without parsing full probe output.
It returns a `(ok, stderr)` tuple instead of raising on bad media.

```python
ok, stderr = ffmpeg.validate("video.mkv")
if not ok:
    print(f"Invalid media: {stderr}")
```

The default `loglevel="warning"` surfaces ffprobe warnings (non-monotonic DTS,
unsupported codecs, truncated frames) in addition to hard errors. Use a stricter
level when only fatal problems matter:

```python
ok, stderr = ffmpeg.validate("video.mkv", loglevel="error")
ok, stderr = ffmpeg.validate("video.mkv", loglevel="fatal")
```

Pass extra ffprobe flags via `extra_args`:

```python
ok, stderr = ffmpeg.validate("video.mkv", extra_args=("-hide_banner",))
```

Use `validate()` when you only need a pass/fail check. Use `probe()` when you need
stream and format metadata. The only exception `validate()` raises is `FFmpegError`
when the ffprobe executable could not be run (missing, not executable, etc.).

```python
try:
    ok, stderr = ffmpeg.validate("video.mkv")
except ffmpeg.FFmpegError:
    print("ffprobe could not be executed")
```

### Error handling

```python
try:
    ffmpeg.probe("nonexistent.mkv")
except ffmpeg.FFmpegError as e:
    print(f"Probe failed: {e}")

try:
    ffmpeg.input("missing.mkv").output("out.mp4").run()
except ffmpeg.FFmpegError as e:
    print(f"FFmpeg failed: {e}")
```

### Custom executable paths

```python
result = ffmpeg.probe("video.mkv", ffprobe_path="/usr/local/bin/ffprobe")

ok, stderr = ffmpeg.validate("video.mkv", ffprobe_path="/usr/local/bin/ffprobe")

ffmpeg.input("input.mkv", ffmpeg_path="/usr/local/bin/ffmpeg").output("output.mp4").run()
```

## API Reference

### probe(filename, ffprobe_path="ffprobe")

Run ffprobe on a file and return a typed `ProbeResult`.

- `filename` -- Path to the media file (str or PathLike).
- `ffprobe_path` -- Path to the ffprobe executable. Defaults to `"ffprobe"`.
- Returns: `ProbeResult` with `streams` and `format` fields.
- Raises: `FFmpegError` on subprocess failure or invalid output.

### validate(filename, ffprobe_path="ffprobe", loglevel="warning", extra_args=())

Run ffprobe in validation mode and check for errors/warnings.

- `filename` -- Path to the media file (str or PathLike).
- `ffprobe_path` -- Path to the ffprobe executable. Defaults to `"ffprobe"`.
- `loglevel` -- Value passed to ffprobe's `-v` flag. Defaults to `"warning"` (surfaces DTS/codec warnings). Use `"error"` to ignore warnings or `"fatal"`/`"panic"` for only unrecoverable failures.
- `extra_args` -- Additional raw arguments forwarded to ffprobe before the filename, e.g. `("-hide_banner",)`.
- Returns: `tuple[bool, str]` -- `(ok, stderr_text)`. `ok` is `True` when ffprobe exits with code 0 and stderr is empty after stripping whitespace.
- Raises: `FFmpegError` only when the ffprobe executable could not be run. Does not raise on invalid media.

### input(filename, ffmpeg_path="ffmpeg", **kwargs)

Create a new `FFmpeg` builder chain starting with an input file.

- `filename` -- Input file path.
- `ffmpeg_path` -- Path to the ffmpeg executable. Defaults to `"ffmpeg"`.
- `**kwargs` -- Input options passed as FFmpeg flags (e.g., `ss=10`, `t=30`).
- Returns: `FFmpeg` instance for method chaining.

### FFmpeg

Fluent builder for FFmpeg commands.

- `input(filename, **kwargs)` -- Add an input file with options.
- `output(filename, **kwargs)` -- Add an output file with options.
- `overwrite_output()` -- Add the `-y` flag to overwrite existing output files.
- `global_args(*args)` -- Add global arguments before inputs.
- `compile()` -- Build the command as a list of strings without executing it.
- `run(capture_stdout=False, capture_stderr=False)` -- Execute the command. Returns `(stdout, stderr)`. Values are `bytes` when the corresponding capture flag is `True`, or `None` otherwise. Raises `FFmpegError` on failure.

### ProbeResult

Typed result from ffprobe.

- `streams` -- List of `Stream` objects.
- `format` -- Optional `Format` object.

### Stream

A single stream from ffprobe output. Fields include `index`, `codec_name`, `codec_type`, `width`, `height`, `channels`, `sample_rate`, `duration`, `bit_rate`, `tags`, `disposition`.

### Format

The format section from ffprobe output. Fields include `filename`, `nb_streams`, `nb_programs`, `format_name`, `format_long_name`, `start_time`, `duration`, `size`, `bit_rate`, `probe_score`, `tags`.

### FFmpegError

Exception raised when FFmpeg or ffprobe operations fail.
