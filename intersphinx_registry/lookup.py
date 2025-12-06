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
    ["url", "package", "domain", "rst_entry", "display_name", "is_fuzzy_match"],
)

UrlReplacement = namedtuple(
    "UrlReplacement",
    ["filepath", "line_num", "original_line", "replacement_line", "context_before", "context_after"],
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
        url, package, domain, rst_entry, display_name, is_fuzzy_match
    """
    _install_cache()

    registry_file = Path(__file__).parent / "registry.json"
    registry = json.loads(registry_file.read_bytes())

    package_urls: dict[str, list[tuple[str, str, str | None]]] = {}

    for url_str in urls:
        base_str = url_str
        if url_str.endswith("/index.html"):
            url_str_index = url_str
            url_str = url_str[:-10]  # remove index.html, keep /
        elif url_str.endswith("/"):
            url_str_index = url_str + "index.html"
        else:
            url_str_index = None

        matched = False
        for package, (base_url, obj_path) in registry.items():
            if url_str.startswith(base_url):
                package_urls.setdefault(package, []).append(
                    (base_str, url_str, url_str_index)
                )
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
                        package_urls.setdefault(package, []).append(
                            (base_str, url_str, url_str_index)
                        )
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
            for base_str, url_str, url_str_index in url_list:
                results.append(
                    ReverseLookupResult(url_str, package, None, None, None, False)
                )
            continue

        inv_urls = {}
        for key, v in inv.items():
            for entry, item in v.items():
                inv_urls[item.uri] = (key, entry, item.display_name)

        for base_str, url_str, url_str_index in url_list:
            found = False

            for check_url in [url_str, url_str_index]:
                if check_url and check_url in inv_urls:
                    key, entry, display_name = inv_urls[check_url]
                    results.append(
                        ReverseLookupResult(
                            base_str, package, key, entry, display_name, False
                        )
                    )
                    found = True
                    break

            if not found:
                results.append(
                    ReverseLookupResult(url_str, package, None, None, None, False)
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
    original_line: str, url: str, rst_ref: str, rst_entry: str
) -> str:
    """
    Compute the replacement line for a URL in an RST file.

    Parameters
    ----------
    original_line : str
        The original line containing the URL
    url : str
        The URL to replace
    rst_ref : str
        The RST reference to replace with (e.g., :py:module:`python:os`)
    rst_entry : str
        The entry name from intersphinx

    Returns
    -------
    str
        The replacement line
    """
    # Check for full RST link with custom text: `text <URL>`_
    full_link_match = re.search(
        r"`([^`<>]+)\s*<" + re.escape(url) + r">`_", original_line
    )
    if full_link_match:
        # Preserve the custom link text
        link_text = full_link_match.group(1).strip()
        original_text = full_link_match.group(0)
        replacement = f"`{link_text} <{rst_ref}>`_"
        return original_line.replace(original_text, replacement)

    # Check for simple RST link: <URL>`_
    simple_link_match = re.search(r"<" + re.escape(url) + r">`_", original_line)
    if simple_link_match:
        # Use rst_entry as link text
        original_text = simple_link_match.group(0)
        replacement = f"`{rst_entry} <{rst_ref}>`_"
        return original_line.replace(original_text, replacement)

    # Plain URL - wrap it with link using rst_entry as text
    replacement = f"`{rst_entry} <{rst_ref}>`_"
    return original_line.replace(url, replacement)


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
            (
                result.url,
                result.package,
                result.domain,
                result.rst_entry,
                result.display_name,
                url_locations[result.url],
            )
            for result in results
            if result.rst_entry is not None
        ]

        if not replaceable:
            continue

        filepath = str(rst_file)

        for (
            url,
            package,
            domain_role,
            rst_entry,
            display_name,
            line_infos,
        ) in replaceable:
            rst_ref = f":{domain_role}:`{package}:{rst_entry}`"

            for line_num, original_line in line_infos:
                replacement_line = _compute_replacement(
                    original_line, url, rst_ref, rst_entry
                )

                # Get context lines (1 before and 1 after)
                context_before = all_lines[line_num - 2].rstrip() if line_num > 1 else None
                context_after = all_lines[line_num].rstrip() if line_num < len(all_lines) else None

                yield UrlReplacement(
                    filepath, line_num, original_line, replacement_line, context_before, context_after
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

        # Print context before
        if replacement.context_before is not None:
            print(f"       {replacement.context_before}")

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
            after = replacement.original_line[url_pos + len(url) :]

            original_text = url
            link_match = re.search(
                r"`([^`<>]+)\s*<" + re.escape(url) + r">`_",
                replacement.original_line,
            )
            simple_link_match = re.search(
                r"<" + re.escape(url) + r">`_", replacement.original_line
            )

            if link_match:
                original_text = link_match.group(0)
                url_pos = replacement.original_line.find(original_text)
                before = replacement.original_line[:url_pos]
                after = replacement.original_line[url_pos + len(original_text) :]
            elif simple_link_match:
                original_text = simple_link_match.group(0)
                url_pos = replacement.original_line.find(original_text)
                before = replacement.original_line[:url_pos]
                after = replacement.original_line[url_pos + len(original_text) :]

            rep_match = re.search(
                r"https?://[^\s<>\"{}|\\^`\[\]]+", replacement.replacement_line
            )
            if rep_match:
                rep_url = rep_match.group(0).rstrip(".,;:!?)")
                rep_pos = replacement.replacement_line.find(rep_url)
                rep_before = replacement.replacement_line[:rep_pos]
                rep_after = replacement.replacement_line[rep_pos + len(rep_url) :]
                rep_text = rep_url
            else:
                rst_ref_match = re.search(
                    r":\w+:\w+:`[^`]+`", replacement.replacement_line
                )
                if rst_ref_match:
                    rep_text = rst_ref_match.group(0)
                    rep_pos = replacement.replacement_line.find(rep_text)
                    rep_before = replacement.replacement_line[:rep_pos]
                    rep_after = replacement.replacement_line[rep_pos + len(rep_text) :]
                else:
                    link_rep_match = re.search(
                        r"`[^`]+<:\w+:\w+:`[^`]+`>`_", replacement.replacement_line
                    )
                    if link_rep_match:
                        rep_text = link_rep_match.group(0)
                        rep_pos = replacement.replacement_line.find(rep_text)
                        rep_before = replacement.replacement_line[:rep_pos]
                        rep_after = replacement.replacement_line[
                            rep_pos + len(rep_text) :
                        ]
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
