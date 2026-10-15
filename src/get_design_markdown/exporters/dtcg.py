"""W3C Design Tokens Community Group format (Format Module 2025.10) helpers.

Value builders used by tokens.py and a small structural validator (`validate_tokens`) that
checks what a DTCG consumer relies on: legal names, every token having an effective `$type`,
values matching their type, and every `{group.token}` alias resolving to a token of the
expected type without cycles. The validator is pure and also used by the tests.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping
from typing import Any

from get_design_markdown.analyzer.css_values import RGBA

#: Reverse-domain key of this tool inside "$extensions".
EXTENSION_KEY = "dev.get-design-markdown"
FORMAT_VERSION = "2025.10"

#: DTCG fontWeight keyword aliases.
FONT_WEIGHT_KEYWORDS: dict[str, int] = {
    "thin": 100,
    "hairline": 100,
    "extra-light": 200,
    "ultra-light": 200,
    "light": 300,
    "normal": 400,
    "regular": 400,
    "book": 400,
    "medium": 500,
    "semi-bold": 600,
    "demi-bold": 600,
    "bold": 700,
    "extra-bold": 800,
    "ultra-bold": 800,
    "black": 900,
    "heavy": 900,
    "extra-black": 950,
    "ultra-black": 950,
}
#: Token name used for each standard weight (first keyword of the spec list).
WEIGHT_NAMES: dict[int, str] = {
    100: "thin",
    200: "extra-light",
    300: "light",
    400: "regular",
    500: "medium",
    600: "semi-bold",
    700: "bold",
    800: "extra-bold",
    900: "black",
    950: "extra-black",
}
COLOR_SPACES = frozenset(
    {
        "srgb",
        "srgb-linear",
        "hsl",
        "hwb",
        "lab",
        "lch",
        "oklab",
        "oklch",
        "display-p3",
        "a98-rgb",
        "prophoto-rgb",
        "rec2020",
        "xyz-d65",
        "xyz-d50",
    }
)
DIMENSION_UNITS = frozenset({"px", "rem"})
TOKEN_PROPERTIES = frozenset({"$value", "$type", "$description", "$extensions", "$deprecated"})
GROUP_PROPERTIES = frozenset({"$type", "$description", "$extensions", "$deprecated", "$extends"})
KNOWN_TYPES = frozenset(
    {
        "color",
        "dimension",
        "fontFamily",
        "fontWeight",
        "duration",
        "cubicBezier",
        "number",
        "strokeStyle",
        "border",
        "transition",
        "shadow",
        "gradient",
        "typography",
    }
)
TYPOGRAPHY_FIELDS: dict[str, str] = {
    "fontFamily": "fontFamily",
    "fontSize": "dimension",
    "fontWeight": "fontWeight",
    "letterSpacing": "dimension",
    "lineHeight": "number",
}
SHADOW_FIELDS: dict[str, str] = {
    "color": "color",
    "offsetX": "dimension",
    "offsetY": "dimension",
    "blur": "dimension",
    "spread": "dimension",
}

_ALIAS_RE = re.compile(r"^\{([^{}]+)\}$")
_HEX6_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME_DROP_RE = re.compile(r"[^a-z0-9-]+")

# ─── Value builders ──────────────────────────────────────────────────────────


def number(value: float, *, digits: int = 4) -> int | float:
    """JSON number: integral values as int (16.0 -> 16), others rounded."""
    rounded = round(float(value), digits)
    if rounded == 0:
        return 0
    return int(rounded) if rounded.is_integer() else rounded


def color_value(color: RGBA) -> dict[str, Any]:
    """{"colorSpace": "srgb", "components": [r, g, b], "alpha": a, "hex": "#rrggbb"}."""
    return {
        "colorSpace": "srgb",
        "components": [number(channel / 255.0) for channel in (color.r, color.g, color.b)],
        "alpha": number(color.alpha, digits=3),
        "hex": color.hex,
    }


def dimension(px: float) -> dict[str, Any]:
    return {"value": number(px), "unit": "px"}


def alias(*path: str) -> str:
    """alias("color", "primary") -> "{color.primary}"."""
    return "{" + ".".join(path) + "}"


def token_name(text: str, *, fallback: str = "token") -> str:
    """Any text -> a legal, kebab-case DTCG name (never "$...", never "{", "}" or ".")."""
    slug = _NAME_DROP_RE.sub("-", text.lower()).strip("-")
    return slug or fallback


def weight_token_name(weight: int) -> str:
    return WEIGHT_NAMES.get(weight, f"weight-{weight}")


def is_alias(value: Any) -> bool:
    return isinstance(value, str) and _ALIAS_RE.match(value) is not None


# ─── Validator ───────────────────────────────────────────────────────────────


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def iter_tokens(
    node: Mapping[str, Any], path: tuple[str, ...] = (), inherited: str | None = None
) -> Iterator[tuple[tuple[str, ...], Mapping[str, Any], str | None]]:
    """(path, token, inherited group $type) for every token of a DTCG document."""
    group_type = node.get("$type", inherited) if isinstance(node.get("$type"), str) else inherited
    for key, child in node.items():
        if key.startswith("$") or not isinstance(child, Mapping):
            continue
        if "$value" in child:
            yield (*path, key), child, group_type
        else:
            yield from iter_tokens(child, (*path, key), group_type)


class _Validator:
    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = document
        self.problems: list[str] = []
        self.tokens: dict[tuple[str, ...], tuple[Mapping[str, Any], str | None]] = {
            path: (token, inherited) for path, token, inherited in iter_tokens(document)
        }
        self._types: dict[tuple[str, ...], str | None] = {}

    def problem(self, path: tuple[str, ...] | str, message: str) -> None:
        where = ".".join(path) if isinstance(path, tuple) else path
        self.problems.append(f"{where or '(root)'}: {message}")

    # Structure ---------------------------------------------------------------

    def check_structure(self, node: Mapping[str, Any], path: tuple[str, ...]) -> None:
        is_token = "$value" in node
        allowed = TOKEN_PROPERTIES if is_token else GROUP_PROPERTIES
        for key, child in node.items():
            if key.startswith("$"):
                if key not in allowed:
                    self.problem(path, f"unknown property {key!r}")
                continue
            if any(char in key for char in "{}."):
                self.problem((*path, key), "name contains '{', '}' or '.'")
            if is_token:
                self.problem(path, f"token has a child {key!r}")
                continue
            if not isinstance(child, Mapping):
                self.problem((*path, key), "member is neither a token nor a group")
                continue
            self.check_structure(child, (*path, key))
        if "$type" in node and node["$type"] not in KNOWN_TYPES:
            self.problem(path, f"unknown $type {node['$type']!r}")
        if "$description" in node and not isinstance(node["$description"], str):
            self.problem(path, "$description must be a string")
        if "$extensions" in node and not isinstance(node["$extensions"], Mapping):
            self.problem(path, "$extensions must be an object")

    # Types & aliases ---------------------------------------------------------

    def resolve(self, reference: str) -> tuple[str, ...] | None:
        match = _ALIAS_RE.match(reference)
        if match is None:
            return None
        target = tuple(match.group(1).split("."))
        return target if target in self.tokens else None

    def effective_type(
        self, path: tuple[str, ...], seen: frozenset[tuple[str, ...]] = frozenset()
    ) -> str | None:
        if path in self._types:
            return self._types[path]
        token, inherited = self.tokens[path]
        explicit = token.get("$type")
        result: str | None
        if isinstance(explicit, str):
            result = explicit
        elif is_alias(token.get("$value")):
            target = self.resolve(token["$value"])
            result = (
                None
                if target is None or target in seen
                else self.effective_type(target, seen | {path})
            )
        else:
            result = inherited
        self._types[path] = result
        return result

    def check_alias(self, path: tuple[str, ...], reference: str, expected: str | None) -> bool:
        """Report a dangling, circular or mistyped alias; True when the alias is sound.

        expected=None only checks that the alias chain ends on an explicit value."""
        target = self.resolve(reference)
        if target is None:
            self.problem(path, f"alias {reference} does not resolve to a token")
            return False
        chain = {path}
        current = target
        while True:
            if current in chain:
                self.problem(path, f"circular alias {reference}")
                return False
            chain.add(current)
            value = self.tokens[current][0].get("$value")
            if not isinstance(value, str) or not is_alias(value):
                break
            following = self.resolve(value)
            if following is None:
                self.problem(path, f"alias chain {reference} breaks at {'.'.join(current)}")
                return False
            current = following
        if expected is None:
            return True
        actual = self.effective_type(target)
        if actual != expected:
            self.problem(path, f"alias {reference} has type {actual!r}, expected {expected!r}")
            return False
        return True

    def check_value(self, path: tuple[str, ...], value: Any, kind: str) -> None:
        if is_alias(value):
            self.check_alias(path, value, kind)
            return
        checkers = {
            "color": self._check_color,
            "dimension": self._check_dimension,
            "fontFamily": self._check_font_family,
            "fontWeight": self._check_font_weight,
            "number": self._check_number,
            "shadow": self._check_shadow,
            "typography": self._check_typography,
        }
        checker = checkers.get(kind)
        if checker is not None:
            checker(path, value)

    def _check_color(self, path: tuple[str, ...], value: Any) -> None:
        if not isinstance(value, Mapping):
            self.problem(path, "color value must be an object")
            return
        space = value.get("colorSpace")
        components = value.get("components")
        if space not in COLOR_SPACES:
            self.problem(path, f"unknown colorSpace {space!r}")
        if not isinstance(components, list) or len(components) != 3:
            self.problem(path, "color components must be a list of 3")
        else:
            for component in components:
                if component == "none":
                    continue
                if not _is_number(component):
                    self.problem(path, f"color component {component!r} is not a number")
                elif space == "srgb" and not 0 <= component <= 1:
                    self.problem(path, f"srgb component {component!r} outside 0..1")
        alpha = value.get("alpha", 1)
        if not _is_number(alpha) or not 0 <= alpha <= 1:
            self.problem(path, f"alpha {alpha!r} outside 0..1")
        hex_value = value.get("hex")
        if hex_value is not None and (
            not isinstance(hex_value, str) or not _HEX6_RE.match(hex_value)
        ):
            self.problem(path, f"hex {hex_value!r} is not #rrggbb")
        extra = set(value) - {"colorSpace", "components", "alpha", "hex"}
        if extra:
            self.problem(path, f"unknown color fields {sorted(extra)}")

    def _check_dimension(self, path: tuple[str, ...], value: Any) -> None:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"value", "unit"}
            or not _is_number(value.get("value"))
            or value.get("unit") not in DIMENSION_UNITS
        ):
            self.problem(path, f"invalid dimension {value!r}")

    def _check_font_family(self, path: tuple[str, ...], value: Any) -> None:
        if isinstance(value, str) and value.strip():
            return
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, str) and item.strip() for item in value)
        ):
            return
        self.problem(path, f"invalid fontFamily {value!r}")

    def _check_font_weight(self, path: tuple[str, ...], value: Any) -> None:
        if isinstance(value, str) and value in FONT_WEIGHT_KEYWORDS:
            return
        if _is_number(value) and 1 <= value <= 1000:
            return
        self.problem(path, f"invalid fontWeight {value!r}")

    def _check_number(self, path: tuple[str, ...], value: Any) -> None:
        if not _is_number(value):
            self.problem(path, f"invalid number {value!r}")

    def _check_shadow(self, path: tuple[str, ...], value: Any) -> None:
        layers = value if isinstance(value, list) else [value]
        if not layers:
            self.problem(path, "empty shadow list")
        for layer in layers:
            if is_alias(layer):
                self.check_alias(path, layer, "shadow")
                continue
            if not isinstance(layer, Mapping):
                self.problem(path, "shadow layer must be an object")
                continue
            for field, kind in SHADOW_FIELDS.items():
                if field not in layer:
                    self.problem(path, f"shadow layer misses {field!r}")
                else:
                    self.check_value(path, layer[field], kind)
            if "inset" in layer and not isinstance(layer["inset"], bool):
                self.problem(path, "shadow inset must be a boolean")
            extra = set(layer) - {*SHADOW_FIELDS, "inset"}
            if extra:
                self.problem(path, f"unknown shadow fields {sorted(extra)}")

    def _check_typography(self, path: tuple[str, ...], value: Any) -> None:
        if not isinstance(value, Mapping):
            self.problem(path, "typography value must be an object")
            return
        for field, kind in TYPOGRAPHY_FIELDS.items():
            if field not in value:
                self.problem(path, f"typography misses {field!r}")
            else:
                self.check_value(path, value[field], kind)
        extra = set(value) - set(TYPOGRAPHY_FIELDS)
        if extra:
            self.problem(path, f"unknown typography fields {sorted(extra)}")

    def run(self) -> list[str]:
        if not isinstance(self.document, Mapping):
            return ["(root): document must be an object"]
        if "$value" in self.document:
            self.problem((), "the root must be a group")
        self.check_structure(self.document, ())
        for path, (token, _) in self.tokens.items():
            kind = self.effective_type(path)
            if kind is None:
                value = token["$value"]
                if (
                    not isinstance(value, str)
                    or not is_alias(value)
                    or self.check_alias(path, value, None)
                ):
                    self.problem(path, "token has no effective $type")
                continue
            self.check_value(path, token["$value"], kind)
        return self.problems


def validate_tokens(document: Mapping[str, Any]) -> list[str]:
    """Structural DTCG problems of a token document ([] when valid)."""
    return _Validator(document).run()
