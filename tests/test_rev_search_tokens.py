"""Tests for rev_search tokenization."""

import pytest

from intersphinx_registry.rev_search import (
    Added,
    ReplacementContext,
    ReplacementInfo,
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
            ReplacementInfo(
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
            ReplacementInfo(
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
            ReplacementInfo(
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
            ReplacementInfo(
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
    result = _compute_replacement(original, lookup_result)

    # Compare context_old (normalized)
    ctx_before_old, target_tokens_old, ctx_after_old = result.context_old
    exp_ctx_before_old, exp_target_tokens_old, exp_ctx_after_old = expected.context_old

    assert normalise_token_stream(ctx_before_old) == normalise_token_stream(
        exp_ctx_before_old
    ), f"context_before mismatch"

    assert normalise_token_stream(target_tokens_old) == normalise_token_stream(
        exp_target_tokens_old
    ), f"target_line (old) mismatch"

    assert normalise_token_stream(ctx_after_old) == normalise_token_stream(
        exp_ctx_after_old
    ), f"context_after mismatch"

    # Compare context_new (normalized)
    ctx_before_new, target_tokens_new, ctx_after_new = result.context_new
    exp_ctx_before_new, exp_target_tokens_new, exp_ctx_after_new = expected.context_new

    assert normalise_token_stream(ctx_before_new) == normalise_token_stream(
        exp_ctx_before_new
    ), f"context_before (new) mismatch"

    assert normalise_token_stream(target_tokens_new) == normalise_token_stream(
        exp_target_tokens_new
    ), f"target_line (new) mismatch"

    assert normalise_token_stream(ctx_after_new) == normalise_token_stream(
        exp_ctx_after_new
    ), f"context_after (new) mismatch"
