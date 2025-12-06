import re
from pathlib import Path
from typing import NamedTuple, Optional, Tuple, Union

from .reverse_lookup import ReverseLookupResult, _do_reverse_lookup
from .utils import _are_dependencies_available, _compress_user_path


class Unchanged(str):
    """Token representing unchanged text."""

    pass


class Removed(str):
    """Token representing removed text."""

    pass


class Added(str):
    """Token representing added text."""

    pass


# Token type: each token can be Unchanged, Removed, or Added
Token = Union[Unchanged, Removed, Added]

# OutputReplacementContext: tuple of three token sequences
# (context_before_tokens, target_line_tokens, context_after_tokens)
OutputReplacementContext = Tuple[
    Tuple[Token, ...],
    Tuple[Token, ...],
    Tuple[Token, ...],
]

class UrlReplacement(NamedTuple):
    """
    Information about a URL replacement in an RST file.

    Attributes
    ----------
    filepath : str
        Path to the RST file containing the URL
    line_num : int
        Line number where the URL was found
    original_line : str
        The original line containing the URL
    replacement_line : Optional[str]
        The replacement line with Sphinx reference, or None if no replacement available
    context_old : OutputReplacementContext
        The old context (before replacement) with tokenized (context_before_tokens, target_line_tokens, context_after_tokens)
    context_new : OutputReplacementContext
        The new context (after replacement) with tokenized (context_before_tokens, target_line_tokens, context_after_tokens)
    inventory_url : Optional[str]
        The inventory URL used for the lookup, or None
    """

    filepath: str
    line_num: int
    original_line: str
    replacement_line: Optional[str]
    context_old: OutputReplacementContext
    context_new: OutputReplacementContext
    inventory_url: Optional[str]


class ReplacementContext(NamedTuple):
    """
    Context for a replacement operation.

    Attributes
    ----------
    context_before : str
        The context line before the target line. Empty string if there is no context before.
    target_line : str
        The target line to be replaced.
    context_after : str
        The context line after the target line. Empty string if there is no context after.
    """

    context_before: str
    target_line: str
    context_after: str


class ReplacementInfo(NamedTuple):
    """
    Information about a computed replacement.

    Attributes
    ----------
    context_old : OutputReplacementContext
        The context before replacement: (context_before_tokens, target_line_tokens, context_after_tokens)
        All tokens are Removed, representing the original state.
    context_new : OutputReplacementContext
        The context after replacement: (context_before_tokens, target_line_tokens, context_after_tokens)
        Contains Removed and Added tokens showing the changes.
    """

    context_old: OutputReplacementContext
    context_new: OutputReplacementContext


def _reconstruct_replacement_from_tokens(
    tokens: Tuple[Token, ...], original: str
) -> str:
    """
    Reconstruct the replacement string from tokens, excluding Removed tokens.

    Parameters
    ----------
    tokens : Tuple[Token, ...]
        Sequence of tokens
    original : str
        The original string (for comparison, not used with subclasses)

    Returns
    -------
    str
        Reconstructed replacement string (excluding Removed tokens)
    """
    if not tokens:
        return ""

    # With subclasses, isinstance() works correctly
    return "".join(str(token) for token in tokens if not isinstance(token, Removed))


def _reconstruct_original_from_tokens(tokens: Tuple[Token, ...]) -> str:
    """
    Reconstruct the original string from tokens, excluding Added tokens.

    Parameters
    ----------
    tokens : Tuple[Token, ...]
        Sequence of tokens

    Returns
    -------
    str
        Reconstructed original string (excluding Added tokens)
    """
    if not tokens:
        return ""

    return "".join(str(token) for token in tokens if not isinstance(token, Added))


