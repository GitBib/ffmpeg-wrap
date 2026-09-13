# Async API

An asynchronous mirror of the public API lives under `ffmpeg_wrap.aio`, powered
by [AnyIO](https://anyio.readthedocs.io/). It lets you drive many
ffmpeg/ffprobe jobs concurrently without blocking the event loop. The core
package stays dependency-free — `import ffmpeg_wrap` never imports anyio — so
the async path ships as an optional extra. With uv:

```
uv add "ffmpeg-wrap[async]"
```

With pip:

```
pip install "ffmpeg-wrap[async]"
```

See the auto-generated [Async API Reference](reference/async.md) for full
signatures.

## Running a command

The fluent builder gains an `arun()` method that mirrors `run()`. Start the
chain at `input()` exactly as in the sync API, then `await` the result:

```python
import anyio

from ffmpeg_wrap import input


async def main():
    await input("input.mkv").output("output.mp4", c="copy").overwrite_output().arun()


anyio.run(main)
```

## Mirrored module functions

The `aio` submodule mirrors the module-level functions. `aio.probe()` returns
the same typed `ProbeResult` as `probe()`; `aio.validate()` returns the same
`(ok, stderr)` tuple; `aio.encoders()`/`aio.has_encoder()` share the same
per-path cache as their sync twins:

```python
import anyio

from ffmpeg_wrap import aio


async def main():
    result = await aio.probe("video.mkv")
    for stream in result.streams:
        print(stream.codec_name, stream.codec_type)

    ok, stderr = await aio.validate("video.mkv")
    if not ok:
        print(f"Invalid media: {stderr}")

    codec = "h264_nvenc" if await aio.has_encoder("h264_nvenc") else "libx264"
    print(codec)


anyio.run(main)
```

## Backend choice (asyncio or trio)

Because the async path is built on AnyIO, the same library code runs on both the
asyncio and trio backends — the consumer picks the backend. `anyio.run(main)`
uses asyncio by default; pass `backend="trio"` to run on trio instead:

```python
import anyio

from ffmpeg_wrap import aio


async def main():
    await aio.probe("video.mkv")


anyio.run(main, backend="trio")
```

!!! note "Thread behaviour under highload"
    On the asyncio backend, depending on platform and Python version, CPython
    may reap child processes with **one `waitpid` thread per subprocess** (the
    threaded child watcher) — so its reaping-thread count grows with the number
    of concurrently running children; newer CPython (3.14+) can instead use a
    **pidfd-based watcher with no thread**. On the trio backend there is **no
    dedicated child-reaping thread** at all. AnyIO's trio backend does, however,
    use a small **bounded, reusable worker-thread pool** for the blocking
    pipe-FD reads behind `run_process`/`open_process`. That pool scales with the
    number of *concurrently running* ffmpeg processes (threads are reused across
    jobs), not with the total number of jobs you queue — so bounding concurrency
    with a `CapacityLimiter` keeps the thread count flat. Trio avoids a
    per-process reaping thread, but it is not literally zero-thread.

## Timeouts

`arun()`, `aio.run()`, `aio.probe()` and `aio.validate()` accept the same
keyword-only `timeout` (seconds, `None` for no limit) as their sync twins. The
deadline covers the launch itself; when it expires the child is killed and
reaped, then `FFmpegTimeoutError` is raised with the same message, `timeout`,
`cmd`, `stderr` (`str | None`) and `returncode=None` as the sync path:

```python
import anyio

from ffmpeg_wrap import FFmpegTimeoutError, aio, input


async def main():
    try:
        await input("input.mkv").output("output.mp4", c="copy").overwrite_output().arun(timeout=60)
    except FFmpegTimeoutError as e:
        print(f"gave up after {e.timeout}s: {e.stderr}")

    result = await aio.probe("video.mkv", timeout=10)
    ok, stderr = await aio.validate("video.mkv", timeout=10)
    print(len(result.streams), ok)


anyio.run(main)
```

As in the sync API, the kill applies to the process launched from
`ffmpeg_path` (or `ffprobe_path`) and never to descendants of a wrapper script.
Without `capture_stderr=True` the deadline bounds the whole call: the pipes are
drained until they close, so a background process left holding stdout or stderr
turns into `FFmpegTimeoutError` at the deadline even though ffmpeg itself
exited.

On the async paths the stderr tail is only collected where stderr is teed:
`arun()` and `aio.run()` without `capture_stderr=True` carry it, while
`capture_stderr=True`, `aio.probe()` and `aio.validate()` discard the child's
output when the deadline cancels the call, so `e.stderr` is `None` there. The
sync API keeps the tail in every case.

The keyword composes with AnyIO's own cancellation. Wrapping a call in your
own `anyio.fail_after` / `anyio.move_on_after`, or cancelling the task group it
runs in, kills and reaps the child the same way, but the exception you see is
the one your scope produces (`TimeoutError` from `fail_after`, nothing from
`move_on_after`), not `FFmpegTimeoutError`. Use `timeout=` when you want a
structured error (with the ffmpeg stderr tail on the tee path); use your own
scope when a whole batch shares one deadline. Both can be active at once, and the earlier
deadline decides which exception you get.

!!! note "The timeout bounds ffmpeg, not your stderr sink"
    The same limit as the sync API applies. Without `capture_stderr=True`,
    stderr is forwarded to `sys.stderr` with a blocking write on the event-loop
    thread, and neither `timeout=` nor a cancel scope can interrupt a write
    that is stuck on a full or stopped sink. Pass `capture_stderr=True` when
    the sink may block.

## Bounding concurrency

The library does not build a job queue — bound concurrency yourself with an
`anyio.CapacityLimiter` so only N ffmpeg processes run at once, while you queue
as many jobs as you like:

```python
import anyio

from ffmpeg_wrap import input


async def transcode(name, limiter):
    async with limiter:
        await input(f"{name}.mkv").output(f"{name}.mp4", c="copy").overwrite_output().arun()


async def main():
    limiter = anyio.CapacityLimiter(4)  # at most 4 concurrent ffmpeg processes
    names = [f"clip{i}" for i in range(100)]
    async with anyio.create_task_group() as tg:
        for name in names:
            tg.start_soon(transcode, name, limiter)


anyio.run(main)
```
