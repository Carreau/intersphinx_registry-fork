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
