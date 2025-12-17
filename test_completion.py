#!/usr/bin/env python3
"""
Test script to demonstrate package name completion for the lookup command.

This script shows how the completion function works by providing
package name suggestions based on partial input.
"""

from intersphinx_registry.cli import complete_package_names

def test_completions():
    """Test completion function with various inputs."""

    test_cases = [
        ("num", "numpy-related packages"),
        ("sci", "scipy packages"),
        ("ipy", "IPython packages"),
        ("pan", "pandas package"),
        ("matplot", "matplotlib package"),
        ("", "all packages (first 10)"),
    ]

    print("=" * 70)
    print("Package Name Completion Test")
    print("=" * 70)
    print()

    for incomplete, description in test_cases:
        completions = list(complete_package_names(incomplete))

        if incomplete:
            print(f"Input: '{incomplete}' ({description})")
        else:
            print(f"Input: <empty> ({description})")
            completions = completions[:10]  # Show first 10 for empty input

        if completions:
            print(f"Completions: {', '.join(completions)}")
        else:
            print("Completions: (none)")
        print()

    print("=" * 70)
    print()
    print("To enable shell completion, run:")
    print("  python -m intersphinx_registry --install-completion")
    print()
    print("Or to see the completion script:")
    print("  python -m intersphinx_registry --show-completion bash")
    print("  python -m intersphinx_registry --show-completion zsh")
    print("=" * 70)

if __name__ == "__main__":
    test_completions()
