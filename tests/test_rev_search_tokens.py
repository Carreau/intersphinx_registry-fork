"""Tests for rev_search tokenization."""

import pytest

from intersphinx_registry.rev_search import (
    Added,
    ReplacementContext,
    Removed,
    Unchanged,
    _compute_replacement,
    normalise_token_stream,
)
from intersphinx_registry.reverse_lookup import ReverseLookupResult


@pytest.mark.parametrize(
    "original,lookup_result,expected",
    [
        # Test case 1: Full link on its own line (no prefix in line, prefix in context_before)
        (
            ReplacementContext(
                "For more details, see the",
                "`setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__",
                "",
            ),
            ReverseLookupResult(
                "https://setuptools.pypa.io/en/latest/setuptools.html",
                "setuptools",
                "std:doc",
                "setuptools",
                None,
                "https://setuptools.pypa.io/en/latest/setuptools.html",
            ),
            (
                (
                    (Unchanged("For more details, see the"),),
                    (
                        Unchanged("`setuptools documentation <"),
                        Removed("https://setuptools.pypa.io/en/latest/setuptools.html"),
                        Unchanged(">`"),
                        Removed("__"),
                    ),
                    (Unchanged(""),),
                ),
                (
                    (Unchanged("For more details, see the"),),
                    (
                        Added(":std:doc:"),
                        Unchanged("`"),
                        Unchanged("setuptools documentation"),
                        Unchanged(" <"),
                        Added("setuptools:setuptools"),
                        Unchanged(">`"),
                    ),
                    (Unchanged(""),),
                ),
            ),
        ),
        # Test case 2: Full link with prefix text in the same line
        (
            ReplacementContext(
                "",
                "For more details, see the `setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__",
                "",
            ),
            ReverseLookupResult(
                "https://setuptools.pypa.io/en/latest/setuptools.html",
                "setuptools",
                "std:doc",
                "setuptools",
                None,
                "https://setuptools.pypa.io/en/latest/setuptools.html",
            ),
            (
                (
                    (Unchanged(""),),
                    (
                        Unchanged("For more details, see the "),
                        Unchanged("`setuptools documentation <"),
                        Removed("https://setuptools.pypa.io/en/latest/setuptools.html"),
                        Unchanged(">`"),
                        Removed("__"),
                    ),
                    (Unchanged(""),),
                ),
                (
                    (Unchanged(""),),
                    (
                        Unchanged("For more details, see the "),
                        Added(":std:doc:"),
                        Unchanged("`"),
                        Unchanged("setuptools documentation"),
                        Unchanged(" <"),
                        Added("setuptools:setuptools"),
                        Unchanged(">`"),
                    ),
                    (Unchanged(""),),
                ),
            ),
        ),
        # Test case 3: Full link with suffix text
        (
            ReplacementContext(
                "",
                "`setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__ for details",
                "",
            ),
            ReverseLookupResult(
                "https://setuptools.pypa.io/en/latest/setuptools.html",
                "setuptools",
                "std:doc",
                "setuptools",
                None,
                "https://setuptools.pypa.io/en/latest/setuptools.html",
            ),
            (
                (
                    (Unchanged(""),),
                    (
                        Unchanged("`setuptools documentation <"),
                        Removed("https://setuptools.pypa.io/en/latest/setuptools.html"),
                        Unchanged(">`"),
                        Removed("__"),
                        Unchanged(" for details"),
                    ),
                    (Unchanged(""),),
                ),
                (
                    (Unchanged(""),),
                    (
                        Added(":std:doc:"),
                        Unchanged("`"),
                        Unchanged("setuptools documentation"),
                        Unchanged(" <"),
                        Added("setuptools:setuptools"),
                        Unchanged(">`"),
                        Unchanged(" for details"),
                    ),
                    (Unchanged(""),),
                ),
            ),
        ),
        # Test case 4: Full link with both prefix and suffix
        (
            ReplacementContext(
                "",
                "See `setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__ for details",
                "",
            ),
            ReverseLookupResult(
                "https://setuptools.pypa.io/en/latest/setuptools.html",
                "setuptools",
                "std:doc",
                "setuptools",
                None,
                "https://setuptools.pypa.io/en/latest/setuptools.html",
            ),
            (
                (
                    (Unchanged(""),),
                    (
                        Unchanged("See "),
                        Unchanged("`setuptools documentation <"),
                        Removed("https://setuptools.pypa.io/en/latest/setuptools.html"),
                        Unchanged(">`"),
                        Removed("__"),
                        Unchanged(" for details"),
                    ),
                    (Unchanged(""),),
                ),
                (
                    (Unchanged(""),),
                    (
                        Unchanged("See "),
                        Added(":std:doc:"),
                        Unchanged("`setuptools documentation <"),
                        Added("setuptools:setuptools"),
                        Unchanged(">`"),
                        Unchanged(" for details"),
                    ),
                    (Unchanged(""),),
                ),
            ),
        ),
    ],
    ids=[
        "link_only_line",
        "link_with_prefix_in_line",
        "link_with_suffix",
        "link_with_prefix_and_suffix",
    ],
)
def test_full_link_tokenization(original, lookup_result, expected):
    """Test that full link replacement tokenizes correctly."""
    context_old, context_new = _compute_replacement(original, lookup_result)
    expected_old, expected_new = expected

    assert context_old == expected_old, "context_old mismatch"
    assert context_new == expected_new, "context_new mismatch"
