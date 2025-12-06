"""Tests for reverse lookup and URL replacement patterns."""

import pytest

from intersphinx_registry.lookup import (
    ReplacementContext,
    ReverseLookupResult,
    _compute_replacement,
)


@pytest.mark.parametrize(
    "original,lookup_result,expected",
    [
        # Simple URL in text - replace with :domain:role:`package:entry`
        (
            ReplacementContext(None, "See https://docs.python.org/3/library/os.html for details", None),
            ReverseLookupResult("https://docs.python.org/3/library/os.html", "python", "std:doc", "library/os", None),
            ReplacementContext(None, "See :std:doc:`python:library/os` for details", None),
        ),
        # URL with trailing punctuation
        (
            ReplacementContext(None, "Check https://docs.python.org/3/library/os.html.", None),
            ReverseLookupResult("https://docs.python.org/3/library/os.html", "python", "std:doc", "library/os", None),
            ReplacementContext(None, "Check :std:doc:`python:library/os`.", None),
        ),
        # Full RST link with custom text - preserve the custom text
        # Format: :domain:role:`custom text <package:entry>`
        (
            ReplacementContext(None, "See `Python os module documentation <https://docs.python.org/3/library/os.html>`_ for details", None),
            ReverseLookupResult("https://docs.python.org/3/library/os.html", "python", "std:doc", "library/os", None),
            ReplacementContext(None, "See :std:doc:`Python os module documentation <python:library/os>` for details", None),
        ),
        # Simple RST link `<URL>`_ - replace with :domain:role:`package:entry`
        (
            ReplacementContext(None, "See `<https://docs.python.org/3/library/os.html>`_ for details", None),
            ReverseLookupResult("https://docs.python.org/3/library/os.html", "python", "std:doc", "library/os", None),
            ReplacementContext(None, "See :std:doc:`python:library/os` for details", None),
        ),
        # URL at start of line
        (
            ReplacementContext(None, "https://docs.python.org/3/library/os.html is the documentation", None),
            ReverseLookupResult("https://docs.python.org/3/library/os.html", "python", "std:doc", "library/os", None),
            ReplacementContext(None, ":std:doc:`python:library/os` is the documentation", None),
        ),
        # URL at end of line
        (
            ReplacementContext(None, "See documentation at https://docs.python.org/3/library/os.html", None),
            ReverseLookupResult("https://docs.python.org/3/library/os.html", "python", "std:doc", "library/os", None),
            ReplacementContext(None, "See documentation at :std:doc:`python:library/os`", None),
        ),
        # URL in the middle of text
        (
            ReplacementContext(None, "The https://docs.python.org/3/library/os.html module is useful", None),
            ReverseLookupResult("https://docs.python.org/3/library/os.html", "python", "std:doc", "library/os", None),
            ReplacementContext(None, "The :std:doc:`python:library/os` module is useful", None),
        ),
        # URL with anchor (matches different intersphinx entry)
        (
            ReplacementContext(None, "Link: https://docs.python.org/3/library/os.html#module-os", None),
            ReverseLookupResult("https://docs.python.org/3/library/os.html#module-os", "python", "py:module", "os", None),
            ReplacementContext(None, "Link: :py:module:`python:os`", None),
        ),
        # Multi-line RST link - both lines are modified
        # Original context_before: See the `section in the Python developer's guide
        # Original line: <https://devguide.python.org/getting-started/setup-building/>`_ on this topic for
        # New context_before: See the :std:doc:`section in the Python developer's guide
        # New line: <devguide:getting-started/setup-building>` on this topic for
        (
            ReplacementContext(
                "See the `section in the Python developer's guide",
                "<https://devguide.python.org/getting-started/setup-building/>`_ on this topic for",
                None,
            ),
            ReverseLookupResult("https://devguide.python.org/getting-started/setup-building/", "devguide", "std:doc", "getting-started/setup-building", None),
            ReplacementContext(
                "See the :std:doc:`section in the Python developer's guide",
                "<devguide:getting-started/setup-building>` on this topic for",
                None,
            ),
        ),
        # Another multi-line RST link example
        (
            ReplacementContext(
                "Ideally you should run ``pytest-run-parallel`` using a `free-threaded build of Python",
                "<https://docs.python.org/3/howto/free-threading-python.html>`_ that is 3.14 or",
                None,
            ),
            ReverseLookupResult("https://docs.python.org/3/howto/free-threading-python.html", "python", "std:doc", "howto/free-threading-python", None),
            ReplacementContext(
                "Ideally you should run ``pytest-run-parallel`` using a :std:doc:`free-threaded build of Python",
                "<python:howto/free-threading-python>` that is 3.14 or",
                None,
            ),
        ),
        # Single-line without opening backtick (no context) - replace with :domain:role:`package:entry`
        (
            ReplacementContext(None, "<https://devguide.python.org/getting-started/setup-building/>`_ on this topic for", None),
            ReverseLookupResult("https://devguide.python.org/getting-started/setup-building/", "devguide", "std:doc", "getting-started/setup-building", None),
            ReplacementContext(None, ":std:doc:`devguide:getting-started/setup-building` on this topic for", None),
        ),
        # Anonymous hyperlink with double trailing underscore
        (
            ReplacementContext(None, "See `Write the Docs <https://www.writethedocs.org/>`__ for more information", None),
            ReverseLookupResult("https://www.writethedocs.org/", "writethedocs", "std:doc", "index", None),
            ReplacementContext(None, "See :std:doc:`Write the Docs <writethedocs:index>` for more information", None),
        ),
    ],
    ids=[
        "simple_url",
        "url_with_punctuation",
        "full_rst_link_preserves_text",
        "simple_rst_link",
        "url_at_start",
        "url_at_end",
        "url_in_middle",
        "url_with_anchor",
        "multiline_devguide",
        "multiline_python",
        "single_line_no_backtick",
        "anonymous_hyperlink_double_underscore",
    ],
)
def test_compute_replacement(original, lookup_result, expected):
    """Test _compute_replacement with various URL patterns."""
    result = _compute_replacement(original, lookup_result)
    assert result == expected
