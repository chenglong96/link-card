"""Render LinkMeta as self-contained HTML embed cards.

The output is plain HTML + a scoped <style> block, so the embed code can be
pasted into any web page, CMS, or saved as a file - no server needed to view it.
"""
from __future__ import annotations

import dataclasses
import re
from datetime import date
from html import escape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from unfurl import LinkMeta

SIZES = ("large", "small", "player")
THEMES = ("light", "dark", "auto")
DEFAULT_WIDTHS = {"large": 520, "small": 600, "player": 640}
# Height presets. Large: the whole card (the image is cropped to fit). Small: the
# card's height (the thumbnail is square). Player: see preset_height().
DEFAULT_HEIGHTS = {"large": 440, "small": 140}
HEIGHT_RANGES = {"large": (160, 900), "small": (72, 320), "player": (80, 900)}

_DARK_VARS = ("--lc-bg:#16181c;--lc-fg:#e7e9ea;--lc-muted:#8b98a5;"
              "--lc-border:#2f3336;--lc-hover:#1d1f23;--lc-thumb:#202327")

CSS = (
    ".lc{--lc-bg:#fff;--lc-fg:#0f1419;--lc-muted:#5b6670;--lc-border:#dfe3e8;--lc-hover:#f6f8fa;--lc-thumb:#eef1f4;"
    "display:block;width:100%;margin:0 auto;color:var(--lc-fg);background:var(--lc-bg);"
    "border:1px solid var(--lc-border);border-radius:12px;overflow:hidden;text-align:left;"
    "font:14px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}"
    ".lc *{box-sizing:border-box;margin:0;padding:0}"
    f".lc-dark{{{_DARK_VARS}}}"
    f"@media (prefers-color-scheme:dark){{.lc-auto{{{_DARK_VARS}}}}}"
    ".lc a.lc-link{display:block;color:inherit;text-decoration:none}"
    ".lc a.lc-link:hover{background:var(--lc-hover)}"
    ".lc-large a.lc-link{display:flex;flex-direction:column;max-height:var(--lc-h,none)}"
    ".lc-large .lc-media{flex:0 1 auto;min-height:0}"
    ".lc-large .lc-body{flex:none}"
    ".lc-media{position:relative;aspect-ratio:1.91/1;background:var(--lc-thumb);overflow:hidden}"
    ".lc-media img,.lc-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}"
    ".lc-body{display:flex;flex-direction:column;gap:4px;min-width:0;padding:12px 14px 14px}"
    ".lc-title{font-size:15px;font-weight:600;color:var(--lc-fg);overflow:hidden;"
    "display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2}"
    ".lc-desc{color:var(--lc-muted);overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3}"
    ".lc-meta{display:flex;align-items:center;gap:6px;margin-top:4px;color:var(--lc-muted);font-size:12.5px;"
    "white-space:nowrap;overflow:hidden}"
    ".lc-meta img{width:16px;height:16px;border-radius:3px;flex:none}"
    ".lc-meta span{overflow:hidden;text-overflow:ellipsis}"
    ".lc-meta .lc-date{flex:none}"
    ".lc-small a.lc-link{display:flex;height:var(--lc-h,140px)}"
    ".lc-small .lc-body{flex:1;justify-content:safe center;overflow:hidden}"
    ".lc-small .lc-desc{-webkit-line-clamp:2}"
    ".lc-thumb{position:relative;flex:none;width:min(40%,var(--lc-h,140px));background:var(--lc-thumb);"
    "border-right:1px solid var(--lc-border)}"
    ".lc-player{position:relative;background:var(--lc-thumb)}"
    ".lc-player.lc-ratio{background:#000}"
    ".lc-player iframe{display:block;width:100%;border:0}"
    ".lc-ratio iframe{position:absolute;inset:0;height:100%}"
)

_IFRAME_ALLOW = "autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"


def effective_size(meta: LinkMeta, size: str) -> str:
    """'player' falls back to 'large' for links with no embeddable player."""
    return "large" if size == "player" and not meta.has_player else size


def preset_height(meta: LinkMeta, size: str, width: int | None = None) -> int:
    """Default height for a size. For players it's the provider's own size:
    a fixed height (Spotify, SoundCloud) or the video's height at `width`."""
    size = effective_size(meta, size)
    if size != "player":
        return DEFAULT_HEIGHTS[size]
    if meta.player_ratio:
        return round((width or DEFAULT_WIDTHS["player"]) * meta.player_ratio)
    return meta.player_height or 400