def _tokenize_diff(original: str, replacement: str) -> Tuple[Token, ...]:
    """
    Tokenize a line diff into a sequence of Unchanged, Removed, and Added tokens.

    Uses a simple approach: find common prefix and suffix, mark middle as removed/added.

    Parameters
    ----------
    original : str
        The original line
    replacement : str
        The replacement line

    Returns
    -------
    Tuple[Token, ...]
        Sequence of tokens representing the diff
    """
    if original == replacement:
        return (Unchanged(original),)

    # Find common prefix
    prefix_len = 0
    while (
        prefix_len < len(original)
        and prefix_len < len(replacement)
        and original[prefix_len] == replacement[prefix_len]
    ):
        prefix_len += 1

    # Find common suffix
    suffix_len = 0
    while (
        suffix_len < len(original) - prefix_len
        and suffix_len < len(replacement) - prefix_len
        and original[-(suffix_len + 1)] == replacement[-(suffix_len + 1)]
    ):
        suffix_len += 1

    tokens: list[Token] = []

    # Add prefix (unchanged)
    if prefix_len > 0:
        tokens.append(Unchanged(original[:prefix_len]))

    # Add middle parts (removed and added)
    original_middle = (
        original[prefix_len : len(original) - suffix_len]
        if suffix_len > 0
        else original[prefix_len:]
    )
    replacement_middle = (
        replacement[prefix_len : len(replacement) - suffix_len]
        if suffix_len > 0
        else replacement[prefix_len:]
    )

    if original_middle:
        tokens.append(Removed(original_middle))
    if replacement_middle:
        tokens.append(Added(replacement_middle))

    # Add suffix (unchanged)
    if suffix_len > 0:
        tokens.append(Unchanged(original[-suffix_len:]))

    # Fallback: if no tokens were created (shouldn't happen, but handle edge cases)
    if not tokens:
        if original:
            tokens.append(Removed(original))
        if replacement:
            tokens.append(Added(replacement))
        if not original and not replacement:
            tokens.append(Unchanged(""))

    return tuple(tokens)


