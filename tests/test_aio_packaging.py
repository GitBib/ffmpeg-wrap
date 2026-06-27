"""Task 1 packaging tests for the optional ``[async]`` extra.

Verify that the ``ffmpeg_wrap.aio`` submodule is importable when ``anyio`` is
installed. The zero-dependency-core invariant (``import ffmpeg_wrap`` must not
pull in ``anyio`` until ``aio`` is accessed) is asserted by the richer
``test_core_import_does_not_import_anyio`` in ``test_aio_highload.py``.
"""

from __future__ import annotations

import pytest


def test_aio_submodule_importable() -> None:
    pytest.importorskip("anyio")
    import ffmpeg_wrap.aio as aio

    assert aio is not None
