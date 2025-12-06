import json
import re
import shutil
import sys
import warnings
from collections import namedtuple
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import platformdirs
import requests
import requests_cache
from sphinx.util.inventory import InventoryFile

from . import __version__, get_intersphinx_mapping

# Named tuple for reverse lookup results
ReverseLookupResult = namedtuple(
    "ReverseLookupResult",
    ["url", "package", "domain", "rst_entry", "display_name"],
)

UrlReplacement = namedtuple(
    "UrlReplacement",
    ["filepath", "line_num", "original_line", "replacement_line",
     "original_context_before", "context_before", "context_after", "preserved_text"],
)

ReplacementContext = namedtuple(
    "ReplacementContext",
    ["context_before", "target_line", "context_after"],
)

# Extended replacement info for better diff highlighting
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


def _get_cache_dir() -> Path:
    """
    Get the cache directory for the current version of intersphinx_registry.

    Returns
    -------
    Path
        Cache directory path with version subdirectory
    """
    base_cache_dir = Path(platformdirs.user_cache_dir("intersphinx_registry"))
    cache_dir = base_cache_dir / __version__
    cache_dir.mkdir(parents=True, exist_ok=True)

    return cache_dir


def _cleanup_old_caches():
    """
    Remove cache directories from old versions of intersphinx_registry.
    Only keeps the current version's cache.
    """
    base_cache_dir = Path(platformdirs.user_cache_dir("intersphinx_registry"))

    if not base_cache_dir.exists():
        return

    current_version = __version__

    for version_dir in base_cache_dir.iterdir():
        if version_dir.is_dir() and version_dir.name != current_version:
            try:
                shutil.rmtree(version_dir)
            except Exception:
                pass


def _install_cache():
    """
    Install the version-specific requests cache.
    Cleans up old caches on first use.
    """
    _cleanup_old_caches()

    cache_dir = _get_cache_dir()
    cache_path = cache_dir / "intersphinx_cache.sqlite"

    requests_cache.install_cache(
        str(cache_path),
        backend="sqlite",
        expire_after=timedelta(hours=6),
        stale_if_error=True,
        cache_control=True,
    )


def _normalize_url_for_matching(url: str) -> str:
    """
    Normalize URL for fuzzy matching by removing version-specific segments.

    This helps match URLs like:
    - /stable/ vs /latest/ vs /main/
    - /v1.2.3/ vs /v2.0/ vs /1.5/
    """
    normalized = re.sub(r"/(latest|stable|main|dev|master)/", "/_VERSION_/", url)
    normalized = re.sub(r"/v?\d+(\.\d+)*/", "/_VERSION_/", normalized)
    return normalized


def uri_match(user_url: str, inv_url: str) -> bool:
    """
    Check if two URIs match, handling index.html variations and version normalization.

    Parameters
    ----------
    user_url : str
        URL from user input or RST file
    inv_url : str
        URL from intersphinx inventory

    Returns
    -------
    bool
        True if the URLs match (considering index.html and version variations), False otherwise
    """
    # Direct match
    if user_url == inv_url:
        return True

    # Handle index.html variations
    # Try adding/removing index.html
    variants = [user_url]
    if user_url.endswith("/index.html"):
        variants.append(user_url[:-10])  # Remove index.html, keep /
    elif user_url.endswith("/"):
        variants.append(user_url + "index.html")
    else:
        variants.append(user_url + "/index.html")

    # Check direct matches with variants
    if inv_url in variants:
        return True

    # Normalize for version-specific matching
    inv_url_normalized = _normalize_url_for_matching(inv_url).rstrip("/").replace("/index.html", "")
    for variant in variants:
        variant_normalized = _normalize_url_for_matching(variant).rstrip("/").replace("/index.html", "")
        if variant_normalized == inv_url_normalized:
            return True

    return False


