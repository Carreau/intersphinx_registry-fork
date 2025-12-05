import json
import os
import re
import sys
import warnings
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
import requests_cache
from sphinx.util.inventory import InventoryFile

from . import get_intersphinx_mapping


def _normalize_url_for_matching(url: str) -> str:
    """
    Normalize URL for fuzzy matching by removing version-specific segments.

    This helps match URLs like:
    - /stable/ vs /latest/ vs /main/
    """
    normalized = re.sub(r'/(latest|stable|main|dev|master)/', '/_VERSION_/', url)
    return normalized


def _do_reverse_lookup(
    urls: list[str],
) -> list[tuple[str, str, str | None, str | None, str | None, bool]]:
    """
    Core reverse lookup logic: given URLs, find which packages they belong to and their rst references.

    Parameters
    ----------
    urls : list[str]
        List of URLs

    Returns
    -------
    list[tuple[str, str, str | None, str | None, str | None, bool]]
        List of tuples: (url, package, domain, rst_entry, display_name, is_fuzzy_match)
    """
    requests_cache.install_cache(
        "intersphinx_cache",
        backend="filesystem",
        expire_after=timedelta(hours=6),
        use_cache_dir=True,
        stale_if_error=True,
        cache_control=True,
    )

    registry_file = Path(__file__).parent / "registry.json"
    registry = json.loads(registry_file.read_bytes())

    # Group URLs by package to avoid downloading inventories multiple times
    # Also track URLs that might need fuzzy matching (same domain, different path)
    package_urls: dict[str, list[tuple[str, str, str | None]]] = {}
    fuzzy_urls: list[tuple[str, str, str | None]] = []

    for url_str in urls:
        base_str = url_str
        if url_str.endswith("/index.html"):
            url_str_index = url_str
            url_str = url_str[:-10]  # remove index.html, keep /
        elif url_str.endswith("/"):
            url_str_index = url_str + "index.html"
        else:
            url_str_index = None

        # Try exact base URL match first
        matched = False
        for package, (base_url, obj_path) in registry.items():
            if url_str.startswith(base_url):
                package_urls.setdefault(package, []).append(
                    (base_str, url_str, url_str_index)
                )
                matched = True
                break

        # If no exact match, check if domain matches and path is similar after normalization
        if not matched:
            url_domain = urlparse(url_str).netloc
            url_path_normalized = _normalize_url_for_matching(urlparse(url_str).path).rstrip('/').replace('/index.html', '')

            for package, (base_url, obj_path) in registry.items():
                base_domain = urlparse(base_url).netloc
                if url_domain == base_domain:
                    base_path_normalized = _normalize_url_for_matching(urlparse(base_url).path).rstrip('/')
                    # Check if normalized paths share a common prefix (domain-level fuzzy match)
                    if url_path_normalized.startswith(base_path_normalized) or base_path_normalized.startswith(url_path_normalized):
                        package_urls.setdefault(package, []).append(
                            (base_str, url_str, url_str_index)
                        )
                        break

    results: list[tuple[str, str, str | None, str | None, str | None, bool]] = []

    # Process each package once, looking up all its URLs
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
                stacklevel=2
            )
            # If inventory fails to load, mark all URLs from this package as not found
            for base_str, url_str, url_str_index in url_list:
                results.append((url_str, package, None, None, None, False))
            continue

        # Build a list of all URLs in the inventory for fuzzy matching
        inv_urls = {}
        inv_urls_normalized = {}
        for key, v in inv.items():
            for entry, item in v.items():
                inv_urls[item.uri] = (key, entry, item.display_name)
                normalized = _normalize_url_for_matching(item.uri)
                inv_urls_normalized[normalized] = item.uri

        # Look up each URL for this package in the inventory
        for base_str, url_str, url_str_index in url_list:
            found = False
            is_fuzzy = False

            # Try exact match first
            for check_url in [url_str, url_str_index]:
                if check_url and check_url in inv_urls:
                    key, entry, display_name = inv_urls[check_url]
                    results.append((base_str, package, key, entry, display_name, False))
                    found = True
                    break

            # If not found, try fuzzy matching with normalized URLs
            if not found:
                try:
                    from rapidfuzz import process, fuzz

                    normalized_query = _normalize_url_for_matching(url_str)

                    # Try multiple fuzzy matching strategies, from most lenient to least
                    best_match = None

                    # Strategy 1: partial_ratio (most lenient - checks if one string is substring of other)
                    best_match = process.extractOne(
                        normalized_query,
                        inv_urls_normalized.keys(),
                        scorer=fuzz.partial_ratio,
                        score_cutoff=70,
                    )

                    # Strategy 2: token_sort_ratio (good for reordered words)
                    if not best_match:
                        best_match = process.extractOne(
                            normalized_query,
                            inv_urls_normalized.keys(),
                            scorer=fuzz.token_sort_ratio,
                            score_cutoff=60,
                        )

                    # Strategy 3: ratio (standard comparison, very lenient cutoff)
                    if not best_match:
                        best_match = process.extractOne(
                            normalized_query,
                            inv_urls_normalized.keys(),
                            scorer=fuzz.ratio,
                            score_cutoff=50,
                        )

                    if best_match:
                        matched_normalized = best_match[0]
                        matched_url = inv_urls_normalized[matched_normalized]
                        key, entry, display_name = inv_urls[matched_url]
                        results.append((base_str, package, key, entry, display_name, True))
                        found = True
                except ImportError:
                    pass

            if not found:
                results.append((url_str, package, None, None, None, False))

    return results


