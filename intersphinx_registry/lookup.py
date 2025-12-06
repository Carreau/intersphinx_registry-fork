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

ReverseLookupResult = namedtuple(
    "ReverseLookupResult",
    ["url", "package", "domain", "rst_entry", "display_name", "inventory_url"],
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
    if user_url == inv_url:
        return True

    variants = [user_url]
    if user_url.endswith("/index.html"):
        variants.append(user_url[:-10])  # Remove index.html, keep /
    elif user_url.endswith("/"):
        variants.append(user_url + "index.html")
    else:
        variants.append(user_url + "/index.html")

    if inv_url in variants:
        return True

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
                    ReverseLookupResult(url_str, package, None, None, None, None)
                )
            continue

        inv_urls = {}
        for key, v in inv.items():
            for entry, item in v.items():
                inv_urls[item.uri] = (key, entry, item.display_name)

        for url_str in url_list:
            found = False

            for inv_uri, (key, entry, display_name) in inv_urls.items():
                if uri_match(url_str, inv_uri):
                    results.append(
                        ReverseLookupResult(
                            url_str, package, key, entry, display_name, inv_uri
                        )
                    )
                    found = True
                    break

            if not found:
                results.append(
                    ReverseLookupResult(url_str, package, None, None, None, None)
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
    from .rev_search import _compress_user_path

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
