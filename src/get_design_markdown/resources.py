"""Access to files shipped inside the package (templates, JS, phrase files)."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from typing import Any

import jinja2

PACKAGE = "get_design_markdown"


def resource_text(*parts: str) -> str:
    """Read a packaged text file, e.g. resource_text("collector", "js", "extract.js")."""
    return files(PACKAGE).joinpath(*parts).read_text(encoding="utf-8")


def _autoescape(template_name: str | None) -> bool:
    return template_name is not None and ".html" in template_name


@lru_cache(maxsize=1)
def template_environment() -> jinja2.Environment:
    """Jinja2 environment over src/get_design_markdown/templates/.

    HTML templates (name contains ".html") are autoescaped; Markdown templates are not.
    Undefined variables raise instead of rendering as empty strings.
    """
    return jinja2.Environment(
        loader=jinja2.PackageLoader(PACKAGE, "templates"),
        autoescape=_autoescape,
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(name: str, **context: Any) -> str:
    """Render a packaged template such as "preview.html.j2"."""
    return template_environment().get_template(name).render(**context)