def _do_reverse_lookup(
    urls: list[str],
) -> list[ReverseLookupResult]:
    """
    Core reverse lookup logic: given URLs, find which packages they belong to and their rst references.

    Parameters
    ----------
    urls : list[str]
        List of URLs

    Returns
    -------
    list[ReverseLookupResult]
        List of ReverseLookupResult named tuples with fields:
        url, package, domain, rst_entry, display_name
    """
    _install_cache()

    registry_file = Path(__file__).parent / "registry.json"
    registry = json.loads(registry_file.read_bytes())

    package_urls: dict[str, list[str]] = {}

    for url_str in urls:
        matched = False
        for package, (base_url, obj_path) in registry.items():
            if url_str.startswith(base_url):
                package_urls.setdefault(package, []).append(url_str)
                matched = True
                break

        if not matched:
            url_domain = urlparse(url_str).netloc
            url_path_normalized = (
                _normalize_url_for_matching(urlparse(url_str).path)
                .rstrip("/")
                .replace("/index.html", "")
            )

            for package, (base_url, obj_path) in registry.items():
                base_domain = urlparse(base_url).netloc
                if url_domain == base_domain:
                    base_path_normalized = _normalize_url_for_matching(
                        urlparse(base_url).path
                    ).rstrip("/")
                    if url_path_normalized.startswith(
                        base_path_normalized
                    ) or base_path_normalized.startswith(url_path_normalized):
                        package_urls.setdefault(package, []).append(url_str)
                        break

    results: list[ReverseLookupResult] = []

    for package, url_list in package_urls.items():
        base_url, obj_path = registry[package]
        inv_url = urljoin(base_url, obj_path if obj_path else "objects.inv")

        try:
            resp = requests.get(inv_url, timeout=25)
            resp.raise_for_status()
            inv = InventoryFile.load(BytesIO(resp.content), base_url, urljoin)
        except Exception as e:
            warnings.warn(
                f"Failed to load inventory for '{package}' from {inv_url}: {e}",
                UserWarning,
                stacklevel=2,
            )
            for url_str in url_list:
                results.append(
                    ReverseLookupResult(url_str, package, None, None, None)
                )
            continue

        inv_urls = {}
        for key, v in inv.items():
            for entry, item in v.items():
                inv_urls[item.uri] = (key, entry, item.display_name)

        for url_str in url_list:
            found = False

            # Use uri_match to check if the URL matches any inventory entry
            for inv_uri, (key, entry, display_name) in inv_urls.items():
                if uri_match(url_str, inv_uri):
                    results.append(
                        ReverseLookupResult(
                            url_str, package, key, entry, display_name
                        )
                    )
                    found = True
                    break

            if not found:
                results.append(
                    ReverseLookupResult(url_str, package, None, None, None)
                )

    return results


def _print_reverse_lookup_results(
    results: list[ReverseLookupResult],
):
    """
    Print formatted reverse lookup results.

    Parameters
    ----------
    results : list[ReverseLookupResult]
        List of ReverseLookupResult named tuples
    """
    if not results:
        return

    header_url = "URL"
    header_rst = "Sphinx Reference"
    header_display = "Description"

    width_url = max(len(header_url), max(len(r.url) for r in results))
    width_rst = max(
        len(header_rst),
        max(
            (
                len(f":{r.domain}:`{r.package}:{r.rst_entry}`")
                if r.rst_entry
                else len("NOT FOUND")
            )
            for r in results
        ),
    )
    width_display = max(
        len(header_display),
        max((len(r.display_name) if r.display_name else 0) for r in results),
    )

    print(f"{header_url:<{width_url}}  {header_rst:<{width_rst}}  {header_display}")
    print(f"{'-' * width_url}  {'-' * width_rst}  {'-' * width_display}")

    for result in results:
        if result.rst_entry:
            rst_ref = f":{result.domain}:`{result.package}:{result.rst_entry}`"
            display = (
                result.display_name
                if result.display_name and result.display_name != "-"
                else result.rst_entry
            )
            print(
                f"{result.url:<{width_url}}  {rst_ref:<{width_rst}}  {display:<{width_display}}"
            )
        elif result.package:
            print(f"{result.url:<{width_url}}  {'NOT FOUND':<{width_rst}}")


