# LinkCard

A self-hosted take on iframely: paste a URL and get an embeddable preview card.

- **Sizes:** large (image on top), small (square thumbnail on the left), player (playable YouTube / Vimeo / Spotify / SoundCloud / X / TikTok / Reddit embed)
- **Adjustable width and height**, with presets per size: large 520 × 440, small 600 × 120, player 640 wide × the provider's own height (360 for a 16:9 video, 352 for Spotify)
- **Published date** shown in the footer (from article / video / paper meta tags, JSON-LD, or the tweet), e.g. `GitHub · 5 Mar 2024`
- **Themes:** light, dark, auto (follows the viewer's OS setting)
- **Self-contained mode:** stores the preview image and icon inside the card as data: URIs, so the card works offline and keeps working if the site deletes its images
- **Output:** a copy-paste HTML snippet, a standalone `.html` file, or metadata as JSON

No API key and no third-party service: the app fetches each page itself.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py          # or double-click run.bat on Windows
```

Then open http://localhost:8501.

### Command line

```bash
python cli.py https://github.com/psf/requests --size small --theme dark -o card.html
python cli.py https://youtu.be/dQw4w9WgXcQ --size player --page --offline -o video.html
python cli.py https://example.com --json
```

## How it works

| File | Role |
|---|---|
| `unfurl.py` | Fetches the URL and extracts metadata: known oEmbed providers → Open Graph / Twitter Card / meta tags / JSON-LD → discovered oEmbed → fallbacks (`<title>`, first paragraph, `/favicon.ico`) |
| `cards.py` | Renders the metadata as HTML with a scoped `<style>` block (works in any page or CMS) |
| `app.py` | Streamlit UI |
| `cli.py` | Command-line interface |

To support a new player provider, add its oEmbed endpoint to `OEMBED_PROVIDERS` in `unfurl.py`.

## Notes

- **Internet is needed to create a card** because the data comes from the target page. Once a card is made in self-contained mode, it displays with no network access. (Player embeds always need internet to play.)
- **Private/intranet addresses are blocked** so a hosted copy can't be used to probe your network. To preview internal links on your own machine, set `LINKCARD_ALLOW_PRIVATE=1` first.
- Some sites block automated fetches or need a login (e.g. Cloudflare-protected sites like datacamp.com, Instagram, LinkedIn, some news paywalls). Instead of failing, LinkCard builds a basic card from the URL (title from the slug, domain, favicon) and shows a warning. You can fill in the title, description and image under **Edit card details**, or with `--title` / `--description` / `--image` on the command line.
- Email clients strip `<style>` and iframes, so the embed code is meant for web pages, not email.
