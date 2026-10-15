"""Offline font knowledge base: Google Fonts membership, category, free substitutes
(stream: analyzer-foundations, phase GĐ4; used by typography.py).

lookup_font() normalizes the family names found in computed styles before looking them up:
case, accents, quotes, spaces, hyphens and underscores are ignored; "variable"/"var"/"vf"/
"web"/"webfont"/"fallback" suffixes are dropped; Next.js `next/font` names ("__Inter_d65c78")
are unwrapped. So "sohne-var" -> "Söhne", "Inter Variable" / "InterVariable" / "Inter var" ->
"Inter", "__GeistSans_3a0388" -> "Geist", '"SF Pro Display"' -> "SF Pro Display".

Fonts that are not on Google Fonts (proprietary, system or other foundries) carry a few freely
available look-alikes, Google Fonts first. Unknown names get a category guessed from the name
(mono/code -> monospace, serif/times/garamond -> serif, else sans-serif) and generic
substitutes for that category.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

from get_design_markdown.models.analysis import FontCategory


@dataclass(frozen=True)
class FontLookup:
    name: str
    is_google_font: bool = False
    category: FontCategory = "unknown"
    #: Freely available look-alikes, Google Fonts first (empty when the font itself is free).
    substitutes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class _FontEntry:
    name: str
    category: FontCategory
    google: bool
    substitutes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


SANS: Final = "sans-serif"
SERIF: Final = "serif"
MONO: Final = "monospace"
DISPLAY: Final = "display"
HAND: Final = "handwriting"

#: Generic substitutes for fonts the table does not know, per category.
GENERIC_SUBSTITUTES: Final[dict[FontCategory, tuple[str, ...]]] = {
    "sans-serif": ("Inter", "Roboto"),
    "serif": ("Source Serif 4", "Lora"),
    "monospace": ("JetBrains Mono", "Roboto Mono"),
    "display": ("Bricolage Grotesque", "Space Grotesk"),
    "handwriting": ("Caveat", "Kalam"),
    "unknown": ("Inter", "Roboto"),
}

_SYSTEM_SANS: Final = ("Inter", "Roboto")
_SYSTEM_MONO: Final = ("JetBrains Mono", "Roboto Mono")
_SYSTEM_SERIF: Final = ("Source Serif 4", "Lora")


def _google(name: str, category: FontCategory, *aliases: str) -> _FontEntry:
    return _FontEntry(name, category, True, (), aliases)


def _other(
    name: str, category: FontCategory, substitutes: tuple[str, ...], *aliases: str
) -> _FontEntry:
    return _FontEntry(name, category, False, substitutes, aliases)


_ENTRIES: Final[tuple[_FontEntry, ...]] = (
    # ── Google Fonts: sans-serif ──
    _google("Inter", SANS, "Inter Display", "Inter UI"),
    _google("Inter Tight", SANS),
    _google("Roboto", SANS),
    _google("Roboto Flex", SANS),
    _google("Roboto Condensed", SANS),
    _google("Open Sans", SANS),
    _google("Lato", SANS),
    _google("Montserrat", SANS),
    _google("Poppins", SANS),
    _google("Nunito", SANS),
    _google("Nunito Sans", SANS),
    _google("Raleway", SANS),
    _google("Work Sans", SANS),
    _google("DM Sans", SANS),
    _google("Manrope", SANS),
    _google("Plus Jakarta Sans", SANS),
    _google("Space Grotesk", SANS),
    _google("Sora", SANS),
    _google("Outfit", SANS),
    _google("Figtree", SANS),
    _google("Geist", SANS, "Geist Sans"),
    _google("IBM Plex Sans", SANS),
    _google("IBM Plex Sans Condensed", SANS),
    _google("Fira Sans", SANS),
    _google("Source Sans 3", SANS, "Source Sans Pro", "Source Sans"),
    _google("Noto Sans", SANS),
    _google("Noto Sans Display", SANS),
    _google("Rubik", SANS),
    _google("Karla", SANS),
    _google("Instrument Sans", SANS),
    _google("Bricolage Grotesque", SANS),
    _google("Mulish", SANS, "Muli"),
    _google("Hanken Grotesk", SANS),
    _google("Onest", SANS),
    _google("Albert Sans", SANS),
    _google("Urbanist", SANS),
    _google("Lexend", SANS),
    _google("Public Sans", SANS),
    _google("Barlow", SANS),
    _google("Barlow Condensed", SANS),
    _google("Josefin Sans", SANS),
    _google("Quicksand", SANS),
    _google("Cabin", SANS),
    _google("Archivo", SANS),
    _google("Archivo Black", SANS),
    _google("Heebo", SANS),
    _google("Assistant", SANS),
    _google("Hind", SANS),
    _google("Titillium Web", SANS),
    _google("Ubuntu", SANS),
    _google("Exo 2", SANS),
    _google("Kanit", SANS),
    _google("Prompt", SANS),
    _google("Overpass", SANS),
    _google("Red Hat Display", SANS),
    _google("Red Hat Text", SANS),
    _google("Be Vietnam Pro", SANS),
    _google("Schibsted Grotesk", SANS),
    _google("Epilogue", SANS),
    _google("Syne", SANS),
    _google("Unbounded", SANS),
    _google("Golos Text", SANS),
    _google("Libre Franklin", SANS),
    _google("Arimo", SANS),
    _google("PT Sans", SANS),
    _google("Varela Round", SANS),
    _google("Asap", SANS),
    _google("Encode Sans", SANS),
    _google("Atkinson Hyperlegible", SANS),
    _google("Jost", SANS),
    _google("Questrial", SANS),
    _google("Oswald", SANS),
    _google("Noto Sans JP", SANS),
    _google("Noto Sans KR", SANS),
    _google("Noto Sans SC", SANS),
    _google("Wix Madefor Display", SANS),
    _google("Wix Madefor Text", SANS),
    _google("Mona Sans", SANS),
    _google("Hubot Sans", SANS),
    # ── Google Fonts: serif ──
    _google("Playfair Display", SERIF, "Playfair"),
    _google("Merriweather", SERIF),
    _google("Lora", SERIF),
    _google("PT Serif", SERIF),
    _google("Noto Serif", SERIF),
    _google("Source Serif 4", SERIF, "Source Serif Pro", "Source Serif"),
    _google("IBM Plex Serif", SERIF),
    _google("Instrument Serif", SERIF),
    _google("Libre Baskerville", SERIF),
    _google("EB Garamond", SERIF),
    _google("Cormorant Garamond", SERIF),
    _google("Cormorant", SERIF),
    _google("Crimson Text", SERIF),
    _google("Crimson Pro", SERIF),
    _google("DM Serif Display", SERIF),
    _google("DM Serif Text", SERIF),
    _google("Fraunces", SERIF),
    _google("Newsreader", SERIF),
    _google("Literata", SERIF),
    _google("Spectral", SERIF),
    _google("Bitter", SERIF),
    _google("Roboto Serif", SERIF),
    _google("Roboto Slab", SERIF),
    _google("Zilla Slab", SERIF),
    _google("Young Serif", SERIF),
    _google("Gelasio", SERIF),
    _google("Libre Caslon Text", SERIF),
    _google("Cardo", SERIF),
    _google("Alegreya", SERIF),
    _google("Domine", SERIF),
    _google("Arvo", SERIF),
    _google("Prata", SERIF),
    _google("Bodoni Moda", SERIF),
    _google("Tinos", SERIF),
    # ── Google Fonts: monospace ──
    _google("JetBrains Mono", MONO),
    _google("Fira Code", MONO),
    _google("Fira Mono", MONO),
    _google("Roboto Mono", MONO),
    _google("Source Code Pro", MONO),
    _google("IBM Plex Mono", MONO),
    _google("Space Mono", MONO),
    _google("Geist Mono", MONO),
    _google("DM Mono", MONO),
    _google("Ubuntu Mono", MONO),
    _google("Inconsolata", MONO),
    _google("Red Hat Mono", MONO),
    _google("Martian Mono", MONO),
    _google("Azeret Mono", MONO),
    _google("Courier Prime", MONO),
    _google("Noto Sans Mono", MONO),
    _google("Overpass Mono", MONO),
    _google("Anonymous Pro", MONO),
    _google("Cousine", MONO),
    _google("Victor Mono", MONO),
    _google("Sometype Mono", MONO),
    # ── Google Fonts: display & handwriting ──
    _google("Bebas Neue", DISPLAY),
    _google("Anton", DISPLAY),
    _google("Abril Fatface", DISPLAY),
    _google("Lobster", DISPLAY),
    _google("Righteous", DISPLAY),
    _google("Pacifico", HAND),
    _google("Caveat", HAND),
    _google("Kalam", HAND),
    _google("Dancing Script", HAND),
    _google("Permanent Marker", HAND),
    # ── System fonts ──
    _other("system-ui", SANS, _SYSTEM_SANS, "-apple-system", "BlinkMacSystemFont", "ui-sans-serif"),
    _other("SF Pro Display", SANS, _SYSTEM_SANS, "SF Pro", "San Francisco", "SF UI Display"),
    _other("SF Pro Text", SANS, _SYSTEM_SANS, "SF UI Text"),
    _other("Segoe UI", SANS, _SYSTEM_SANS, "Segoe UI Variable", "Segoe"),
    _other("Helvetica Neue", SANS, _SYSTEM_SANS, "HelveticaNeue"),
    _other("Helvetica", SANS, _SYSTEM_SANS),
    _other("Arial", SANS, (*_SYSTEM_SANS, "Arimo")),
    _other("Verdana", SANS, ("Inter", "Noto Sans")),
    _other("Tahoma", SANS, ("Inter", "Noto Sans")),
    _other("Trebuchet MS", SANS, ("Fira Sans", "Inter")),
    _other("Lucida Grande", SANS, _SYSTEM_SANS),
    _other("Calibri", SANS, ("Open Sans", "Inter")),
    _other("Avenir", SANS, ("Nunito Sans", "Figtree")),
    _other("Avenir Next", SANS, ("Nunito Sans", "Figtree")),
    _other("Futura", SANS, ("Jost", "Outfit")),
    _other("Gill Sans", SANS, ("Lato", "Cabin")),
    _other("Georgia", SERIF, _SYSTEM_SERIF),
    _other("Times New Roman", SERIF, (*_SYSTEM_SERIF, "Tinos"), "Times", "TimesNewRoman"),
    _other("Palatino", SERIF, ("EB Garamond", "Lora"), "Palatino Linotype"),
    _other("Garamond", SERIF, ("EB Garamond", "Cormorant Garamond")),
    _other("Baskerville", SERIF, ("Libre Baskerville", "Source Serif 4")),
    _other("SF Mono", MONO, _SYSTEM_MONO, "SFMono", "SFMono-Regular", "SF Mono Regular"),
    _other("ui-monospace", MONO, _SYSTEM_MONO),
    _other("Menlo", MONO, _SYSTEM_MONO),
    _other("Monaco", MONO, _SYSTEM_MONO),
    _other("Consolas", MONO, _SYSTEM_MONO),
    _other("Courier New", MONO, (*_SYSTEM_MONO, "Courier Prime"), "Courier"),
    _other("Cascadia Code", MONO, _SYSTEM_MONO, "Cascadia Mono"),
    _other("Lucida Console", MONO, _SYSTEM_MONO, "Lucida Sans Typewriter"),
    _other("Liberation Mono", MONO, _SYSTEM_MONO),
    _other("DejaVu Sans Mono", MONO, _SYSTEM_MONO),
    # ── Proprietary / other foundries: sans-serif ──
    _other("Söhne", SANS, ("Inter", "Manrope"), "Sohne", "Söhne Web"),
    _other("Söhne Breit", SANS, ("Inter", "Manrope"), "Sohne Breit"),
    _other("Graphik", SANS, ("Inter", "Figtree")),
    _other("Circular", SANS, ("DM Sans", "Plus Jakarta Sans"), "CircularXX", "Circular Std"),
    _other("GT America", SANS, ("Inter", "Work Sans")),
    _other("GT Walsheim", SANS, ("DM Sans",)),
    _other("GT Eesti", SANS, ("Inter", "Figtree")),
    _other("GT Flexa", SANS, ("Space Grotesk", "Inter")),
    _other("GT Standard", SANS, ("Inter", "Work Sans")),
    _other("Aeonik", SANS, ("Manrope", "Inter")),
    _other("Neue Haas Grotesk", SANS, ("Inter",), "NHaasGrotesk", "Neue Haas Grotesk Display"),
    _other("Neue Haas Unica", SANS, ("Inter",)),
    _other("Neue Montreal", SANS, ("Inter",), "PP Neue Montreal"),
    _other("Proxima Nova", SANS, ("Montserrat", "Figtree")),
    _other("Gotham", SANS, ("Montserrat",)),
    _other("Brandon Grotesque", SANS, ("Josefin Sans", "Montserrat")),
    _other("Gilroy", SANS, ("Plus Jakarta Sans", "Manrope")),
    _other("Satoshi", SANS, ("Manrope", "Inter")),
    _other("General Sans", SANS, ("Inter", "Manrope")),
    _other("Switzer", SANS, ("Inter",)),
    _other("Cabinet Grotesk", SANS, ("Space Grotesk", "Bricolage Grotesque")),
    _other("Clash Display", DISPLAY, ("Space Grotesk", "Syne")),
    _other("Basier Circle", SANS, ("DM Sans", "Inter")),
    _other("Founders Grotesk", SANS, ("Inter",)),
    _other("Untitled Sans", SANS, ("Inter",)),
    _other("Suisse Intl", SANS, ("Inter",), "Suisse Int'l", "Suisse International"),
    _other("ABC Diatype", SANS, ("Inter",), "Diatype"),
    _other("ABC Favorit", SANS, ("Space Grotesk", "Inter"), "Favorit"),
    _other("Calibre", SANS, ("Inter", "Figtree")),
    _other("Euclid Circular", SANS, ("DM Sans", "Plus Jakarta Sans"), "Euclid Circular A"),
    _other("Apercu", SANS, ("DM Sans", "Work Sans")),
    _other("Sharp Sans", SANS, ("Plus Jakarta Sans", "Manrope")),
    _other("Styrene", SANS, ("DM Sans", "Inter"), "Styrene A", "Styrene B"),
    _other("Roobert", SANS, ("Inter", "Space Grotesk")),
    _other("Matter", SANS, ("Inter", "Manrope")),
    _other("Whyte", SANS, ("Inter",)),
    _other("TT Norms", SANS, ("Montserrat", "Plus Jakarta Sans"), "TT Norms Pro"),
    _other("Moderat", SANS, ("Manrope", "Inter")),
    _other("Gordita", SANS, ("Poppins", "Plus Jakarta Sans")),
    _other("Lausanne", SANS, ("Inter",)),
    _other("PolySans", SANS, ("Space Grotesk", "Inter")),
    _other("Camphor", SANS, ("Inter", "Open Sans")),
    _other("Akkurat", SANS, ("Inter",)),
    _other("Atlas Grotesk", SANS, ("Inter",)),
    _other("Messina Sans", SANS, ("Inter",)),
    _other("National 2", SANS, ("Inter",)),
    _other("Google Sans", SANS, ("Figtree", "DM Sans"), "Product Sans", "Google Sans Text"),
    _other("Uber Move", SANS, ("Inter",), "UberMove"),
    _other("Airbnb Cereal", SANS, ("Nunito Sans", "Plus Jakarta Sans"), "Cereal"),
    _other("Netflix Sans", SANS, ("Inter",)),
    _other("Shopify Sans", SANS, ("Inter",)),
    _other("Pretendard", SANS, ("Noto Sans KR", "Inter")),
    # ── Proprietary / other foundries: serif ──
    _other("Tiempos", SERIF, _SYSTEM_SERIF, "Tiempos Text", "Tiempos Headline"),
    _other("GT Sectra", SERIF, ("Fraunces", "Source Serif 4")),
    _other("GT Super", SERIF, ("Fraunces", "DM Serif Display")),
    _other("Lyon", SERIF, _SYSTEM_SERIF, "Lyon Text", "Lyon Display"),
    _other("Canela", SERIF, ("Playfair Display", "DM Serif Display")),
    _other("Freight", SERIF, _SYSTEM_SERIF, "Freight Text", "FreightText Pro"),
    _other("Copernicus", SERIF, ("Lora", "Source Serif 4")),
    _other("Recoleta", SERIF, ("Fraunces", "DM Serif Display")),
    _other("Editorial New", SERIF, ("Instrument Serif", "Playfair Display"), "PP Editorial New"),
    # ── Proprietary / other foundries: monospace ──
    _other("Söhne Mono", MONO, ("JetBrains Mono", "IBM Plex Mono"), "Sohne Mono"),
    _other("GT America Mono", MONO, ("JetBrains Mono", "IBM Plex Mono")),
    _other("Berkeley Mono", MONO, _SYSTEM_MONO),
    _other("Operator Mono", MONO, ("JetBrains Mono", "Fira Code")),
    _other("Monaspace", MONO, ("JetBrains Mono", "Fira Code"), "Monaspace Neon"),
    _other("Commit Mono", MONO, ("JetBrains Mono", "Roboto Mono")),
    _other("Input Mono", MONO, ("JetBrains Mono", "Roboto Mono")),
    _other("Hack", MONO, ("JetBrains Mono", "Fira Code")),
    _other("ABC Diatype Mono", MONO, ("JetBrains Mono", "IBM Plex Mono"), "Diatype Mono"),
)

_NEXT_FONT_RE = re.compile(r"^__(.+?)(?:_Fallback)?_[0-9a-f]{5,8}$", re.IGNORECASE)
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_TOKEN_SPLIT_RE = re.compile(r"[\s\-_]+")
#: Trailing name parts that do not change the typeface.
_SUFFIX_TOKENS: Final = frozenset({"variable", "var", "vf", "web", "webfont", "fallback"})
#: Remainders that keep a prefix match on the same typeface ("GT America Standard").
_STYLE_TOKENS: Final = frozenset(
    {
        "standard",
        "std",
        "pro",
        "lt",
        "text",
        "display",
        "regular",
        "book",
        "medium",
        "light",
        "bold",
        "semibold",
        "condensed",
        "extended",
        "compressed",
        "expanded",
        "wide",
        "narrow",
        "a",
        "b",
        "neue",
        "new",
        "headline",
        "deck",
        "micro",
        "ui",
    }
)
_ICON_FONT_RE = re.compile(
    r"icon|awesome|material symbols|glyph|feather|remixicon|lucide|phosphor|dashicons|"
    r"octicons|ionicons|fontello|icomoon|emoji|symbol",
    re.IGNORECASE,
)
_SERIF_HINTS = ("serif", "times", "garamond", "georgia", "baskerville", "caslon", "bodoni")
_MONO_HINTS = ("mono", "code", "courier", "consol", "menlo", "typewriter")
_HAND_HINTS = ("script", "hand", "brush", "marker")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def clean_family_name(family: str) -> str:
    """Display form of a raw family name: quotes, Next.js wrappers and variable-font
    suffixes removed ("__Inter_d65c78" -> "Inter", "sohne-var" -> "sohne")."""
    text = " ".join(family.strip().strip("\"'").split())
    match = _NEXT_FONT_RE.match(text)
    if match is not None:
        text = match.group(1).replace("_", " ")
    tokens = [token for token in _TOKEN_SPLIT_RE.split(text) if token]
    while len(tokens) > 1 and tokens[-1].lower() in _SUFFIX_TOKENS:
        tokens.pop()
    if len(tokens) == 1:
        camel = [part for part in _CAMEL_RE.split(tokens[0]) if part]
        if len(camel) > 1 and camel[-1].lower() in _SUFFIX_TOKENS:
            return "".join(camel[:-1])
        return tokens[0]
    # Keep the original separators when nothing was stripped.
    joined = " ".join(tokens)
    return text if _TOKEN_SPLIT_RE.sub(" ", text).strip() == joined else joined


def _name_tokens(family: str) -> list[str]:
    text = _strip_accents(clean_family_name(family)).replace("'", "")
    tokens: list[str] = []
    for chunk in _TOKEN_SPLIT_RE.split(text):
        tokens.extend(part.lower() for part in _CAMEL_RE.split(chunk) if part)
    while len(tokens) > 1 and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    return tokens


def font_key(family: str) -> str:
    """Normalized lookup key ("Inter Variable" -> "inter", "SF Pro Display" -> "sfprodisplay")."""
    return "".join(_name_tokens(family))


@lru_cache(maxsize=1)
def _table() -> dict[str, _FontEntry]:
    table: dict[str, _FontEntry] = {}
    for entry in _ENTRIES:
        for name in (entry.name, *entry.aliases):
            table.setdefault(font_key(name), entry)
    return table


def _to_lookup(entry: _FontEntry, name: str | None = None) -> FontLookup:
    return FontLookup(
        name=name or entry.name,
        is_google_font=entry.google,
        category=entry.category,
        substitutes=() if entry.google else entry.substitutes,
    )


def _find_entry(tokens: list[str]) -> tuple[_FontEntry, bool] | None:
    """(entry, exact) for the name tokens; prefix matches only across style words."""
    table = _table()
    key = "".join(tokens)
    if key in table:
        return table[key], True
    for cut in range(len(tokens) - 1, 0, -1):
        rest = tokens[cut:]
        if not all(token in _STYLE_TOKENS or token.isdigit() for token in rest):
            continue
        entry = table.get("".join(tokens[:cut]))
        if entry is not None:
            return entry, False
    return None


def guess_category(family: str) -> FontCategory:
    """Category from name hints only (used for unknown fonts)."""
    lowered = _strip_accents(family).lower()
    if any(hint in lowered for hint in _MONO_HINTS):
        return "monospace"
    if "sans" not in lowered and any(hint in lowered for hint in _SERIF_HINTS):
        return "serif"
    if any(hint in lowered for hint in _HAND_HINTS):
        return "handwriting"
    return "sans-serif"


def is_icon_font(family: str) -> bool:
    """Icon/emoji/symbol fonts (Material Icons, Font Awesome, Apple Color Emoji...)."""
    return _ICON_FONT_RE.search(family) is not None


def is_known_font(family: str) -> bool:
    return _find_entry(_name_tokens(family)) is not None


@lru_cache(maxsize=1024)
def lookup_font(family: str) -> FontLookup:
    """Case-insensitive lookup of a family name; unknown fonts get category from the name
    (mono/serif hints) and generic substitutes for that category."""
    tokens = _name_tokens(family)
    found = _find_entry(tokens) if tokens else None
    if found is not None:
        # Exact name, alias, or the typeface plus style words ("GT America Standard").
        return _to_lookup(found[0])
    cleaned = clean_family_name(family) or family.strip()
    category = guess_category(cleaned)
    return FontLookup(
        name=cleaned,
        is_google_font=False,
        category=category,
        substitutes=GENERIC_SUBSTITUTES[category],
    )


def generic_substitutes(category: FontCategory) -> tuple[str, ...]:
    return GENERIC_SUBSTITUTES.get(category, GENERIC_SUBSTITUTES["sans-serif"])