def reverse_lookup(urls: list[str]):
    """
    Reverse lookup: given URLs, find which packages they belong to and their rst references.

    Parameters
    ----------
    urls : list[str]
        List of URLs
    """
    if not urls:
        print("ERROR: No URLs provided")
        return

    if not _are_dependencies_available():
        return

    results = _do_reverse_lookup(urls)
    _print_reverse_lookup_results(results)


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
    # Build the reference target: package:entry
    target = f"{lookup_result.package}:{lookup_result.rst_entry}"
    # Build the full rst reference: :domain:role:`target`
    rst_ref = f":{lookup_result.domain}:`{target}`"

    # Check for full RST link with custom text: `text <URL>`_ or `text <URL>`__
    # Convert to :domain:role:`text <target>` format
    full_link_match = re.search(
        r"`([^`<>]+)\s*<" + re.escape(lookup_result.url) + r">`__?", original.target_line
    )
    if full_link_match:
        # Preserve the custom link text
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

    # Check for simple RST link: <URL>`_ or `<URL>`_ or <URL>`__ or `<URL>`__
    # This could be a single-line or multi-line link
    simple_link_match = re.search(r"`?<" + re.escape(lookup_result.url) + r">`__?", original.target_line)
    if simple_link_match:
        original_text = simple_link_match.group(0)

        # Check if this is part of a multi-line link by looking at the previous line
        # Multi-line pattern: previous line contains `text at the end (not ending with `_)
        if original.context_before:
            # Look for a backtick followed by text at the end of the line
            link_text_match = re.search(r"`([^`]+)$", original.context_before)
            if link_text_match:
                # Multi-line link - need to modify both lines
                # Preserve the link text
                link_text = link_text_match.group(1).strip()

                # Modify context_before: replace `link_text with :domain:role:`link_text
                new_context_before = re.sub(
                    r"`([^`]+)$", f":{lookup_result.domain}:`\\1", original.context_before
                )

                # Modify current line: replace <URL>`_ with <target>`
                new_line = original.target_line.replace(original_text, f"<{target}>`")

                return ReplacementInfo(
                    ReplacementContext(new_context_before, new_line, original.context_after),
                    link_text,  # Preserved text
                )

        # Single-line simple link - just replace with rst_ref
        return ReplacementInfo(
            ReplacementContext(
                original.context_before,
                original.target_line.replace(original_text, rst_ref),
                original.context_after,
            ),
            None,  # No preserved text
        )

    # Plain URL - replace with just the rst_ref
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

        # Keep ReverseLookupResult objects, pair them with their line locations
        replaceable = [
            (result, url_locations[result.url])
            for result in results
            if result.rst_entry is not None
        ]

        if not replaceable:
            continue

        filepath = str(rst_file)

        for lookup_result, line_infos in replaceable:
            for line_num, original_line in line_infos:
                # Get context lines (1 before and 1 after)
                context_before = all_lines[line_num - 2].rstrip() if line_num > 1 else None
                context_after = all_lines[line_num].rstrip() if line_num < len(all_lines) else None

                # Create the original lines tuple
                original = ReplacementContext(context_before, original_line, context_after)

                # Compute replacement
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
    RED_BG = "\033[41;37m"
    GREEN_BG = "\033[42;30m"
    RESET = "\033[0m"

    found_any = False
    for replacement in _find_url_replacements(directory):
        if not found_any:
            found_any = True
        display_path = _compress_user_path(replacement.filepath)
        print(f"{CYAN}{display_path}:{replacement.line_num}{RESET}")

        # Check if we have preserved text for smart highlighting
        if replacement.preserved_text:
            # Check if this is a multi-line link (context_before changed)
            context_before_changed = (replacement.original_context_before is not None and
                                     replacement.context_before is not None and
                                     replacement.original_context_before != replacement.context_before)

            # If context_before changed, print the diff for that line too
            if context_before_changed:
                # Multi-line link: print old lines together, then new lines together
                # This is more readable: old_before + old_line, then new_before + new_line

                # Pattern: `text at end -> :domain:role:`text at end
                orig_ctx_match = re.search(r"^(.+)`([^`]+)$", replacement.original_context_before)
                # Use non-greedy match for the first group to avoid capturing the colon
                rep_ctx_match = re.search(r"^(.+?):([\w:]+):`([^`]+)$", replacement.context_before)

                # Highlight context_before line
                if orig_ctx_match and rep_ctx_match:
                    orig_ctx_before_text = orig_ctx_match.group(1)
                    orig_ctx_link_text = orig_ctx_match.group(2)
                    rep_ctx_before_text = rep_ctx_match.group(1)
                    rep_ctx_domain_role = rep_ctx_match.group(2)  # This is "std:doc" for example
                    rep_ctx_link_text = rep_ctx_match.group(3)

                    orig_ctx_highlighted = f"{orig_ctx_before_text}{RED_BG}`{RESET}{RED}{orig_ctx_link_text}{RESET}"
                    rep_ctx_highlighted = f"{rep_ctx_before_text}{GREEN_BG}:{rep_ctx_domain_role}:`{RESET}{GREEN}{rep_ctx_link_text}{RESET}"
                else:
                    # Fallback if regex doesn't match
                    orig_ctx_highlighted = replacement.original_context_before
                    rep_ctx_highlighted = replacement.context_before

                # Now handle the target line highlighting
                url_match = re.search(
                    r"https?://[^\s<>\"{}|\\^`\[\]]+", replacement.original_line
                )
                if url_match:
                    url = url_match.group(0).rstrip(".,;:!?)")

                    # For multi-line links, the target line has <URL>`_ pattern
                    # Original: <URL>`_ or <URL>`__
                    # Replacement: <target>`
                    orig_target_match = re.search(r"^(.*)(<" + re.escape(url) + r">`__?)(.*)$", replacement.original_line)
                    rep_target_match = re.search(r"^(.*)<([^>]+)>`(.*)$", replacement.replacement_line)

                    if orig_target_match and rep_target_match:
                        orig_target_before = orig_target_match.group(1)
                        orig_target_full = orig_target_match.group(2)
                        orig_target_after = orig_target_match.group(3)

                        rep_target_before = rep_target_match.group(1)
                        rep_target = rep_target_match.group(2)
                        rep_target_after = rep_target_match.group(3)

                        # Highlight the target lines
                        orig_line_highlighted = f"{orig_target_before}{RED_BG}{orig_target_full}{RESET}{RED}{orig_target_after}{RESET}" if orig_target_after else f"{orig_target_before}{RED_BG}{orig_target_full}{RESET}"
                        rep_line_highlighted = f"{rep_target_before}{GREEN_BG}<{rep_target}>`{RESET}{GREEN}{rep_target_after}{RESET}" if rep_target_after else f"{rep_target_before}{GREEN_BG}<{rep_target}>`{RESET}"

                        # Print old lines together
                        print(f"     {RED}- {orig_ctx_highlighted}{RESET}")
                        print(f"     {RED}- {orig_line_highlighted}{RESET}")
                        # Print new lines together
                        print(f"     {GREEN}+ {rep_ctx_highlighted}{RESET}")
                        print(f"     {GREEN}+ {rep_line_highlighted}{RESET}")

                        # Print context after
                        if replacement.context_after is not None:
                            print(f"       {replacement.context_after}")

                        print()
                        continue
            elif replacement.original_context_before is not None:
                # Context before exists but unchanged - just print it
                print(f"       {replacement.original_context_before}")

            # Smart highlighting: show preserved text with foreground only (single-line links)
            url_match = re.search(
                r"https?://[^\s<>\"{}|\\^`\[\]]+", replacement.original_line
            )
            if url_match:
                url = url_match.group(0).rstrip(".,;:!?)")

                # Find the full link pattern in original
                link_match = re.search(
                    r"`([^`<>]+)\s*<" + re.escape(url) + r">`__?",
                    replacement.original_line,
                )
                if link_match:
                    orig_full = link_match.group(0)
                    orig_pos = replacement.original_line.find(orig_full)
                    before = replacement.original_line[:orig_pos]
                    after = replacement.original_line[orig_pos + len(orig_full):]

                    # Original: `text <URL>`_ with background on syntax, foreground on text
                    orig_highlighted = f"{RED_BG}`{RESET}{RED}{replacement.preserved_text} {RESET}{RED_BG}<{url}>`__{RESET}" if orig_full.endswith("__") else f"{RED_BG}`{RESET}{RED}{replacement.preserved_text} {RESET}{RED_BG}<{url}>`_{RESET}"

                    # Find the replacement pattern
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

                        # Replacement: :domain:role:`text <target>` with background on syntax, foreground on text
                        rep_highlighted = f"{GREEN_BG}:{domain_role}:`{RESET}{GREEN}{replacement.preserved_text} {RESET}{GREEN_BG}<{target}>`{RESET}"

                        after_with_color = f"{RED}{after}" if after else ""
                        rep_after_with_color = f"{GREEN}{rep_after}" if rep_after else ""

                        print(f"     {RED}- {before}{orig_highlighted}{after_with_color}{RESET}")
                        print(f"     {GREEN}+ {rep_before}{rep_highlighted}{rep_after_with_color}{RESET}")

                        # Print context after
                        if replacement.context_after is not None:
                            print(f"       {replacement.context_after}")

                        print()
                        continue

        # Fallback to simple highlighting
        # Print context before if it exists and hasn't changed
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
            print(
                f"     {GREEN}+ {rep_before}{GREEN_BG}{rep_text}{RESET}{rep_after_with_color}{RESET}"
            )

        # Print context after
        if replacement.context_after is not None:
            print(f"       {replacement.context_after}")

        print()

    if not found_any:
        print("No URLs found that can be replaced with Sphinx references")


