import sys

from . import get_intersphinx_mapping, _get_all_mappings
from . import __version__
from urllib.parse import urljoin, urlparse

from typing import List, Tuple, Set

from sphinx.util.inventory import InventoryFile
from io import BytesIO

import requests

if len(sys.argv) != 2:
    sys.exit(
        """Usage: python -m intersphinx_registry.inverse_lookup <url>

        Example:

        $ python -m intersphinx_registry.inverse_lookup https://numpy.org/doc/stable/reference/ufuncs.html#ufuncs
        $ python -m intersphinx_registry.inverse_lookup https://numpy.org/doc/stable/reference/ufuncs.html

        Finds which package(s) contain the given URL by matching the domain/base URL
        against the registry, then returns the appropriate :ref: or :doc: reference to use.

        """
    )


def format_reference(key: str, package: str, entry: str) -> str:
    """
    Format a reference based on the inventory key type.
    
    Parameters
    ----------
    key : str
        Inventory key type (e.g., 'std:label', 'std:doc')
    package : str
        Package name
    entry : str
        Entry name (label or doc path)
    
    Returns
    -------
    str
        Formatted reference string
    """
    if key == "std:label":
        return f":ref:`{package}:{entry}`"
    elif key == "std:doc":
        return f":doc:`{package}:{entry}`"
    elif key.startswith("std:"):
        # For other std: types, use :ref: as a fallback
        return f":ref:`{package}:{entry}`"
    else:
        # For custom types, use the key type
        return f":{key.split(':')[0]}:`{package}:{entry}`"


def find_matching_packages(target_url: str, all_mappings: dict) -> Set[str]:
    """
    Find packages whose base URL matches the target URL's domain/path.
    
    Parameters
    ----------
    target_url : str
        The URL to match against
    all_mappings : dict
        All package mappings from the registry
    
    Returns
    -------
    Set[str]
        Set of package names that might match
    """
    parsed_target = urlparse(target_url)
    target_domain = parsed_target.netloc
    target_path = parsed_target.path.rstrip('/')
    
    matching_packages: Set[str] = set()
    
    for package, (base_url, _) in all_mappings.items():
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc
        base_path = parsed_base.path.rstrip('/')
        
        # Match if domains match and target path starts with base path
        if target_domain == base_domain:
            if target_path.startswith(base_path) or base_path.startswith(target_path):
                matching_packages.add(package)
    
    return matching_packages


def main():
    print(f"Intersphinx-registry version {__version__}")

    target_url = sys.argv[1]
    
    # Get all mappings to find matching packages
    all_mappings = _get_all_mappings()
    
    # Find packages that might match based on URL domain/path
    matching_packages = find_matching_packages(target_url, all_mappings)
    
    if not matching_packages:
        print(f"No matching packages found for URL: {target_url}")
        print("\nTip: Make sure the URL is from a package's documentation in the registry.")
        sys.exit(1)
    
    print(f"Found {len(matching_packages)} potential package(s): {', '.join(sorted(matching_packages))}")
    print(f"Searching for: {target_url}\n")

    # Get intersphinx mappings for matching packages
    try:
        mapping = get_intersphinx_mapping(packages=matching_packages)
    except ValueError as e:
        sys.exit(str(e))

    matches: List[Tuple[str, str, str, str, str, str]] = []  # (package, key, entry, display_name, ref_format, url)

    for package, (base_url, obj) in mapping.items():
        final_url = urljoin(base_url, obj if obj else "objects.inv")

        try:
            resp = requests.get(final_url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"Warning: Could not fetch inventory for {package}: {e}", file=sys.stderr)
            continue

        try:
            inv = InventoryFile.load(BytesIO(resp.content), base_url, urljoin)
        except Exception as e:
            print(f"Warning: Could not parse inventory for {package}: {e}", file=sys.stderr)
            continue

        for key, entries in inv.items():
            for entry, (_proj, _ver, url_path, display_name) in entries.items():
                # Construct full URL
                full_url = urljoin(base_url, url_path)
                
                # Normalize URLs for comparison (remove trailing slashes, handle fragments)
                normalized_target = target_url.rstrip('/')
                normalized_full = full_url.rstrip('/')
                
                # Check for exact match
                if normalized_target == normalized_full:
                    ref_format = format_reference(key, package, entry)
                    matches.append((package, key, entry, display_name, ref_format, full_url))
                # Check if target URL contains the full URL or vice versa
                # (handles cases with fragments, query params, etc.)
                elif normalized_target.startswith(normalized_full) or normalized_full.startswith(normalized_target):
                    ref_format = format_reference(key, package, entry)
                    matches.append((package, key, entry, display_name, ref_format, full_url))

    if not matches:
        print(f"No matches found for URL: {target_url}")
        print(f"\nSearched in packages: {', '.join(sorted(matching_packages))}")
        print("\nTip: The URL might not be in the objects.inv inventory, or the URL format might differ.")
        sys.exit(1)

    # Print results
    print(f"\nFound {len(matches)} match(es):\n")
    
    # Calculate column widths
    if matches:
        widths = [
            max(len(m[0]) for m in matches),  # package
            max(len(m[1]) for m in matches),  # key
            max(len(m[2]) for m in matches),  # entry
            max(len(m[3]) if m[3] else 0 for m in matches),  # display_name
            max(len(m[4]) for m in matches),  # ref_format
            max(len(m[5]) for m in matches),  # url
        ]
    else:
        widths = [10, 10, 10, 20, 20, 50]

    # Print header
    print(f"{'Package':<{widths[0]}}  {'Type':<{widths[1]}}  {'Entry':<{widths[2]}}  {'Display Name':<{widths[3]}}  {'Reference':<{widths[4]}}  {'URL':<{widths[5]}}")
    print("-" * (sum(widths) + 5 * 2))

    # Print matches
    for package, key, entry, display_name, ref_format, url in matches:
        display = display_name if display_name else "-"
        print(f"{package:<{widths[0]}}  {key:<{widths[1]}}  {entry:<{widths[2]}}  {display:<{widths[3]}}  {ref_format:<{widths[4]}}  {url:<{widths[5]}}")


if __name__ == "__main__":
    main()
