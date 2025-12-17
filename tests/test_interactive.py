"""
Unit tests for interactive mode functionality.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from intersphinx_registry.rev_search import (
    QuitInteractive,
    _getch,
    prompt_replaceable_url,
    prompt_non_replaceable_url,
    open_url_in_browser,
    apply_replacements_to_file,
    UrlReplacement,
    OutputReplacementContext,
    Unchanged,
    Removed,
    Added,
)


class TestGetch:
    """Test the _getch() function for reading single keypresses."""

    @patch("sys.stdin.isatty", return_value=False)
    @patch("sys.stdin.read", return_value="r")
    def test_getch_non_tty(self, mock_read, mock_isatty):
        """Test _getch() in non-TTY environment (fallback mode)."""
        result = _getch()
        assert result == "r"
        mock_read.assert_called_once_with(1)

    @patch("sys.stdin.isatty", return_value=True)
    @patch("sys.stdin.fileno", return_value=0)
    @patch("termios.tcgetattr")
    @patch("tty.setraw")
    @patch("sys.stdin.read", return_value="s")
    @patch("termios.tcsetattr")
    def test_getch_tty(
        self,
        mock_tcsetattr,
        mock_read,
        mock_setraw,
        mock_tcgetattr,
        mock_fileno,
        mock_isatty,
    ):
        """Test _getch() in TTY environment (terminal mode)."""
        mock_tcgetattr.return_value = ["original", "settings"]
        result = _getch()
        assert result == "s"
        mock_setraw.assert_called_once()
        mock_read.assert_called_once_with(1)
        # Ensure terminal settings were restored
        assert mock_tcsetattr.called


class TestPromptFunctions:
    """Test the interactive prompt functions."""

    @patch("intersphinx_registry.rev_search._getch", return_value="r")
    @patch("builtins.print")
    def test_prompt_replaceable_url_replace(self, mock_print, mock_getch):
        """Test prompt_replaceable_url() when user chooses to replace."""
        result = prompt_replaceable_url()
        assert result is True

    @patch("intersphinx_registry.rev_search._getch", return_value="s")
    @patch("builtins.print")
    def test_prompt_replaceable_url_skip(self, mock_print, mock_getch):
        """Test prompt_replaceable_url() when user chooses to skip."""
        result = prompt_replaceable_url()
        assert result is False

    @patch("intersphinx_registry.rev_search._getch", return_value="q")
    @patch("builtins.print")
    def test_prompt_replaceable_url_quit(self, mock_print, mock_getch):
        """Test prompt_replaceable_url() when user chooses to quit."""
        with pytest.raises(QuitInteractive):
            prompt_replaceable_url()

    @patch("intersphinx_registry.rev_search._getch", side_effect=["x", "r"])
    @patch("builtins.print")
    def test_prompt_replaceable_url_invalid_then_valid(self, mock_print, mock_getch):
        """Test prompt_replaceable_url() with invalid key then valid key."""
        result = prompt_replaceable_url()
        assert result is True
        # Should have been called twice (invalid, then valid)
        assert mock_getch.call_count == 2

    @patch("intersphinx_registry.rev_search._getch", return_value="o")
    @patch("builtins.print")
    def test_prompt_non_replaceable_url_open(self, mock_print, mock_getch):
        """Test prompt_non_replaceable_url() when user chooses to open."""
        result = prompt_non_replaceable_url("https://example.com")
        assert result == "open"

    @patch("intersphinx_registry.rev_search._getch", return_value="i")
    @patch("builtins.print")
    def test_prompt_non_replaceable_url_ignore(self, mock_print, mock_getch):
        """Test prompt_non_replaceable_url() when user chooses to ignore."""
        result = prompt_non_replaceable_url("https://example.com")
        assert result == "ignore"


class TestBrowserOpening:
    """Test the browser opening functionality."""

    @patch("webbrowser.open", return_value=True)
    @patch("builtins.print")
    def test_open_url_in_browser_success(self, mock_print, mock_webbrowser):
        """Test open_url_in_browser() with successful open."""
        result = open_url_in_browser("https://example.com")
        assert result is True
        mock_webbrowser.assert_called_once_with("https://example.com")

    @patch("webbrowser.open", side_effect=Exception("Browser not found"))
    @patch("builtins.print")
    def test_open_url_in_browser_failure(self, mock_print, mock_webbrowser):
        """Test open_url_in_browser() when browser opening fails."""
        result = open_url_in_browser("https://example.com")
        assert result is False


class TestFileReplacements:
    """Test the file replacement functionality."""

    def test_apply_replacements_empty_list(self, tmp_path):
        """Test apply_replacements_to_file() with empty replacement list."""
        test_file = tmp_path / "test.rst"
        test_file.write_text("Original content\n")

        count = apply_replacements_to_file(test_file, [])
        assert count == 0
        assert test_file.read_text() == "Original content\n"

    def test_apply_replacements_single_replacement(self, tmp_path):
        """Test apply_replacements_to_file() with a single replacement."""
        test_file = tmp_path / "test.rst"
        test_file.write_text("Line 1\nLine 2 with URL\nLine 3\n")

        # Create a mock replacement
        old_context = (
            (),  # context_before
            (Unchanged("Line 2 "), Removed("with URL")),  # target
            (),  # context_after
        )
        new_context = (
            (),  # context_before
            (Unchanged("Line 2 "), Added("with replacement")),  # target
            (),  # context_after
        )

        replacement = UrlReplacement(
            line_num=2,
            matched_url="https://example.com",
            context_old=old_context,
            context_new=new_context,
            inventory_url=None,
        )

        count = apply_replacements_to_file(test_file, [replacement])
        assert count == 1

        content = test_file.read_text()
        assert "Line 2 with replacement\n" in content
        assert "with URL" not in content

    def test_apply_replacements_multiple_replacements(self, tmp_path):
        """Test apply_replacements_to_file() with multiple replacements."""
        test_file = tmp_path / "test.rst"
        test_file.write_text("Line 1\nLine 2\nLine 3\nLine 4\n")

        # Create two replacements
        replacement1 = UrlReplacement(
            line_num=2,
            matched_url="https://example.com",
            context_old=((), (Unchanged("Line "), Removed("2")), ()),
            context_new=((), (Unchanged("Line "), Added("TWO")), ()),
            inventory_url=None,
        )

        replacement2 = UrlReplacement(
            line_num=4,
            matched_url="https://example.com",
            context_old=((), (Unchanged("Line "), Removed("4")), ()),
            context_new=((), (Unchanged("Line "), Added("FOUR")), ()),
            inventory_url=None,
        )

        count = apply_replacements_to_file(test_file, [replacement1, replacement2])
        assert count == 2

        content = test_file.read_text()
        assert "Line TWO\n" in content
        assert "Line FOUR\n" in content

    def test_apply_replacements_preserves_line_endings(self, tmp_path):
        """Test that apply_replacements_to_file() preserves line endings."""
        test_file = tmp_path / "test.rst"
        test_file.write_text("Line 1\nLine 2\n")

        replacement = UrlReplacement(
            line_num=2,
            matched_url="https://example.com",
            context_old=((), (Removed("Line 2"),), ()),
            context_new=((), (Added("Modified Line 2"),), ()),
            inventory_url=None,
        )

        apply_replacements_to_file(test_file, [replacement])

        content = test_file.read_text()
        assert content == "Line 1\nModified Line 2\n"
        assert content.endswith("\n")


class TestQuitInteractive:
    """Test the QuitInteractive exception."""

    def test_quit_interactive_is_exception(self):
        """Test that QuitInteractive is an Exception subclass."""
        assert issubclass(QuitInteractive, Exception)

    def test_quit_interactive_can_be_raised(self):
        """Test that QuitInteractive can be raised and caught."""
        with pytest.raises(QuitInteractive):
            raise QuitInteractive()


class TestMultiFileQuit:
    """Test that quit exits all files, not just the current one."""

    @patch("intersphinx_registry.rev_search.search_one_file")
    @patch("builtins.print")
    def test_quit_exits_all_files(self, mock_print, mock_search_one_file, tmp_path):
        """Test that pressing 'q' exits processing of all files."""
        from intersphinx_registry.rev_search import rev_search, QuitInteractive

        # Create three test files
        file1 = tmp_path / "file1.rst"
        file2 = tmp_path / "file2.rst"
        file3 = tmp_path / "file3.rst"

        file1.write_text("Content\n")
        file2.write_text("Content\n")
        file3.write_text("Content\n")

        # Make search_one_file raise QuitInteractive on first call
        mock_search_one_file.side_effect = QuitInteractive()

        # Call rev_search on directory with multiple files
        # Should exit after first file when QuitInteractive is raised
        rev_search(str(tmp_path), interactive=True)

        # Verify search_one_file was only called once (for file1)
        # If it processed all files, it would be called 3 times
        assert mock_search_one_file.call_count == 1, (
            "Should stop after first file when quitting"
        )
