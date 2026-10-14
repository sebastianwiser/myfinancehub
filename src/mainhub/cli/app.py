import typer

app = typer.Typer(help="MainHub: personal finance platform.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """MainHub: personal finance platform."""


@app.command()
def version() -> None:
    """Print the MainHub version."""
    from importlib.metadata import version as pkg_version

    typer.echo(pkg_version("mainhub"))
