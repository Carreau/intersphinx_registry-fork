import re
from collections import namedtuple
from pathlib import Path

from .lookup import _are_dependencies_available
from .reverse_lookup import ReverseLookupResult, _do_reverse_lookup

UrlReplacement = namedtuple(
    "UrlReplacement",
    ["filepath", "line_num", "original_line", "replacement_line",
     "original_context_before", "context_before", "context_after", "preserved_text", "inventory_url"],
)

ReplacementContext = namedtuple(
    "ReplacementContext",
    ["context_before", "target_line", "context_after"],
)

ReplacementInfo = namedtuple(
    "ReplacementInfo",
    ["context", "preserved_text"],
)


def _compress_user_path(path: str) -> str:
    """
    Replace home directory with ~ in a path string.

    Parameters
    ----------
    path : str
        Path to compress

    Returns
    -------
    str
        Path with home directory replaced by ~
    """
    home = str(Path.home())
    if path.startswith(home):
        return path.replace(home, "~", 1)
    return path


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
        A named tuple with (context, preserved_text).
        - context: ReplacementContext with (context_before, target_line, context_after)
        - preserved_text: The link text if preserved, None otherwise
    """
    target = f"{lookup_result.package}:{lookup_result.rst_entry}"
    rst_ref = f":{lookup_result.domain}:`{target}`"

    full_link_match = re.search(
        r"`([^`<>]+)\s*<" + re.escape(lookup_result.url) + r">`__?", original.target_line
    )
    if full_link_match:
        link_text = full_link_match.group(1).strip()
        original_text = full_link_match.group(0)
        replacement = f":{lookup_result.domain}:`{link_text} <{target}>`"
        return ReplacementInfo(
            ReplacementContext(
                original.context_before,
                original.target_line.replace(original_text, replacement),
                original.context_after,
            ),
            link_text,  # Preserved text
        )

    simple_link_match = re.search(r"`?<" + re.escape(lookup_result.url) + r">`__?", original.target_line)
    if simple_link_match:
        original_text = simple_link_match.group(0)

        if original.context_before:
            link_text_match = re.search(r"`([^`]+)$", original.context_before)
            if link_text_match:
                link_text = link_text_match.group(1).strip()

                new_context_before = re.sub(
                    r"`([^`]+)$", f":{lookup_result.domain}:`\\1", original.context_before
                )

                new_line = original.target_line.replace(original_text, f"<{target}>`")

                return ReplacementInfo(
                    ReplacementContext(new_context_before, new_line, original.context_after),
                    link_text,  # Preserved text
                )

        return ReplacementInfo(
            ReplacementContext(
                original.context_before,
                original.target_line.replace(original_text, rst_ref),
                original.context_after,
            ),
            None,  # No preserved text
        )

    return ReplacementInfo(
        ReplacementContext(
            original.context_before,
            original.target_line.replace(lookup_result.url, rst_ref),
            original.context_after,
        ),
        None,  # No preserved text
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
                context_before = all_lines[line_num - 2].rstrip() if line_num > 1 else None
                context_after = all_lines[line_num].rstrip() if line_num < len(all_lines) else None

                if lookup_result.rst_entry is not None:
                    original = ReplacementContext(context_before, original_line, context_after)
                    replacement_info = _compute_replacement(original, lookup_result)

                    yield UrlReplacement(
                        filepath,
                        line_num,
                        original_line,
                        replacement_info.context.target_line,
                        context_before,  # Original context_before
                        replacement_info.context.context_before,  # Modified context_before
                        replacement_info.context.context_after,
                        replacement_info.preserved_text,
                        lookup_result.inventory_url,
                    )
                else:
                    yield UrlReplacement(
                        filepath,
                        line_num,
                        original_line,
                        None,  # No replacement available
                        context_before,
                        context_before,  # Unchanged
                        context_after,
                        None,  # No preserved text
                        lookup_result.inventory_url,
                    )


def rev_search(directory: str):
    """
    Search for URLs in .rst files that can be replaced with Sphinx references.

    Parameters
    ----------
    directory : str
        Directory to search for .rst files
    """
    if not _are_dependencies_available():
        return

    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    RED_BG = "\033[41;37m"
    GREEN_BG = "\033[42;30m"
    BLUE_BG = "\033[44;37m"
    YELLOW_BG = "\033[43;30m"
    RESET = "\033[0m"

    found_any = False
    for replacement in _find_url_replacements(directory):
        if not found_any:
            found_any = True
        display_path = _compress_user_path(replacement.filepath)
        print(f"{CYAN}{display_path}:{replacement.line_num}{RESET}")

        if replacement.replacement_line is None:
            if replacement.original_context_before is not None:
                print(f"       {replacement.original_context_before}")

            url_match = re.search(
                r"https?://[^\s<>\"{}|\\^`\[\]]+", replacement.original_line
            )
            if url_match:
                url = url_match.group(0).rstrip(".,;:!?)")
                url_pos = replacement.original_line.find(url)
                before = replacement.original_line[:url_pos]
                after = replacement.original_line[url_pos + len(url):]

                link_match = re.search(
                    r"`([^`<>]+)\s*<" + re.escape(url) + r">`__?",
                    replacement.original_line,
                )
                simple_link_match = re.search(
                    r"<" + re.escape(url) + r">`__?", replacement.original_line
                )

                if link_match:
                    original_text = link_match.group(0)
                    url_pos = replacement.original_line.find(original_text)
                    before = replacement.original_line[:url_pos]
                    after = replacement.original_line[url_pos + len(original_text):]
                    print(f"     {BLUE}? {before}{BLUE_BG}{original_text}{RESET}{BLUE}{after}{RESET}")
                elif simple_link_match:
                    original_text = simple_link_match.group(0)
                    url_pos = replacement.original_line.find(original_text)
                    before = replacement.original_line[:url_pos]
                    after = replacement.original_line[url_pos + len(original_text):]
                    print(f"     {BLUE}? {before}{BLUE_BG}{original_text}{RESET}{BLUE}{after}{RESET}")
                else:
                    print(f"     {BLUE}? {before}{BLUE_BG}{url}{RESET}{BLUE}{after}{RESET}")
            else:
                print(f"     {BLUE}? {replacement.original_line}{RESET}")

            if replacement.context_after is not None:
                print(f"       {replacement.context_after}")

            print()
            continue

        if replacement.preserved_text:
            context_before_changed = (replacement.original_context_before is not None and
                                     replacement.context_before is not None and
                                     replacement.original_context_before != replacement.context_before)

            if context_before_changed:
                orig_ctx_match = re.search(r"^(.+)`([^`]+)$", replacement.original_context_before)
                rep_ctx_match = re.search(r"^(.+?):([\w:]+):`([^`]+)$", replacement.context_before)

                if orig_ctx_match and rep_ctx_match:
                    orig_ctx_before_text = orig_ctx_match.group(1)
                    orig_ctx_link_text = orig_ctx_match.group(2)
                    rep_ctx_before_text = rep_ctx_match.group(1)
                    rep_ctx_domain_role = rep_ctx_match.group(2)  # This is "std:doc" for example
                    rep_ctx_link_text = rep_ctx_match.group(3)

                    orig_ctx_highlighted = f"{orig_ctx_before_text}{RED_BG}`{RESET}{RED}{orig_ctx_link_text}{RESET}"
                    rep_ctx_highlighted = f"{rep_ctx_before_text}{GREEN_BG}:{rep_ctx_domain_role}:`{RESET}{GREEN}{rep_ctx_link_text}{RESET}"
                else:
                    orig_ctx_highlighted = replacement.original_context_before
                    rep_ctx_highlighted = replacement.context_before

                url_match = re.search(
                    r"https?://[^\s<>\"{}|\\^`\[\]]+", replacement.original_line
                )
                if url_match:
                    url = url_match.group(0).rstrip(".,;:!?)")

                    orig_target_match = re.search(r"^(.*)(<" + re.escape(url) + r">`__?)(.*)$", replacement.original_line)
                    rep_target_match = re.search(r"^(.*)<([^>]+)>`(.*)$", replacement.replacement_line)

                    if orig_target_match and rep_target_match:
                        orig_target_before = orig_target_match.group(1)
                        orig_target_full = orig_target_match.group(2)
                        orig_target_after = orig_target_match.group(3)

                        rep_target_before = rep_target_match.group(1)
                        rep_target = rep_target_match.group(2)
                        rep_target_after = rep_target_match.group(3)

                        orig_line_highlighted = f"{orig_target_before}{RED_BG}{orig_target_full}{RESET}{RED}{orig_target_after}{RESET}" if orig_target_after else f"{orig_target_before}{RED_BG}{orig_target_full}{RESET}"
                        rep_line_highlighted = f"{rep_target_before}{GREEN_BG}<{rep_target}>`{RESET}{GREEN}{rep_target_after}{RESET}" if rep_target_after else f"{rep_target_before}{GREEN_BG}<{rep_target}>`{RESET}"

                        print(f"     {RED}- {orig_ctx_highlighted}{RESET}")
                        print(f"     {RED}- {orig_line_highlighted}{RESET}")

                        if replacement.inventory_url and url != replacement.inventory_url:
                            url_pos_in_line = replacement.original_line.find(url)
                            spaces = " " * (7 + url_pos_in_line)
                            print(f"{spaces}{YELLOW}{YELLOW_BG}{replacement.inventory_url}{RESET}")

                        print(f"     {GREEN}+ {rep_ctx_highlighted}{RESET}")
                        print(f"     {GREEN}+ {rep_line_highlighted}{RESET}")

                        if replacement.context_after is not None:
                            print(f"       {replacement.context_after}")

                        print()
                        continue
            elif replacement.original_context_before is not None:
                print(f"       {replacement.original_context_before}")

            url_match = re.search(
                r"https?://[^\s<>\"{}|\\^`\[\]]+", replacement.original_line
            )
            if url_match:
                url = url_match.group(0).rstrip(".,;:!?)")

                link_match = re.search(
                    r"`([^`<>]+)\s*<" + re.escape(url) + r">`__?",
                    replacement.original_line,
                )
                if link_match:
                    orig_full = link_match.group(0)
                    orig_pos = replacement.original_line.find(orig_full)
                    before = replacement.original_line[:orig_pos]
                    after = replacement.original_line[orig_pos + len(orig_full):]

                    orig_highlighted = f"{RED_BG}`{RESET}{RED}{replacement.preserved_text} {RESET}{RED_BG}<{url}>`__{RESET}" if orig_full.endswith("__") else f"{RED_BG}`{RESET}{RED}{replacement.preserved_text} {RESET}{RED_BG}<{url}>`_{RESET}"

                    rep_match = re.search(
                        r":([\w:]+):`([^`<>]+)\s*<([^>]+)>`",
                        replacement.replacement_line,
                    )
                    if rep_match:
                        domain_role = rep_match.group(1)
                        target = rep_match.group(3)
                        rep_full = rep_match.group(0)
                        rep_pos = replacement.replacement_line.find(rep_full)
                        rep_before = replacement.replacement_line[:rep_pos]
                        rep_after = replacement.replacement_line[rep_pos + len(rep_full):]

                        rep_highlighted = f"{GREEN_BG}:{domain_role}:`{RESET}{GREEN}{replacement.preserved_text} {RESET}{GREEN_BG}<{target}>`{RESET}"

                        after_with_color = f"{RED}{after}" if after else ""
                        rep_after_with_color = f"{GREEN}{rep_after}" if rep_after else ""

                        print(f"     {RED}- {before}{orig_highlighted}{after_with_color}{RESET}")

                        if replacement.inventory_url and url != replacement.inventory_url:
                            url_pos_in_line = replacement.original_line.find(url)
                            spaces = " " * (7 + url_pos_in_line)
                            print(f"{spaces}{YELLOW}{YELLOW_BG}{replacement.inventory_url}{RESET}")

                        print(f"     {GREEN}+ {rep_before}{rep_highlighted}{rep_after_with_color}{RESET}")

                        if replacement.context_after is not None:
                            print(f"       {replacement.context_after}")

                        print()
                        continue

        if replacement.original_context_before is not None and replacement.original_context_before == replacement.context_before:
            print(f"       {replacement.original_context_before}")

        url_match = re.search(
            r"https?://[^\s<>\"{}|\\^`\[\]]+", replacement.original_line
        )
        if not url_match:
            print(f"     {RED}- {replacement.original_line}{RESET}")
            print(f"     {GREEN}+ {replacement.replacement_line}{RESET}")
        else:
            url = url_match.group(0).rstrip(".,;:!?)")
            url_pos = replacement.original_line.find(url)

            before = replacement.original_line[:url_pos]
            after = replacement.original_line[url_pos + len(url):]

            original_text = url
            link_match = re.search(
                r"`([^`<>]+)\s*<" + re.escape(url) + r">`__?",
                replacement.original_line,
            )
            simple_link_match = re.search(
                r"<" + re.escape(url) + r">`__?", replacement.original_line
            )

            if link_match:
                original_text = link_match.group(0)
                url_pos = replacement.original_line.find(original_text)
                before = replacement.original_line[:url_pos]
                after = replacement.original_line[url_pos + len(original_text):]
            elif simple_link_match:
                original_text = simple_link_match.group(0)
                url_pos = replacement.original_line.find(original_text)
                before = replacement.original_line[:url_pos]
                after = replacement.original_line[url_pos + len(original_text):]

            rep_match = re.search(
                r"https?://[^\s<>\"{}|\\^`\[\]]+", replacement.replacement_line
            )
            if rep_match:
                rep_url = rep_match.group(0).rstrip(".,;:!?)")
                rep_pos = replacement.replacement_line.find(rep_url)
                rep_before = replacement.replacement_line[:rep_pos]
                rep_after = replacement.replacement_line[rep_pos + len(rep_url):]
                rep_text = rep_url
            else:
                rst_ref_match = re.search(
                    r":\w+:\w+:`[^`]+`", replacement.replacement_line
                )
                if rst_ref_match:
                    rep_text = rst_ref_match.group(0)
                    rep_pos = replacement.replacement_line.find(rep_text)
                    rep_before = replacement.replacement_line[:rep_pos]
                    rep_after = replacement.replacement_line[rep_pos + len(rep_text):]
                else:
                    rep_before = ""
                    rep_text = replacement.replacement_line
                    rep_after = ""

            after_with_color = f"{RED}{after}" if after else ""
            rep_after_with_color = f"{GREEN}{rep_after}" if rep_after else ""
            print(
                f"     {RED}- {before}{RED_BG}{original_text}{RESET}{after_with_color}{RESET}"
            )

            if replacement.inventory_url and url != replacement.inventory_url:
                url_pos_in_line = replacement.original_line.find(url)
                spaces = " " * (7 + url_pos_in_line)
                print(f"{spaces}{YELLOW}{YELLOW_BG}{replacement.inventory_url}{RESET}")

            print(
                f"     {GREEN}+ {rep_before}{GREEN_BG}{rep_text}{RESET}{rep_after_with_color}{RESET}"
            )

        if replacement.context_after is not None:
            print(f"       {replacement.context_after}")

        print()

    if not found_any:
        print("No URLs found that can be replaced with Sphinx references")
