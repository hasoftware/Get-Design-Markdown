"""History of analysed sites (ui/history.py read_entry / list_history)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.storage import save_model
from get_design_markdown.ui import history
from get_design_markdown.ui.history import HistoryEntry, list_history, read_entry
from tests.conftest import FIXED_TIME


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    history.clear_cache()
    yield
    history.clear_cache()


def _site(
    root: Path,
    name: str,
    analysis: DesignAnalysis | None = None,
    *,
    files: tuple[str, ...] = (),
) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    if analysis is not None:
        save_model(analysis, folder / "analysis.json")
    for relative in files:
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return folder


def _analysis(
    design_analysis: DesignAnalysis, *, name: str, url: str, at: datetime, pages: list[str]
) -> DesignAnalysis:
    site = design_analysis.site.model_copy(
        update={"name": name, "url": url, "pages_analyzed": pages}
    )
    return design_analysis.model_copy(update={"site": site, "analyzed_at": at})


def _touch(path: Path, when: datetime) -> None:
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


# ─── read_entry ──────────────────────────────────────────────────────────────


def test_full_site_folder(tmp_path: Path, design_analysis: DesignAnalysis) -> None:
    analysis = _analysis(
        design_analysis,
        name="Stripe",
        url="https://stripe.com/",
        at=FIXED_TIME,
        pages=["home", "pricing"],
    )
    folder = _site(
        tmp_path,
        "stripe.com",
        analysis,
        files=(
            "raw.json",
            "DESIGN.md",
            "preview.html",
            "prompt.md",
            "tokens.json",
            "screenshots/home-1440.png",
            "screenshots/home-768.png",
        ),
    )
    assert read_entry(folder) == HistoryEntry(
        site="stripe.com",
        name="Stripe",
        url="https://stripe.com/",
        analyzed_at=FIXED_TIME,
        pages=["home", "pricing"],
        has_raw=True,
        has_analysis=True,
        has_design=True,
        has_preview=True,
        has_prompt=True,
        has_tokens=True,
        thumbnail="screenshots/home-1440.png",
    )


@pytest.mark.parametrize("marker", ["raw.json", "analysis.json", "DESIGN.md"])
def test_any_marker_makes_a_site_folder(tmp_path: Path, marker: str) -> None:
    folder = _site(tmp_path, "example.com", files=(marker,))
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    _touch(folder / marker, when)
    entry = read_entry(folder)
    assert entry is not None
    assert (entry.site, entry.name, entry.url, entry.pages) == (
        "example.com",
        "example.com",
        None,
        [],
    )
    assert entry.analyzed_at == when
    assert (entry.has_raw, entry.has_analysis, entry.has_design) == (
        marker == "raw.json",
        marker == "analysis.json",
        marker == "DESIGN.md",
    )


def test_newest_marker_time_is_used_without_a_readable_analysis(tmp_path: Path) -> None:
    folder = _site(
        tmp_path, "example.com", files=("raw.json", "DESIGN.md", "analysis.json", "tokens.json")
    )
    (folder / "analysis.json").write_text("{broken", encoding="utf-8")
    old, new = datetime(2025, 5, 1, tzinfo=UTC), datetime(2026, 5, 1, tzinfo=UTC)
    _touch(folder / "raw.json", old)
    _touch(folder / "analysis.json", old)
    _touch(folder / "DESIGN.md", new)
    _touch(folder / "tokens.json", new + timedelta(days=9))  # not a marker: ignored
    entry = read_entry(folder)
    assert entry is not None and entry.analyzed_at == new
    assert entry.name == "example.com" and entry.url is None and entry.pages == []
    assert entry.has_analysis


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        '{"site": "nope", "analyzed_at": 5}',
        '{"site": {"url": 1}}',
        "��",
    ],
)
def test_damaged_analysis_never_raises(tmp_path: Path, content: str) -> None:
    folder = _site(tmp_path, "example.com")
    (folder / "analysis.json").write_text(content, encoding="utf-8")
    entry = read_entry(folder)
    assert entry is not None
    assert entry.name == "example.com" and entry.url is None and entry.pages == []
    assert entry.analyzed_at is not None  # file time


def test_partial_analysis_keeps_what_can_be_read(tmp_path: Path) -> None:
    folder = _site(tmp_path, "linear.app")
    data: dict[str, Any] = {
        "schema_version": 999,
        "colors": "a future format",
        "analyzed_at": "2026-03-04T05:06:07",  # naive: taken as UTC
        "site": {
            "url": "https://linear.app/",
            "name": "  Linear  ",
            "pages_analyzed": ["home"],
            "added_later": True,
        },
    }
    (folder / "analysis.json").write_text(json.dumps(data), encoding="utf-8")
    entry = read_entry(folder)
    assert entry is not None
    assert entry.name == "Linear" and entry.url == "https://linear.app/"
    assert entry.pages == ["home"]
    assert entry.analyzed_at == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def test_blank_site_name_falls_back_to_the_folder(
    tmp_path: Path, design_analysis: DesignAnalysis
) -> None:
    analysis = _analysis(
        design_analysis, name="  ", url="https://x.example/", at=FIXED_TIME, pages=[]
    )
    folder = _site(tmp_path, "x.example", analysis)
    entry = read_entry(folder)
    assert entry is not None and entry.name == "x.example"


def test_analysis_time_converts_to_utc(tmp_path: Path) -> None:
    folder = _site(tmp_path, "x.example")
    (folder / "analysis.json").write_text(
        json.dumps({"analyzed_at": "2026-03-04T12:00:00+07:00"}), encoding="utf-8"
    )
    entry = read_entry(folder)
    assert entry is not None and entry.analyzed_at == datetime(2026, 3, 4, 5, 0, tzinfo=UTC)
    assert entry.analyzed_at.tzinfo == UTC


@pytest.mark.parametrize("name", [".hidden", "a b", "ü.example", "-dash.example", "x" * 254])
def test_invalid_folder_names_are_not_sites(tmp_path: Path, name: str) -> None:
    folder = tmp_path / name
    try:
        folder.mkdir()
    except OSError:
        pytest.skip(f"the file system refuses the folder name {name!r}")
    (folder / "DESIGN.md").write_text("# x", encoding="utf-8")
    assert read_entry(folder) is None


def test_dot_dot_is_not_a_site(tmp_path: Path) -> None:
    inner = _site(tmp_path, "inner", files=("DESIGN.md",))
    (tmp_path / "DESIGN.md").write_text("# parent", encoding="utf-8")
    assert read_entry(inner / "..") is None


def test_not_a_site_folder(tmp_path: Path) -> None:
    assert read_entry(tmp_path / "missing.example") is None
    empty = _site(tmp_path, "empty.example", files=("tokens.json", "screenshots/home-1440.png"))
    assert read_entry(empty) is None
    file = tmp_path / "file.example"
    file.write_text("x", encoding="utf-8")
    assert read_entry(file) is None
    odd = _site(tmp_path, "odd.example")
    (odd / "DESIGN.md").mkdir()  # a folder named like the marker
    assert read_entry(odd) is None


@pytest.mark.parametrize(
    ("files", "thumbnail"),
    [
        (("screenshots/home-390.png", "screenshots/home-768.png"), "screenshots/home-768.png"),
        (("screenshots/home-390.png",), "screenshots/home-390.png"),
        (
            ("screenshots/pricing-1440.png", "screenshots/about-1440.png"),
            "screenshots/about-1440.png",
        ),
        (("screenshots/.secret-1440.png", "screenshots/x-768.png"), None),
        ((), None),
    ],
)
def test_thumbnail(tmp_path: Path, files: tuple[str, ...], thumbnail: str | None) -> None:
    folder = _site(tmp_path, "x.example", files=("DESIGN.md", *files))
    entry = read_entry(folder)
    assert entry is not None and entry.thumbnail == thumbnail


def test_raw_json_is_never_read(
    tmp_path: Path, design_analysis: DesignAnalysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _site(tmp_path, "x.example", design_analysis, files=("raw.json",))
    read: list[str] = []
    original_read_text = Path.read_text
    original_open = Path.open

    def spy_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        read.append(self.name)
        return original_read_text(self, *args, **kwargs)

    def spy_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        read.append(self.name)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)
    monkeypatch.setattr(Path, "open", spy_open)
    assert read_entry(folder) is not None
    assert list_history(tmp_path)
    assert "raw.json" not in read and "analysis.json" in read


# ─── list_history ────────────────────────────────────────────────────────────


def test_missing_or_unusable_output_root(tmp_path: Path) -> None:
    assert list_history(tmp_path / "missing") == []
    file = tmp_path / "file"
    file.write_text("x", encoding="utf-8")
    assert list_history(file) == []


def test_newest_first_then_by_folder_name(tmp_path: Path, design_analysis: DesignAnalysis) -> None:
    def add(site: str, at: datetime) -> None:
        _site(
            tmp_path,
            site,
            _analysis(design_analysis, name=site.upper(), url=f"https://{site}/", at=at, pages=[]),
        )

    add("old.example", FIXED_TIME - timedelta(days=3))
    add("b.example", FIXED_TIME)
    add("a.example", FIXED_TIME)
    add("new.example", FIXED_TIME + timedelta(hours=1))
    _site(tmp_path, ".hidden", design_analysis)
    _site(tmp_path, "no-marker.example", files=("tokens.json",))
    (tmp_path / "stray-file.json").write_text("{}", encoding="utf-8")
    entries = list_history(tmp_path)
    assert [entry.site for entry in entries] == [
        "new.example",
        "a.example",
        "b.example",
        "old.example",
    ]
    assert entries[0].name == "NEW.EXAMPLE"


def test_cache_follows_changes(tmp_path: Path, design_analysis: DesignAnalysis) -> None:
    folder = _site(
        tmp_path,
        "x.example",
        _analysis(design_analysis, name="First", url="https://x.example/", at=FIXED_TIME, pages=[]),
        files=("DESIGN.md",),
    )
    first = list_history(tmp_path)
    assert [entry.name for entry in first] == ["First"]
    assert list_history(tmp_path) == first  # served from the cache

    renamed = _analysis(
        design_analysis, name="Second name", url="https://x.example/", at=FIXED_TIME, pages=[]
    )
    save_model(renamed, folder / "analysis.json")
    later = datetime.now(UTC) + timedelta(seconds=5)
    _touch(folder / "analysis.json", later)
    assert [entry.name for entry in list_history(tmp_path)] == ["Second name"]

    (folder / "DESIGN.md").unlink()
    assert not list_history(tmp_path)[0].has_design

    (folder / "screenshots").mkdir()
    (folder / "screenshots" / "home-1440.png").write_bytes(b"png")
    _touch(folder / "screenshots", later + timedelta(seconds=5))
    assert list_history(tmp_path)[0].thumbnail == "screenshots/home-1440.png"

    (folder / "analysis.json").unlink()
    assert list_history(tmp_path) == []


def test_damaged_folders_do_not_break_the_list(
    tmp_path: Path, design_analysis: DesignAnalysis
) -> None:
    _site(tmp_path, "good.example", design_analysis)
    broken = _site(tmp_path, "broken.example")
    (broken / "analysis.json").write_bytes(b"\x00\xff garbage")
    sites = {entry.site for entry in list_history(tmp_path)}
    assert sites == {"good.example", "broken.example"}


# ─── prompt.md of the latest synthesis ───────────────────────────────────────


def _synthesis(folder: Path, data: object) -> None:
    (folder / "synthesis.json").write_text(json.dumps(data), encoding="utf-8")


def test_prompt_left_by_an_earlier_run_does_not_count(tmp_path: Path) -> None:
    folder = _site(tmp_path, "x.example", files=("DESIGN.md", "prompt.md"))
    entry = read_entry(folder)
    assert entry is not None and entry.has_prompt  # no synthesis.json: by presence
    _synthesis(folder, {"mode": "rules", "prompt_path": None, "image_paths": []})
    entry = read_entry(folder)
    assert entry is not None and not entry.has_prompt
    _synthesis(
        folder,
        {
            "mode": "prompt-only",
            "prompt_path": "prompt.md",
            "image_paths": ["prompt-images/desktop.png", 5, "prompt-images\\mobile.png"],
        },
    )
    entry = read_entry(folder)
    assert entry is not None and entry.has_prompt
    assert history.recorded_prompt(folder) == (
        "prompt.md",
        ["prompt-images/desktop.png", "prompt-images/mobile.png"],
    )


@pytest.mark.parametrize("content", ["{broken", "[]", '{"prompt_path": 5}'])
def test_unreadable_synthesis_record_falls_back_to_the_files(tmp_path: Path, content: str) -> None:
    folder = _site(tmp_path, "x.example", files=("DESIGN.md", "prompt.md"))
    (folder / "synthesis.json").write_text(content, encoding="utf-8")
    assert history.recorded_prompt(folder) is None
    entry = read_entry(folder)
    assert entry is not None and entry.has_prompt


def test_cache_follows_the_synthesis_record(tmp_path: Path) -> None:
    folder = _site(tmp_path, "x.example", files=("DESIGN.md", "prompt.md"))
    _synthesis(folder, {"prompt_path": "prompt.md", "image_paths": []})
    assert [entry.has_prompt for entry in list_history(tmp_path)] == [True]
    _synthesis(folder, {"prompt_path": None, "image_paths": [], "lang": "vi-VN"})
    assert [entry.has_prompt for entry in list_history(tmp_path)] == [False]
