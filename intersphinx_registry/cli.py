import sys
from typing import List, Optional

import typer

from intersphinx_registry import __version__
from intersphinx_registry.lookup import clear_cache, lookup_packages, print_info
from intersphinx_registry.reverse_lookup import reverse_lookup
from intersphinx_registry.rev_search import rev_search
from intersphinx_registry.utils import _are_dependencies_available

app = typer.Typer(
    name="intersphinx-registry",
    help="Default intersphinx mapping for the Python ecosystem",
    epilog="For more information, see: https://github.com/Quansight-labs/intersphinx_registry",
    no_args_is_help=True,
    add_help_option=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def version_callback(value: bool):
    """Show version and exit."""
    if value:
        typer.echo(f"intersphinx-registry {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "-v",
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
):
    """Default intersphinx mapping for the Python ecosystem."""
    pass


@app.command()
def lookup(
    packages: Optional[str] = typer.Argument(
        None,
        help="Comma-separated list of package names (e.g., numpy,scipy)",
    ),
    search_term: Optional[str] = typer.Argument(
        None,
        help="Optional search term to filter results",
    ),
):
    """Search and lookup intersphinx targets/webpages.

    Examples:

      intersphinx-registry lookup numpy,scipy array

      intersphinx-registry lookup ipython formatters.html
    """
    if not _are_dependencies_available():
        raise typer.Exit(1)

    if not packages:
        typer.echo("Usage: intersphinx-registry lookup <package>[,package] [search_term]\n")
        typer.echo("Examples:")
        typer.echo("  intersphinx-registry lookup numpy,scipy array")
        typer.echo("  intersphinx-registry lookup ipython formatters.html")
        raise typer.Exit(0)

    lookup_packages(packages, search_term)


@app.command(name="reverse-lookup")
def reverse_lookup_command(
    urls: List[str] = typer.Argument(
        None,
        help="URLs to look up (space-separated)",
    ),
):
    """Find which packages documentation URLs belong to.

    Examples:

      intersphinx-registry reverse-lookup https://numpy.org/doc/stable/reference/arrays.html

      intersphinx-registry reverse-lookup https://docs.python.org/3/ https://numpy.org/doc/stable/
    """
    if not urls:
        typer.echo("Error: Missing argument 'URLS...'")
        raise typer.Exit(1)
    reverse_lookup(urls)


@app.command(name="rev-search")
def rev_search_command(
    directory: str = typer.Argument(
        ...,
        help="Directory to search for .rst files",
    ),
    interactive: bool = typer.Option(
        False,
        "-i",
        "--interactive",
        help="Interactively review each URL replacement before applying",
    ),
):
    """Search .rst files for URLs that can be replaced with Sphinx references.

    Examples:

      intersphinx-registry rev-search docs/

      intersphinx-registry rev-search .
    """
    rev_search(directory, interactive=interactive)


@app.command(name="clear-cache")
def clear_cache_command():
    """Clear the intersphinx inventory cache."""
    clear_cache()


@app.command()
def info():
    """Display information about the intersphinx-registry installation.

    Shows version, cache location, registry file location, and package count.
    """
    print_info()


def main():
    """Entry point for the console script."""
    app()


if __name__ == "__main__":
    app()
