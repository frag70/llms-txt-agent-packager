#!/usr/bin/env python3
"""
llms-txt-agent-packager — Standby Pay-Per-Event (PPE) Apify Actor.

Audits how ingestible a website is for AI agents / LLM crawlers and generates a
ready-to-deploy `llms.txt` (https://llmstxt.org) plus a prioritised remediation plan.

Modes
-----
* BATCH          : audit up to 25 domains, push one JSON record per domain to the dataset
* STANDBY_SERVER : persistent HTTP API for MCP clients / autonomous agents
* --cli <domain> : local run, pretty JSON on stdout
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

try:
    from apify import Actor
except Exception:  # pragma: no cover - the SDK is optional for local/CLI runs
    Actor = None

ACTOR_SLUG = "llms-txt-agent-packager"
ACTOR_VERSION = "0.1.0"
UA = (
    "Mozilla/5.0 (compatible; AgentReadinessAudit/0.1; "
    "+https://apify.com/fragthom/llms-txt-agent-packager)"
)
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

AI_CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User",
    "ClaudeBot", "Claude-User", "anthropic-ai",
    "PerplexityBot", "Perplexity-User",
    "Google-Extended", "CCBot", "Applebot-Extended",
    "Bytespider", "meta-externalagent", "Amazonbot",
    "DuckAssistBot", "cohere-ai",
]

OPENAPI_CANDIDATES = [
    "/openapi.json", "/openapi.yaml", "/swagger.json", "/api/openapi.json",
    "/v1/openapi.json", "/api-docs", "/.well-known/mcp.json",
    "/.well-known/ai-plugin.json", "/.well-known/ai.txt",
]

LLMS_LINK_RE = re.compile(
    r"^\s*[-*+]\s+\[(?P<title>[^\]]+)\]\((?P<url>[^)\s]+)\)\s*(?:(?::|–|—|-)\s*(?P<note>.+))?$"
)
LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S
)
TAG_RE = re.compile(r"<[^>]+>")

# --------------------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------------------


class FetchResult:
    """Small value object describing one HTTP GET."""

    __slots__ = ("url", "final_url", "status", "headers", "text", "raw_len", "error", "elapsed_ms")

    def __init__(self, url: str) -> None:
        self.url = url
        self.final_url: Optional[str] = None
        self.status: Optional[int] = None
        self.headers: Dict[str, str] = {}
        self.text: str = ""
        self.raw_len: int = 0
        self.error: Optional[str] = None
        self.elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "ok": self.ok,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.headers.get("content-type"):
            out["content_type"] = self.headers["content-type"]
        if self.error:
            out["error"] = self.error
        return out


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    try:
        if "gzip" in encoding:
            return gzip.decompress(raw)
        if "deflate" in encoding:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        return raw
    return raw


def fetch(
    url: str,
    timeout: float = 12.0,
    max_bytes: int = 2_000_000,
    accept: str = ACCEPT,
) -> FetchResult:
    """GET a URL. Never raises: transport problems are reported on the result."""
    started = time.monotonic()
    res = FetchResult(url)
    headers = {
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "close",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res.status = resp.status
            res.final_url = resp.geturl()
            res.headers = {k.lower(): v for k, v in resp.headers.items()}
            raw = resp.read(max_bytes)
    except urllib.error.HTTPError as exc:
        res.status = exc.code
        res.final_url = getattr(exc, "url", url)
        res.headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        try:
            raw = exc.read(max_bytes)
        except Exception:
            raw = b""
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
        res.elapsed_ms = int((time.monotonic() - started) * 1000)
        return res

    res.raw_len = len(raw)
    res.text = _decompress(raw, res.headers.get("content-encoding", "")).decode(
        "utf-8", errors="replace"
    )
    res.elapsed_ms = int((time.monotonic() - started) * 1000)
    return res


def normalize_domain(value: str) -> str:
    """`https://WWW.Example.com/path?q=1` -> `www.example.com`."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = urllib.parse.urlparse(raw).netloc or urllib.parse.urlparse("https://" + raw).netloc
    return host.split("@")[-1].split(":")[0].strip(".")


def base_url(host: str) -> str:
    return f"https://{host}"


# --------------------------------------------------------------------------------------
# robots.txt (RFC 9309 subset)
# --------------------------------------------------------------------------------------


