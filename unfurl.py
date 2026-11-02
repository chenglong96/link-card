"""Turn a URL into the metadata needed for an embed card.

Resolution order (roughly what iframely does):
  1. oEmbed - known provider endpoints (YouTube, Vimeo, Spotify, X, ...) or the
     endpoint a page advertises with <link type="application/json+oembed">.
  2. Open Graph / Twitter Card / standard <meta> tags / JSON-LD on the page.
  3. Fallbacks: <title>, <h1>, twitter:player, /favicon.ico, the domain name.
"""
from __future__ import annotations

import base64
import dataclasses
import html
import io
import ipaddress
import json
import os
import re
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TIMEOUT = 10
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5
# Private/intranet addresses are refused so a hosted copy can't be used to probe
# your network. Set LINKCARD_ALLOW_PRIVATE=1 to unfurl intranet links locally.
ALLOW_PRIVATE = os.environ.get("LINKCARD_ALLOW_PRIVATE") == "1"

# Providers whose oEmbed endpoint is known up front. Many of these block
# scrapers or render client-side, so asking oEmbed directly is more reliable.
# (pattern on host+path with "www."/"m." stripped, endpoint, trust provider HTML)
# Trusted providers embed via their own <script>; everyone else's oEmbed HTML is
# reduced to a single iframe we rebuild ourselves.
OEMBED_PROVIDERS = [
    (r"^(youtube\.com/(watch|shorts/|live/)|youtu\.be/)", "https://www.youtube.com/oembed", False),
    (r"^vimeo\.com/\d+", "https://vimeo.com/api/oembed.json", False),
    (r"^open\.spotify\.com/", "https://open.spotify.com/oembed", False),
    (r"^soundcloud\.com/[^/]+/", "https://soundcloud.com/oembed", False),
    (r"^loom\.com/share/", "https://www.loom.com/v1/oembed", False),
    (r"^codepen\.io/[^/]+/pen/", "https://codepen.io/api/oembed", False),
    (r"^(twitter|x)\.com/\w+/status/\d+", "https://publish.twitter.com/oembed", True),
    (r"^tiktok\.com/@[^/]+/video/\d+", "https://www.tiktok.com/oembed", True),
    (r"^(old\.)?reddit\.com/r/[^/]+/comments/", "https://www.reddit.com/oembed", True),
]


class UnfurlError(Exception):
    pass


