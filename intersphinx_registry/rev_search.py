import re
from pathlib import Path
from typing import Iterable, NamedTuple, Optional, Tuple, Union

from .reverse_lookup import ReverseLookupResult, _do_reverse_lookup
from .utils import _are_dependencies_available, _compress_user_path

# ANSI escape sequences
RED = "\033[31m"
RED_BG = "\033[41;37m"
GREEN = "\033[32m"
GREEN_BG = "\033[42;30m"
BLUE = "\033[34m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
YELLOW_BG = "\033[43;30m"
RESET = "\033[0m"


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


def normalise_token_stream(tokens: Tuple[Token, ...]) -> Tuple[Token, ...]:
    """
    Normalize a token stream by:
    1. Filtering out empty tokens
    2. Merging consecutive tokens of the same type

    This is useful for comparing token streams that are semantically
    equivalent but may have different tokenization.
    """
    if not tokens:
        return ()

    normalized = []
    current_type = None
    current_content = ""

    for token in tokens:
        if not str(token):
            continue

        token_type = type(token)

        if token_type == current_type:
            current_content += str(token)
        else:
            if current_type is not None:
                normalized.append(current_type(current_content))
            current_type = token_type
            current_content = str(token)

    if current_type is not None:
        normalized.append(current_type(current_content))

    return tuple(normalized)


class UrlReplacement(NamedTuple):
    """
    Information about a URL replacement in an RST file.

    Attributes
    ----------
    filepath : str
        Path to the RST file containing the URL
    line_num : int
        Line number where the URL was found
    matched_url : str
        The URL that was matched in the text and replaced
    context_old : OutputReplacementContext
        The old context (before replacement) with tokenized (context_before_tokens, target_line_tokens, context_after_tokens)
    context_new : OutputReplacementContext
        The new context (after replacement) with tokenized (context_before_tokens, target_line_tokens, context_after_tokens)
    inventory_url : Optional[str]
        The inventory URL used for the lookup, or None
    """

    filepath: str
    line_num: int
    matched_url: str
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


class ReplacementInfo:
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

    def __init__(
        self,
        context_old: OutputReplacementContext,
        context_new: OutputReplacementContext,
    ):
        """Initialize ReplacementInfo with normalized token streams."""
        # Normalize all token sequences
        ctx_before_old, target_old, ctx_after_old = context_old
        ctx_before_new, target_new, ctx_after_new = context_new

        self.context_old = (
            normalise_token_stream(ctx_before_old),
            normalise_token_stream(target_old),
            normalise_token_stream(ctx_after_old),
        )
        self.context_new = (
            normalise_token_stream(ctx_before_new),
            normalise_token_stream(target_new),
            normalise_token_stream(ctx_after_new),
        )


