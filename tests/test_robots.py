from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from get_design_markdown.collector.robots import check_robots, evaluate_robots
from get_design_markdown.models.raw import RobotsCheck

ROBOTS_URL = "https://example.com/robots.txt"


def _check(url: str, handler: Callable[[httpx.Request], httpx.Response]) -> RobotsCheck:
    async def run() -> RobotsCheck:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await check_robots(url, client=client)

    return asyncio.run(run())


def test_allowed_when_not_disallowed() -> None:
    result = evaluate_robots(
        ROBOTS_URL, 200, "User-agent: *\nDisallow: /admin\n", "https://example.com/"
    )
    assert result.allowed is True


def test_disallowed_for_everyone() -> None:
    result = evaluate_robots(
        ROBOTS_URL, 200, "User-agent: *\nDisallow: /\n", "https://example.com/"
    )
    assert result.allowed is False
    assert "không cho phép" in result.note


def test_specific_agent_rule_wins() -> None:
    body = "User-agent: get-design-markdown\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert evaluate_robots(ROBOTS_URL, 200, body, "https://example.com/").allowed is False


@pytest.mark.parametrize(("status", "allowed"), [(404, True), (403, True), (503, False)])
def test_status_codes_follow_rfc_9309(status: int, allowed: bool) -> None:
    result = evaluate_robots(ROBOTS_URL, status, "", "https://example.com/")
    assert result.allowed is allowed
    assert result.status_code == status


def test_check_robots_fetches_root_robots_file() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")

    result = _check("example.com/private/page", handler)
    assert seen == [ROBOTS_URL]
    assert result.allowed is False


def test_network_error_gives_unknown_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    result = _check("https://example.com", handler)
    assert result.allowed is None
    # Plain Vietnamese for the user: no exception class names.
    assert "không kiểm tra được" in result.note
    assert "ConnectError" not in result.note


def test_timeout_gives_unknown_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = _check("https://example.com", handler)
    assert result.allowed is None
    assert "Hết thời gian chờ" in result.note and "ReadTimeout" not in result.note
