"""robots.txt check before collecting (RFC 9309 semantics)."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from get_design_markdown.config import ROBOTS_AGENT, USER_AGENT
from get_design_markdown.models.raw import RobotsCheck
from get_design_markdown.storage import normalize_url

logger = logging.getLogger(__name__)


def robots_url_for(page_url: str) -> str:
    parts = urlsplit(page_url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


def evaluate_robots(
    robots_url: str,
    status_code: int,
    body: str,
    page_url: str,
    agent: str = ROBOTS_AGENT,
) -> RobotsCheck:
    """Decide whether `agent` may fetch `page_url` given a robots.txt response."""
    if 400 <= status_code < 500:
        return RobotsCheck(
            robots_url=robots_url,
            allowed=True,
            status_code=status_code,
            note=f"Trang không có robots.txt (HTTP {status_code}) — mặc định được phép.",
        )
    if status_code >= 500:
        return RobotsCheck(
            robots_url=robots_url,
            allowed=False,
            status_code=status_code,
            note=(
                f"Máy chủ lỗi khi trả robots.txt (HTTP {status_code}) — "
                "theo RFC 9309 coi như không cho phép."
            ),
        )
    parser = RobotFileParser(robots_url)
    parser.parse(body.splitlines())
    allowed = parser.can_fetch(agent, page_url)
    path = urlsplit(page_url).path or "/"
    note = (
        f"robots.txt cho phép truy cập {path}."
        if allowed
        else f"robots.txt không cho phép bot '{agent}' (hoặc mọi bot '*') truy cập {path}."
    )
    return RobotsCheck(robots_url=robots_url, allowed=allowed, status_code=status_code, note=note)


async def check_robots(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = 15.0,
) -> RobotsCheck:
    """Fetch and evaluate robots.txt; network failures give allowed=None instead of raising."""
    page_url = normalize_url(url)
    robots_url = robots_url_for(page_url)
    http = client or httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=timeout_s
    )
    try:
        response = await http.get(robots_url)
    except httpx.TimeoutException:
        return RobotsCheck(
            robots_url=robots_url,
            allowed=None,
            note=(
                f"Hết thời gian chờ khi tải robots.txt (quá {timeout_s:g} giây) nên không kiểm "
                "tra được (bỏ qua bước này)."
            ),
        )
    except httpx.HTTPError as exc:
        # The exception class (ConnectError...) is for the debug log, not for the user.
        logger.debug("robots.txt %s could not be fetched: %r", robots_url, exc)
        return RobotsCheck(
            robots_url=robots_url,
            allowed=None,
            note="Không tải được robots.txt nên không kiểm tra được (bỏ qua bước này).",
        )
    finally:
        if client is None:
            await http.aclose()
    return evaluate_robots(robots_url, response.status_code, response.text, page_url)
