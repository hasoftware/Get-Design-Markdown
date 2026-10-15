"""--prompt-only: prompt.md rendering, screenshot preparation and the glue end to end."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from get_design_markdown.models.raw import Screenshot
from get_design_markdown.storage import ANALYSIS_FILE, PROMPT_FILE, save_model
from get_design_markdown.synthesizer import synthesize
from get_design_markdown.synthesizer.prompt_export import (
    MAX_IMAGE_SIDE,
    pick_desktop,
    pick_mobile,
    prepare_prompt_images,
    render_prompt_md,
)
from get_design_markdown.synthesizer.render import render_design_md
from tests.conftest import FIXED_TIME
from tests.fixture_data import load_analysis_sample


def _shot(width: int, height: int, scheme: str = "light", name: str = "home") -> Screenshot:
    suffix = "-dark" if scheme == "dark" else ""
    return Screenshot.model_validate(
        {
            "name": f"{name}{suffix}-{width}",
            "viewport_width": width,
            "viewport_height": height,
            "color_scheme": scheme,
            "path": f"screenshots/{name}{suffix}-{width}.png",
            "full_page": True,
        }
    )


def _write_png(folder: Path, shot: Screenshot, size: tuple[int, int]) -> None:
    path = folder / shot.path
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 210, 230)).save(path)


def test_prepare_prompt_images_crops_and_resizes(tmp_path: Path) -> None:
    desktop = _shot(1440, 900)
    mobile = _shot(390, 844)
    dark = _shot(1440, 900, "dark")
    _write_png(tmp_path, desktop, (1440, 5000))
    _write_png(tmp_path, mobile, (390, 4000))
    _write_png(tmp_path, dark, (1440, 5000))
    paths = prepare_prompt_images(tmp_path, [dark, desktop, _shot(768, 1024), mobile])
    assert paths == ["prompt-images/desktop.png", "prompt-images/mobile.png"]
    with Image.open(tmp_path / paths[0]) as image:
        assert max(image.size) <= MAX_IMAGE_SIDE
        assert image.size == (836, 1568)  # 1440x2700 (3 screens) scaled to 1568 px tall
    with Image.open(tmp_path / paths[1]) as image:
        assert max(image.size) <= MAX_IMAGE_SIDE
        assert image.size == (242, 1568)


def test_small_images_are_kept_and_max_side_respected(tmp_path: Path) -> None:
    desktop = _shot(1440, 900)
    _write_png(tmp_path, desktop, (1440, 800))
    assert prepare_prompt_images(tmp_path, [desktop], max_side=2000) == [
        "prompt-images/desktop.png"
    ]
    with Image.open(tmp_path / "prompt-images" / "desktop.png") as image:
        assert image.size == (1440, 800)
    prepare_prompt_images(tmp_path, [desktop], max_side=720)
    with Image.open(tmp_path / "prompt-images" / "desktop.png") as image:
        assert image.size == (720, 400)


def test_hidpi_screenshots_are_cropped_in_image_pixels(tmp_path: Path) -> None:
    desktop = _shot(1440, 900)
    _write_png(tmp_path, desktop, (2880, 9000))
    prepare_prompt_images(tmp_path, [desktop], max_side=10_000)
    with Image.open(tmp_path / "prompt-images" / "desktop.png") as image:
        assert image.size == (2880, 5400)


def test_missing_or_broken_files_are_skipped(tmp_path: Path) -> None:
    desktop = _shot(1440, 900)
    mobile = _shot(390, 844)
    (tmp_path / "screenshots").mkdir()
    (tmp_path / mobile.path).write_bytes(b"not a png")
    assert prepare_prompt_images(tmp_path, [desktop, mobile]) == []
    assert prepare_prompt_images(tmp_path, []) == []


def test_screenshot_selection_fallbacks() -> None:
    shots = [
        _shot(1280, 800),
        _shot(1920, 1080),
        _shot(414, 896),
        _shot(360, 740),
        _shot(768, 1024),
    ]
    picked_desktop = pick_desktop(shots)
    picked_mobile = pick_mobile(shots)
    assert picked_desktop is not None and picked_desktop.viewport_width == 1920
    assert picked_mobile is not None and picked_mobile.viewport_width == 360
    assert pick_desktop([_shot(768, 1024)]) is None
    assert pick_mobile([_shot(1440, 900, "dark"), _shot(390, 844, "dark")]) is None


@pytest.mark.parametrize("lang", ["en", "vi"])
def test_render_prompt_md(lang: str) -> None:
    analysis = load_analysis_sample()
    design = render_design_md(analysis, lang=lang)
    images = ["prompt-images/desktop.png", "prompt-images/mobile.png"]
    text = render_prompt_md(analysis, design, images, lang=lang)
    assert text == render_prompt_md(analysis, design, images, lang=lang)
    assert f"````markdown\n{design}````\n" in text
    assert text.endswith("````\n")
    assert "- `prompt-images/desktop.png`" in text and "- `prompt-images/mobile.png`" in text
    assert "2.56:1" in text and "640px, 768px, 1024px, 1280px" in text
    assert "primary #4f46e5" in text
    if lang == "en":
        assert "never calls an AI API" in text
        assert "Never change numbers, hex values, token names, `{group.token}` references" in text
        assert "Write the prose in English." in text
    else:
        assert "không bao giờ gọi API AI" in text
        assert "Tuyệt đối không đổi con số" in text
        assert "Viết phần văn xuôi bằng tiếng Việt." in text


def test_prompt_without_images_and_with_long_fences() -> None:
    analysis = load_analysis_sample()
    design = "---\nversion: alpha\nname: X\n---\n\n````text\nnested\n````\n"
    text = render_prompt_md(analysis, design, [])
    assert "No screenshot was available" in text
    assert "`````markdown\n" in text and text.endswith("`````\n")


def test_run_synthesize_prompt_only_end_to_end(tmp_path: Path) -> None:
    analysis = load_analysis_sample()
    save_model(analysis, tmp_path / ANALYSIS_FILE)
    _write_png(tmp_path, analysis.screenshots[0], (1440, 5000))
    _write_png(tmp_path, analysis.screenshots[2], (390, 5180))
    result = synthesize.run_synthesize(
        tmp_path, lang="vi", prompt_only=True, examples=True, now=FIXED_TIME
    )
    assert result.mode == "prompt-only"
    assert result.image_paths == ["prompt-images/desktop.png", "prompt-images/mobile.png"]
    assert result.sections[-1] == "Agent Prompt Guide"
    assert result.token_counts["components"] == 21
    assert not [issue for issue in result.issues if issue.severity != "info"]
    design = (tmp_path / "DESIGN.md").read_text(encoding="utf-8")
    prompt = (tmp_path / PROMPT_FILE).read_text(encoding="utf-8")
    assert design in prompt
