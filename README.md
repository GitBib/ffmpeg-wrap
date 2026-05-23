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

`FFmpegError` carries structured introspection so you can classify a failure
without re-parsing the message string. `str(e)` is still the human-readable
message; the `returncode`, `stderr`, and `cmd` attributes describe the
underlying process failure (each is `None` when not applicable):

```python
try:
    ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().run()
except ffmpeg.FFmpegError as e:
    # Branch on the exit code / inspect stderr instead of scraping str(e).
    if e.returncode == 1 and e.stderr and "No space left" in e.stderr:
        raise
    print(f"ffmpeg exited {e.returncode}: {e.stderr}")
```

This is the building block for consumer-side retry policies (e.g. retrying only
on transient exit codes): the wrapper stays unopinionated about which failures
are retryable and exposes the raw `returncode`/`stderr` for the caller to decide.

### Custom executable paths

```python
result = ffmpeg.probe("video.mkv", ffprobe_path="/usr/local/bin/ffprobe")

ok, stderr = ffmpeg.validate("video.mkv", ffprobe_path="/usr/local/bin/ffprobe")

ffmpeg.input("input.mkv", ffmpeg_path="/usr/local/bin/ffmpeg").output("output.mp4").run()
```

### Pipelines always start at `input()`

Every chain begins with `ffmpeg.input(...)`, which returns the `FFmpeg` builder.
There is no separate `output()`/`graph()` entry point: outputs, filters, and
global flags are all methods on the chain rooted at an input.

Synthetic sources (test patterns, silence, color) are inputs too — pass the
`lavfi` virtual device as the input and the source description as its
"filename":

```python
(
    ffmpeg.input("anullsrc=channel_layout=stereo:sample_rate=48000", f="lavfi", t=5)
    .output("silence.wav")
    .overwrite_output()
    .run()
)
# ffmpeg -f lavfi -t 5 -i anullsrc=channel_layout=stereo:sample_rate=48000 silence.wav
```

### Complex pipelines

Use `filter_complex()` for a graph-level `-filter_complex` and `map()` to wire
its labelled outputs to multiple output files. The filtergraph is emitted in a
dedicated slot (after global args, before inputs), so its placement no longer
depends on which output it is attached to:

```python
(
    ffmpeg.input("input.mkv")
    .filter_complex("[0:v]split=2[full][thumb];[thumb]scale=320:-2[thumb]")
    .output("full.mp4").map("[full]")
    .output("thumb.mp4").map("[thumb]")
    .overwrite_output()
    .run()
)
# ffmpeg -filter_complex [0:v]split=2[full][thumb];[thumb]scale=320:-2[thumb] \
#   -i input.mkv -map [full] full.mp4 -map [thumb] thumb.mp4
```

For large graphs, read them from a file with `filter_complex_script()` (mutually
exclusive with `filter_complex()` at runtime):

```python
(
    ffmpeg.input("input.mkv")
    .filter_complex_script("graph.txt")
    .output("output.mp4")
    .run()
)
# ffmpeg -filter_complex_script graph.txt -i input.mkv output.mp4
```

`map()` is repeatable and accepts raw specifiers, so a stream-preserving remux
emits one `-map` per stream type:

```python
(
    ffmpeg.input("input.mkv")
    .output("output.mkv", c="copy")
    .map("0:v").map("0:a").map("0:s")
    .overwrite_output()
    .run()
)
# ffmpeg -i input.mkv -c copy -map 0:v -map 0:a -map 0:s output.mkv
```

The same expansion works by passing a list directly: `output("out.mkv",
map=["0:v", "0:a", "0:s"])`. `map(stream)` accepts a `Stream` from `probe()` and
emits its per-type specifier (e.g. `0:s:0`); `map_stream("s", 0)` builds the
specifier explicitly.

When embedding a path in a filtergraph (e.g. `subtitles=`), escape it with
`filter_arg_escape()` so colons and backslashes in the path do not break the
graph:

```python
path = r"C:\videos\clip.srt"
graph = f"subtitles={ffmpeg.filter_arg_escape(path)}"
# subtitles='C\:\\videos\\clip.srt'
```

### Hardware acceleration

Discover what the installed ffmpeg build actually supports at runtime instead of
guessing. `encoders()` returns the full set of encoder names (cached per
ffmpeg path); `has_encoder()` is a single-name membership check:

```python
if ffmpeg.has_encoder("h264_nvenc"):
    video_codec = "h264_nvenc"
else:
    video_codec = "libx264"
```

Request a hardware decode/acceleration backend with the `hwaccel` input option,
or the discoverable `hwaccel()` sugar — both emit `-hwaccel` before the input's
`-i`:

```python
(
    ffmpeg.input("input.mkv")
    .hwaccel("cuda")
    .output("output.mp4")
    .codec("v", video_codec)
    .overwrite_output()
    .run()
)
# ffmpeg -hwaccel cuda -i input.mkv -c:v h264_nvenc output.mp4

# Equivalent, set directly on the input:
ffmpeg.input("input.mkv", hwaccel="cuda").output("output.mp4").codec("v", video_codec)
```

