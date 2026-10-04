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

### Bit depth

`Stream` carries the three raw fields ffprobe reports for sample width
(`sample_fmt`, `bits_per_sample`, `bits_per_raw_sample`), and `bit_depth()`
resolves them the way ffprobe populates them: `bits_per_raw_sample` when it is
present and numeric, else a positive `bits_per_sample`, else `None`:

```python
for stream in result.streams:
    if stream.is_audio:
        print(stream.sample_fmt, stream.bits_per_sample, stream.bits_per_raw_sample)
        print(stream.bit_depth())
```

The value is the width ffprobe reports and says nothing about lossiness.
16-bit WAV and FLAC answer `16`; 24-bit PCM and FLAC answer `24`, although both
report `sample_fmt="s32"`, which is why `sample_fmt` alone is not enough; IMA
ADPCM answers `4` and µ-law answers `8` because those codecs have a real coded
width; MP3 and Opus answer `None` because they carry no fixed width.

Video streams answer their pixel bit depth, because ffprobe reports it in
`bits_per_raw_sample` too: `8` for 8-bit H.264, `10` for `yuv420p10le`.
`sample_fmt` and `bits_per_sample` stay `None` for video.

## Build and run a command

Every chain starts at `ffmpeg.input(...)`, which returns the `FFmpeg` builder.
Outputs, filters, and global flags are all methods on that chain.

```python
ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().run()
```

Input/output options are passed as keyword arguments:

```python
ffmpeg.input("input.mkv", ss=10, t=30).output("clip.mp4", vcodec="libx264", acodec="aac").overwrite_output().run()
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
    .output("full.mp4")
    .map("[full]")
    .output("thumb.mp4")
    .map("[thumb]")
    .overwrite_output()
    .run()
)
```

For large graphs, write them to a UTF-8 file and pass the path with
`filter_complex_script()`. The builder emits `-/filter_complex <path>`, the
ffmpeg 7.0+ syntax for loading an option value from a file, so ffmpeg reads the
graph itself and it never becomes a single argv element. That matters because
Linux caps one argument at 128 KiB (`MAX_ARG_STRLEN`): an inlined `-filter_complex`
of that size fails with `E2BIG` before ffmpeg even starts. It is mutually
exclusive with `filter_complex()` at runtime, so use one or the other:

```python
ffmpeg.input("input.mkv").filter_complex_script("graph.txt").output("output.mp4").run()
# ffmpeg -/filter_complex graph.txt -i input.mkv output.mp4
```

For ffmpeg 6.1 and older pass `legacy=True` to emit `-filter_complex_script <path>`
instead. ffmpeg 7.x and 8.x still accept that option with a deprecation warning;
ffmpeg 9.0 removed it, so keep the default there.

```python
ffmpeg.input("input.mkv").filter_complex_script("graph.txt", legacy=True).output("output.mp4").run()
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
tuple instead of raising on bad media. It raises `FFmpegError` only when the
ffprobe executable itself could not be run, or `FFmpegTimeoutError` when a
`timeout=` expires (see [Timeouts](#timeouts)).

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
ffmpeg.input("input.mkv").hwaccel("cuda").output("output.mp4").codec("v", video_codec).overwrite_output().run()
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

## Timeouts

`run()`, `probe()` and `validate()` accept a keyword-only `timeout` in seconds;
`None`, the default, means no limit. When it expires the child is killed,
reaped, and `FFmpegTimeoutError` is raised:

```python
try:
    ffmpeg.input("input.mkv").output("output.mp4", c="copy").overwrite_output().run(timeout=60)
except ffmpeg.FFmpegTimeoutError as e:
    print(f"gave up after {e.timeout}s: {e.stderr}")

result = ffmpeg.probe("video.mkv", timeout=10)
ok, stderr = ffmpeg.validate("video.mkv", timeout=10)
```

`FFmpegTimeoutError` subclasses `FFmpegError`, so `except FFmpegError` keeps
catching it. It carries `timeout` (the limit that expired), `cmd`, and
`stderr`: the tail collected before the kill, decoded the same way the failure
path decodes it, so it is `str | None` in both text modes. `returncode` is
always `None`, because the run did not complete within the limit and a kill
status is not an ffmpeg exit code. A `timeout` that is not a positive finite number, or one above 2147483.647 seconds (the largest
value the subprocess timers accept on every platform), raises `ValueError` before any process starts.

The kill applies to the process launched from `ffmpeg_path` (or
`ffprobe_path`); descendants of a wrapper script are not killed. Without
`capture_stderr=True` the limit also bounds the whole call rather than just
ffmpeg's lifetime: the wrapper keeps draining ffmpeg's stdout and stderr until
they close, so a background process that a wrapper script leaves holding either
pipe turns into `FFmpegTimeoutError` once the deadline, plus a one-second grace
for the last buffered output, has passed, even though ffmpeg itself exited and
nothing was killed. After a kill the wrapper waits up to five seconds for the
forwarding threads before raising.

!!! note "The timeout bounds ffmpeg, not your stderr sink"
    Without `capture_stderr=True`, ffmpeg's stderr is forwarded live to the
    inherited `sys.stderr` with a synchronous write. If that sink itself blocks
    (a full pipe nobody reads, a stopped terminal), the timeout cannot
    interrupt the write in progress: after the kill the wrapper waits a bounded
    time for the forwarding thread and raises regardless, but the blocked write
    is not unblocked. Callers with a slow or blocking stderr sink should pass
    `capture_stderr=True`, which sends stderr to a buffer instead.