def parse_robots(text: str) -> Dict[str, Any]:
    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    last_was_agent = False
    sitemaps: List[str] = []

    for raw_line in (text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = {"agents": [], "rules": []}
                groups.append(current)
            if value:
                current["agents"].append(value.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            last_was_agent = False
            if current is None:
                current = {"agents": ["*"], "rules": []}
                groups.append(current)
            if value:
                current["rules"].append({"allow": key == "allow", "path": value})
        elif key == "sitemap" and value:
            sitemaps.append(value)
        else:
            last_was_agent = False

    return {"groups": groups, "sitemaps": sitemaps, "bytes": len(text or "")}


def _path_matches(rule_path: str, path: str) -> bool:
    if rule_path == "/":
        return True
    if rule_path.endswith("$"):
        pattern = re.escape(rule_path[:-1]).replace(r"\*", ".*") + "$"
        return re.match(pattern, path) is not None
    pattern = re.escape(rule_path).replace(r"\*", ".*")
    return re.match(pattern, path) is not None


def crawler_access(robots: Dict[str, Any], bot: str, path: str = "/") -> Dict[str, Any]:
    """Resolve whether `bot` may fetch `path`. Longest matching rule wins, Allow breaks ties."""
    bot_l = bot.lower()
    specific: List[Dict[str, Any]] = []
    wildcard: List[Dict[str, Any]] = []
    for group in robots.get("groups", []):
        for agent in group.get("agents", []):
            if agent == "*":
                wildcard.append(group)
            elif agent in bot_l or bot_l.startswith(agent):
                specific.append(group)

    chosen = specific or wildcard
    if not chosen:
        return {"bot": bot, "access": "unspecified", "matched_rule": None}

    best: Optional[Dict[str, Any]] = None
    for group in chosen:
        for rule in group.get("rules", []):
            if not _path_matches(rule["path"], path):
                continue
            if best is None:
                best = rule
                continue
            if len(rule["path"]) > len(best["path"]):
                best = rule
            elif len(rule["path"]) == len(best["path"]) and rule["allow"] and not best["allow"]:
                best = rule

    if best is None:
        return {"bot": bot, "access": "allowed", "matched_rule": None}
    return {
        "bot": bot,
        "access": "allowed" if best["allow"] else "blocked",
        "matched_rule": ("Allow: " if best["allow"] else "Disallow: ") + best["path"],
    }


# --------------------------------------------------------------------------------------
# llms.txt parsing / validation (llmstxt.org)
# --------------------------------------------------------------------------------------


def parse_llms_txt(text: str, url: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "found": True,
        "url": url,
        "bytes": len((text or "").encode("utf-8")),
        "h1": None,
        "summary": None,
        "sections": [],
        "link_count": 0,
        "issues": [],
        "valid": False,
    }
    lines = (text or "").splitlines()
    non_empty = [(i, ln.strip()) for i, ln in enumerate(lines) if ln.strip()]
    if not non_empty:
        out["issues"].append({"code": "empty_file", "detail": "The file is empty."})
        return out

    first_raw = lines[non_empty[0][0]]
    h1_match = re.match(r"^#\s+(.+?)\s*$", first_raw)
    if h1_match:
        out["h1"] = h1_match.group(1).strip()
    else:
        out["issues"].append(
            {"code": "missing_h1", "detail": "First line must be an H1: `# Project name`."}
        )

    body = lines[non_empty[0][0] + 1:]

    # Optional block-quote summary immediately after the H1
    for line in body:
        if not line.strip():
            continue
        if line.lstrip().startswith(">"):
            out["summary"] = line.lstrip().lstrip(">").strip()
        break

    section: Optional[Dict[str, Any]] = None
    seen_urls = set()
    for line in body:
        h2 = re.match(r"^##\s+(.+?)\s*$", line)
        if h2:
            section = {"title": h2.group(1).strip(), "links": []}
            out["sections"].append(section)
            continue
        link = LLMS_LINK_RE.match(line)
        if not link:
            continue
        target = link.group("url")
        title = link.group("title").strip()
        if section is None:
            section = {"title": "(unsectioned)", "links": []}
            out["sections"].append(section)
        if target in seen_urls:
            continue
        seen_urls.add(target)
        section["links"].append(
            {"title": title, "url": target, "note": (link.group("note") or "").strip()}
        )
        out["link_count"] += 1

    if not out["sections"]:
        out["issues"].append(
            {"code": "no_sections", "detail": "No `## Section` headings found; agents cannot prioritise."}
        )
    if out["link_count"] == 0:
        out["issues"].append(
            {"code": "no_links", "detail": "No markdown links found; the file carries no navigable content."}
        )
    relative = [
        ln["url"] for sec in out["sections"] for ln in sec["links"] if not ln["url"].startswith(("http://", "https://"))
    ]
    if relative:
        out["issues"].append(
            {
                "code": "relative_links",
                "detail": f"{len(relative)} link(s) are relative; llms.txt must use absolute URLs.",
                "examples": relative[:5],
            }
        )
    if out["bytes"] > 512_000:
        out["issues"].append(
            {"code": "too_large", "detail": "File exceeds 500 KB; agents truncate it silently."}
        )
    if not out["summary"]:
        out["issues"].append(
            {"code": "no_summary", "detail": "Missing `> one-line summary` block quote under the H1."}
        )

    out["valid"] = bool(out["h1"]) and out["link_count"] > 0 and not relative
    return out


def render_llms_txt(
    brand: str,
    description: str,
    docs: List[Tuple[str, str]],
    api: List[Tuple[str, str]],
    product: List[Tuple[str, str]],
    company: List[Tuple[str, str]],
) -> str:
    """Render a spec-compliant llms.txt the site owner can publish verbatim."""

    def section(title: str, links: List[Tuple[str, str]]) -> List[str]:
        if not links:
            return []
        rows = [f"## {title}", ""]
        for name, url in links:
            rows.append(f"- [{name}]({url})")
        rows.append("")
        return rows

    out = [f"# {brand}", ""]
    if description:
        out += [f"> {description}", ""]
    out += section("Documentation", docs)
    out += section("API reference", api)
    out += section("Product", product)
    out += section("Company", company)
    return "\n".join(out).rstrip() + "\n"


def render_robots_policy(blocked: List[str]) -> str:
    if not blocked:
        return ""
    lines = ["# Recommended AI/LLM crawler policy (append to robots.txt)", ""]
    for bot in blocked:
        lines += [f"User-agent: {bot}", "Allow: /", ""]
    lines += ["# Optional machine-readable pointer", "Sitemap: /sitemap.xml", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Page introspection helpers
# --------------------------------------------------------------------------------------


def page_meta(html: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "title": "",
        "description": "",
        "site_name": "",
        "canonical": "",
        "h1": [],
        "word_count": 0,
        "token_estimate": 0,
        "json_ld_types": [],
    }
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if title:
        meta["title"] = unescape(TAG_RE.sub("", title.group(1))).strip()[:300]

    for pattern, key in (
        (r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', "description"),
        (r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', "description"),
        (r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']*)["\']', "site_name"),
        (r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']*)["\']', "canonical"),
    ):
        hit = re.search(pattern, html, re.I)
        if hit and not meta[key]:
            meta[key] = unescape(hit.group(1)).strip()[:400]

    meta["h1"] = [unescape(TAG_RE.sub("", h)).strip()[:160] for h in re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)][:5]

    for blob in LD_JSON_RE.findall(html):
        try:
            data = json.loads(blob.strip())
        except Exception:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict):
                node_type = node.get("@type")
                if isinstance(node_type, str):
                    meta["json_ld_types"].append(node_type)
                elif isinstance(node_type, list):
                    meta["json_ld_types"] += [t for t in node_type if isinstance(t, str)]
    meta["json_ld_types"] = sorted(set(meta["json_ld_types"]))

    visible = TAG_RE.sub(" ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S))
    words = len(visible.split())
    meta["word_count"] = words
    meta["token_estimate"] = int(len(visible) / 4)
    meta["html_bytes"] = len(html)
    return meta


def extract_internal_links(html: str, host: str, limit: int = 40) -> List[Tuple[str, str]]:
    links: List[Tuple[str, str]] = []
    seen = set()
    for href, anchor in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        absolute = urllib.parse.urljoin(f"https://{host}", unescape(href))
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.replace("www.", "") != host.replace("www.", ""):
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean in seen:
            continue
        seen.add(clean)
        text = unescape(TAG_RE.sub("", anchor)).strip()
        if not text or len(text) > 90:
            continue
        links.append((text, clean))
        if len(links) >= limit:
            break
    return links


def humanize_path(url: str) -> str:
    path = urllib.parse.urlparse(url).path.strip("/")
    if not path:
        return "Home"
    tail = path.split("/")[-1].replace("-", " ").replace("_", " ").strip()
    tail = re.sub(r"\.[a-z0-9]{1,5}$", "", tail)
    return (tail[:1].upper() + tail[1:]) if tail else path


def parse_sitemap(xml: str) -> Tuple[List[str], bool]:
    locs = [unescape(x) for x in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "", re.I)]
    is_index = "<sitemapindex" in (xml or "").lower()
    return locs, is_index


# --------------------------------------------------------------------------------------
# The audit
# --------------------------------------------------------------------------------------


def _bucket(urls: List[str], pattern: str) -> List[str]:
    rx = re.compile(pattern, re.I)
    return [u for u in urls if rx.search(urllib.parse.urlparse(u).path or "/")]


def audit_site(domain_input: str, max_pages: int = 4, timeout: float = 12.0) -> Dict[str, Any]:
    host = normalize_domain(domain_input)
    if not host or "." not in host:
        return {"domain": domain_input, "status": "INVALID_DOMAIN", "error": "Could not parse a domain."}

    root = base_url(host)
    started = time.monotonic()
    checks: Dict[str, Any] = {}

    # Phase 1 — the four root-level documents are independent, so fetch them in parallel.
    root_targets = [
        ("llms", f"{root}/llms.txt", "text/plain,*/*"),
        ("llms_full", f"{root}/llms-full.txt", "text/plain,*/*"),
        ("robots", f"{root}/robots.txt", "text/plain,*/*"),
        ("home", root, ACCEPT),
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(lambda t: fetch(t[1], timeout=timeout, accept=t[2]), root_targets))
    pre = {name: res for (name, _, _), res in zip(root_targets, fetched)}

    # 1. llms.txt / llms-full.txt
    llms = pre["llms"]
    if llms.status == 200 and len(llms.text.strip()) > 0 and "<html" not in llms.text[:400].lower():
        checks["llms_txt"] = parse_llms_txt(llms.text, f"{root}/llms.txt")
    else:
        checks["llms_txt"] = {
            "found": False,
            "url": f"{root}/llms.txt",
            "status": llms.status,
            "error": llms.error,
            "issues": [{"code": "missing", "detail": "No llms.txt published at the site root."}],
            "valid": False,
            "sections": [],
            "link_count": 0,
            "h1": None,
            "summary": None,
        }

    _parsed = checks["llms_txt"]
    if _parsed.get("link_count", 0) > 60 or len(_parsed.get("sections") or []) > 8:
        _parsed["sections_truncated"] = True
        _parsed["sections"] = _parsed["sections"][:8]
        for _sec in _parsed["sections"]:
            _sec["links"] = _sec["links"][:10]

    llms_full = pre["llms_full"]
    checks["llms_full_txt"] = {
        "found": bool(llms_full.status == 200 and llms_full.text.strip() and "<html" not in llms_full.text[:400].lower()),
        "url": f"{root}/llms-full.txt",
        "bytes": len(llms_full.text or ""),
    }

    # 2. robots.txt + per-crawler policy
    robots_res = pre["robots"]
    robots_found = robots_res.status == 200 and "user-agent" in (robots_res.text or "").lower()
    robots = parse_robots(robots_res.text if robots_found else "")
    policy = [crawler_access(robots, bot) for bot in AI_CRAWLERS]
    blocked = [p["bot"] for p in policy if p["access"] == "blocked"]
    allowed = [p["bot"] for p in policy if p["access"] == "allowed"]
    checks["robots"] = {
        "found": robots_found,
        "url": f"{root}/robots.txt",
        "status": robots_res.status,
        "bytes": robots["bytes"],
        "ai_crawlers_allowed": len(allowed),
        "ai_crawlers_blocked": len(blocked),
        "blocked": blocked,
        "policy": policy,
        "sitemaps_declared": robots["sitemaps"][:5],
    }

    # 3. sitemap
    sitemap_urls: List[str] = []
    sitemap_source = None
    candidates = robots["sitemaps"][:2] + [f"{root}/sitemap.xml", f"{root}/sitemap_index.xml"]
    for candidate in candidates:
        if not candidate.startswith("http"):
            candidate = urllib.parse.urljoin(root, candidate)
        sm = fetch(candidate, timeout=timeout, accept="application/xml,text/xml,*/*")
        body_l = sm.text.lower()
        if sm.status != 200 or ("<urlset" not in body_l and "<sitemapindex" not in body_l):
            continue
        locs, is_index = parse_sitemap(sm.text)
        sitemap_source = candidate
        if is_index:
            for child in locs[:2]:
                sub = fetch(child, timeout=timeout, accept="application/xml,text/xml,*/*")
                sub_locs, _ = parse_sitemap(sub.text)
                sitemap_urls += sub_locs
        else:
            sitemap_urls += locs
        if sitemap_urls:
            break
    sitemap_urls = list(dict.fromkeys(sitemap_urls))[:2000]
    checks["sitemap"] = {
        "found": bool(sitemap_urls),
        "url": sitemap_source,
        "url_count": len(sitemap_urls),
    }

    # 4. Homepage metadata + structured data + ingestibility
    home = pre["home"]
    meta = page_meta(home.text) if home.ok else page_meta("")
    checks["homepage"] = {
        **home.to_dict(),
        "title": meta["title"],
        "description": meta["description"],
        "h1": meta["h1"],
        "word_count": meta["word_count"],
        "token_estimate": meta["token_estimate"],
        "html_bytes": meta.get("html_bytes", 0),
    }
    ld_types = set(meta["json_ld_types"])
    checks["structured_data"] = {
        "json_ld_blocks": len(meta["json_ld_types"]),
        "types": sorted(ld_types),
        "has_organization": bool(ld_types & {"Organization", "LocalBusiness", "Corporation", "NGO", "EducationalOrganization"}),
        "has_website": "WebSite" in ld_types,
        "rich_types": sorted(ld_types & {"Product", "FAQPage", "Article", "BreadcrumbList", "SoftwareApplication", "HowTo", "TechArticle"}),
    }

    # 5. Machine-readable API surface
    api_found: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(OPENAPI_CANDIDATES)) as pool:
        probed = list(
            pool.map(
                lambda p: (p, fetch(f"{root}{p}", timeout=min(timeout, 7.0), max_bytes=400_000)),
                OPENAPI_CANDIDATES,
            )
        )
    for path, probe in probed:
        if probe.status != 200:
            continue
        head = probe.text.lstrip()[:200].lower()
        ctype = probe.headers.get("content-type", "")
        looks_json = head.startswith("{") or head.startswith("[")
        if "<html" in head and "openapi" not in head:
            continue
        if not (looks_json or "json" in ctype or "yaml" in ctype or "text/plain" in ctype):
            continue
        api_found.append({"path": path, "status": 200, "bytes": len(probe.text)})
    internal_links = extract_internal_links(home.text, host) if home.ok else []
    api_link = next(
        ((t, u) for t, u in internal_links if re.search(r"(api|developer|reference)", u, re.I)),
        None,
    )
    checks["api_discoverability"] = {
        "machine_readable_endpoints": api_found,
        "api_link_on_homepage": api_link[1] if api_link else None,
    }

    text_ratio = 0.0
    if meta.get("html_bytes"):
        text_ratio = meta["word_count"] / max(1, meta["html_bytes"] / 1000)

    # 6. Candidate pages for the generated llms.txt
    pool: List[str] = []
    if sitemap_urls:
        pool = sitemap_urls
    elif internal_links:
        pool = [u for _, u in internal_links]
    pool = list(dict.fromkeys(pool))

    docs_pool = _bucket(pool, r"/(docs?|documentation|guides?|learn|manual|handbook|reference|tutorials?)(/|$)")
    api_pool = _bucket(pool, r"/(api|graphql|openapi|sdk|cli)(/|$)")
    product_pool = _bucket(pool, r"/(pricing|features|product|products|solutions|use-cases|platform)(/|$)")
    company_pool = _bucket(pool, r"/(about|company|blog|careers|contact|team|customers)(/|$)")

    def top(urls: List[str], limit: int) -> List[Tuple[str, str]]:
        ranked = sorted(urls, key=lambda u: (len(urllib.parse.urlparse(u).path), u))
        return [(humanize_path(u), u) for u in ranked[:limit]]

    docs = top(docs_pool, 15)
    api = top(api_pool, 6)
    product = top(product_pool, 6)
    company = top(company_pool, 4)
    if not (docs or api or product):
        docs = [(humanize_path(u), u) for u in pool[:8]]

    brand = (meta["site_name"] or meta["title"] or host.split(".")[0].title()).split("|")[0].split("—")[0].strip()[:80]
    description = meta["description"] or f"{brand} website ({host})."
    generated = render_llms_txt(brand, description[:300], docs, api, product, company)

    # 7. Scoring
    dims = {
        "llms_txt": {"max": 30, "score": 0, "detail": ""},
        "ai_crawler_access": {"max": 20, "score": 0, "detail": ""},
        "sitemap": {"max": 10, "score": 0, "detail": ""},
        "structured_data": {"max": 10, "score": 0, "detail": ""},
        "api_discoverability": {"max": 15, "score": 0, "detail": ""},
        "content_ingestibility": {"max": 15, "score": 0, "detail": ""},
    }

    lt = checks["llms_txt"]
    if lt["found"] and lt.get("valid"):
        dims["llms_txt"]["score"] = 30
        dims["llms_txt"]["detail"] = f"Valid llms.txt with {lt['link_count']} links."
    elif lt["found"]:
        dims["llms_txt"]["score"] = max(10, 30 - 5 * len(lt.get("issues", [])))
        dims["llms_txt"]["detail"] = f"llms.txt present but has {len(lt.get('issues', []))} spec issue(s)."
    elif checks["llms_full_txt"]["found"]:
        dims["llms_txt"]["score"] = 5
        dims["llms_txt"]["detail"] = "Only llms-full.txt is published; add the link index llms.txt."
    else:
        dims["llms_txt"]["detail"] = "No llms.txt found."

    ratio = (len(allowed) / len(AI_CRAWLERS)) if AI_CRAWLERS else 0.0
    dims["ai_crawler_access"]["score"] = round(ratio * 20, 1)
    dims["ai_crawler_access"]["detail"] = (
        f"{len(allowed)}/{len(AI_CRAWLERS)} AI crawlers allowed"
        + (f"; blocked: {', '.join(blocked[:6])}" if blocked else ".")
    )

    if checks["sitemap"]["found"]:
        dims["sitemap"]["score"] = 10
        dims["sitemap"]["detail"] = f"{checks['sitemap']['url_count']} URLs discovered."
    else:
        dims["sitemap"]["detail"] = "No sitemap found; agents cannot enumerate your pages."

    sd = checks["structured_data"]
    sd_score = (5 if sd["has_organization"] else 0) + (3 if sd["has_website"] else 0) + (2 if sd["rich_types"] else 0)
    dims["structured_data"]["score"] = min(10, sd_score)
    dims["structured_data"]["detail"] = (
        f"JSON-LD types: {', '.join(sd['types']) if sd['types'] else 'none'}"
    )

    ad = checks["api_discoverability"]
    ad_score = 0
    if any(e["path"].endswith(("openapi.json", "openapi.yaml", "swagger.json")) for e in ad["machine_readable_endpoints"]):
        ad_score += 8
    if any(e["path"] == "/.well-known/mcp.json" for e in ad["machine_readable_endpoints"]):
        ad_score += 5
    if any(e["path"] == "/.well-known/ai-plugin.json" for e in ad["machine_readable_endpoints"]):
        ad_score += 2
    if ad["api_link_on_homepage"]:
        ad_score += 3
    dims["api_discoverability"]["score"] = min(15, ad_score)
    dims["api_discoverability"]["detail"] = (
        f"{len(ad['machine_readable_endpoints'])} machine-readable endpoint(s) found"
        + (f"; docs link: {ad['api_link_on_homepage']}" if ad["api_link_on_homepage"] else "")
    )

    ci = 0
    if home.ok:
        ci += 4
    if len(meta["title"]) >= 10:
        ci += 2
    if len(meta["description"]) >= 40:
        ci += 2
    if meta["word_count"] >= 200:
        ci += 3
    if 0 < meta["token_estimate"] <= 60_000:
        ci += 2
    if text_ratio >= 1.0:
        ci += 2
    dims["content_ingestibility"]["score"] = min(15, ci)
    dims["content_ingestibility"]["detail"] = (
        f"{meta['word_count']} words (~{meta['token_estimate']} tokens) on the homepage; "
        f"text/byte ratio {text_ratio:.2f}"
    )

    total = round(sum(d["score"] for d in dims.values()), 1)
    grade = "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 50 else "D" if total >= 30 else "F"

    # 8. Prioritised recommendations
    recs: List[Dict[str, Any]] = []
    if not lt["found"]:
        recs.append(
            {
                "id": "publish_llms_txt",
                "severity": "high",
                "impact_points": 30,
                "title": "Publish /llms.txt",
                "detail": "A generated, spec-compliant file is included in `generated_llms_txt`; host it at "
                          f"{root}/llms.txt as text/plain.",
            }
        )
    elif not lt.get("valid"):
        recs.append(
            {
                "id": "fix_llms_txt",
                "severity": "high",
                "impact_points": 30 - dims["llms_txt"]["score"],
                "title": "Fix llms.txt spec violations",
                "detail": "; ".join(i["detail"] for i in lt.get("issues", [])[:4]),
            }
        )
    if blocked:
        recs.append(
            {
                "id": "allow_ai_crawlers",
                "severity": "high",
                "impact_points": round(20 - dims["ai_crawler_access"]["score"], 1),
                "title": f"Unblock {len(blocked)} AI crawler(s) in robots.txt",
                "detail": f"Currently blocked: {', '.join(blocked)}. A ready-to-paste policy is in "
                          "`robots_policy_snippet`. Note: this is a business decision — blocking may be intentional.",
            }
        )
    if not checks["sitemap"]["found"]:
        recs.append(
            {
                "id": "add_sitemap",
                "severity": "medium",
                "impact_points": 10,
                "title": "Publish sitemap.xml",
                "detail": "Agents need an enumeration source to resolve links inside llms.txt.",
            }
        )
    if not sd["has_organization"]:
        recs.append(
            {
                "id": "add_organization_jsonld",
                "severity": "medium",
                "impact_points": 5,
                "title": "Add Organization JSON-LD",
                "detail": "Entity disambiguation is the strongest predictor of being named correctly in LLM answers.",
            }
        )
    if not ad["machine_readable_endpoints"]:
        recs.append(
            {
                "id": "publish_openapi",
                "severity": "medium",
                "impact_points": 8,
                "title": "Publish an OpenAPI description",
                "detail": "Expose /openapi.json so tool-using agents can call your API without scraping HTML.",
            }
        )
    if not meta["description"]:
        recs.append(
            {
                "id": "add_meta_description",
                "severity": "low",
                "impact_points": 2,
                "title": "Add a meta description",
                "detail": "Used as the llms.txt summary and as an AI answer snippet.",
            }
        )
    recs.sort(key=lambda r: (-r["impact_points"], r["severity"]))

    return {
        "domain": host,
        "url": root,
        "status": "OK" if home.ok else "PARTIAL",
        "audited_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "agent_readiness_score": total,
        "grade": grade,
        "dimensions": dims,
        "checks": checks,
        "ai_crawler_policy": {
            "allowed": allowed,
            "blocked": blocked,
            "unspecified": [p["bot"] for p in policy if p["access"] == "unspecified"],
        },
        "context_cost": {
            "homepage_tokens": meta["token_estimate"],
            "homepage_words": meta["word_count"],
            "text_to_byte_ratio": round(text_ratio, 2),
        },
        "generated_llms_txt": generated,
        "robots_policy_snippet": render_robots_policy(blocked),
        "recommendations": recs,
        "meta": {"actor": ACTOR_SLUG, "version": ACTOR_VERSION, "pages_sampled": max_pages},
    }


def audit_many(domains: List[str], max_pages: int = 4) -> List[Dict[str, Any]]:
    return [audit_site(d, max_pages=max_pages) for d in domains]


# --------------------------------------------------------------------------------------
# Apify plumbing
# --------------------------------------------------------------------------------------


async def _charge(event: str, count: int = 1) -> None:
    if not Actor:
        return
    try:
        await Actor.charge(event_name=event, count=count)
    except Exception as exc:  # charging failures must never break the run
        print(f"[WARN] charge {event} x{count} failed: {exc}", file=sys.stderr)


def _targets(actor_input: Dict[str, Any]) -> List[str]:
    domains: List[str] = []
    single = (actor_input.get("domain") or "").strip()
    if single:
        domains.append(single)
    extra = actor_input.get("domains") or []
    if isinstance(extra, str):
        extra = [x.strip() for x in extra.split(",")]
    for item in extra:
        if isinstance(item, str) and item.strip():
            domains.append(item.strip())
    if not domains:
        domains = ["stripe.com"]
    deduped: List[str] = []
    for d in domains:
        if d not in deduped:
            deduped.append(d)
    return deduped[:25]


async def run_batch(actor_input: Dict[str, Any]) -> List[Dict[str, Any]]:
    targets = _targets(actor_input)
    max_pages = int(actor_input.get("maxPages") or 4)
    print(f"[BATCH] Auditing {len(targets)} domain(s): {targets}")
    loop = asyncio.get_running_loop()
    records = await loop.run_in_executor(None, audit_many, targets, max_pages)

    if Actor:
        await _charge("actor-start", 1)
        await Actor.push_data(records)
        await _charge("site-audited", len(records))
    else:
        print(json.dumps(records, indent=2))
    print(f"[BATCH] Completed {len(records)} audit(s).")
    return records


STANDBY_HTML = """<!doctype html><html><head><title>llms.txt Agent Packager</title></head>
<body style="font-family:system-ui;max-width:56rem;margin:3rem auto;line-height:1.5">
<h1>llms.txt Agent Packager</h1>
<p>Audit a domain for AI-agent readability and generate an <code>llms.txt</code>.</p>
<pre>curl "https://ACTOR-URL?domain=example.com"</pre>
<p>Batch: <code>?domains=example.com,example.org</code></p>
</body></html>"""


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            await writer.wait_closed()
            return
        parts = request_line.decode("utf-8", errors="ignore").strip().split()
        if len(parts) < 2:
            writer.close()
            await writer.wait_closed()
            return
        method, path = parts[0].upper(), parts[1]

        headers: Dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("utf-8", errors="ignore").strip()
            if ":" in decoded:
                k, v = decoded.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        if "x-apify-container-server-readiness-probe" in headers or path.startswith("/healthz"):
            body = b'{"status":"ready"}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        parsed = urllib.parse.urlparse(path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path in ("/", "/docs", "/index.html") and not (query.get("domain") or query.get("domains")):
            body = STANDBY_HTML.replace("ACTOR-URL", f"https://{headers.get('host', 'localhost')}").encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
                + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        await _charge("actor-start", 1)

        if parsed.path.rstrip("/") not in ("/audit", "/generate"):
            target_domains = [d for d in parsed.path.strip("/").split(",") if d]
        else:
            target_domains = []

        target_domains += query.get("domain", [])
        if "domains" in query:
            target_domains += [d.strip() for d in query["domains"][0].split(",") if d.strip()]
        target_domains = [d for d in dict.fromkeys(target_domains) if d][:10]

        if not target_domains:
            payload = {
                "status": "error",
                "error": "missing_domain",
                "hint": "Pass ?domain=example.com or ?domains=a.com,b.com",
            }
            body = json.dumps(payload, indent=2).encode()
            status_line = b"HTTP/1.1 400 Bad Request\r\n"
        else:
            await _charge("site-audited", len(target_domains))
            loop = asyncio.get_running_loop()
            try:
                max_pages = int(query.get("maxPages", ["4"])[0])
            except ValueError:
                max_pages = 4
            results = await loop.run_in_executor(None, audit_many, target_domains, max_pages)
            payload = {
                "status": "success",
                "actor": ACTOR_SLUG,
                "version": ACTOR_VERSION,
                "pricing_model": "PAY_PER_EVENT",
                "count": len(results),
                "data": results,
            }
            body = json.dumps(payload, indent=2).encode()
            status_line = b"HTTP/1.1 200 OK\r\n"
    except Exception as exc:  # pragma: no cover - defensive
        body = json.dumps({"status": "error", "message": f"{type(exc).__name__}: {exc}"}).encode()
        status_line = b"HTTP/1.1 500 Internal Server Error\r\n"

    writer.write(
        status_line
        + b"Content-Type: application/json; charset=utf-8\r\n"
        + b"Access-Control-Allow-Origin: *\r\n"
        + b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        + b"Connection: close\r\n\r\n" + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def run_server(port: int) -> None:
    server = await asyncio.start_server(handle_client, "0.0.0.0", port)
    print(f"[STANDBY] llms.txt Agent Packager listening on 0.0.0.0:{port}")
    async with server:
        await server.serve_forever()


async def main() -> None:
    if "--cli" in sys.argv:
        idx = sys.argv.index("--cli")
        target = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "stripe.com"
        print(json.dumps(audit_site(target), indent=2))
        return

    if "--server" in sys.argv:
        await run_server(int(os.environ.get("ACTOR_WEB_SERVER_PORT", "8080")))
        return

    if Actor:
        async with Actor:
            actor_input = await Actor.get_input() or {}
            if str(actor_input.get("runMode", "BATCH")).upper() == "STANDBY_SERVER":
                port = int(os.environ.get("ACTOR_WEB_SERVER_PORT", "8080"))
                web_port = getattr(getattr(Actor, "configuration", None), "web_server_port", None)
                await run_server(web_port or port)
            else:
                await run_batch(actor_input)
    else:
        await run_batch({})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