@dataclass
class LinkMeta:
    url: str
    title: str = ""
    description: str = ""
    site_name: str = ""
    author: str = ""
    published: str = ""        # ISO date (YYYY-MM-DD) the content was published
    image: str = ""
    favicon: str = ""
    theme_color: str = ""
    player_url: str = ""       # iframe src of a rich player (video, audio, ...)
    player_ratio: float = 0.0  # height / width, for responsive video players
    player_height: int = 0     # fixed height, for audio and other rich players
    player_html: str = ""      # provider HTML, only for trusted script embeds
    source: str = ""           # which strategies produced the data

    @property
    def domain(self) -> str:
        return (urlparse(self.url).hostname or "").removeprefix("www.")

    @property
    def has_player(self) -> bool:
        return bool(self.player_url or self.player_html)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise UnfurlError("Please enter a URL.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    return url


def unfurl(url: str) -> LinkMeta:
    """Fetch `url` and return everything needed to draw its card."""
    url = normalize_url(url)
    meta = LinkMeta(url=url)
    sources: list[str] = []

    known = _known_provider(url)
    if known:
        endpoint, trusted = known
        if _try_oembed(meta, f"{endpoint}?{urlencode({'url': url, 'format': 'json'})}", trusted):
            sources.append("oembed")

    page = None
    try:
        page = _get(url, MAX_PAGE_BYTES, truncate=True)
    except (requests.RequestException, UnfurlError) as exc:
        if not sources:
            raise UnfurlError(f"Could not fetch {url}: {exc}") from exc

    soup = None
    if page:
        final_url, ctype, body, encoding = page
        meta.url = final_url
        if ctype.startswith("image/"):
            meta.image = final_url
            _fill(meta, "title", _filename(final_url))
            sources.append("image")
        elif "html" in ctype or not ctype:
            soup = BeautifulSoup(body, "lxml", from_encoding=encoding)
            _apply_html(meta, soup, final_url)
            sources.append("html")
            if not known and not meta.has_player:
                link = soup.find("link", type="application/json+oembed", href=True)
                if link and _try_oembed(meta, urljoin(final_url, link["href"]), trusted=False):
                    sources.append("oembed")
        else:
            _fill(meta, "title", _filename(final_url))
            _fill(meta, "description", ctype.split(";")[0])
            sources.append("file")

    _apply_fallbacks(meta, soup)
    meta.source = "+".join(sources)
    return meta


def inline_images(meta: LinkMeta, max_width: int = 1000) -> LinkMeta:
    """Return a copy with the image and favicon embedded as data: URIs, so the
    card renders with no network access and survives the site removing them."""
    return dataclasses.replace(
        meta,
        image=_to_data_uri(meta.image, max_width) if meta.image else "",
        favicon=_to_data_uri(meta.favicon, 64) if meta.favicon else "",
    )


# --- fetching ---------------------------------------------------------------

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnfurlError(f"Only http(s) URLs are supported: {url}")
    if ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror:
        raise UnfurlError(f"Could not resolve host: {parsed.hostname}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnfurlError(
                f"Refusing to fetch private address {ip} ({parsed.hostname}). "
                "Set LINKCARD_ALLOW_PRIVATE=1 to allow intranet links."
            )


def _get(url: str, max_bytes: int, accept: str = "text/html,application/xhtml+xml,*/*;q=0.8",
         truncate: bool = False) -> tuple[str, str, bytes, str | None]:
    """GET following redirects one hop at a time (each hop's host is checked),
    with a download cap. Returns (final_url, content_type, body, charset)."""
    for _ in range(MAX_REDIRECTS + 1):
        _check_host(url)
        resp = _session.get(url, headers={"Accept": accept}, timeout=TIMEOUT,
                            stream=True, allow_redirects=False)
        with resp:
            if resp.is_redirect:
                url = urljoin(url, resp.headers["Location"])
                continue
            resp.raise_for_status()
            body = bytearray()
            for chunk in resp.iter_content(64 * 1024):
                body += chunk
                if len(body) > max_bytes:
                    if not truncate:
                        raise UnfurlError(f"Response too large: {url}")
                    del body[max_bytes:]
                    break
            ctype = resp.headers.get("Content-Type", "").lower()
            charset = resp.encoding if "charset" in ctype else None
            return url, ctype, bytes(body), charset
    raise UnfurlError(f"Too many redirects: {url}")


def _to_data_uri(url: str, max_width: int) -> str:
    if url.startswith("data:"):
        return url
    try:
        _, ctype, body, _ = _get(url, MAX_IMAGE_BYTES, accept="image/*")
    except (requests.RequestException, UnfurlError):
        return url  # keep the remote image rather than dropping it
    ctype = ctype.split(";")[0].strip()
    if not ctype.startswith("image/"):
        return url
    body, ctype = _shrink(body, ctype, max_width)
    return f"data:{ctype};base64,{base64.b64encode(body).decode()}"


def _shrink(body: bytes, ctype: str, max_width: int) -> tuple[bytes, str]:
    """Downscale big raster images so data: URIs stay a sensible size."""
    if ctype in ("image/svg+xml", "image/gif"):  # keep vectors and animation
        return body, ctype
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(body))
        if img.width <= max_width and len(body) < 300_000:
            return body, ctype
        img.thumbnail((max_width, max_width * 4))
        out = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img.save(out, "PNG", optimize=True)
            new_type = "image/png"
        else:
            img.convert("RGB").save(out, "JPEG", quality=82, optimize=True)
            new_type = "image/jpeg"
    except Exception:  # Pillow missing or an image format it can't read
        return body, ctype
    return (out.getvalue(), new_type) if out.tell() < len(body) else (body, ctype)


# --- oEmbed -----------------------------------------------------------------

def _known_provider(url: str) -> tuple[str, bool] | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")
    for pattern, endpoint, trusted in OEMBED_PROVIDERS:
        if re.match(pattern, host + parsed.path, re.I):
            return endpoint, trusted
    return None


