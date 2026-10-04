---
worth: yes
where: tests/test_aio_timeout.py:121
added: 2026-10-04
---
# detached-grandchild tee test asserts exit before the grandchild has exited

`test_tee_path_without_timeout_waits_for_detached_grandchild` checks `not fake_child.alive()` right after
`aio.run()` returns. The run returns on pipe EOF, but the detached grandchild closes stdout during
interpreter shutdown and exits about 12 ms later, so the assertion races. It fails about one run in four in
a `python:3.13-slim-trixie` container on unmodified master, with static ffmpeg 8.1.1 and 9.0.2 alike, and
passes on macOS and in CI. Surfaced while verifying PR #7. Fix: poll `alive()` with a short deadline
instead of asserting once.
