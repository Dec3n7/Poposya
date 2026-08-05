"""Клиент публичного GitHub REST API на aiohttp.

Сессия создаётся на запрос — опрос идёт раз в минуты, накладные расходы
ничтожны, зато не нужно тащить жизненный цикл через композит-рут. Токен
опционален: без него лимит 60 запросов/час на IP, с ним — 5000. Условные
запросы (If-None-Match) экономят и трафик, и разбор."""

import logging
from datetime import UTC, datetime

import aiohttp

from src.application.repos.dto import ReleaseDTO, ReleasesPage, RepoInfoDTO
from src.application.repos.interfaces import IGitHubClient

logger = logging.getLogger(__name__)

_API = "https://api.github.com"


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _int_or_none(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_repo(owner: str, name: str, data: dict) -> RepoInfoDTO:
    owner_login = (data.get("owner") or {}).get("login") or owner
    repo_name = data.get("name") or name
    return RepoInfoDTO(
        owner=owner_login,
        name=repo_name,
        description=data.get("description") or "",
        stars=int(data.get("stargazers_count") or 0),
        language=data.get("language") or "",
        html_url=data.get("html_url") or f"https://github.com/{owner_login}/{repo_name}",
        default_branch=data.get("default_branch") or "main",
    )


def _parse_release(item: dict) -> ReleaseDTO | None:
    published = _parse_dt(item.get("published_at") or item.get("created_at"))
    if published is None:
        return None
    return ReleaseDTO(
        id=int(item.get("id") or 0),
        tag_name=item.get("tag_name") or "",
        name=item.get("name") or item.get("tag_name") or "",
        body=item.get("body") or "",
        html_url=item.get("html_url") or "",
        published_at=published,
        author=(item.get("author") or {}).get("login") or "",
        prerelease=bool(item.get("prerelease")),
        draft=bool(item.get("draft")),
    )


class GitHubClient(IGitHubClient):
    def __init__(
        self, token: str = "", timeout_seconds: float = 10.0, user_agent: str = "PoposyaBot"
    ):
        self._token = token
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    def _headers(self, etag: str | None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self._user_agent,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if etag:
            headers["If-None-Match"] = etag
        return headers

    async def _request(
        self, path: str, etag: str | None = None
    ) -> tuple[object, int, str | None, int | None]:
        """(тело, статус, ETag ответа, остаток лимита). Статус 0 — сеть/таймаут."""
        url = _API + path
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=self._headers(etag)) as resp:
                    resp_etag = resp.headers.get("ETag")
                    remaining = _int_or_none(resp.headers.get("X-RateLimit-Remaining"))
                    if resp.status == 200:
                        return await resp.json(), 200, resp_etag, remaining
                    return None, resp.status, resp_etag, remaining
        except (TimeoutError, aiohttp.ClientError) as exc:
            logger.warning("GitHub-запрос не удался", extra={"path": path, "error": str(exc)})
            return None, 0, None, None

    async def get_repo(self, owner: str, name: str) -> RepoInfoDTO | None:
        data, status, _, _ = await self._request(f"/repos/{owner}/{name}")
        if status == 200 and isinstance(data, dict):
            return _parse_repo(owner, name, data)
        return None

    async def list_releases(self, owner: str, name: str, etag: str | None = None) -> ReleasesPage:
        data, status, resp_etag, remaining = await self._request(
            f"/repos/{owner}/{name}/releases?per_page=10", etag
        )
        if status == 304:
            return ReleasesPage(
                not_modified=True, etag=etag or resp_etag, ok=True, rate_remaining=remaining
            )
        if status == 200 and isinstance(data, list):
            releases = [
                r for item in data if isinstance(item, dict) and (r := _parse_release(item))
            ]
            return ReleasesPage(
                releases=releases, etag=resp_etag, ok=True, rate_remaining=remaining
            )
        # 404 / 403 (лимит) / 5xx / сеть — состояние не трогаем, повторим позже
        return ReleasesPage(ok=False, rate_remaining=remaining)