def render_card(meta: LinkMeta, size: str = "large", theme: str = "light",
                max_width: int | None = None, show_date: bool = True,
                max_height: int | None = None, show_desc: bool = True) -> str:
    """Embed-code HTML for one card. Falls back to 'large' if 'player' was
    requested for a link that has no embeddable player.

    max_height: large - the whole card (the image is cropped to fit); small -
    the card's height; player - the player's height (a video keeps its shape,
    so the card narrows instead). Ignored for X / TikTok / Reddit embeds.
    To play only part of a video, pass the result of clip_player() as meta."""
    if not show_desc:
        meta = dataclasses.replace(meta, description="")
    if size not in SIZES:
        raise ValueError(f"size must be one of {SIZES}")
    if theme not in THEMES:
        raise ValueError(f"theme must be one of {THEMES}")
    size = effective_size(meta, size)
    width = max_width or DEFAULT_WIDTHS[size]

    if size == "player" and meta.player_html:
        return _provider_embed(meta, theme, width)
    style = f"max-width:{width}px"
    if size == "player":
        height = max_height or preset_height(meta, size, width)
        if meta.player_ratio:  # fit the video inside width x height
            style = f"max-width:{min(width, round(height / meta.player_ratio))}px"
        body = _player(meta, show_date, height)
    else:
        if max_height:
            style += f";--lc-h:{max_height}px"
        body = (_large if size == "large" else _small)(meta, show_date)
    return (f"<style>{CSS}</style>\n"
            f'<div class="lc lc-{size} lc-{theme}" style="{style}">{body}</div>')


def render_page(card_html: str, theme: str = "light", title: str = "Link card") -> str:
    """Wrap a card in a standalone HTML document (for preview / download)."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
body{{margin:0;padding:16px;background:#f3f5f7}}
body.dark{{background:#0b0d0f}}
@media (prefers-color-scheme:dark){{body.auto{{background:#0b0d0f}}}}
</style>
</head>
<body class="{escape(theme)}">
{card_html}
</body>
</html>
"""


def _large(meta: LinkMeta, show_date: bool) -> str:
    image = _safe_url(meta.image)
    media = (f'<div class="lc-media"><img src="{image}" alt="" onerror="this.parentNode.remove()"></div>'
             if image else "")
    return f"{_open_link(meta)}{media}{_text(meta, show_date)}</a>"


def _small(meta: LinkMeta, show_date: bool) -> str:
    image = _safe_url(meta.image)
    thumb = (f'<div class="lc-thumb"><img src="{image}" alt="" onerror="this.parentNode.remove()"></div>'
             if image else "")
    return f"{_open_link(meta)}{thumb}{_text(meta, show_date)}</a>"


def _player(meta: LinkMeta, show_date: bool, height: int) -> str:
    src = _safe_url(meta.player_url)
    frame = f'<iframe src="{src}" title="{escape(meta.title)}" allow="{_IFRAME_ALLOW}" allowfullscreen'
    if meta.player_ratio:
        player = (f'<div class="lc-player lc-ratio" style="aspect-ratio:{1 / meta.player_ratio:.4f}">'
                  f"{frame}></iframe></div>")
    else:
        player = f'<div class="lc-player">{frame} height="{height}"></iframe></div>'
    return f"{player}{_open_link(meta)}{_text(meta, show_date)}</a>"


def _provider_embed(meta: LinkMeta, theme: str, width: int) -> str:
    """Script-based embeds (X/Twitter, TikTok, Reddit) style themselves."""
    html = meta.player_html
    if theme == "dark":
        html = html.replace('class="twitter-tweet"', 'class="twitter-tweet" data-theme="dark"', 1)
    return f'<div style="max-width:{width}px;margin:0 auto">{html}</div>'


def _open_link(meta: LinkMeta) -> str:
    return f'<a class="lc-link" href="{_safe_url(meta.url)}" target="_blank" rel="noopener">'


def _text(meta: LinkMeta, show_date: bool) -> str:
    parts = [f'<div class="lc-title">{escape(meta.title)}</div>']
    if meta.description:
        parts.append(f'<div class="lc-desc">{escape(_card_description(meta.description))}</div>')
    parts.append(_footer(meta, show_date))
    return f'<div class="lc-body">{"".join(parts)}</div>'


def _footer(meta: LinkMeta, show_date: bool) -> str:
    favicon = _safe_url(meta.favicon)
    icon = f'<img src="{favicon}" alt="" width="16" height="16" onerror="this.remove()">' if favicon else ""
    label = meta.site_name or meta.domain
    if meta.author and meta.author.lower() != label.lower():
        label += f" · {meta.author}"
    published = ""
    if show_date and meta.published:
        published = (f'<time class="lc-date" datetime="{escape(meta.published)}">'
                     f"· {_format_date(meta.published)}</time>")
    return f'<div class="lc-meta">{icon}<span>{escape(label)}</span>{published}</div>'