def _compute_replacement(
    original: ReplacementContext,
    lookup_result: ReverseLookupResult,
) -> ReplacementInfo:
    """
    Compute the replacement line(s) for a URL in an RST file.

    Parameters
    ----------
    original : ReplacementContext
        The original lines (context_before, target_line, context_after)
    lookup_result : ReverseLookupResult
        The reverse lookup result containing url, package, domain, rst_entry, etc.

    Returns
    -------
    ReplacementInfo
        A named tuple with (context_old, context_new).
        - context_old: OutputReplacementContext with all Removed tokens (original state)
        - context_new: OutputReplacementContext with Removed/Added tokens (modified state)
    """
    target = f"{lookup_result.package}:{lookup_result.rst_entry}"
    rst_ref = f":{lookup_result.domain}:`{target}`"

    original_line = original.target_line
    context_before_str = original.context_before
    context_after_str = original.context_after

    # Allow optional trailing punctuation before the closing >
    # The URL in the text might have trailing punctuation that was stripped during lookup
    full_link_match = re.search(
        r"`([^`<>]+)\s*<" + re.escape(lookup_result.url) + r"[.,;:!?)]*>`__?", original_line
    )
    if full_link_match:
        link_text = full_link_match.group(1).strip()
        original_text = full_link_match.group(0)

        # Find the indexes where replacement should happen
        start_idx = full_link_match.start()
        end_idx = full_link_match.end()

        # Extract the URL part from the original link
        # Format: `link_text <URL>`__ or `link_text <URL>`_
        # We want: Unchanged(`link_text <), Removed(URL), Unchanged(>), Removed(`__)
        # New: Added(:std:doc:`), Unchanged(link_text), Unchanged( <) [if space exists], Added(target), Unchanged(`>)
        url_match_in_link = re.search(r"<" + re.escape(lookup_result.url) + r"[.,;:!?)]*>", original_text)
        if url_match_in_link:
            url_start_in_link = url_match_in_link.start()  # Position of `<`
            url_end_in_link = url_match_in_link.end()  # Position after `>`
            
            # Find where the URL itself starts (after the `<`)
            url_only_start = url_start_in_link + 1  # After `<`
            # Find where the URL ends (before the `>`)
            url_only_end = url_end_in_link - 1  # Before `>`
            
            # Check if there's a space before `<` in the original
            # Look at the character before `<` in original_text
            space_before_angle = ""
            if url_start_in_link > 0 and original_text[url_start_in_link - 1] == " ":
                space_before_angle = " "
            
            # Build target_tokens_old and target_tokens_new in parallel
            domain_prefix = f":{lookup_result.domain}:`"
            
            target_tokens_old: list[Token] = []
            target_tokens_new: list[Token] = []
            if start_idx > 0:
                target_tokens_old.append(Unchanged(original_line[:start_idx]))
                target_tokens_new.append(Unchanged(original_line[:start_idx]))
            
            # Before URL in the link: `link_text < (opening backtick, link text, space if exists, opening <)
            before_url_in_link = original_text[:url_only_start]
            target_tokens_old.append(Unchanged(before_url_in_link))
            target_tokens_new.append(Added(domain_prefix))
            target_tokens_new.append(Unchanged(link_text))
            if space_before_angle:
                target_tokens_new.append(Unchanged(space_before_angle + "<"))
            else:
                target_tokens_new.append(Unchanged("<"))
            
            # URL part: just the URL (removed)
            url_part = original_text[url_only_start:url_only_end]
            target_tokens_old.append(Removed(url_part))
            
            # After URL in the link: `__ or `_ (closing backtick and underscores)
            after_url_in_link = original_text[url_end_in_link:]  # This is "`__" or "`_"
            # The closing backtick should be part of Unchanged(">`") in both old and new
            # Only the underscores should be removed
            if after_url_in_link.startswith("`"):
                closing_backtick = after_url_in_link[0]  # "`"
                underscores = after_url_in_link[1:]  # "__" or "_"
                # Closing `>` and backtick together (unchanged in both)
                closing_angle = original_text[url_only_end:url_end_in_link]  # This is just ">"
                target_tokens_old.append(Unchanged(closing_angle + closing_backtick))
                target_tokens_old.append(Removed(underscores))
                # In new context: Added(target), then Unchanged(">`") (the > and closing backtick)
                target_tokens_new.append(Added(target))
                target_tokens_new.append(Unchanged(closing_angle + closing_backtick))
            else:
                # Fallback: treat everything as removed
                closing_angle = original_text[url_only_end:url_end_in_link]  # This is just ">"
                target_tokens_old.append(Unchanged(closing_angle))
                target_tokens_old.append(Removed(after_url_in_link))
                target_tokens_new.append(Added(target))
                target_tokens_new.append(Unchanged(closing_angle))
            
            if end_idx < len(original_line):
                target_tokens_old.append(Unchanged(original_line[end_idx:]))
                target_tokens_new.append(Unchanged(original_line[end_idx:]))
        else:
            # Fallback: if we can't find the URL in the link, treat the whole thing as removed
            target_tokens_old: list[Token] = []
            target_tokens_new: list[Token] = []
            if start_idx > 0:
                target_tokens_old.append(Unchanged(original_line[:start_idx]))
                target_tokens_new.append(Unchanged(original_line[:start_idx]))
            target_tokens_old.append(Removed(original_text))
            domain_prefix = f":{lookup_result.domain}:`"
            target_suffix = f" <{target}>`"
            target_tokens_new.append(Added(domain_prefix))
            target_tokens_new.append(Unchanged(link_text))
            target_tokens_new.append(Added(target_suffix))
            if end_idx < len(original_line):
                target_tokens_old.append(Unchanged(original_line[end_idx:]))
                target_tokens_new.append(Unchanged(original_line[end_idx:]))

        # Tokenize context_before and context_after (unchanged in this case)
        ctx_before_tokens_old = (Unchanged(context_before_str),)
        ctx_before_tokens_new = (Unchanged(context_before_str),)

        ctx_after_tokens_old = (Unchanged(context_after_str),)
        ctx_after_tokens_new = (Unchanged(context_after_str),)

        context_old: OutputReplacementContext = (
            ctx_before_tokens_old,
            tuple(target_tokens_old),
            ctx_after_tokens_old,
        )

        context_new: OutputReplacementContext = (
            ctx_before_tokens_new,
            tuple(target_tokens_new),
            ctx_after_tokens_new,
        )

        return ReplacementInfo(
            context_old,
            context_new,
        )

    # Allow optional trailing punctuation before the closing >
    simple_link_match = re.search(
        r"`?<" + re.escape(lookup_result.url) + r"[.,;:!?)]*>`__?", original_line
    )
    if simple_link_match:
        original_text = simple_link_match.group(0)
        start_idx = simple_link_match.start()
        end_idx = simple_link_match.end()

        link_text_match = re.search(r"`([^`]+)$", context_before_str)
        if link_text_match:
            link_text = link_text_match.group(1).strip()

            # Find the index in context_before where replacement should happen
            ctx_match_start = link_text_match.start()
            ctx_match_end = link_text_match.end()

            # Build ctx_before_tokens_old and ctx_before_tokens_new in parallel
            ctx_before_tokens_old_list: list[Token] = []
            ctx_before_tokens_new_list: list[Token] = []
            if ctx_match_start > 0:
                ctx_before_tokens_old_list.append(
                    Unchanged(context_before_str[:ctx_match_start])
                )
                ctx_before_tokens_new_list.append(
                    Unchanged(context_before_str[:ctx_match_start])
                )
            # The `[^`]+` part becomes the domain role prefix
            removed_text = context_before_str[ctx_match_start:ctx_match_end]
            ctx_before_tokens_old_list.append(Removed(removed_text))
            new_context_before_prefix = f":{lookup_result.domain}:`"
            ctx_before_tokens_new_list.append(
                Added(new_context_before_prefix + link_text)
            )
            if ctx_match_end < len(context_before_str):
                ctx_before_tokens_old_list.append(
                    Unchanged(context_before_str[ctx_match_end:])
                )
                ctx_before_tokens_new_list.append(
                    Unchanged(context_before_str[ctx_match_end:])
                )

            # Build target_tokens_old and target_tokens_new in parallel
            target_tokens_old_list: list[Token] = []
            target_tokens_new_list: list[Token] = []
            if start_idx > 0:
                target_tokens_old_list.append(Unchanged(original_line[:start_idx]))
                target_tokens_new_list.append(Unchanged(original_line[:start_idx]))
            target_tokens_old_list.append(Removed(original_text))
            target_tokens_new_list.append(Added(f"<{target}>`"))
            if end_idx < len(original_line):
                target_tokens_old_list.append(Unchanged(original_line[end_idx:]))
                target_tokens_new_list.append(Unchanged(original_line[end_idx:]))

            # Tokenize context_after (unchanged in this case)
            ctx_after_tokens_old = (Unchanged(context_after_str),)
            ctx_after_tokens_new = (Unchanged(context_after_str),)

            context_old = (
                tuple(ctx_before_tokens_old_list),
                tuple(target_tokens_old_list),
                ctx_after_tokens_old,
            )

            context_new = (
                tuple(ctx_before_tokens_new_list),
                tuple(target_tokens_new_list),
                ctx_after_tokens_new,
            )

            return ReplacementInfo(
                context_old,
                context_new,
            )

        # Build target_tokens_old and target_tokens_new in parallel
        target_tokens_old_list2: list[Token] = []
        target_tokens_new_list2: list[Token] = []
        if start_idx > 0:
            target_tokens_old_list2.append(Unchanged(original_line[:start_idx]))
            target_tokens_new_list2.append(Unchanged(original_line[:start_idx]))
        target_tokens_old_list2.append(Removed(original_text))
        target_tokens_new_list2.append(Added(rst_ref))
        if end_idx < len(original_line):
            target_tokens_old_list2.append(Unchanged(original_line[end_idx:]))
            target_tokens_new_list2.append(Unchanged(original_line[end_idx:]))

        ctx_before_tokens_old = (Unchanged(context_before_str),)
        ctx_before_tokens_new = (Unchanged(context_before_str),)
        ctx_after_tokens_old = (Unchanged(context_after_str),)
        ctx_after_tokens_new = (Unchanged(context_after_str),)

        context_old = (
            ctx_before_tokens_old,
            tuple(target_tokens_old_list2),
            ctx_after_tokens_old,
        )

        context_new = (
            ctx_before_tokens_new,
            tuple(target_tokens_new_list2),
            ctx_after_tokens_new,
        )

        return ReplacementInfo(
            context_old,
            context_new,
        )

    # Find the URL in the original line (allow optional trailing punctuation)
    url_match = re.search(re.escape(lookup_result.url) + r"[.,;:!?)]*", original_line)
    if url_match:
        start_idx = url_match.start()
        end_idx = url_match.end()

        # Build target_tokens_old and target_tokens_new in parallel
        target_tokens_old_list3: list[Token] = []
        target_tokens_new_list3: list[Token] = []
        if start_idx > 0:
            target_tokens_old_list3.append(Unchanged(original_line[:start_idx]))
            target_tokens_new_list3.append(Unchanged(original_line[:start_idx]))
        target_tokens_old_list3.append(Removed(lookup_result.url))
        target_tokens_new_list3.append(Added(rst_ref))
        if end_idx < len(original_line):
            target_tokens_old_list3.append(Unchanged(original_line[end_idx:]))
            target_tokens_new_list3.append(Unchanged(original_line[end_idx:]))
    else:
        # Fallback: entire line is replaced
        target_tokens_old_list3 = [Removed(original_line)]
        target_tokens_new_list3 = [Added(rst_ref)]

    ctx_before_tokens_old = (Unchanged(context_before_str),)
    ctx_before_tokens_new = (Unchanged(context_before_str),)
    ctx_after_tokens_old = (Unchanged(context_after_str),)
    ctx_after_tokens_new = (Unchanged(context_after_str),)

    ctx_before_final = (Unchanged(context_before_str),)
    ctx_after_final = (Unchanged(context_after_str),)

    context_old_final: OutputReplacementContext = (
        ctx_before_final,
        tuple(target_tokens_old_list3),
        ctx_after_final,
    )

    context_new_final: OutputReplacementContext = (
        ctx_before_final,
        tuple(target_tokens_new_list3),
        ctx_after_final,
    )

    return ReplacementInfo(
        context_old_final,
        context_new_final,
    )


