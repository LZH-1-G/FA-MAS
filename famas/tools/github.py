"""GitHub REST API client used by research agents in live mode."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import httpx

from famas.core.models import Source


class ToolError(RuntimeError):
    """A tool call failed. ``transient`` tells the failure analyzer whether a retry may help."""

    def __init__(self, message: str, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class RepoInfo:
    repo: str
    description: str = ""
    stars: int = 0
    forks: int = 0
    language: str | None = None
    license: str | None = None
    topics: list[str] = field(default_factory=list)
    pushed_at: str | None = None
    html_url: str = ""
    readme: str = ""
    docs_files: list[str] = field(default_factory=list)

    def as_prompt(self) -> str:
        meta = (
            f"Repository: {self.repo}\nDescription: {self.description}\nPrimary language: {self.language}\n"
            f"License: {self.license}\nStars: {self.stars}  Forks: {self.forks}\nTopics: {', '.join(self.topics)}\n"
            f"Last push: {self.pushed_at}\nDocs files: {', '.join(self.docs_files[:30])}\n"
        )
        return meta + "\nREADME (truncated):\n" + self.readme[:10000]

    def sources(self) -> list[Source]:
        out = [
            Source(
                title=f"{self.repo} repository",
                url=self.html_url or f"https://github.com/{self.repo}",
                kind="github",
            )
        ]
        if self.readme:
            out.append(
                Source(
                    title=f"{self.repo} README", url=f"https://github.com/{self.repo}#readme", kind="github"
                )
            )
        return out


class GitHubClient:
    def __init__(
        self, token: str | None = None, base_url: str = "https://api.github.com", timeout_s: float = 20.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": "fa-mas"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.timeout_s = timeout_s
        self._cache: dict[str, RepoInfo] = {}

    async def _get(self, client: httpx.AsyncClient, path: str) -> httpx.Response:
        try:
            r = await client.get(f"{self.base_url}{path}")
        except httpx.TimeoutException as e:
            raise ToolError(f"GitHub timeout on {path}", transient=True) from e
        except httpx.HTTPError as e:
            raise ToolError(f"GitHub connection error on {path}: {e}", transient=True) from e
        if r.status_code == 404:
            raise ToolError(f"GitHub 404 for {path}", transient=False)
        if r.status_code in (403, 429) and "rate limit" in r.text.lower():
            raise ToolError("GitHub rate limit exceeded (set GITHUB_TOKEN)", transient=True)
        if r.status_code >= 500:
            raise ToolError(f"GitHub {r.status_code} on {path}", transient=True)
        if r.status_code >= 400:
            raise ToolError(f"GitHub {r.status_code} on {path}: {r.text[:200]}", transient=False)
        return r

    async def repo_overview(self, repo: str) -> RepoInfo:
        if repo in self._cache:
            return self._cache[repo]
        async with httpx.AsyncClient(
            headers=self.headers, timeout=self.timeout_s, follow_redirects=True
        ) as client:
            meta = (await self._get(client, f"/repos/{repo}")).json()
            info = RepoInfo(
                repo=meta.get("full_name", repo),
                description=meta.get("description") or "",
                stars=meta.get("stargazers_count", 0),
                forks=meta.get("forks_count", 0),
                language=meta.get("language"),
                license=(meta.get("license") or {}).get("spdx_id"),
                topics=meta.get("topics") or [],
                pushed_at=meta.get("pushed_at"),
                html_url=meta.get("html_url", f"https://github.com/{repo}"),
            )
            try:
                rd = (await self._get(client, f"/repos/{repo}/readme")).json()
                if rd.get("encoding") == "base64":
                    info.readme = base64.b64decode(rd.get("content", "")).decode("utf-8", errors="replace")
            except ToolError as e:
                if e.transient:
                    raise
            try:
                docs = (await self._get(client, f"/repos/{repo}/contents/docs")).json()
                if isinstance(docs, list):
                    info.docs_files = [d.get("name", "") for d in docs]
            except ToolError as e:
                if e.transient:
                    raise
        self._cache[repo] = info
        return info
