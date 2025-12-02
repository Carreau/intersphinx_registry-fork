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

                    # Find the best matching normalized URL
                    best_match = process.extractOne(
                        normalized_query,
                        inv_urls_normalized.keys(),
                        scorer=fuzz.ratio,
                        score_cutoff=90,
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

    # Process files one by one to avoid memory issues
    for rst_file in Path(directory).rglob("*.rst"):
        url_locations: dict[str, list[int]] = {}

        try:
            with open(rst_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    urls = url_pattern.findall(line)
                    for url in urls:
                        url = url.rstrip(".,;:!?)")
                        url_locations.setdefault(url, []).append(line_num)
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

        # Build output rows for this file
        output_rows = []
        for url, package, domain_role, rst_entry, display_name, is_fuzzy, line_nums in replaceable:
            rst_ref = f":{domain_role}:`{package}:{rst_entry}`"
            if is_fuzzy:
                rst_ref += " ~"

            filepath = str(rst_file)
            display_path = filepath.replace(home, "~") if filepath.startswith(home) else filepath

            for line_num in line_nums:
                location = f"{display_path}:{line_num}"
                output_rows.append((location, url, rst_ref))

        # Print results for this file with headers
        header_location = "Location"
        header_url = "URL"
        header_ref = "Sphinx Reference"

        width_location = max(len(header_location), max(len(row[0]) for row in output_rows))
        width_url = max(len(header_url), max(len(row[1]) for row in output_rows))
        width_ref = max(len(header_ref), max(len(row[2]) for row in output_rows))

        print(f"{header_location:<{width_location}}  {header_url:<{width_url}}  {header_ref}")
        print(f"{'-' * width_location}  {'-' * width_url}  {'-' * width_ref}")

        for location, url, rst_ref in output_rows:
            print(f"{location:<{width_location}}  {url:<{width_url}}  {rst_ref}")
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
