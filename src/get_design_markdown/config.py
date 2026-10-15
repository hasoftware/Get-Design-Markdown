"""Runtime settings from environment variables and an optional .env file."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from get_design_markdown import __version__
from get_design_markdown.errors import ConfigError

#: Product token used for robots.txt matching and the HTTP User-Agent.
ROBOTS_AGENT = "get-design-markdown"
USER_AGENT = f"{ROBOTS_AGENT}/{__version__} (+design reference tool)"

DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_MAX_ELEMENTS = 3000
DEFAULT_TIMEOUT_S = 45.0


@dataclass(frozen=True)
class Settings:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    max_elements: int = DEFAULT_MAX_ELEMENTS
    timeout_s: float = DEFAULT_TIMEOUT_S


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(
            f"Biến {name}={raw!r} không phải số nguyên.", hint=f"Ví dụ: {name}={default}"
        ) from None
    if value <= 0:
        raise ConfigError(
            f"Biến {name} phải lớn hơn 0 (đang là {value}).", hint=f"Ví dụ: {name}={default}"
        )
    return value


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(
            f"Biến {name}={raw!r} không phải số.", hint=f"Ví dụ: {name}={default:g}"
        ) from None
    if value <= 0:
        raise ConfigError(
            f"Biến {name} phải lớn hơn 0 (đang là {value:g}).", hint=f"Ví dụ: {name}={default:g}"
        )
    return value


def load_settings(env: Mapping[str, str] | None = None, *, use_dotenv: bool = True) -> Settings:
    """Build settings; values already in the environment win over .env."""
    if env is None:
        if use_dotenv:
            load_dotenv(override=False)
        env = os.environ
    output_dir = env.get("GDM_OUTPUT_DIR", "").strip()
    return Settings(
        output_dir=Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR,
        max_elements=_positive_int(env, "GDM_MAX_ELEMENTS", DEFAULT_MAX_ELEMENTS),
        timeout_s=_positive_float(env, "GDM_TIMEOUT", DEFAULT_TIMEOUT_S),
    )