def _try_oembed(meta: LinkMeta, endpoint: str, trusted: bool) -> bool:
    try:
        _, _, body, _ = _get(endpoint, 1024 * 1024, accept="application/json")
        data = json.loads(body)
    except (requests.RequestException, UnfurlError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    _apply_oembed(meta, data, trusted)
    return True


def _apply_oembed(meta: LinkMeta, data: dict, trusted: bool) -> None:
    author = _clean(data.get("author_name"))
    provider = _clean(data.get("provider_name"))
    title = _clean(data.get("title"), 200)
    if not title and trusted and author:  # tweets etc. have no title
        title = f"{author} on {provider}" if provider else author
    _fill(meta, "title", title)
    _fill(meta, "author", author)
    _fill(meta, "site_name", provider)
    _fill(meta, "published", _parse_date(data.get("upload_date")))  # Vimeo and others
    image = data.get("thumbnail_url") or (data.get("url") if data.get("type") == "photo" else "")
    _fill(meta, "image", _abs_url(image, meta.url))

    html = data.get("html") or ""
    if not html or meta.has_player:
        return
    if trusted:
        meta.player_html = html
        quote = BeautifulSoup(html, "lxml").find("blockquote")
        if quote:
            _fill(meta, "description", _clean((quote.find("p") or quote).get_text(" "), 300))
            links = quote.find_all("a")  # tweets end with a "March 21, 2006"-style link
            if links and re.search(r"\b(19|20)\d{2}\b", links[-1].get_text()):
                _fill(meta, "published", _parse_date(links[-1].get_text()))
        return
    frame = BeautifulSoup(html, "lxml").find("iframe", src=True)
    if not frame:
        return
    src = _abs_url(frame["src"], "https://")
    if not src:
        return
    meta.player_url = src
    width = _to_int(data.get("width")) or _to_int(frame.get("width"))
    height = _to_int(data.get("height")) or _to_int(frame.get("height"))
    if data.get("type") == "video" and width and height:
        meta.player_ratio = height / width
    else:
        meta.player_height = height or 400


# --- HTML meta tags ---------------------------------------------------------

def _apply_html(meta: LinkMeta, soup: BeautifulSoup, base: str) -> None:
    ld = _json_ld(soup)
    _fill(meta, "title", _clean(_meta_content(soup, "og:title", "twitter:title") or ld.get("headline"), 200))
    _fill(meta, "description", _clean(
        _meta_content(soup, "og:description", "twitter:description", "description") or ld.get("description"), 300))
    _fill(meta, "site_name", _clean(_meta_content(soup, "og:site_name", "application-name")))
    author = _clean(_meta_content(soup, "author", "article:author", "twitter:creator") or _ld_value(ld.get("author")))
    _fill(meta, "author", "" if author.startswith("http") else author)
    _fill(meta, "published", _parse_date(
        _meta_content(soup, "article:published_time", "og:article:published_time", "article:published",
                      "datePublished", "uploadDate", "video:release_date", "music:release_date",
                      "citation_publication_date", "citation_date", "dc.date.issued", "DC.date.issued",
                      "dcterms.issued", "DC.date", "parsely-pub-date", "sailthru.date", "publish-date",
                      "publishdate", "pubdate", "date")
        or ld.get("datePublished") or ld.get("uploadDate") or ld.get("dateCreated")))

    image = (_meta_content(soup, "og:image:secure_url", "og:image", "og:image:url",
                           "twitter:image", "twitter:image:src") or _ld_value(ld.get("image")))
    if not image:
        link = soup.find("link", rel="image_src", href=True)
        image = link["href"] if link else ""
    _fill(meta, "image", _abs_url(image, base))
    _fill(meta, "theme_color", _clean(_meta_content(soup, "theme-color")))
    _fill(meta, "favicon", _favicon(soup, base))


def _apply_fallbacks(meta: LinkMeta, soup: BeautifulSoup | None) -> None:
    if soup is not None:
        if not meta.has_player:
            _twitter_player(meta, soup)
        if not meta.title and soup.title:
            meta.title = _clean(soup.title.get_text(), 200)
        if not meta.title and (h1 := soup.find("h1")):
            meta.title = _clean(h1.get_text(), 200)
        if not meta.description:  # first real paragraph of the main content
            scope = soup.find("article") or soup.find("main") or soup
            for p in scope.find_all("p", limit=30):
                text = _clean(re.sub(r"\[\w{1,3}\]", "", p.get_text()))  # drop [1]-style refs
                if len(text) >= 80:
                    meta.description = _clean(text, 300)
                    break
        # <time datetime="..."> in the article - only on single-article pages, so
        # listing/home pages don't borrow the date of the first post they list.
        articles = soup.find_all("article", limit=2)
        if not meta.published and len(articles) == 1:
            if time_tag := articles[0].find("time", datetime=True):
                meta.published = _parse_date(time_tag["datetime"])
    parsed = urlparse(meta.url)
    _fill(meta, "site_name", meta.domain)
    _fill(meta, "title", meta.url)
    _fill(meta, "favicon", f"{parsed.scheme}://{parsed.netloc}/favicon.ico")


def _twitter_player(meta: LinkMeta, soup: BeautifulSoup) -> None:
    src = _abs_url(_meta_content(soup, "twitter:player"), meta.url)
    if not src.startswith("https://"):
        return
    meta.player_url = src
    width = _to_int(_meta_content(soup, "twitter:player:width"))
    height = _to_int(_meta_content(soup, "twitter:player:height"))
    if width and height:
        meta.player_ratio = height / width
    else:
        meta.player_height = height or 360


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        for attr in ("property", "name", "itemprop"):
            tag = soup.find("meta", attrs={attr: name, "content": True})
            if tag and tag["content"].strip():
                return tag["content"]
    return ""


def _json_ld(soup: BeautifulSoup) -> dict:
    """First JSON-LD object that looks like the page's main entity."""
    items: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            queue = [json.loads(tag.string or "")]
        except ValueError:
            continue
        for node in queue:  # breadth-first through lists and @graph
            if isinstance(node, list):
                queue.extend(node)
            elif isinstance(node, dict):
                items.append(node)
                if "@graph" in node:
                    queue.append(node["@graph"])
    for key in ("headline", "description"):
        for item in items:
            if item.get(key):
                return item
    return {}


def _ld_value(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("name") or ""
    return value if isinstance(value, str) else ""


def _favicon(soup: BeautifulSoup, base: str) -> str:
    """The declared icon closest to 64px (crisp at 16px on retina, still small)."""
    best, best_score = "", None
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])).lower()
        if "icon" not in rel or "mask-icon" in rel:
            continue
        sizes = link.get("sizes", "")
        match = re.match(r"(\d+)", sizes)
        size = int(match.group(1)) if match else 512 if sizes == "any" else 180 if "apple" in rel else 32
        score = abs(size - 64)
        if best_score is None or score < best_score:
            best, best_score = _abs_url(link["href"], base), score
    return best


