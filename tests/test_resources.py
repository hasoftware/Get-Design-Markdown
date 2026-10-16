from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from get_design_markdown import resources


@pytest.fixture
def no_template_cache() -> Iterator[None]:
    resources.template_environment.cache_clear()
    yield None
    resources.template_environment.cache_clear()


def test_package_dir_is_the_source_folder_when_not_frozen() -> None:
    assert not resources.is_frozen()
    folder = resources.package_dir()
    assert folder == Path(resources.__file__).resolve().parent
    assert (folder / "templates").is_dir()


def test_resource_path_finds_folders_and_files() -> None:
    templates = resources.resource_path("templates")
    assert templates.is_dir()
    assert resources.resource_path("collector", "js", "extract.js").is_file()
    assert resources.resource_traversable("templates").is_dir()


def test_resource_path_raises_for_missing_resources() -> None:
    with pytest.raises(FileNotFoundError):
        resources.resource_path("does-not-exist")


def test_frozen_bundle_reads_resources_from_meipass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_template_cache: object
) -> None:
    bundle_pkg = tmp_path / resources.PACKAGE
    (bundle_pkg / "collector" / "js").mkdir(parents=True)
    (bundle_pkg / "collector" / "js" / "extract.js").write_text("// bundled", encoding="utf-8")
    (bundle_pkg / "templates").mkdir()
    (bundle_pkg / "templates" / "hello.md.j2").write_text("Hi {{ who }}", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.is_frozen()
    assert resources.package_dir() == bundle_pkg
    assert resources.resource_path("collector", "js") == bundle_pkg / "collector" / "js"
    assert resources.resource_text("collector", "js", "extract.js") == "// bundled"
    assert resources.render_template("hello.md.j2", who="gdm") == "Hi gdm"


def test_frozen_flag_without_bundle_is_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert not resources.is_frozen()
