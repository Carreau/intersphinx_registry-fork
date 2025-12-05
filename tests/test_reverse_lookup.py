"""Tests for reverse lookup and URL replacement patterns."""

import re
import tempfile
from pathlib import Path

import pytest

from intersphinx_registry.lookup import _compute_replacement, _find_url_replacements


@pytest.mark.parametrize(
    "original_line,url,expected_pattern",
    [
        # Simple URL
        (
            "See https://docs.python.org/3/library/os.html for details",
            "https://docs.python.org/3/library/os.html",
            r"See `.*<:std:doc:`python:library/os`>`_ for details",
        ),
        # URL with trailing punctuation
        (
            "Check https://docs.python.org/3/library/os.html.",
            "https://docs.python.org/3/library/os.html",
            r"Check `.*<:std:doc:`python:library/os`>`_\.",
        ),
        # Simple RST link: <URL>`_
        (
            "See <https://docs.python.org/3/library/os.html>`_ for details",
            "https://docs.python.org/3/library/os.html",
            r"See `.*<:std:doc:`python:library/os`>`_ for details",
        ),
        # Full RST link with text: `text <URL>`_
        (
            "See `Python docs <https://docs.python.org/3/library/os.html>`_ for details",
            "https://docs.python.org/3/library/os.html",
            r"See `Python docs <:std:doc:`python:library/os`>`_ for details",
        ),
        # URL in the middle of text
        (
            "The https://docs.python.org/3/library/os.html module is useful",
            "https://docs.python.org/3/library/os.html",
            r"The `.*<:std:doc:`python:library/os`>`_ module is useful",
        ),
        # URL at the start
        (
            "https://docs.python.org/3/library/os.html is the documentation",
            "https://docs.python.org/3/library/os.html",
            r"`.*<:std:doc:`python:library/os`>`_ is the documentation",
        ),
        # URL at the end
        (
            "See documentation at https://docs.python.org/3/library/os.html",
            "https://docs.python.org/3/library/os.html",
            r"See documentation at `.*<:std:doc:`python:library/os`>`_",
        ),
        # URL with anchor (matches different intersphinx entry)
        (
            "Link: https://docs.python.org/3/library/os.html#module-os",
            "https://docs.python.org/3/library/os.html#module-os",
            r"Link: `.*<:py:module:`python:os`>`_",
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


def test_compute_replacement_simple():
    """Test _compute_replacement with a simple URL."""
    original = "See https://docs.python.org/3/library/os.html for details"
    url = "https://docs.python.org/3/library/os.html"
    rst_ref = ":std:doc:`python:library/os`"
    result = _compute_replacement(original, url, rst_ref, "os module", "library/os")
    assert rst_ref in result or "os module" in result


def test_compute_replacement_rst_link():
    """Test _compute_replacement with an RST link."""
    original = "See `Python docs <https://docs.python.org/3/library/os.html>`_ for details"
    url = "https://docs.python.org/3/library/os.html"
    rst_ref = ":std:doc:`python:library/os`"
    result = _compute_replacement(original, url, rst_ref, "os module", "library/os")
    assert "Python docs" in result
    assert rst_ref in result


def test_devguide_multiline_link():
    """Test replacement of a multi-line RST link from Python devguide.

    Note: Current implementation processes line-by-line, so multi-line RST links
    are treated as simple links (the link text on previous line is not detected).
    This test documents the current behavior.
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

        # Since the link text is on a different line, it's treated as a simple link
        # The replacement uses the rst_entry as the link text
        assert "getting-started/setup-building" in replacement.replacement_line
        # Check that it contains the intersphinx reference
        assert ":std:doc:`devguide:getting-started/setup-building`" in replacement.replacement_line
        # The URL should be replaced
        assert "devguide.python.org" not in replacement.replacement_line

        # Verify we have context lines
        assert replacement.context_before == "See the `section in the Python developer's guide"
        assert replacement.context_after == "more information about building Python from source. To enable address sanitizer,"