def _print_reverse_lookup_results(
    results: list[tuple[str, str, str | None, str | None, str | None, bool]],
):
    """
    Print formatted reverse lookup results.

    Parameters
    ----------
    results : list[tuple[str, str, str | None, str | None, str | None, bool]]
        List of tuples: (url, package, domain, rst_entry, display_name, is_fuzzy)
    """
    if not results:
        return

    header_url = "URL"
    header_rst = "Sphinx Reference"
    header_display = "Description"

    width_url = max(len(header_url), max(len(r[0]) for r in results))
    width_rst = max(
        len(header_rst),
        max(
            (len(f":{r[2]}:`{r[1]}:{r[3]}`") + (3 if r[5] else 0) if r[3] else len("NOT FOUND"))
            for r in results
        ),
    )
    width_display = max(
        len(header_display), max((len(r[4]) if r[4] else 0) for r in results)
    )

    print(f"{header_url:<{width_url}}  {header_rst:<{width_rst}}  {header_display}")
    print(f"{'-' * width_url}  {'-' * width_rst}  {'-' * width_display}")

    for url_str, package, domain_role, rst_entry, display_name, is_fuzzy in results:
        if rst_entry:
            rst_ref = f":{domain_role}:`{package}:{rst_entry}`"
            if is_fuzzy:
                rst_ref += " ~"
            display = (
                display_name if display_name and display_name != "-" else rst_entry
            )
            print(
                f"{url_str:<{width_url}}  {rst_ref:<{width_rst}}  {display:<{width_display}}"
            )
        elif package:
            print(f"{url_str:<{width_url}}  {'NOT FOUND':<{width_rst}}")


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

    url_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

    home = str(Path.home())
    found_any = False

    # ANSI color codes
    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    RED_BG = "\033[41;37m"  # Red background with white foreground
    GREEN_BG = "\033[42;30m"  # Green background with black foreground
    RESET = "\033[0m"

    # Process files one by one to avoid memory issues
    directory_path = Path(directory)
    if directory_path.is_file():
        rst_files = [directory_path] if directory_path.suffix == ".rst" else []
    else:
        rst_files = directory_path.rglob("*.rst")

    for rst_file in rst_files:
        url_locations: dict[str, list[tuple[int, str]]] = {}

        try:
            with open(rst_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line_num, line in enumerate(lines, start=1):
                    urls = url_pattern.findall(line)
                    for url in urls:
                        url = url.rstrip(".,;:!?)")
                        url_locations.setdefault(url, []).append((line_num, line.rstrip()))
        except Exception as e:
            print(f"Error reading {rst_file}: {e}")
            continue

        if not url_locations:
            continue

        # Do reverse lookup for URLs in this file
        urls = list(url_locations.keys())
        results = _do_reverse_lookup(urls)

        # Filter to only URLs that were found in inventories
        replaceable = [
            (url, package, domain_role, rst_entry, display_name, is_fuzzy, url_locations[url])
            for url, package, domain_role, rst_entry, display_name, is_fuzzy in results
            if rst_entry is not None
        ]

        if not replaceable:
            continue

        if not found_any:
            found_any = True

        filepath = str(rst_file)
        display_path = filepath.replace(home, "~") if filepath.startswith(home) else filepath

        # Print results for this file in diff format
        for url, package, domain_role, rst_entry, display_name, is_fuzzy, line_infos in replaceable:
            rst_ref = f":{domain_role}:`{package}:{rst_entry}`"

            for line_num, original_line in line_infos:
                print(f"{CYAN}{display_path}:{line_num}{RESET}")

                # Find the position of the URL in the line
                url_pos = original_line.find(url)
                if url_pos == -1:
                    # Fallback if URL not found exactly (shouldn't happen)
                    print(f"     {RED}- {original_line}{RESET}")
                    print(f"     {GREEN}+ {original_line.replace(url, rst_ref)}{RESET}")
                else:
                    before = original_line[:url_pos]
                    after = original_line[url_pos + len(url):]

                    # Detect context and generate smart replacement
                    replacement = rst_ref
                    original_text = url

                    # Check if URL is in a RST link: `text <url>`_
                    link_match = re.search(r'`([^`<>]+)\s*<' + re.escape(url) + r'>`_', original_line)
                    if link_match:
                        # Preserve the link text, just replace URL with rst_ref
                        link_text = link_match.group(1).strip()
                        original_text = link_match.group(0)
                        replacement = f"`{link_text} <{rst_ref}>`_"
                        url_pos = original_line.find(original_text)
                        before = original_line[:url_pos]
                        after = original_line[url_pos + len(original_text):]

                    # Check if URL is preceded by '<' but we didn't match the full link pattern
                    # This means it's in some link-like context, so just replace the URL
                    elif before and before[-1] == '<':
                        replacement = rst_ref

                    # Check if URL is inside a role (like :ref:`url` or :doc:`url`)
                    elif re.search(r':\w+:`[^`]*' + re.escape(url), original_line):
                        # Inside a role, just replace the URL
                        replacement = rst_ref

                    # Check if line contains a RST directive (.. directive::)
                    elif re.search(r'\.\.\s+\w+::', original_line):
                        # For directive lines like ".. seealso:: url", just replace URL
                        replacement = rst_ref

                    # Check if URL is bare in text (not in link syntax)
                    else:
                        # Use display name if available, otherwise use the entry name
                        if display_name and display_name != "-":
                            replacement = f"`{display_name} <{rst_ref}>`_"
                        else:
                            replacement = f"`{rst_entry} <{rst_ref}>`_"

                    # Print original line: full line in red, with red background on the URL
                    print(f"     {RED}- {before}{RED_BG}{original_text}{RESET}{RED}{after}{RESET}")
                    # Print suggested line: full line in green, with green background on the replacement
                    print(f"     {GREEN}+ {before}{GREEN_BG}{replacement}{RESET}{GREEN}{after}{RESET}")
                print()

    if not found_any:
        print("No URLs found that can be replaced with Sphinx references")


def clear_cache() -> None:
    """Clear the intersphinx inventory cache."""
    if not _are_dependencies_available():
        return

    import requests_cache

    cache = requests_cache.CachedSession(
        "intersphinx_cache",
        backend="filesystem",
        use_cache_dir=True,
    )
    cache.cache.clear()
    print("Cache cleared successfully")


def get_info() -> dict[str, str]:
    """
    Get information about the intersphinx-registry installation.

    Returns
    -------
    dict[str, str]
        Dictionary containing version and cache location
    """
    from intersphinx_registry import __version__

    info = {
        "version": __version__,
    }

    # Get cache location if dependencies are available
    try:
        import requests_cache
        cache = requests_cache.CachedSession(
            "intersphinx_cache",
            backend="filesystem",
            use_cache_dir=True,
        )
        info["cache_location"] = str(cache.cache.cache_dir)
    except Exception:
        info["cache_location"] = "N/A (dependencies not installed)"

    return info


def print_info() -> None:
    """Print information about the intersphinx-registry installation."""
    info = get_info()

    # Compress paths by replacing home directory with ~
    home = str(Path.home())
    cache_location = info['cache_location']

    if cache_location.startswith(home):
        cache_location = cache_location.replace(home, "~", 1)

    print("Intersphinx Registry Information")
    print("=" * 50)
    print(f"Version:               {info['version']}")
    print(f"Cache location:        {cache_location}")

    # Count packages in registry
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

    import requests
    import requests_cache
    from sphinx.util.inventory import InventoryFile

    packages = set(packages_str.split(","))

    requests_cache.install_cache(
        "intersphinx_cache",
        backend="filesystem",
        expire_after=timedelta(hours=6),
        use_cache_dir=True,
        stale_if_error=True,
        cache_control=True,
    )

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