# --- helpers ----------------------------------------------------------------

def _fill(meta: LinkMeta, field: str, value) -> None:
    """Set a field only if it is still empty (earlier sources win)."""
    if value and not getattr(meta, field):
        setattr(meta, field, value)


def _clean(value, limit: int = 0) -> str:
    text = str(value or "")
    if "<" in text or "&" in text:  # some sites put HTML in their descriptions
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def _parse_date(value) -> str:
    """Any common date format (ISO 8601, RFC 2822, 'March 5, 2024', Unix time)
    -> 'YYYY-MM-DD', or '' if it isn't a plausible date."""
    text = _clean(value)
    if not text:
        return ""
    try:
        if text.isdigit() and len(text) in (10, 13):  # Unix seconds / milliseconds
            parsed = datetime.fromtimestamp(int(text[:10]), tz=timezone.utc)
        else:
            parsed = date_parser.parse(text)
    except (ValueError, OverflowError, OSError):
        return ""
    if not 1900 <= parsed.year <= datetime.now().year + 1:
        return ""
    return parsed.date().isoformat()


def _abs_url(url, base: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    full = urljoin(base, url)
    return full if full.startswith(("http://", "https://")) else ""


def _to_int(value) -> int:
    try:
        return int(float(str(value).removesuffix("px")))
    except (TypeError, ValueError):
        return 0


def _filename(url: str) -> str:
    return unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]) or urlparse(url).hostname or url
