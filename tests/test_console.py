from __future__ import annotations

import pytest
from rich.console import Console

from get_design_markdown import console as console_module
from get_design_markdown import pipeline
from get_design_markdown.errors import CollectError
from tests.fixture_data import load_raw_sample


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Console:
    recorder = Console(record=True, width=200, highlight=False)
    monkeypatch.setattr(console_module, "console", recorder)
    monkeypatch.setattr(console_module, "err_console", recorder)
    return recorder


def test_helpers_print_square_brackets_literally(recorded: Console) -> None:
    console_module.print_warning("[home] Phát hiện [bold]lớp phủ[/bold]")
    console_module.print_info("Selector a[href] [link=x]")
    console_module.print_success("Đã ghi C:\\out\\[site]\\raw.json")
    console_module.print_step(2, 4, "Phân tích [beta]")
    console_module.print_error(CollectError("Lỗi tại [main]", hint="Thử lại [sau]"))
    text = recorded.export_text()
    for literal in (
        "[home] Phát hiện [bold]lớp phủ[/bold]",
        "Selector a[href] [link=x]",
        "[site]",
        "[2/4]",
        "Phân tích [beta]",
        "Lỗi tại [main]",
        "Thử lại [sau]",
    ):
        assert literal in text


def test_page_warnings_keep_the_page_name(recorded: Console) -> None:
    raw = load_raw_sample()
    page = raw.pages[0].model_copy(update={"warnings": ["Phát hiện lớp phủ cookie."]})
    raw = raw.model_copy(update={"pages": [page], "warnings": []})
    pipeline.summarize_capture(raw)
    assert f"[{page.name}] Phát hiện lớp phủ cookie." in recorded.export_text()