The wrapper stays unopinionated about encoder tuning: it reports what is
available and lets you choose the codec and parameters.

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
- `output(filename, **kwargs)` -- Add an output file with options. List/tuple values expand to a repeated flag (e.g. `map=["0:v", "0:a"]`).
- `overwrite_output()` -- Add the `-y` flag to overwrite existing output files.
- `global_args(*args)` -- Add global arguments before inputs.
- `map(*specs)` -- Append one `-map` per spec on the current output. Accepts raw specifiers (`"0:v"`) or a `Stream` (emits its per-type specifier). Repeatable.
- `map_stream(kind, ordinal, input=0)` -- Append `-map {input}:{kind}:{ordinal}` (e.g. `map_stream("s", 0)` -> `-map 0:s:0`).
- `codec(kind, name)` -- `-c:{kind} {name}` on the current output (e.g. `codec("v", "libx264")`).
- `bitrate(kind, value)` -- `-b:{kind} {value}`.
- `quality(kind, value)` -- `-q:{kind} {value}`.
- `audio_filter(chain)` / `video_filter(chain)` -- `-filter:a` / `-filter:v` on the current output.
- `flag(*names)` -- Append bare `-{name}` switches (e.g. `flag("vn", "sn")`).
- `filter_complex(graph_str)` -- Graph-level `-filter_complex`, emitted in a dedicated slot after global args and before inputs.
- `filter_complex_script(path)` -- Graph-level `-filter_complex_script` (mutually exclusive with `filter_complex()` at runtime).
- `hwaccel(name)` -- Input-side `-hwaccel {name}` sugar, emitted before `-i`.
- `hide_banner()` -- `-hide_banner` global flag.
- `loglevel(level)` -- `-loglevel {level}` global flag.
- `compile()` -- Build the command as a list of strings without executing it.
- `run(capture_stdout=False, capture_stderr=False, text=False)` -- Execute the command. Returns `(stdout, stderr)`. With `text=True` the captured values are `str`; otherwise `bytes` (or `None` when the corresponding capture flag is `False`). Raises `FFmpegError` on failure.

### ProbeResult

Typed result from ffprobe.

- `streams` -- List of `Stream` objects.
- `format` -- Optional `Format` object.
- `duration_seconds()` -- `float | None`. Delegates to `format.duration_seconds()`; `None` when there is no format.

### Stream

A single stream from ffprobe output. Fields include `index`, `codec_name`, `codec_type`, `type_index`, `width`, `height`, `channels`, `sample_rate`, `duration`, `bit_rate`, `tags`, `disposition`.

- `map_specifier(input_index=0)` -- The per-type `-map` specifier for this stream (e.g. `0:s:0`), falling back to the absolute-index form for unknown `codec_type`.
- `duration_seconds()` -- `float | None`. Parses `duration`, returning `None` for missing/`"N/A"`/non-numeric values.
- `is_video` / `is_audio` / `is_subtitle` / `is_data` / `is_attachment` -- Properties comparing `codec_type` against `CodecType`. All `False` for unknown/`None`.
- `is_text_subtitle` / `is_image_subtitle` -- Best-effort properties classifying a subtitle stream by `codec_name`.

### Format

The format section from ffprobe output. Fields include `filename`, `nb_streams`, `nb_programs`, `format_name`, `format_long_name`, `start_time`, `duration`, `size`, `bit_rate`, `probe_score`, `tags`.

- `duration_seconds()` -- `float | None`. Parses `duration`, returning `None` for missing/`"N/A"`/non-numeric values (the raw `duration: str` is preserved).

### CodecType

`StrEnum` of the known ffprobe `codec_type` values (`VIDEO`, `AUDIO`, `SUBTITLE`, `DATA`, `ATTACHMENT`). Used by the `Stream` predicates. `Stream.codec_type` stays typed `str | None` so exotic values still decode without `ValidationError` — compare against these members rather than retyping the field.

### encoders(ffmpeg_path="ffmpeg")

Return the set of encoder names supported by the installed ffmpeg build.

- Parses `ffmpeg -hide_banner -encoders`; the result is cached per `ffmpeg_path`.
- Returns: `frozenset[str]`.

### has_encoder(name, ffmpeg_path="ffmpeg")

`bool`. Whether `name` is in `encoders(ffmpeg_path)`. Use this instead of hard-coding encoder availability.

### filter_arg_escape(value)

`str`. Escape a value for use inside a filtergraph argument (e.g. a `subtitles=` path), handling `\`, `:`, and `'`, and wrapping the result in single quotes.

### FFmpegError

Exception raised when FFmpeg or ffprobe operations fail. `str(e)` is the human-readable message; the keyword-only attributes `stderr` (`str | None`), `returncode` (`int | None`), and `cmd` (`list[str] | None`) describe the underlying process failure and are `None` when not applicable.