def _find_url_replacements(directory: str):
    """
    Find all URLs in RST files that can be replaced with Sphinx references.

    Parameters
    ----------
    directory : str
        Directory to search for .rst files

    Yields
    ------
    UrlReplacement
        UrlReplacement namedtuples containing:
        filepath, line_num, original_line, replacement_line
    """
    url_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

    directory_path = Path(directory)
    if directory_path.is_file():
        rst_files = [directory_path] if directory_path.suffix == ".rst" else []
    else:
        rst_files = list(directory_path.rglob("*.rst"))

    for rst_file in rst_files:
        url_locations: dict[str, list[tuple[int, str]]] = {}
        all_lines = []

        try:
            with open(rst_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                for line_num, line in enumerate(all_lines, start=1):
                    urls = url_pattern.findall(line)
                    for url in urls:
                        url = url.rstrip(".,;:!?)")
                        url_locations.setdefault(url, []).append(
                            (line_num, line.rstrip())
                        )
        except Exception:
            continue

        if not url_locations:
            continue

        urls = list(url_locations.keys())
        results = _do_reverse_lookup(urls)

        replaceable = [
            (result, url_locations[result.url])
            for result in results
            if result.package is not None
        ]

        if not replaceable:
            continue

        filepath = str(rst_file)

        for lookup_result, line_infos in replaceable:
            for line_num, original_line in line_infos:
                context_before = (
                    all_lines[line_num - 2].rstrip() if line_num > 1 else ""
                )
                context_after = (
                    all_lines[line_num].rstrip() if line_num < len(all_lines) else ""
                )

                if lookup_result.rst_entry is not None:
                    replacement_info = _compute_replacement(
                        ReplacementContext(
                            context_before,
                            original_line,
                            context_after,
                        ),
                        lookup_result,
                    )

                    # Extract target_line_tokens from context_new to reconstruct replacement_line
                    (
                        _,
                        target_line_tokens,
                        _,
                    ) = replacement_info.context_new

                    # Reconstruct replacement_line from target_line_tokens (excluding Removed)
                    replacement_line = _reconstruct_replacement_from_tokens(
                        target_line_tokens, original_line
                    )

                    yield UrlReplacement(
                        filepath,
                        line_num,
                        original_line,
                        replacement_line,
                        replacement_info.context_old,
                        replacement_info.context_new,
                        lookup_result.inventory_url,
                    )
                else:
                    # No replacement available - create empty contexts
                    empty_context: OutputReplacementContext = (
                        (Unchanged(context_before),),
                        (Unchanged(original_line),),
                        (Unchanged(context_after),),
                    )
                    yield UrlReplacement(
                        filepath,
                        line_num,
                        original_line,
                        None,  # No replacement available
                        empty_context,
                        empty_context,
                        lookup_result.inventory_url,
                    )


def _print_tokens(tokens: Tuple[Token, ...], prefix: str = "", use_bg_for_added: bool = False, use_blue: bool = False, default_fg: Optional[str] = None) -> None:
    """
    Print tokens with appropriate colors.

    Parameters
    ----------
    tokens : Tuple[Token, ...]
        Sequence of tokens to print
    prefix : str
        Prefix to print before the tokens (e.g., "     - " or "       ")
    use_bg_for_added : bool
        If True, use background green for Added tokens and foreground green for the rest
    use_blue : bool
        If True, use blue color for the entire line
    default_fg : Optional[str]
        Default foreground color for the line (e.g., RED, GREEN, BLUE)
    """
    RED = "\033[31m"
    RED_BG = "\033[41;37m"
    GREEN = "\033[32m"
    GREEN_BG = "\033[42;30m"
    BLUE = "\033[34m"
    RESET = "\033[0m"

    if not tokens:
        return

    # Extract prefix color and text
    prefix_color = ""
    prefix_text = prefix
    if prefix.startswith("     - "):
        prefix_color = RED
        prefix_text = "     - "
    elif prefix.startswith("     + "):
        prefix_color = GREEN
        prefix_text = "     + "
    elif prefix.startswith("     ? "):
        prefix_color = BLUE
        prefix_text = "     ? "

    output = ""
    if prefix_color:
        output += prefix_color + prefix_text + RESET
    else:
        output += prefix_text

    # Determine default foreground color
    if default_fg is None:
        if prefix.startswith("     - "):
            default_fg = RED
        elif prefix.startswith("     + "):
            default_fg = GREEN
        elif prefix.startswith("     ? "):
            default_fg = BLUE

    if use_blue:
        # Blue color for "?" lines
        output += BLUE
        for token in tokens:
            output += str(token)
        output += RESET
    elif use_bg_for_added:
        # For Added lines: BG GREEN for Added tokens, FG GREEN for the rest
        has_added = any(isinstance(token, Added) for token in tokens)
        if has_added:
            fg_color = default_fg if default_fg else GREEN
            output += fg_color  # Start with default FG color for the whole line
            for token in tokens:
                if isinstance(token, Added):
                    output += f"{GREEN_BG}{str(token)}{RESET}{fg_color}"
                elif isinstance(token, Removed):
                    output += f"{RED_BG}{str(token)}{RESET}{fg_color}"
                else:  # Unchanged
                    output += str(token)
            output += RESET
        else:
            # No Added tokens, print with default color
            if default_fg:
                output += default_fg
            for token in tokens:
                if isinstance(token, Removed):
                    output += f"{RED_BG}{str(token)}{RESET}"
                    if default_fg:
                        output += default_fg
                else:  # Unchanged
                    output += str(token)
            if default_fg:
                output += RESET
    else:
        # Normal printing with default foreground color
        if default_fg:
            output += default_fg
        for token in tokens:
            if isinstance(token, Removed):
                output += f"{RED_BG}{str(token)}{RESET}"
                if default_fg:
                    output += default_fg
            elif isinstance(token, Added):
                output += f"{GREEN}{str(token)}{RESET}"
                if default_fg:
                    output += default_fg
            else:  # Unchanged
                output += str(token)
        if default_fg:
            output += RESET

    print(output)


def rev_search(directory: str) -> None:
    """
    Search for URLs in .rst files that can be replaced with Sphinx references.

    Parameters
    ----------
    directory : str
        Directory to search for .rst files
    """
    if not _are_dependencies_available():
        return

    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    YELLOW_BG = "\033[43;30m"
    RESET = "\033[0m"

    found_any = False
    for replacement in _find_url_replacements(directory):
        if not found_any:
            found_any = True
        display_path = _compress_user_path(replacement.filepath)
        print(f"{CYAN}{display_path}:{replacement.line_num}{RESET}")

        if replacement.replacement_line is None:
            # No replacement available - print old context only
            ctx_before_tokens_old, target_tokens_old, ctx_after_tokens_old = replacement.context_old

            if ctx_before_tokens_old:
                _print_tokens(ctx_before_tokens_old, "       ")

            _print_tokens(target_tokens_old, "     ? ", use_blue=True)

            if ctx_after_tokens_old:
                _print_tokens(ctx_after_tokens_old, "       ")

            print()
            continue

        # Extract tokens from old and new contexts
        ctx_before_tokens_old, target_tokens_old, ctx_after_tokens_old = replacement.context_old
        ctx_before_tokens_new, target_tokens_new, ctx_after_tokens_new = replacement.context_new

        # Print all old lines together (before and target)
        # Print context_before (old) if it exists
        if ctx_before_tokens_old or ctx_before_tokens_new:
            # Check if context_before changed
            if ctx_before_tokens_old != ctx_before_tokens_new:
                # Print old context_before (removed)
                _print_tokens(ctx_before_tokens_old, "     - ")
            else:
                # Print unchanged context_before
                _print_tokens(ctx_before_tokens_old, "       ")

        # Print target line: old (removed)
        _print_tokens(target_tokens_old, "     - ")

        # Print inventory URL if different from the URL in the line (between old and new sections)
        if replacement.inventory_url:
            # Check if inventory_url appears in target_tokens_old
            old_text = "".join(str(token) for token in target_tokens_old if not isinstance(token, Added))
            if replacement.inventory_url in old_text:
                # Find position to align the inventory URL
                url_pos = old_text.find(replacement.inventory_url)
                spaces = " " * (7 + url_pos)
                print(f"{spaces}{YELLOW}{YELLOW_BG}{replacement.inventory_url}{RESET}")

        # Print all new lines together (before and target)
        # Print context_before (new) if it changed
        if ctx_before_tokens_old or ctx_before_tokens_new:
            if ctx_before_tokens_old != ctx_before_tokens_new:
                # Print new context_before (added) with BG GREEN for Added tokens
                _print_tokens(ctx_before_tokens_new, "     + ", use_bg_for_added=True)

        # Print target line: new (added) with BG GREEN for Added tokens
        _print_tokens(target_tokens_new, "     + ", use_bg_for_added=True)

        # Print context_after at the end (old and new together)
        if ctx_after_tokens_old or ctx_after_tokens_new:
            # Check if context_after changed
            if ctx_after_tokens_old != ctx_after_tokens_new:
                # Print old context_after (removed)
                _print_tokens(ctx_after_tokens_old, "     - ")
                # Print new context_after (added) with BG GREEN for Added tokens
                _print_tokens(ctx_after_tokens_new, "     + ", use_bg_for_added=True)
            else:
                # Print unchanged context_after
                _print_tokens(ctx_after_tokens_old, "       ")

        print()

    if not found_any:
        print("No URLs found that can be replaced with Sphinx references")
