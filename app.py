"""LinkCard - paste a URL, get an embeddable preview card.

    streamlit run app.py
"""
import dataclasses
import json
import re

import streamlit as st
import streamlit.components.v1 as components

from cards import (DEFAULT_WIDTHS, HEIGHT_RANGES, SIZES, THEMES, effective_size, preset_height,
                   render_card, render_page)
from unfurl import LinkMeta, UnfurlError, inline_images, unfurl

st.set_page_config(page_title="LinkCard", page_icon="🔗", layout="wide")


@st.cache_data(ttl=3600, show_spinner=False)
def get_meta(url: str) -> LinkMeta:
    return unfurl(url)


@st.cache_data(ttl=3600, show_spinner=False)
def get_inlined(image: str, favicon: str) -> tuple[str, str]:
    inlined = inline_images(LinkMeta(url="", image=image, favicon=favicon))
    return inlined.image, inlined.favicon


def preview_height(meta: LinkMeta, size: str, width: int, height: int) -> int:
    """components.html needs a fixed height; overestimating is harmless."""
    size = effective_size(meta, size)
    if size == "player":
        if meta.player_html:
            return 760
        if meta.player_ratio:
            height = min(width, height / meta.player_ratio) * meta.player_ratio
        return int(height) + 110
    if size == "small":
        return height + 40
    natural = int(width / 1.91) + 200 if meta.image else 200
    return min(height, natural) + 40


HEIGHT_HELP = {
    "large": "Height of the whole card. The image is cropped to fit; the text is never cut.",
    "small": "Height of the card. The thumbnail on the left is square (up to 40% of the width).",
    "player": "Height of the player. Videos keep their shape, so a lower height makes the card narrower.",
}


with st.sidebar:
    st.header("Card options")
    size = st.radio("Size", SIZES, format_func=str.title, horizontal=True)
    theme = st.radio("Theme", THEMES, format_func=str.title, horizontal=True,
                     help="Auto follows the viewer's light/dark system setting.")
    width = st.slider("Max width (px)", 280, 900, DEFAULT_WIDTHS[size], step=10)
    height_slot = st.container()  # filled once the link is known (player presets depend on it)
    show_date = st.checkbox("Show published date", value=True)
    offline = st.toggle("Self-contained (embed images)",
                        help="Downloads the preview image and icon into the card as data URIs, so the "
                             "embed code works with no internet and never breaks if the site deletes them.")

st.title("🔗 LinkCard")
st.caption("Paste a link and get an embeddable preview card: large, small, or a playable embed, in light or dark mode.")

with st.form("unfurl"):
    url = st.text_input("URL", placeholder="https://www.youtube.com/watch?v=...")
    st.form_submit_button("Generate card", type="primary")

if not url.strip():
    st.info("Try a YouTube, Spotify, X, GitHub, news or blog link.")
    st.stop()

try:
    with st.spinner("Fetching page…"):
        meta = get_meta(url)
except UnfurlError as exc:
    st.error(str(exc))
    st.stop()

if meta.warning:
    st.warning(meta.warning, icon="🛡️")

# Let the user correct or complete anything the page didn't provide (essential
# for sites that block automated requests). Keys include the URL so the fields
# reset when a new link is entered.
with st.expander("✏️ Edit card details", expanded=bool(meta.warning)):
    if meta.warning:
        st.caption("Open the link in your browser and copy its title, summary and main image "
                   "(right-click the image → Copy image address).")
    k = meta.url
    c1, c2 = st.columns(2)
    edits = {
        "title": c1.text_input("Title", meta.title, key=f"title|{k}"),
        "site_name": c2.text_input("Site name", meta.site_name, key=f"site|{k}"),
        "description": st.text_area("Description", meta.description, height=80, key=f"desc|{k}"),
        "image": c1.text_input("Image URL", meta.image, key=f"image|{k}"),
        "author": c2.text_input("Author", meta.author, key=f"author|{k}"),
        "published": c1.text_input("Published (YYYY-MM-DD)", meta.published, key=f"date|{k}"),
        "favicon": c2.text_input("Icon URL", meta.favicon, key=f"icon|{k}"),
    }
meta = dataclasses.replace(meta, **{f: v.strip() for f, v in edits.items()})

card_meta = meta
if offline:
    with st.spinner("Embedding images…"):
        image, favicon = get_inlined(meta.image, meta.favicon)
    card_meta = dataclasses.replace(meta, image=image, favicon=favicon)

if size == "player" and not meta.has_player:
    st.info("This link has no embeddable player, so it is shown as a large card.")

shape = effective_size(meta, size)
if shape == "player" and meta.player_html:
    height = 0
    height_slot.caption("Height: set by the provider for X / TikTok / Reddit embeds.")
else:
    lo, hi = HEIGHT_RANGES[shape]
    preset = min(max(preset_height(meta, shape), lo), hi)
    # Large/small presets are fixed; the player preset is the provider's own size,
    # so that slider is keyed per link and resets for a new one.
    key = f"height|{shape}" + (f"|{meta.url}" if shape == "player" else "")
    height = height_slot.slider("Max height (px)", lo, hi, preset, step=4, key=key,
                                help=HEIGHT_HELP[shape] + f" Preset: {preset}px.")

card = render_card(card_meta, size, theme, width, show_date, height or None)
page = render_page(card, theme, meta.title)
slug = re.sub(r"[^a-z0-9]+", "-", meta.title.lower()).strip("-")[:60] or "card"

preview_col, code_col = st.columns([3, 2], gap="large")
with preview_col:
    st.subheader("Preview")
    if show_date and not meta.published:
        st.caption("No published date found on this page.")
    components.html(page, height=preview_height(meta, size, width, height), scrolling=True)
with code_col:
    st.subheader("Embed code")
    st.caption("Paste this into any web page or CMS that accepts HTML.")
    st.code(card, language="html", wrap_lines=True, height=260)
    dl1, dl2 = st.columns(2)
    dl1.download_button("⬇ Card (.html)", page, file_name=f"{slug}.html", mime="text/html",
                        width="stretch", on_click="ignore")
    dl2.download_button("⬇ Metadata (.json)", json.dumps(meta.to_dict(), indent=2, ensure_ascii=False),
                        file_name=f"{slug}.json", mime="application/json", width="stretch", on_click="ignore")
    with st.expander("Extracted metadata"):
        st.json(meta.to_dict())