def _compute_full_link_replacement(
    original_line: str,
    context_before_str: str,
    context_after_str: str,
    lookup_result: ReverseLookupResult,
    target: str,
) -> Optional[ReplacementInfo]:
    """
    Handle full RST link replacement.

    Handles cases like:
    - `` `setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__ ``
    - `` `link text <URL>`_ ``
    - `` See `link text <URL>`__ for details ``

    Returns None if the pattern doesn't match.
    """
    full_link_match = re.search(
        r"`([^`<>]+)\s*<" + re.escape(lookup_result.url) + r"[.,;:!?)]*>`__?",
        original_line,
    )
    if full_link_match:
        link_text = full_link_match.group(1).strip()
        original_text = full_link_match.group(0)

        start_idx = full_link_match.start()
        end_idx = full_link_match.end()
        url_match_in_link = re.search(
            r"<" + re.escape(lookup_result.url) + r"[.,;:!?)]*>", original_text
        )
        if url_match_in_link:
            url_start_in_link = url_match_in_link.start()
            url_end_in_link = url_match_in_link.end()
            url_only_start = url_start_in_link + 1
            url_only_end = url_end_in_link - 1

            space_before_angle = ""
            if url_start_in_link > 0 and original_text[url_start_in_link - 1] == " ":
                space_before_angle = " "

            domain_prefix = f":{lookup_result.domain}:"

            target_tokens_old: list[Token] = []
            target_tokens_new: list[Token] = []
            if start_idx > 0:
                target_tokens_old.append(Unchanged(original_line[:start_idx]))
                target_tokens_new.append(Unchanged(original_line[:start_idx]))

            before_url_in_link = original_text[:url_only_start]
            target_tokens_old.append(Unchanged(before_url_in_link))
            target_tokens_new.append(Added(domain_prefix))
            target_tokens_new.append(Unchanged("`" + link_text))
            if space_before_angle:
                target_tokens_new.append(Unchanged(space_before_angle + "<"))
            else:
                target_tokens_new.append(Unchanged("<"))

            url_part = original_text[url_only_start:url_only_end]
            target_tokens_old.append(Removed(url_part))

            after_url_in_link = original_text[url_end_in_link:]
            if after_url_in_link.startswith("`"):
                closing_backtick = after_url_in_link[0]
                underscores = after_url_in_link[1:]
                closing_angle = original_text[url_only_end:url_end_in_link]
                target_tokens_new.append(Added(target))
                target_tokens_new.append(Unchanged(closing_angle + closing_backtick))
                target_tokens_old.append(Unchanged(closing_angle + closing_backtick))
                target_tokens_old.append(Removed(underscores))
            else:
                closing_angle = original_text[url_only_end:url_end_in_link]
                target_tokens_old.append(Unchanged(closing_angle))
                target_tokens_old.append(Removed(after_url_in_link))
                target_tokens_new.append(Added(target))
                target_tokens_new.append(Unchanged(closing_angle))

            if end_idx < len(original_line):
                target_tokens_old.append(Unchanged(original_line[end_idx:]))
                target_tokens_new.append(Unchanged(original_line[end_idx:]))
        else:
            target_tokens_old = []
            target_tokens_new = []
            if start_idx > 0:
                target_tokens_old.append(Unchanged(original_line[:start_idx]))
                target_tokens_new.append(Unchanged(original_line[:start_idx]))
            target_tokens_old.append(Removed(original_text))
            domain_prefix = f":{lookup_result.domain}:"
            target_suffix = f" <{target}>`"
            target_tokens_new.append(Added(domain_prefix))
            target_tokens_new.append(Unchanged("`" + link_text))
            target_tokens_new.append(Added(target_suffix))
            if end_idx < len(original_line):
                target_tokens_old.append(Unchanged(original_line[end_idx:]))
                target_tokens_new.append(Unchanged(original_line[end_idx:]))

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
    return None


def _compute_simple_link_replacement(
    original_line: str,
    context_before_str: str,
    context_after_str: str,
    lookup_result: ReverseLookupResult,
    target: str,
    rst_ref: str,
) -> Optional[ReplacementInfo]:
    """
    Handle simple RST link replacement (may span multiple lines).

    Handles cases like:
    - `` `<https://docs.python.org/3/library/os.html>`_ ``
    - `` `<https://docs.python.org/3/library/os.html>`__ ``
    - Multi-line: context_before has `` `link text ``, target_line has `` <URL>`_ ``

    Returns None if the pattern doesn't match.
    """
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

            ctx_match_start = link_text_match.start()
            ctx_match_end = link_text_match.end()
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
    return None


def _compute_url_replacement(
    original_line: str,
    context_before_str: str,
    context_after_str: str,
    lookup_result: ReverseLookupResult,
    rst_ref: str,
) -> ReplacementInfo:
    """
    Handle plain URL replacement in text.

    Handles cases like:
    - `` See https://docs.python.org/3/library/os.html for details ``
    - `` Check https://docs.python.org/3/library/os.html. ``
    - `` https://docs.python.org/3/library/os.html is the documentation ``

    This is the fallback case when no RST link pattern matches.
    """
    url_match = re.search(re.escape(lookup_result.url) + r"[.,;:!?)]*", original_line)
    if url_match:
        start_idx = url_match.start()
        end_idx = url_match.end()

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
        target_tokens_old_list3 = [Removed(original_line)]
        target_tokens_new_list3 = [Added(rst_ref)]

    ctx_before_tokens_old = (Unchanged(context_before_str),)
    ctx_before_tokens_new = (Unchanged(context_before_str),)
    ctx_after_tokens_old = (Unchanged(context_after_str),)
    ctx_after_tokens_new = (Unchanged(context_after_str),)

    context_old: OutputReplacementContext = (
        ctx_before_tokens_old,
        tuple(target_tokens_old_list3),
        ctx_after_tokens_old,
    )

    context_new: OutputReplacementContext = (
        ctx_before_tokens_new,
        tuple(target_tokens_new_list3),
        ctx_after_tokens_new,
    )

    return ReplacementInfo(
        context_old,
        context_new,
    )


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

    result = _compute_full_link_replacement(
        original_line, context_before_str, context_after_str, lookup_result, target
    )
    if result is not None:
        return result

    result = _compute_simple_link_replacement(
        original_line,
        context_before_str,
        context_after_str,
        lookup_result,
        target,
        rst_ref,
    )
    if result is not None:
        return result

    return _compute_url_replacement(
        original_line, context_before_str, context_after_str, lookup_result, rst_ref
    )


