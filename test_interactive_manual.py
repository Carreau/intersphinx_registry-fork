#!/usr/bin/env python3
"""
Manual test script for interactive mode.

Run this script to test the interactive functionality:
    python3 test_interactive_manual.py

This will test the rev-search command in interactive mode on the ex.rst file.
You will be prompted to make decisions about URL replacements.
"""

import subprocess
import sys


def main():
    print("=" * 60)
    print("Manual Test: Interactive Mode for rev-search")
    print("=" * 60)
    print()
    print("This test will run:")
    print("  python3 -m intersphinx_registry rev-search ex.rst -i")
    print()
    print("You should see:")
    print("  1. The diff for the setuptools URL")
    print("  2. A prompt: [s]kip, [r]eplace, [q]uit?")
    print("  3. After pressing 'r', the file should be modified")
    print()
    input("Press Enter to start the test...")
    print()

    # Run the interactive command
    result = subprocess.run(
        [sys.executable, "-m", "intersphinx_registry", "rev-search", "ex.rst", "-i"],
        cwd="/home/ubuntu/dev/intersphinx_registry",
    )

    if result.returncode == 0:
        print()
        print("=" * 60)
        print("Test completed successfully!")
        print("=" * 60)
        print()
        print("Check if ex.rst was modified:")
        subprocess.run(["cat", "ex.rst"])
    else:
        print()
        print("Test failed with return code:", result.returncode)


if __name__ == "__main__":
    main()
