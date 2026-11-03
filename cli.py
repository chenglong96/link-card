"""Command-line LinkCard.

    python cli.py https://example.com --size small --theme dark -o card.html
    python cli.py https://example.com --json
"""
import argparse
import dataclasses
import json
import sys
from pathlib import Path

from cards import SIZES, THEMES, render_card, render_page
from unfurl import UnfurlError, inline_images, unfurl


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn a URL into an embeddable preview card.")
    parser.add_argument("url")
    parser.add_argument("--size", choices=SIZES, default="large")
    parser.add_argument("--theme", choices=THEMES, default="light")
    parser.add_argument("--width", type=int, help="max card width in px")
    parser.add_argument("--height", type=int,
                        help="max height in px (large: whole card, small: card height, player: player height)")
    parser.add_argument("--no-date", action="store_true", help="hide the published date")
    parser.add_argument("--offline", action="store_true",
                        help="embed images as data: URIs so the card works without internet")
    parser.add_argument("--page", action="store_true", help="output a full HTML page, not just the snippet")
    parser.add_argument("--json", action="store_true", help="output the extracted metadata as JSON")
    parser.add_argument("--title", help="override the title")
    parser.add_argument("--description", help="override the description")
    parser.add_argument("--image", help="override the preview image URL")
    parser.add_argument("-o", "--output", help="write to this file instead of stdout")
    args = parser.parse_args()

    try:
        meta = unfurl(args.url)
    except UnfurlError as exc:
        sys.exit(f"error: {exc}")
    if meta.warning:
        print(f"warning: {meta.warning} (use --title / --description / --image)", file=sys.stderr)
    overrides = {f: getattr(args, f) for f in ("title", "description", "image") if getattr(args, f)}
    meta = dataclasses.replace(meta, **overrides)
    if args.offline:
        meta = inline_images(meta)

    if args.json:
        out = json.dumps(meta.to_dict(), indent=2, ensure_ascii=False)
    else:
        out = render_card(meta, args.size, args.theme, args.width, not args.no_date, args.height)
        if args.page:
            out = render_page(out, args.theme, meta.title)

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(out)


if __name__ == "__main__":
    main()
