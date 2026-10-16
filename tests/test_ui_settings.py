"""Saving and loading the UI settings (ui/settings.py load_ui_settings / save_ui_settings)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from get_design_markdown.errors import ConfigError
from get_design_markdown.ui import settings as ui_settings
from get_design_markdown.ui.settings import (
    UI_SETTINGS_SCHEMA_VERSION,
    UiSettings,
    absolute_output_dir,
    load_ui_settings,
    save_ui_settings,
)

UI_LOGGER = "get_design_markdown.ui"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A fake home folder for relative output_dir values and "~"."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setattr(ui_settings, "_home", lambda: folder)
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    return folder


@pytest.fixture
def defaults(tmp_path: Path) -> UiSettings:
    return UiSettings(output_dir=(tmp_path / "default-output").resolve(), timeout_s=30.0)


def _write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ─── Loading ─────────────────────────────────────────────────────────────────


def test_missing_file_gives_the_defaults(tmp_path: Path, defaults: UiSettings) -> None:
    assert load_ui_settings(tmp_path / "missing" / "ui-settings.json", defaults) == defaults


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"[1, 2, 3]", b'"text"', b"42", b"null", b"\xff\xfe\x00bad", b""],
)
def test_unusable_file_gives_the_defaults_and_a_warning(
    tmp_path: Path,
    defaults: UiSettings,
    caplog: pytest.LogCaptureFixture,
    content: bytes,
) -> None:
    path = tmp_path / "ui-settings.json"
    path.write_bytes(content)
    with caplog.at_level(logging.WARNING, logger=UI_LOGGER):
        assert load_ui_settings(path, defaults) == defaults
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert str(path) in caplog.text
    assert path.read_bytes() == content  # left in place for the user to inspect


def test_unreadable_path_gives_the_defaults(
    tmp_path: Path, defaults: UiSettings, caplog: pytest.LogCaptureFixture
) -> None:
    folder = tmp_path / "ui-settings.json"
    folder.mkdir()  # a folder where the file should be
    with caplog.at_level(logging.WARNING, logger=UI_LOGGER):
        assert load_ui_settings(folder, defaults) == defaults
    assert caplog.records


def test_saved_values_override_the_defaults(
    tmp_path: Path, defaults: UiSettings, caplog: pytest.LogCaptureFixture
) -> None:
    out = (tmp_path / "results").resolve()
    path = _write(
        tmp_path / "ui-settings.json",
        {
            "schema_version": 1,
            "output_dir": str(out),
            "pages": 3,
            "dark": True,
            "lang": "vi",
            "prompt_only": True,
            "examples": True,
            "max_elements": 500,
            "delta_e": 4.5,
            "browser": "msedge",
            "open_after": False,
        },
    )
    with caplog.at_level(logging.WARNING, logger=UI_LOGGER):
        loaded = load_ui_settings(path, defaults)
    assert not caplog.records
    assert loaded == UiSettings(
        output_dir=out,
        pages=3,
        dark=True,
        lang="vi",
        prompt_only=True,
        examples=True,
        max_elements=500,
        timeout_s=30.0,  # not saved: from the defaults
        delta_e=4.5,
        browser="msedge",
        open_after=False,
    )


def test_unknown_keys_are_ignored_and_schema_version_is_not_trusted(
    tmp_path: Path, defaults: UiSettings
) -> None:
    path = _write(
        tmp_path / "ui-settings.json",
        {"schema_version": 99, "theme": "dark", "future": {"x": 1}, "pages": 2},
    )
    loaded = load_ui_settings(path, defaults)
    assert loaded.schema_version == UI_SETTINGS_SCHEMA_VERSION
    assert loaded == defaults.model_copy(update={"pages": 2})


def test_an_invalid_value_keeps_its_default_while_the_others_apply(
    tmp_path: Path, defaults: UiSettings, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(
        tmp_path / "ui-settings.json",
        {
            "pages": 42,
            "lang": "vi",
            "browser": "firefox",
            "dark": True,
            "timeout_s": "slow",
            "delta_e": 0.1,
            "max_elements": 2000,
            "output_dir": "",
        },
    )
    with caplog.at_level(logging.WARNING, logger=UI_LOGGER):
        loaded = load_ui_settings(path, defaults)
    assert loaded == defaults.model_copy(update={"lang": "vi", "dark": True, "max_elements": 2000})
    warned = caplog.text
    for key in ("pages", "browser", "timeout_s", "delta_e", "output_dir"):
        assert key in warned


@pytest.mark.parametrize("value", [42, None, ["a"], "   ", "bad\x00path"])
def test_unusable_output_dir_keeps_the_default(
    tmp_path: Path, defaults: UiSettings, value: object
) -> None:
    path = _write(tmp_path / "ui-settings.json", {"output_dir": value, "pages": 1})
    assert load_ui_settings(path, defaults) == defaults.model_copy(update={"pages": 1})


def test_relative_output_dir_is_resolved_against_home(
    tmp_path: Path, defaults: UiSettings, home: Path
) -> None:
    path = _write(tmp_path / "ui-settings.json", {"output_dir": "Design/results"})
    assert load_ui_settings(path, defaults).output_dir == home / "Design" / "results"
    _write(path, {"output_dir": "~/elsewhere"})
    assert load_ui_settings(path, defaults).output_dir == home / "elsewhere"
    _write(path, {"output_dir": "a/../b"})
    assert load_ui_settings(path, defaults).output_dir == home / "b"


def test_absolute_output_dir(home: Path, tmp_path: Path) -> None:
    assert absolute_output_dir("x") == home / "x"
    assert absolute_output_dir(Path("~") / "y") == home / "y"
    absolute = (tmp_path / "abs").resolve()
    assert absolute_output_dir(absolute) == absolute
    assert absolute_output_dir(str(absolute / "a" / ".." / "b")) == absolute / "b"


# ─── Saving ──────────────────────────────────────────────────────────────────


def test_save_writes_pretty_json_with_an_absolute_output_dir(
    tmp_path: Path, home: Path, defaults: UiSettings
) -> None:
    path = tmp_path / "config" / "nested" / "ui-settings.json"
    settings = defaults.model_copy(
        update={"output_dir": Path("relative/out"), "pages": 4, "lang": "vi"}
    )
    assert save_ui_settings(settings, path) == path
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["output_dir"] == str(home / "relative" / "out")
    assert Path(data["output_dir"]).is_absolute()
    assert data["schema_version"] == UI_SETTINGS_SCHEMA_VERSION
    assert (data["pages"], data["lang"]) == (4, "vi")
    expected = settings.model_copy(update={"output_dir": home / "relative" / "out"})
    assert text == expected.model_dump_json(indent=2)
    assert sorted(os.listdir(path.parent)) == ["ui-settings.json"]  # no temp file left


def test_save_then_load_round_trip(tmp_path: Path, defaults: UiSettings) -> None:
    path = tmp_path / "ui-settings.json"
    settings = UiSettings(
        output_dir=(tmp_path / "out").resolve(),
        pages=10,
        dark=True,
        lang="vi",
        prompt_only=True,
        examples=True,
        max_elements=20000,
        timeout_s=600,
        delta_e=0.5,
        browser="chrome",
        open_after=False,
    )
    save_ui_settings(settings, path)
    assert load_ui_settings(path, defaults) == settings
    # Saving again replaces the file.
    save_ui_settings(settings.model_copy(update={"pages": 1}), path)
    assert load_ui_settings(path, defaults).pages == 1


def test_save_failure_is_a_vietnamese_config_error(tmp_path: Path, defaults: UiSettings) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    path = blocker / "ui-settings.json"
    with pytest.raises(ConfigError) as caught:
        save_ui_settings(defaults, path)
    assert caught.value.message.startswith("Không lưu được cấu hình giao diện vào")
    assert str(path) in caught.value.message
    assert caught.value.hint and ui_settings.CONFIG_DIR_ENV in caught.value.hint


def test_failed_replace_leaves_no_temp_file(
    tmp_path: Path, defaults: UiSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ui-settings.json"
    save_ui_settings(defaults, path)
    before = path.read_text(encoding="utf-8")

    def refuse(source: object, target: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "replace", refuse)  # what storage.save_text uses
    with pytest.raises(ConfigError) as caught:
        save_ui_settings(defaults.model_copy(update={"pages": 9}), path)
    assert "Access is denied" in caught.value.message
    assert path.read_text(encoding="utf-8") == before  # the old file is intact
    assert sorted(os.listdir(tmp_path)) == ["ui-settings.json"]


def test_default_settings_path_is_the_isolated_test_folder(isolated_ui_folders: Path) -> None:
    assert ui_settings.settings_path().parent == isolated_ui_folders.resolve()
