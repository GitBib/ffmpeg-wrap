# Guide

This page walks through the synchronous API. For exhaustive signatures and field
documentation, see the auto-generated [API Reference](reference/sync.md).

```python
import ffmpeg_wrap as ffmpeg
```

## Probe a media file

```python
result = ffmpeg.probe("video.mkv")

for stream in result.streams:
    print(stream.codec_name, stream.codec_type)

if result.format:
    print(result.format.duration)
    print(result.format.format_name)
```

`probe()` raises `FFmpegError` on subprocess failure or invalid output.

## Build and run a command

Every chain starts at `ffmpeg.input(...)`, which returns the `FFmpeg` builder.
Outputs, filters, and global flags are all methods on that chain.

```python
ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().run()
```

Input/output options are passed as keyword arguments:

```python
(
    ffmpeg.input("input.mkv", ss=10, t=30)
    .output("clip.mp4", vcodec="libx264", acodec="aac")
    .overwrite_output()
    .run()
)
```

Use `global_args()` for flags before the inputs, and `run(capture_stdout=...,
capture_stderr=...)` to capture process output.

Synthetic sources (test patterns, silence, color) are inputs too. Pass the
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

By default the builder runs the `ffmpeg`/`ffprobe` executables found on `PATH`.
If they live elsewhere, point the builder and the probe helpers at the
executables explicitly with `ffmpeg_path` and `ffprobe_path`:

```python
result = ffmpeg.probe("video.mkv", ffprobe_path="/usr/local/bin/ffprobe")

ok, stderr = ffmpeg.validate("video.mkv", ffprobe_path="/usr/local/bin/ffprobe")

ffmpeg.input("input.mkv", ffmpeg_path="/usr/local/bin/ffmpeg").output("output.mp4").run()
```

## Mapping and complex graphs

`map()` is repeatable and accepts raw specifiers or a `Stream` from `probe()`.
Use `filter_complex()` for a graph-level `-filter_complex` and wire labelled
outputs to multiple files:

```python
(
    ffmpeg.input("input.mkv")
    .filter_complex("[0:v]split=2[full][thumb];[thumb]scale=320:-2[thumb]")
    .output("full.mp4").map("[full]")
    .output("thumb.mp4").map("[thumb]")
    .overwrite_output()
    .run()
)
```

For large graphs, read them from a file with `filter_complex_script()`. It is
mutually exclusive with `filter_complex()` at runtime, so use one or the other:

```python
(
    ffmpeg.input("input.mkv")
    .filter_complex_script("graph.txt")
    .output("output.mp4")
    .run()
)
# ffmpeg -filter_complex_script graph.txt -i input.mkv output.mp4
```

When embedding a path inside a filtergraph (e.g. `subtitles=`), escape it with
`filter_arg_escape()`:

```python
path = r"C:\videos\clip.srt"
graph = f"subtitles={ffmpeg.filter_arg_escape(path)}"
# subtitles='C\:\\videos\\clip.srt'
```

## Validate a media file

`validate()` checks whether a file is valid media and returns a `(ok, stderr)`
tuple instead of raising on bad media. It only raises `FFmpegError` when the
ffprobe executable itself could not be run.

```python
ok, stderr = ffmpeg.validate("video.mkv")
if not ok:
    print(f"Invalid media: {stderr}")
```

The default `loglevel="warning"` surfaces ffprobe warnings (non-monotonic DTS,
unsupported codecs, truncated frames). Use `"error"` or `"fatal"` when only
hard failures matter, and pass extra ffprobe flags via `extra_args`.

## Encoder discovery

Discover what the installed ffmpeg build supports at runtime instead of
hard-coding it. `encoders()` returns the full set (cached per ffmpeg path);
`has_encoder()` is a single-name membership check:

```python
if ffmpeg.has_encoder("h264_nvenc"):
    video_codec = "h264_nvenc"
else:
    video_codec = "libx264"
```

Request a hardware acceleration backend with `hwaccel()` (emits `-hwaccel`
before the input's `-i`):

```python
(
    ffmpeg.input("input.mkv")
    .hwaccel("cuda")
    .output("output.mp4")
    .codec("v", video_codec)
    .overwrite_output()
    .run()
)
```

## Error handling

`FFmpegError` carries structured introspection so you can classify a failure
without re-parsing the message. `str(e)` is the human-readable message; the
`returncode`, `stderr`, and `cmd` attributes describe the underlying process
failure (each is `None` when not applicable):

```python
try:
    ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().run()
except ffmpeg.FFmpegError as e:
    if e.returncode == 1 and e.stderr and "No space left" in e.stderr:
        raise
    print(f"ffmpeg exited {e.returncode}: {e.stderr}")
```

This is the building block for consumer-side retry policies: the wrapper stays
unopinionated about which failures are retryable.
