from __future__ import annotations


def filter_arg_escape(value: str) -> str:
    r"""Escape a value for safe embedding in a filtergraph option.

    ffmpeg parses a filtergraph at two levels, and a value pasted into a filter
    option (such as a ``subtitles=`` path) must survive both:

    * The *filter-option* parser, where ``:`` separates options and ``\``/``'``
      are escaping/quoting characters. A raw colon (every Windows drive path,
      and POSIX paths containing one) is otherwise read as an option separator,
      so ffmpeg mis-parses the tail (e.g. ``original_size``). This level is
      handled by backslash-escaping ``\``, ``'`` and ``:``.
    * The *filtergraph* parser, where ``'`` quotes and ``[],;`` are special.
      This level is handled by wrapping the whole value in single quotes; a
      literal ``'`` is emitted via the close/escape/reopen idiom (``'\''``).

    Single-quote wrapping alone is **not** sufficient: the colon still reaches
    the filter-option parser and splits the value, so the backslash escaping is
    required as well. The result parses back to the original ``value``.

    Typical use is building a ``subtitles=`` (or any path-bearing) filter::

        graph = f"subtitles={filter_arg_escape(path)}"

    Args:
        value: The raw string to escape (e.g. a filesystem path).

    Returns:
        The escaped, single-quote-wrapped token.
    """
    # Filter-option level: escape backslash first, then quote and colon.
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    # Filtergraph level: single-quote wrap, emitting any embedded ' literally.
    return "'" + escaped.replace("'", "'\\''") + "'"