def clear_cache() -> None:
    """Clear the intersphinx inventory cache for the current version."""
    if not _are_dependencies_available():
        return

    cache_dir = _get_cache_dir()

    if cache_dir.exists():
        for item in cache_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                print(f"Warning: Could not remove {item}: {e}")

    print("Cache cleared successfully")


def get_info() -> dict[str, str]:
    """
    Get information about the intersphinx-registry installation.

    Returns
    -------
    dict[str, str]
        Dictionary containing version and cache location
    """
    info = {
        "version": __version__,
    }

    try:
        cache_dir = _get_cache_dir()
        info["cache_location"] = str(cache_dir)
    except Exception:
        info["cache_location"] = "N/A (dependencies not installed)"

    return info


def print_info() -> None:
    """Print information about the intersphinx-registry installation."""
    info = get_info()

    cache_location = _compress_user_path(info["cache_location"])

    print("Intersphinx Registry Information")
    print("=" * 50)
    print(f"Version:               {info['version']}")
    print(f"Cache location:        {cache_location}")

    try:
        registry_file_path = Path(__file__).parent / "registry.json"
        registry = json.loads(registry_file_path.read_bytes())
        print(f"Packages in registry:  {len(registry)}")
    except Exception as e:
        print(f"Packages in registry:  Error reading registry ({e})")


