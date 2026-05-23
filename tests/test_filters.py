r"""Tests for the ``filter_arg_escape`` filtergraph escaping utility."""

from ffmpeg_wrap import filter_arg_escape


class TestFilterArgEscape:
    def test_plain_path_only_wrapped(self):
        # No special characters: just single-quote wrapping.
        assert filter_arg_escape("/videos/clip.srt") == "'/videos/clip.srt'"

    def test_windows_drive_path(self):
        # The drive-letter colon must be escaped for the filter-option parser
        # (otherwise ffmpeg splits the path there) and backslashes doubled.
        assert filter_arg_escape(r"C:\videos\clip.srt") == r"'C\:\\videos\\clip.srt'"

    def test_colon_in_filename(self):
        # A bare colon would be read as an option separator, so it is escaped.
        assert filter_arg_escape("/tmp/12:30 show.srt") == r"'/tmp/12\:30 show.srt'"

    def test_embedded_single_quote(self):
        # ' is special at both levels: backslash-escaped for the option parser
        # (\') and emitted via the close/escape/reopen idiom ('\'') for the
        # filtergraph parser.
        assert filter_arg_escape("it's a clip.srt") == "'it\\'\\''s a clip.srt'"

    def test_backslash_and_colon_escaped(self):
        # A literal backslash is doubled and the colon escaped for the
        # filter-option parser.
        assert filter_arg_escape("a\\b:c") == r"'a\\b\:c'"

    def test_roundtrips_into_subtitles_filter(self):
        path = r"C:\subs\ru.srt"
        graph = f"subtitles={filter_arg_escape(path)}"
        assert graph == r"subtitles='C\:\\subs\\ru.srt'"
