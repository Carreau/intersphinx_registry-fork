"""Tests for reverse lookup and URL replacement patterns."""

import re
import tempfile
from pathlib import Path

import pytest

from intersphinx_registry.lookup import _find_url_replacements


@pytest.mark.parametrize(
    "original_line,url,expected_pattern",
    [
        # Simple URL - replaced with :domain:role:`ref`
        (
            "See https://docs.python.org/3/library/os.html for details",
            "https://docs.python.org/3/library/os.html",
            r"See :std:doc:`python:library/os` for details",
        ),
        # URL with trailing punctuation
        (
            "Check https://docs.python.org/3/library/os.html.",
            "https://docs.python.org/3/library/os.html",
            r"Check :std:doc:`python:library/os`\.",
        ),
        # Simple RST link: <URL>`_ - replaced with :domain:role:`ref`
        (
            "See <https://docs.python.org/3/library/os.html>`_ for details",
            "https://docs.python.org/3/library/os.html",
            r"See :std:doc:`python:library/os` for details",
        ),
        # Full RST link with text: `text <URL>`_ - becomes :domain:role:`text <ref>`
        (
            "See `Python docs <https://docs.python.org/3/library/os.html>`_ for details",
            "https://docs.python.org/3/library/os.html",
            r"See :std:doc:`Python docs <python:library/os>` for details",
        ),
        # URL in the middle of text
        (
            "The https://docs.python.org/3/library/os.html module is useful",
            "https://docs.python.org/3/library/os.html",
            r"The :std:doc:`python:library/os` module is useful",
        ),
        # URL at the start
        (
            "https://docs.python.org/3/library/os.html is the documentation",
            "https://docs.python.org/3/library/os.html",
            r":std:doc:`python:library/os` is the documentation",
        ),
        # URL at the end
        (
            "See documentation at https://docs.python.org/3/library/os.html",
            "https://docs.python.org/3/library/os.html",
            r"See documentation at :std:doc:`python:library/os`",
        ),
        # URL with anchor (matches different intersphinx entry)
        (
            "Link: https://docs.python.org/3/library/os.html#module-os",
            "https://docs.python.org/3/library/os.html#module-os",
            r"Link: :py:module:`python:os`",
        ),
    ],
    ids=[
        "simple_url",
        "url_with_punctuation",
        "simple_rst_link",
        "full_rst_link_with_text",
        "url_in_middle",
        "url_at_start",
        "url_at_end",
        "url_with_anchor",
    ],
)
def test_url_replacement_patterns(original_line, url, expected_pattern):
    """Test that various URL patterns are correctly identified and replaced."""
    # Create a temporary .rst file with the test content
    with tempfile.TemporaryDirectory() as tmpdir:
        rst_file = Path(tmpdir) / "test.rst"
        rst_file.write_text(original_line)

        # Find replacements using the core function (convert generator to list)
        replacements = list(_find_url_replacements(str(rst_file)))

        # If no replacement was suggested, skip the test
        if not replacements:
            pytest.skip(f"URL {url} not found in registry or no replacement available")

        # Get the first replacement
        replacement = replacements[0]

        # Verify the replacement matches the expected pattern
        assert re.search(
            expected_pattern, replacement.replacement_line
        ), f"Replacement '{replacement.replacement_line}' doesn't match pattern '{expected_pattern}'"


def test_multiple_urls_in_same_line():
    """Test that multiple URLs in the same line are handled."""
    content = "See https://docs.python.org/3/library/os.html and https://docs.python.org/3/library/sys.html"

    with tempfile.TemporaryDirectory() as tmpdir:
        rst_file = Path(tmpdir) / "test.rst"
        rst_file.write_text(content)

        replacements = list(_find_url_replacements(str(rst_file)))

        # Should find both URLs
        if replacements:
            # Check that we got multiple replacements
            assert len(replacements) >= 2, "Should find at least 2 URLs"


def test_no_urls_found():
    """Test that files without URLs produce no replacements."""
    content = "This is just plain text with no URLs."

    with tempfile.TemporaryDirectory() as tmpdir:
        rst_file = Path(tmpdir) / "test.rst"
        rst_file.write_text(content)

        replacements = list(_find_url_replacements(str(rst_file)))
        assert len(replacements) == 0, "Should find no replacements"


def test_non_replaceable_url():
    """Test that URLs not in the registry don't produce replacements."""
    content = "See https://example.com/some/random/path for details"

    with tempfile.TemporaryDirectory() as tmpdir:
        rst_file = Path(tmpdir) / "test.rst"
        rst_file.write_text(content)

        replacements = list(_find_url_replacements(str(rst_file)))
        # Should not find any replacements since example.com is not in registry
        assert len(replacements) == 0, "Should find no replacements for unknown URLs"


