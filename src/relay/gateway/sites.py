"""Fetches a lead's company website from the local fixture sites only, and reduces it to visible text.

Enrichment never touches the real internet: the website a lead typed is normalized to a domain, and only reserved
`.example` domains that exist among the fixture sites are read (from disk, or from the mocks server when
RELAY_SITES_BASE_URL is set). Hidden elements are dropped and sentences addressed to AI systems are removed before the
text reaches the model, and both are counted."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from relay.gateway.textutil import ai_directed_sentences

PAGES = ("index.html", "about.html")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0", re.I)
_DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


class SiteRefusedError(ValueError):
    pass


def normalize_domain(website: str) -> str:
    raw = website.strip().lower()
    if not raw:
        raise SiteRefusedError("no website given")
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlsplit(raw).hostname or "").removeprefix("www.")
    if not _DOMAIN.match(host):
        raise SiteRefusedError(f"not a valid domain: {website[:80]!r}")
    if not host.endswith(".example"):
        raise SiteRefusedError(f"{host} is not a fixture site; Relay's demo never fetches the real internet")
    return host


class _TextExtractor(HTMLParser):
    VOID = frozenset({"br", "img", "meta", "link", "input", "hr", "source", "wbr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.hidden = 0

    def _is_hidden(self) -> bool:
        return any(hidden for _, hidden in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID:
            return
        attributes = dict(attrs)
        hidden = (
            tag in {"script", "style", "template", "noscript"}
            or "hidden" in attributes
            or attributes.get("aria-hidden") == "true"
            or bool(_HIDDEN_STYLE.search(attributes.get("style") or ""))
        )
        if hidden and tag not in {"script", "style"}:
            self.hidden += 1
        self.stack.append((tag, hidden))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if not self._is_hidden() and data.strip():
            self.parts.append(data.strip())


@dataclass
class SiteText:
    domain: str
    pages: list[str] = field(default_factory=list)
    text: str = ""
    removed_hidden: int = 0
    removed_ai_directed: int = 0


def visible_text(html: str) -> tuple[str, int]:
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(parser.parts), parser.hidden


class SiteFetcher:
    def __init__(self, sites_dir: Path, base_url: str = "", timeout: float = 10.0) -> None:
        self.sites_dir = sites_dir
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _read(self, domain: str, page: str) -> str | None:
        if self.base_url:
            try:
                response = httpx.get(f"{self.base_url}/{domain}/{page}", timeout=self.timeout, follow_redirects=False)
            except httpx.HTTPError:
                return None
            return response.text if response.status_code == 200 else None
        path = self.sites_dir / domain / page
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def fetch(self, website: str) -> SiteText:
        domain = normalize_domain(website)
        site = SiteText(domain=domain)
        chunks: list[str] = []
        for page in PAGES:
            html = self._read(domain, page)
            if html is None:
                continue
            text, hidden = visible_text(html)
            flagged = ai_directed_sentences(text)
            for sentence in flagged:
                text = text.replace(sentence, " ")
            site.pages.append(page)
            site.removed_hidden += hidden
            site.removed_ai_directed += len(flagged)
            chunks.append(f"[{page}] {text.strip()}")
        site.text = "\n".join(chunks)[:8000]
        return site
