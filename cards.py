"""Render LinkMeta as self-contained HTML embed cards.

The output is plain HTML + a scoped <style> block, so the embed code can be
pasted into any web page, CMS, or saved as a file - no server needed to view it.
"""
from __future__ import annotations

from datetime import date
from html import escape

from unfurl import LinkMeta

SIZES = ("large", "small", "player")
THEMES = ("light", "dark", "auto")
DEFAULT_WIDTHS = {"large": 520, "small": 600, "player": 640}

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
    ".lc-small a.lc-link{display:flex;min-height:104px}"
    ".lc-small .lc-body{flex:1;justify-content:center}"
    ".lc-small .lc-desc{-webkit-line-clamp:2}"
    ".lc-thumb{position:relative;flex:none;width:clamp(88px,28%,150px);background:var(--lc-thumb);"
    "border-left:1px solid var(--lc-border)}"
    ".lc-player{position:relative;background:var(--lc-thumb)}"
    ".lc-player.lc-ratio{background:#000}"
    ".lc-player iframe{display:block;width:100%;border:0}"
    ".lc-ratio iframe{position:absolute;inset:0;height:100%}"
)

_IFRAME_ALLOW = "autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"


def render_card(meta: LinkMeta, size: str = "large", theme: str = "light",
                max_width: int | None = None, show_date: bool = True) -> str:
    """Embed-code HTML for one card. Falls back to 'large' if 'player' was
    requested for a link that has no embeddable player."""
    if size not in SIZES:
        raise ValueError(f"size must be one of {SIZES}")
    if theme not in THEMES:
        raise ValueError(f"theme must be one of {THEMES}")
    if size == "player" and not meta.has_player:
        size = "large"
    width = max_width or DEFAULT_WIDTHS[size]

    if size == "player" and meta.player_html:
        return _provider_embed(meta, theme, width)
    body = {"large": _large, "small": _small, "player": _player}[size](meta, show_date)
    return (f"<style>{CSS}</style>\n"
            f'<div class="lc lc-{size} lc-{theme}" style="max-width:{width}px">{body}</div>')


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
    return f"{_open_link(meta)}{_text(meta, show_date)}{thumb}</a>"


def _player(meta: LinkMeta, show_date: bool) -> str:
    src = _safe_url(meta.player_url)
    frame = f'<iframe src="{src}" title="{escape(meta.title)}" allow="{_IFRAME_ALLOW}" allowfullscreen'
    if meta.player_ratio:
        player = (f'<div class="lc-player lc-ratio" style="aspect-ratio:{1 / meta.player_ratio:.4f}">'
                  f"{frame}></iframe></div>")
    else:
        height = meta.player_height or 400
        player = f'<div class="lc-player">{frame} height="{height}"></iframe></div>'
    return f"{player}{_open_link(meta)}{_text(meta, show_date, with_desc=False)}</a>"


def _provider_embed(meta: LinkMeta, theme: str, width: int) -> str:
    """Script-based embeds (X/Twitter, TikTok, Reddit) style themselves."""
    html = meta.player_html
    if theme == "dark":
        html = html.replace('class="twitter-tweet"', 'class="twitter-tweet" data-theme="dark"', 1)
    return f'<div style="max-width:{width}px;margin:0 auto">{html}</div>'


def _open_link(meta: LinkMeta) -> str:
    return f'<a class="lc-link" href="{_safe_url(meta.url)}" target="_blank" rel="noopener">'


def _text(meta: LinkMeta, show_date: bool, with_desc: bool = True) -> str:
    parts = [f'<div class="lc-title">{escape(meta.title)}</div>']
    if with_desc and meta.description:
        parts.append(f'<div class="lc-desc">{escape(meta.description)}</div>')
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