def _are_dependencies_available() -> bool:
    """
    Check if CLI dependencies are missing or not.
    Returns True if all dependencies are available, False otherwise.
    """
    missing = []
    try:
        import sphinx  # noqa: F401
    except ModuleNotFoundError:
        missing.append("sphinx")

    try:
        import requests  # noqa: F401
    except ModuleNotFoundError:
        missing.append("requests")

    try:
        import requests_cache  # noqa: F401
    except ModuleNotFoundError:
        missing.append("requests-cache")

    try:
        import platformdirs  # noqa: F401
    except ModuleNotFoundError:
        missing.append("platformdirs")

    if missing:
        print(
            "ERROR: the lookup functionality requires additional dependencies.",
            file=sys.stderr,
        )
        print(
            "Please install with: pip install 'intersphinx_registry[cli]'",
            file=sys.stderr,
        )
        print(f"Missing dependencies: {', '.join(missing)}", file=sys.stderr)
        return False

    return True


def lookup_packages(packages_str: str, search_term: Optional[str] = None):
    """
    Look up intersphinx targets for specified packages.

    Parameters
    ----------
    packages_str : str
        Comma-separated list of package names
    search_term : str, optional
        Search term to filter results
    """
    if not _are_dependencies_available():
        return

    packages = set(packages_str.split(","))

    _install_cache()

    urls = [
        (u[0], (u[1] if u[1] else "objects.inv"))
        for u in get_intersphinx_mapping(packages=packages).values()
    ]

    flattened = []
    for base_url, obj in urls:
        final_url = urljoin(base_url, obj)

        resp = requests.get(final_url)

        inv = InventoryFile.load(BytesIO(resp.content), base_url, urljoin)

        for key, v in inv.items():
            inv_entries = sorted(v.items())
            for entry, (_proj, _ver, url_path, display_name) in inv_entries:
                # display_name = display_name * (display_name != '-')
                flattened.append((key, entry, _proj, _ver, display_name, url_path))

    filtered = []

    width = [len(x) for x in flattened[0]]

    for item in flattened:
        key, entry, proj, version, display_name, url_path = item
        if (
            (search_term is None)
            or (search_term in entry)
            or (search_term in display_name)
            or (search_term in url_path)
        ):
            filtered.append((key, entry, proj, version, display_name, url_path))
            width = [max(w, len(x)) for w, x in zip(width, item)]

    for key, entry, proj, version, display_name, url_path in filtered:
        w_key, w_entry, w_proj, w_version, w_di, w_url = width
        print(
            f"{key:<{w_key}}  {entry:<{w_entry}}  {proj:<{w_proj}}  "
            f"{version:<{w_version}}  {display_name!r:<{w_di + 2}}  {url_path}"
        )


if __name__ == "__main__":
    if len(sys.argv) not in [2, 3]:
        sys.exit(
            """Usage: python -m intersphinx_registry.lookup <package>[,package] [search_term]

        Example:

        $ python -m intersphinx_registry.lookup numpy,scipy array
        $ python -m intersphinx_registry.lookup ipython formatters.html

        """
        )

    packages_str = sys.argv[1]
    search_term = sys.argv[2] if len(sys.argv) == 3 else None

    try:
        lookup_packages(packages_str, search_term)
    except Exception as e:
        sys.exit(f"ERROR: {e}")