def process_one_file(rst_file: Path):
    """
    Process a single RST file to find URLs that can be replaced with Sphinx references.

    Yields UrlReplacement objects for each URL found that has a corresponding
    inventory entry. Files are read, URLs are extracted and looked up, and
    replacements are computed with token-based diffs.

    Parameters
    ----------
    rst_file : Path
        Path to the RST file to process

    Yields
    ------
    UrlReplacement
        Information about each URL replacement found in the file
    """
    url_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
    url_locations: dict[str, list[tuple[int, str]]] = {}
    all_lines = []

    try:
        with open(rst_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            for line_num, line in enumerate(all_lines, start=1):
                urls = url_pattern.findall(line)
                for url in urls:
                    url = url.rstrip(".,;:!?)")
                    url_locations.setdefault(url, []).append((line_num, line.rstrip()))
    except Exception:
        return

    if not url_locations:
        return

    urls = list(url_locations.keys())
    results = _do_reverse_lookup(urls)

    replaceable = [
        (result, url_locations[result.url])
        for result in results
        if result.package is not None
    ]

    if not replaceable:
        return

    filepath = str(rst_file)

    for lookup_result, line_infos in replaceable:
        for line_num, original_line in line_infos:
            context_before = all_lines[line_num - 2].rstrip() if line_num > 1 else ""
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

                yield UrlReplacement(
                    filepath,
                    line_num,
                    lookup_result.url,
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
                    lookup_result.url,
                    empty_context,
                    empty_context,
                    lookup_result.inventory_url,
                )


def format_tokens(
    tokens: Tuple[Token, ...],
    prefix: str = "",
    defaultFG: str = "",
    AddedHighlight: str = "",
    RemovedHighlight: str = "",
) -> str:
    """
    Format tokens with appropriate colors.

    Parameters
    ----------
    tokens : Tuple[Token, ...]
        Sequence of tokens to format
    prefix : str
        Prefix to add before the tokens (e.g., "     - " or "       ")
    defaultFG : str
        Default foreground color for the line (e.g., RED, GREEN, BLUE). Empty string for no color.
    AddedHighlight : str
        Highlight style for Added tokens (e.g., GREEN_BG for background). Empty string for no highlight.
    RemovedHighlight : str
        Highlight style for Removed tokens (e.g., RED_BG for background). Empty string for no highlight.

    Returns
    -------
    str
        Formatted string with ANSI color codes
    """
    if not tokens:
        return ""

    output = ""
    # Add prefix with defaultFG color
    output += defaultFG + prefix + RESET + defaultFG

    for token in tokens:
        if isinstance(token, Added):
            output += f"{AddedHighlight}{str(token)}{RESET}{defaultFG}"
        elif isinstance(token, Removed):
            output += f"{RemovedHighlight}{str(token)}{RESET}{defaultFG}"
        else:  # Unchanged
            output += str(token)

    output += RESET

    return output


def rev_search(directory: str) -> None:
    """
    Search for URLs in .rst files that can be replaced with Sphinx references.

    Parameters
    ----------
    directory : str
        Path to a directory to search for .rst files, or a single .rst file path
    """
    if not _are_dependencies_available():
        return

    directory_path = Path(directory)
    if directory_path.is_file():
        rst_files: Iterable[Path] = (
            [directory_path] if directory_path.suffix == ".rst" else []
        )
    else:
        rst_files = directory_path.rglob("*.rst")

    for rst_file in rst_files:
        search_one_file(rst_file)


def search_one_file(rst_file: Path) -> None:
    """
    Search a single RST file and print formatted diffs for replaceable URLs.

    Processes the file to find replaceable URLs and prints a formatted diff
    showing the original and replacement text with color-coded token highlights.

    Parameters
    ----------
    rst_file : Path
        Path to the RST file to search and display results for
    """
    for replacement in process_one_file(rst_file):
        display_path = _compress_user_path(replacement.filepath)
        print(f"{CYAN}{display_path}:{replacement.line_num}{RESET}")

        if replacement.context_old == replacement.context_new:
            ctx_before_tokens_old, target_tokens_old, ctx_after_tokens_old = (
                replacement.context_old
            )

            if ctx_before_tokens_old:
                print(format_tokens(ctx_before_tokens_old, "       "))

            print(format_tokens(target_tokens_old, "     ? ", defaultFG=BLUE))

            if ctx_after_tokens_old:
                print(format_tokens(ctx_after_tokens_old, "       "))

            print()
            continue

        ctx_before_tokens_old, target_tokens_old, ctx_after_tokens_old = (
            replacement.context_old
        )
        ctx_before_tokens_new, target_tokens_new, ctx_after_tokens_new = (
            replacement.context_new
        )

        if ctx_before_tokens_old or ctx_before_tokens_new:
            if ctx_before_tokens_old != ctx_before_tokens_new:
                print(
                    format_tokens(
                        ctx_before_tokens_old,
                        "     - ",
                        defaultFG=RED,
                        RemovedHighlight=RED_BG,
                    )
                )
            else:
                print(format_tokens(ctx_before_tokens_old, "       "))
        print(
            format_tokens(
                target_tokens_old, "     - ", defaultFG=RED, RemovedHighlight=RED_BG
            )
        )

        if replacement.inventory_url:
            old_text = "".join(
                str(token)
                for token in target_tokens_old
                if not isinstance(token, Added)
            )
            if replacement.inventory_url not in old_text:
                https_pos = old_text.find("https://")
                if https_pos >= 0:
                    spaces = " " * (7 + https_pos)
                else:
                    spaces = "       "

                matched_url = replacement.matched_url
                inventory_url = replacement.inventory_url

                prefix_len = 0
                while (
                    prefix_len < len(matched_url)
                    and prefix_len < len(inventory_url)
                    and matched_url[prefix_len] == inventory_url[prefix_len]
                ):
                    prefix_len += 1

                suffix_len = 0
                while (
                    suffix_len < len(matched_url) - prefix_len
                    and suffix_len < len(inventory_url) - prefix_len
                    and matched_url[-(suffix_len + 1)]
                    == inventory_url[-(suffix_len + 1)]
                ):
                    suffix_len += 1

                if prefix_len > 0 or suffix_len > 0:
                    prefix = inventory_url[:prefix_len]
                    middle = (
                        inventory_url[prefix_len : len(inventory_url) - suffix_len]
                        if suffix_len > 0
                        else inventory_url[prefix_len:]
                    )
                    suffix = inventory_url[-suffix_len:] if suffix_len > 0 else ""
                    highlighted_url = f"{YELLOW}{prefix}{YELLOW_BG}{middle}{RESET}{YELLOW}{suffix}{RESET}"
                else:
                    highlighted_url = f"{YELLOW_BG}{inventory_url}{RESET}"

                print(f"{spaces}{highlighted_url}")

        if ctx_before_tokens_old or ctx_before_tokens_new:
            if ctx_before_tokens_old != ctx_before_tokens_new:
                print(
                    format_tokens(
                        ctx_before_tokens_new,
                        "     + ",
                        defaultFG=GREEN,
                        AddedHighlight=GREEN_BG,
                        RemovedHighlight=RED_BG,
                    )
                )
        print(
            format_tokens(
                target_tokens_new,
                "     + ",
                defaultFG=GREEN,
                AddedHighlight=GREEN_BG,
                RemovedHighlight=RED_BG,
            )
        )

        if ctx_after_tokens_old or ctx_after_tokens_new:
            if ctx_after_tokens_old != ctx_after_tokens_new:
                print(
                    format_tokens(
                        ctx_after_tokens_old,
                        "     - ",
                        defaultFG=RED,
                        RemovedHighlight=RED_BG,
                    )
                )
                print(
                    format_tokens(
                        ctx_after_tokens_new,
                        "     + ",
                        defaultFG=GREEN,
                        AddedHighlight=GREEN_BG,
                        RemovedHighlight=RED_BG,
                    )
                )
            else:
                print(format_tokens(ctx_after_tokens_old, "       "))

        print()
