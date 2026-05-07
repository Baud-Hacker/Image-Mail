# [Image-mail]

Render an HTML email to a PNG and emit a new HTML email where every original
`<a href>` becomes a clickable region (HTML image map) over its rendered
position. The result looks like a flat image to email clients but remains
fully clickable, including in Outlook desktop.

## Why

Dynamic emails sound like a cool idea. Data soviregnty, phishing, you name it 'we' do it.

## Requirements

- Python 3.9+
- A one-time Chromium download via Playwright

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

## Usage

```bash
python html_to_image_email.py input.html [options]
```

Options:

| Flag         | Default | Description                                                       |
|--------------|---------|-------------------------------------------------------------------|
| `--out-dir`  | `./out` | Where to write `email.png` and `email.html`.                      |
| `--width`    | `600`   | Email width in CSS pixels. 600 is the email-industry standard.    |
| `--scale`    | `4`     | Image DPI scale. 1=baseline, 2=retina, 4=very crisp.              |
| `--alt`      | `""`    | Alt text for the image (shown when remote images are blocked).    |
| `--debug`    | off     | Also writes `email.debug.html` with red overlays for hotspot QA.  |

Example:

```bash
python html_to_image_email.py examples/example.html \
  --out-dir build \
  --alt "Your monthly statement" \
  --debug
```

Output:

```
build/email.png         # rendered image
build/email.html        # ready-to-send HTML (image + image map)
build/email.debug.html  # only with --debug — visualize hotspots in a browser
```

## How it works

1. Chromium (headless, via Playwright) renders the source HTML at the
   chosen width.
2. Before screenshotting, the script:
   - waits for `document.fonts.ready` so custom fonts are painted
   - waits for in-flight `<img>` loads to complete
   - disables CSS animations and transitions (no mid-frame captures)
   - rewrites `loading="lazy"` images to eager and scrolls the document
     to trigger any IntersectionObserver-based lazy loaders
   - hides scrollbars
3. Captures a full-page PNG.
4. Walks every `<a href>` in the live DOM and records each line-box rect
   via `getClientRects()` — so wrapped links produce one tight rect per
   line, not a big union covering the gap.
5. Emits an HTML page using a `<map>` and one `<area>` per rect.

## Email-client compatibility

Image maps are supported in every major email client:

- Gmail (web, iOS, Android)
- Outlook (desktop 2007+, Mac, web, mobile)
- Apple Mail (macOS, iOS)
- Yahoo, AOL, Outlook.com

Other notes:

- **Image maps don't auto-scale.** Don't apply `max-width: 100%` to the
  image — coordinates would misalign on resized renders. Keep the image
  at its native displayed size.
- **Outlook and many corporate clients block remote images by default.**
  Recipients see only `alt` text until they click "show images". Provide
  meaningful text via `--alt`.

## Image hosting and Gmail caching

The output HTML references `email.png` as a relative path. To send the
HTML as an actual email, host the PNG at a URL and rewrite the `src`
attribute to that URL.

If you plan to swap the image content after sending, be aware:

- **Gmail proxies and caches every external image** through
  `googleusercontent.com` and ignores `Cache-Control`, `Expires`, ETag,
  and similar headers. Once a Gmail recipient has opened the message,
  they will keep seeing the cached image — likely for the lifetime of
  that mailbox entry. The cache TTL is undocumented and effectively
  permanent in practice.
- **Outlook desktop and Apple Mail** generally re-fetch on each open and
  honor cache headers, so swaps reach those recipients.
- A new URL is the only guaranteed-fresh fetch on Gmail, and that has
  to be set at send time.

Plan around this: for already-sent mail, assume Gmail recipients see the
image as it was at first open.


