"""Render an HTML email to a PNG and emit a new HTML email where every
original <a href> becomes a transparent, absolutely-positioned anchor over
its rendered location.

Usage:
    python html_to_image_email.py input.html --out-dir ./out [--width 600]
                                             [--scale 4] [--debug]

Notes on email-client compatibility:
    The output uses an HTML image map (<map>/<area>), which is supported in
    every major email client including all Outlook versions, Apple Mail,
    iOS Mail, Gmail web/mobile, Outlook.com, and Yahoo. The image is sent
    at a fixed pixel size — image-map coordinates do not auto-scale if the
    client resizes the image, so do not apply max-width:100% styling to it.

Dependencies:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path


# JS evaluated in the page to extract one rect per visible line box for
# every <a href>. getClientRects() (vs getBoundingClientRect) yields a
# separate rect for each line a wrapped link occupies, which keeps the
# overlays tight against the actual rendered text.
EXTRACT_LINKS_JS = r"""
() => {
    const out = [];
    const anchors = document.querySelectorAll('a[href]');
    for (const a of anchors) {
        const href = a.getAttribute('href');
        if (!href) continue;
        if (href.startsWith('#') || href.toLowerCase().startsWith('javascript:')) continue;
        const rects = a.getClientRects();
        for (const r of rects) {
            if (r.width <= 0 || r.height <= 0) continue;
            out.push({
                href: a.href,
                x: r.left + window.scrollX,
                y: r.top + window.scrollY,
                w: r.width,
                h: r.height,
            });
        }
    }
    return out;
}
"""


def render(input_html: str, width: int, scale: int):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "playwright is not installed. Run:\n"
            "    pip install playwright\n"
            "    playwright install chromium"
        )

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            sys.exit(
                f"Failed to launch chromium: {exc}\n"
                "Did you run `playwright install chromium`?"
            )

        context = browser.new_context(
            viewport={"width": width, "height": 800},
            device_scale_factor=scale,
        )
        page = context.new_page()
        page.set_content(input_html, wait_until="networkidle")

        # Kill animations/transitions and the scrollbar so neither bleeds
        # into the screenshot.
        page.add_style_tag(content=(
            "*, *::before, *::after {"
            " animation: none !important;"
            " transition: none !important;"
            "}"
            "html { scrollbar-width: none !important; }"
            "html::-webkit-scrollbar, body::-webkit-scrollbar"
            " { display: none !important; width: 0 !important; height: 0 !important; }"
        ))

        # Force any lazy-loaded images to load immediately, then scroll
        # through the document to trigger IntersectionObserver-based loaders,
        # then return to the top so the screenshot starts at origin.
        page.evaluate(
            "() => {"
            " for (const img of document.querySelectorAll('img[loading=\"lazy\"]'))"
            " { img.loading = 'eager'; }"
            "}"
        )
        page.evaluate(
            "async () => {"
            " const total = Math.max("
            "   document.documentElement.scrollHeight, document.body.scrollHeight);"
            " const step = window.innerHeight;"
            " for (let y = 0; y < total; y += step) {"
            "   window.scrollTo(0, y);"
            "   await new Promise(r => setTimeout(r, 50));"
            " }"
            " window.scrollTo(0, 0);"
            "}"
        )

        # Wait for web fonts and any in-flight image loads to finish before
        # we screenshot — guarantees fonts are painted, not fallbacks.
        page.evaluate(
            "async () => {"
            " if (document.fonts && document.fonts.ready)"
            "   { await document.fonts.ready; }"
            " await Promise.all("
            "   Array.from(document.images)"
            "     .filter(img => !img.complete)"
            "     .map(img => new Promise(r => { img.onload = img.onerror = r; }))"
            " );"
            "}"
        )

        # Pull link rects in CSS pixels.
        link_data = page.evaluate(EXTRACT_LINKS_JS)

        # Full-page screenshot: PNG bytes covering the entire scroll height.
        png_bytes = page.screenshot(full_page=True, type="png")

        # Determine the rendered image dimensions in image pixels and the
        # corresponding CSS pixel size of the page (image px = CSS px * scale).
        css_height = page.evaluate(
            "() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"
        )
        css_width = width
        img_w = css_width * scale
        img_h = css_height * scale

        browser.close()

    # Convert link rects (CSS px) into image px and into the overlay format.
    overlays = []
    for link in link_data:
        x_img = link["x"] * scale
        y_img = link["y"] * scale
        w_img = link["w"] * scale
        h_img = link["h"] * scale
        if w_img <= 0 or h_img <= 0:
            continue
        overlays.append(
            {
                "url": link["href"],
                "x": round(x_img),
                "y": round(y_img),
                "w": round(w_img),
                "h": round(h_img),
            }
        )

    return png_bytes, img_w, img_h, overlays


def build_email_html(image_name: str, img_w: int, img_h: int, overlays, scale: int, alt_text: str) -> str:
    """Image-map output. Works in every major email client including all
    Outlook versions, unlike CSS absolute positioning."""
    css_w = img_w // scale
    css_h = img_h // scale
    safe_alt = html.escape(alt_text)
    safe_img = html.escape(image_name)

    areas = []
    for o in overlays:
        x1 = o["x"] // scale
        y1 = o["y"] // scale
        x2 = x1 + (o["w"] // scale)
        y2 = y1 + (o["h"] // scale)
        url = html.escape(o["url"], quote=True)
        areas.append(
            f'<area shape="rect" coords="{x1},{y1},{x2},{y2}" '
            f'href="{url}" alt="{url}" title="{url}" '
            f'target="_blank" rel="noopener">'
        )

    return (
        "<!doctype html>\n"
        '<html><body style="margin:0;padding:0;">\n'
        f'<img src="{safe_img}" width="{css_w}" height="{css_h}" '
        f'alt="{safe_alt}" border="0" usemap="#emailmap" '
        'style="display:block;border:0;outline:none;text-decoration:none;">\n'
        '<map name="emailmap">\n'
        + "\n".join("  " + a for a in areas)
        + "\n</map>\n</body></html>\n"
    )


def build_debug_html(image_name: str, img_w: int, img_h: int, overlays, scale: int) -> str:
    """Browser-only QA view: tinted absolute-positioned overlays so you can
    visually verify hotspot alignment before sending."""
    css_w = img_w // scale
    css_h = img_h // scale
    anchors = []
    for o in overlays:
        anchors.append(
            '<a href="{url}" target="_blank" rel="noopener" '
            'style="position:absolute;left:{x}px;top:{y}px;'
            "width:{w}px;height:{h}px;background:rgba(255,0,0,0.3);"
            'border:1px solid red;text-decoration:none;"></a>'.format(
                url=html.escape(o["url"], quote=True),
                x=o["x"] // scale,
                y=o["y"] // scale,
                w=o["w"] // scale,
                h=o["h"] // scale,
            )
        )
    return (
        "<!doctype html>\n"
        '<html><body style="margin:0">\n'
        f'<div style="position:relative;width:{css_w}px;height:{css_h}px;">\n'
        f'  <img src="{html.escape(image_name)}" width="{css_w}" height="{css_h}" '
        'style="display:block;border:0;" alt="">\n'
        + "\n".join("  " + a for a in anchors)
        + "\n</div>\n</body></html>\n"
    )


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("input", type=Path, help="Input HTML file")
    p.add_argument("--out-dir", type=Path, default=Path("./out"))
    p.add_argument("--width", type=int, default=600, help="Email width in px (default 600)")
    p.add_argument(
        "--scale",
        type=int,
        default=4,
        help="Image DPI scale: 1=baseline, 2=retina, 4=very crisp (default 4)",
    )
    p.add_argument(
        "--alt",
        default="",
        help="Alt text for the image (shown when remote images are blocked).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Also write email.debug.html with tinted hotspot overlays for QA.",
    )
    args = p.parse_args()

    if args.scale < 1:
        sys.exit("--scale must be >= 1")

    src = args.input.read_text(encoding="utf-8")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    png_bytes, img_w, img_h, overlays = render(src, args.width, args.scale)

    img_path = args.out_dir / "email.png"
    html_path = args.out_dir / "email.html"
    img_path.write_bytes(png_bytes)

    out_html = build_email_html("email.png", img_w, img_h, overlays, args.scale, args.alt)
    html_path.write_text(out_html, encoding="utf-8")

    print(f"Image:    {img_path}  ({img_w}x{img_h}px)")
    print(f"HTML:     {html_path}")
    print(f"Overlays: {len(overlays)} link(s)")

    if args.debug:
        debug_path = args.out_dir / "email.debug.html"
        debug_path.write_text(
            build_debug_html("email.png", img_w, img_h, overlays, args.scale),
            encoding="utf-8",
        )
        print(f"Debug:    {debug_path}")


if __name__ == "__main__":
    main()