def _card_description(text: str, limit: int = 400) -> str:
    """The card shows at most 3 lines, so one paragraph of ~400 characters is
    plenty; this keeps a long video description out of the embed code."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def _format_date(iso: str) -> str:
    """'2024-03-05' -> '5 Mar 2024' (unambiguous in every locale)."""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return escape(iso)
    return f"{day.day} {day:%b %Y}"


def _safe_url(url: str) -> str:
    """Only http(s) links and data: images may reach an href/src."""
    return escape(url) if url.startswith(("https://", "http://", "data:image/")) else ""


# --- clips: play only part of a video ---------------------------------------

_YOUTUBE_EMBED_HOSTS = ("youtube.com", "youtube-nocookie.com")


def parse_time(text: str) -> int | None:
    """'3:16', '3m16s', '1:02:03', '1h2m3s', '196' or '196s' -> seconds.
    '' -> None. Raises ValueError for anything else."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?s?", text):
        return int(float(text.rstrip("s")))
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", text):
        first, *rest = (int(p) for p in text.split(":"))
        if any(p > 59 for p in rest):
            raise ValueError(f"'{text}' isn't a valid time: minutes and seconds go up to 59.")
        seconds = first
        for part in rest:
            seconds = seconds * 60 + part
        return seconds
    match = re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", text)
    if match and any(match.groups()):
        h, m, s = (int(g or 0) for g in match.groups())
        return h * 3600 + m * 60 + s
    raise ValueError(f"Can't read the time '{text}'. Use a form like 3:16, 3m16s or 196.")


def format_time(seconds: int) -> str:
    """196 -> '3:16', 3723 -> '1:02:03'."""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def clip_support(meta: LinkMeta) -> str:
    """What part-of-a-video options the player accepts: 'start-end' (YouTube),
    'start' (Vimeo), or '' (none)."""
    host = (urlparse(meta.player_url).hostname or "").removeprefix("www.")
    if host in _YOUTUBE_EMBED_HOSTS and "/embed/" in meta.player_url:
        return "start-end"
    if host == "player.vimeo.com":
        return "start"
    return ""


def start_time_from_url(url: str) -> int | None:
    """The start time a shared link asks for: YouTube ?t=1m30s / ?start=90,
    or Vimeo #t=90s."""
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query))
    fragment = dict(parse_qsl(parsed.fragment))
    for value in (params.get("t"), params.get("start"), fragment.get("t")):
        if value:
            try:
                return parse_time(value) or None
            except ValueError:
                pass
    return None


def clip_player(meta: LinkMeta, start: int | None, end: int | None) -> LinkMeta:
    """A copy whose player plays only start..end (seconds; None = from the
    beginning / to the end), replacing any start time the link already had.
    The card's link also opens the video at `start`.
    Raises ValueError if the player can't do it or the times don't make sense."""
    support = clip_support(meta)
    if not support:
        if start or end:
            raise ValueError("This player can't be set to start or stop at a chosen time "
                             "(only YouTube and Vimeo can).")
        return meta
    if end is not None and support != "start-end":
        raise ValueError("Vimeo players can start at a chosen time but can't stop early; "
                         "leave the end time empty.")
    start = start or None
    if end is not None and end <= (start or 0):
        raise ValueError("The end time must be after the start time.")
    if meta.duration:
        if start is not None and start >= meta.duration:
            raise ValueError(f"The start time is past the end of the video ({format_time(meta.duration)}).")
        if end is not None and end >= meta.duration:
            end = None  # "to the end" - the same thing, and avoids an off-by-one stop

    player, link = urlparse(meta.player_url), urlparse(meta.url)
    if support == "start-end":
        query = [(k, v) for k, v in parse_qsl(player.query) if k not in ("start", "end", "t")]
        query += [(k, str(v)) for k, v in (("start", start), ("end", end)) if v is not None]
        player = player._replace(query=urlencode(query))
        link_query = [(k, v) for k, v in parse_qsl(link.query) if k not in ("t", "start")]
        if start is not None:
            link_query.append(("t", f"{start}s"))
        link = link._replace(query=urlencode(link_query))
    else:  # Vimeo reads the start time from the fragment
        fragment = f"t={start}s" if start is not None else ""
        player, link = player._replace(fragment=fragment), link._replace(fragment=fragment)
    return dataclasses.replace(meta, player_url=urlunparse(player), url=urlunparse(link))