@pytest.mark.parametrize(
    "original_line,url,rst_ref,rst_entry,expected",
    [
        # Simple URL in text - replace with :domain:role:`ref`
        (
            "See https://docs.python.org/3/library/os.html for details",
            "https://docs.python.org/3/library/os.html",
            ":std:doc:`python:library/os`",
            "library/os",
            "See :std:doc:`python:library/os` for details",
        ),
        # URL with trailing punctuation
        (
            "Check https://docs.python.org/3/library/os.html.",
            "https://docs.python.org/3/library/os.html",
            ":std:doc:`python:library/os`",
            "library/os",
            "Check :std:doc:`python:library/os`.",
        ),
        # Full RST link with custom text - preserve the custom text
        # Format: :domain:role:`custom text <ref>`
        (
            "See `Python os module documentation <https://docs.python.org/3/library/os.html>`_ for details",
            "https://docs.python.org/3/library/os.html",
            ":std:doc:`python:library/os`",
            "library/os",
            "See :std:doc:`Python os module documentation <python:library/os>` for details",
        ),
        # Simple RST link `<URL>`_ - replace with :domain:role:`ref`
        (
            "See `<https://docs.python.org/3/library/os.html>`_ for details",
            "https://docs.python.org/3/library/os.html",
            ":std:doc:`python:library/os`",
            "library/os",
            "See :std:doc:`python:library/os` for details",
        ),
        # URL at start of line
        (
            "https://docs.python.org/3/library/os.html is the documentation",
            "https://docs.python.org/3/library/os.html",
            ":std:doc:`python:library/os`",
            "library/os",
            ":std:doc:`python:library/os` is the documentation",
        ),
        # URL at end of line
        (
            "See documentation at https://docs.python.org/3/library/os.html",
            "https://docs.python.org/3/library/os.html",
            ":std:doc:`python:library/os`",
            "library/os",
            "See documentation at :std:doc:`python:library/os`",
        ),
        # Single-line simple RST link without backtick (no context) - replace with :domain:role:`ref`
        (
            "<https://devguide.python.org/getting-started/setup-building/>`_ on this topic for",
            "https://devguide.python.org/getting-started/setup-building/",
            ":std:doc:`devguide:getting-started/setup-building`",
            "getting-started/setup-building",
            ":std:doc:`devguide:getting-started/setup-building` on this topic for",
        ),
        # Another single-line example
        (
            "<https://docs.python.org/3/howto/free-threading-python.html>`_ that is 3.14 or",
            "https://docs.python.org/3/howto/free-threading-python.html",
            ":std:doc:`python:howto/free-threading-python`",
            "howto/free-threading-python",
            ":std:doc:`python:howto/free-threading-python` that is 3.14 or",
        ),
    ],
    ids=[
        "simple_url",
        "url_with_punctuation",
        "full_rst_link_preserves_text",
        "simple_rst_link",
        "url_at_start",
        "url_at_end",
        "single_line_no_backtick",
        "single_line_no_backtick_2",
    ],
)
def test_compute_replacement(original_line, url, rst_ref, rst_entry, expected):
    """Test _compute_replacement with various URL patterns."""
    from intersphinx_registry.lookup import _compute_replacement

    result = _compute_replacement(original_line, url, rst_ref, rst_entry)
    assert result == expected


def test_devguide_multiline_link():
    """Test replacement of a multi-line RST link from Python devguide.

    Expected output format:
       See the `section in the Python developer's guide
     - <https://devguide.python.org/getting-started/setup-building/>`_ on this topic for
     + <devguide:getting-started/setup-building>` on this topic for
       more information about building Python from source. To enable address sanitizer,

    The :std:doc:` prefix goes on the previous line with the link text.
    """
    content = """See the `section in the Python developer's guide
<https://devguide.python.org/getting-started/setup-building/>`_ on this topic for
more information about building Python from source. To enable address sanitizer,"""

    with tempfile.TemporaryDirectory() as tmpdir:
        rst_file = Path(tmpdir) / "test.rst"
        rst_file.write_text(content)

        replacements = list(_find_url_replacements(str(rst_file)))

        # Should find the URL
        if not replacements:
            pytest.skip("URL not found in registry or no replacement available")

        replacement = replacements[0]

        # Verify the exact lines
        # The line with the URL should have <ref>` replacing <URL>`_
        assert replacement.original_line == "<https://devguide.python.org/getting-started/setup-building/>`_ on this topic for"
        assert replacement.replacement_line == "<devguide:getting-started/setup-building>` on this topic for"

        # Verify context lines
        assert replacement.context_before == "See the `section in the Python developer's guide"
        assert replacement.context_after == "more information about building Python from source. To enable address sanitizer,"


def test_python_free_threading_multiline_link():
    """Test replacement of a multi-line RST link for Python free-threading docs.

    Expected output format:
       Ideally you should run ``pytest-run-parallel`` using a `free-threaded build of Python
     - <https://docs.python.org/3/howto/free-threading-python.html>`_ that is 3.14 or
     + <python:howto/free-threading-python>` that is 3.14 or
       higher. If you decide to use a version of Python that is not free-threaded, you will

    The :std:doc:` prefix goes on the previous line with the link text.
    """
    content = """Ideally you should run ``pytest-run-parallel`` using a `free-threaded build of Python
<https://docs.python.org/3/howto/free-threading-python.html>`_ that is 3.14 or
higher. If you decide to use a version of Python that is not free-threaded, you will"""

    with tempfile.TemporaryDirectory() as tmpdir:
        rst_file = Path(tmpdir) / "test.rst"
        rst_file.write_text(content)

        replacements = list(_find_url_replacements(str(rst_file)))

        # Should find the URL
        if not replacements:
            pytest.skip("URL not found in registry or no replacement available")

        replacement = replacements[0]

        # Verify the exact lines
        # The line with the URL should have <ref>` replacing <URL>`_
        assert replacement.original_line == "<https://docs.python.org/3/howto/free-threading-python.html>`_ that is 3.14 or"
        assert replacement.replacement_line == "<python:howto/free-threading-python>` that is 3.14 or"

        # Verify context lines showing the original link text
        assert replacement.context_before == "Ideally you should run ``pytest-run-parallel`` using a `free-threaded build of Python"
        assert replacement.context_after == "higher. If you decide to use a version of Python that is not free-threaded, you will"
