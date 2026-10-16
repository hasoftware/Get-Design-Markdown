"""Access to files shipped inside the package (templates, JS, phrase files, the UI's static/).

Works from a source checkout, an installed wheel and a PyInstaller bundle (the Windows exe),
where the package data is collected under sys._MEIPASS/get_design_markdown/.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import jinja2

PACKAGE = "get_design_markdown"


def is_frozen() -> bool:
    """True inside a PyInstaller bundle (the Windows exe)."""
    return bool(getattr(sys, "frozen", False)) and getattr(sys, "_MEIPASS", None) is not None


def package_dir() -> Path:
    """Filesystem folder holding the package's files.

    src/get_design_markdown in a checkout, site-packages/get_design_markdown for a wheel, and
    sys._MEIPASS/get_design_markdown in a PyInstaller bundle (the spec must collect the package
    data there, e.g. collect_data_files("get_design_markdown")).
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if is_frozen() and bundle is not None:
        return Path(str(bundle)) / PACKAGE
    root = files(PACKAGE)
    if isinstance(root, Path):
        return root
    return Path(__file__).resolve().parent


def resource_traversable(*parts: str) -> Traversable:
    """A packaged file or folder as an importlib Traversable (inside the bundle when frozen)."""
    if is_frozen():
        return package_dir().joinpath(*parts)
    return files(PACKAGE).joinpath(*parts)


def resource_path(*parts: str) -> Path:
    """Filesystem path of a packaged file or folder, e.g. resource_path("ui", "static").

    Raises FileNotFoundError when it does not exist (a wheel or exe built without its data).
    """
    path = package_dir().joinpath(*parts)
    if not path.exists():
        raise FileNotFoundError(f"Packaged resource not found: {path}")
    return path


def resource_text(*parts: str) -> str:
    """Read a packaged text file, e.g. resource_text("collector", "js", "extract.js")."""
    return resource_traversable(*parts).read_text(encoding="utf-8")


def _autoescape(template_name: str | None) -> bool:
    return template_name is not None and ".html" in template_name


def _template_loader() -> jinja2.BaseLoader:
    if is_frozen():
        return jinja2.FileSystemLoader(str(resource_path("templates")))
    return jinja2.PackageLoader(PACKAGE, "templates")


@lru_cache(maxsize=1)
def template_environment() -> jinja2.Environment:
    """Jinja2 environment over src/get_design_markdown/templates/.

    HTML templates (name contains ".html") are autoescaped; Markdown templates are not.
    Undefined variables raise instead of rendering as empty strings.
    """
    return jinja2.Environment(
        loader=_template_loader(),
        autoescape=_autoescape,
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(name: str, **context: Any) -> str:
    """Render a packaged template such as "preview.html.j2"."""
    return template_environment().get_template(name).render(**context)
