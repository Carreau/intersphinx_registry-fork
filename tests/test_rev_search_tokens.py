"""Tests for rev_search tokenization."""

import pytest

from intersphinx_registry.rev_search import (
    Added,
    ReplacementContext,
    ReplacementInfo,
    Removed,
    Unchanged,
    _compute_replacement,
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
                        Removed("`setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__"),
                    ),
                    (Unchanged(""),),
                ),
                    (
                        (Unchanged("For more details, see the"),),
                        (
                            Added(":std:doc:`"),
                            Unchanged("setuptools documentation"),
                            Added(" <setuptools:setuptools>`"),
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
                        Removed("`setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__"),
                    ),
                    (Unchanged(""),),
                ),
                    (
                        (Unchanged(""),),
                        (
                            Unchanged("For more details, see the "),
                            Added(":std:doc:`"),
                            Unchanged("setuptools documentation"),
                            Added(" <setuptools:setuptools>`"),
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
                        Removed("`setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__"),
                        Unchanged(" for details"),
                    ),
                    (Unchanged(""),),
                ),
                    (
                        (Unchanged(""),),
                        (
                            Added(":std:doc:`"),
                            Unchanged("setuptools documentation"),
                            Added(" <setuptools:setuptools>`"),
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
                        Removed("`setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__"),
                        Unchanged(" for details"),
                    ),
                    (Unchanged(""),),
                ),
                    (
                        (Unchanged(""),),
                        (
                            Unchanged("See "),
                            Added(":std:doc:`"),
                            Unchanged("setuptools documentation"),
                            Added(" <setuptools:setuptools>`"),
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

    # Compare context_old
    ctx_before_old, target_tokens_old, ctx_after_old = result.context_old
    exp_ctx_before_old, exp_target_tokens_old, exp_ctx_after_old = expected.context_old

    # Compare context_before tokens
    assert len(ctx_before_old) == len(exp_ctx_before_old), (
        f"context_before: Expected {len(exp_ctx_before_old)} tokens, got {len(ctx_before_old)}\n"
        f"Expected: {exp_ctx_before_old}\n"
        f"Got: {ctx_before_old}"
    )
    for i, (actual, exp) in enumerate(zip(ctx_before_old, exp_ctx_before_old)):
        assert type(actual) == type(exp), (
            f"context_before token {i}: Expected {type(exp).__name__}, got {type(actual).__name__}"
        )
        assert str(actual) == str(exp), (
            f"context_before token {i}: Expected '{exp}', got '{actual}'"
        )

    # Compare target_line tokens
    assert len(target_tokens_old) == len(exp_target_tokens_old), (
        f"target_line: Expected {len(exp_target_tokens_old)} tokens, got {len(target_tokens_old)}\n"
        f"Expected: {exp_target_tokens_old}\n"
        f"Got: {target_tokens_old}"
    )
    for i, (actual, exp) in enumerate(zip(target_tokens_old, exp_target_tokens_old)):
        assert type(actual) == type(exp), (
            f"target_line token {i}: Expected {type(exp).__name__}, got {type(actual).__name__}\n"
            f"Expected: {exp}\n"
            f"Got: {actual}"
        )
        assert str(actual) == str(exp), (
            f"target_line token {i}: Expected '{exp}', got '{actual}'"
        )

    # Compare context_after tokens
    assert len(ctx_after_old) == len(exp_ctx_after_old), (
        f"context_after: Expected {len(exp_ctx_after_old)} tokens, got {len(ctx_after_old)}\n"
        f"Expected: {exp_ctx_after_old}\n"
        f"Got: {ctx_after_old}"
    )
    for i, (actual, exp) in enumerate(zip(ctx_after_old, exp_ctx_after_old)):
        assert type(actual) == type(exp), (
            f"context_after token {i}: Expected {type(exp).__name__}, got {type(actual).__name__}"
        )
        assert str(actual) == str(exp), (
            f"context_after token {i}: Expected '{exp}', got '{actual}'"
        )

    # Compare context_new
    ctx_before_new, target_tokens_new, ctx_after_new = result.context_new
    exp_ctx_before_new, exp_target_tokens_new, exp_ctx_after_new = expected.context_new

    # Compare context_before tokens (new)
    assert len(ctx_before_new) == len(exp_ctx_before_new), (
        f"context_before (new): Expected {len(exp_ctx_before_new)} tokens, got {len(ctx_before_new)}"
    )
    for i, (actual, exp) in enumerate(zip(ctx_before_new, exp_ctx_before_new)):
        assert type(actual) == type(exp), (
            f"context_before (new) token {i}: Expected {type(exp).__name__}, got {type(actual).__name__}"
        )
        assert str(actual) == str(exp), (
            f"context_before (new) token {i}: Expected '{exp}', got '{actual}'"
        )

    # Compare target_line tokens (new)
    assert len(target_tokens_new) == len(exp_target_tokens_new), (
        f"target_line (new): Expected {len(exp_target_tokens_new)} tokens, got {len(target_tokens_new)}\n"
        f"Expected: {exp_target_tokens_new}\n"
        f"Got: {target_tokens_new}"
    )
    for i, (actual, exp) in enumerate(zip(target_tokens_new, exp_target_tokens_new)):
        assert type(actual) == type(exp), (
            f"target_line (new) token {i}: Expected {type(exp).__name__}, got {type(actual).__name__}\n"
            f"Expected: {exp}\n"
            f"Got: {actual}"
        )
        assert str(actual) == str(exp), (
            f"target_line (new) token {i}: Expected '{exp}', got '{actual}'"
        )

    # Compare context_after tokens (new)
    assert len(ctx_after_new) == len(exp_ctx_after_new), (
        f"context_after (new): Expected {len(exp_ctx_after_new)} tokens, got {len(ctx_after_new)}"
    )
    for i, (actual, exp) in enumerate(zip(ctx_after_new, exp_ctx_after_new)):
        assert type(actual) == type(exp), (
            f"context_after (new) token {i}: Expected {type(exp).__name__}, got {type(actual).__name__}"
        )
        assert str(actual) == str(exp), (
            f"context_after (new) token {i}: Expected '{exp}', got '{actual}'"
        )
