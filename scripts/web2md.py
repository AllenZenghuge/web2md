#!/usr/bin/env python3
"""web2md — Convert web pages and WeChat articles to Markdown with local images."""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from markdownify import markdownify as md_convert
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from readability import Document


def is_wechat_url(url: str) -> bool:
    """Check if the URL is a WeChat official account article."""
    parsed = urlparse(url)
    return parsed.netloc.endswith("mp.weixin.qq.com")


def sanitize_filename(title: str) -> str:
    """Remove characters unsafe for file/directory names, preserve CJK."""
    sanitized = re.sub(r'[\\/:*?"<>|]', "-", title)
    sanitized = sanitized.strip(" .-")
    if len(sanitized) > 100:
        sanitized = sanitized[:100].rsplit("-", 1)[0]
    return sanitized or "untitled"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert web pages and WeChat articles to Markdown"
    )
    parser.add_argument("url", help="Article or page URL")
    parser.add_argument(
        "--output-dir", default=".", help="Output directory (default: current)"
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip image download, keep original URLs",
    )
    parser.add_argument(
        "--timeout", type=int, default=30, help="Page load timeout in seconds"
    )
    return parser.parse_args()


def fetch_page(url: str, timeout: int) -> tuple:
    """Open URL in headless Chromium, return (page, is_wechat, playwright, browser, context)."""
    wechat = is_wechat_url(url)
    wait_selector = "#js_content" if wechat else "body"

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        channel="chrome",
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
        ],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
    )
    page = context.new_page()

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        page.wait_for_selector(wait_selector, timeout=timeout * 1000)
    except PlaywrightTimeout:
        print("ERROR: Page load timeout — article content not found.", file=sys.stderr)
        browser.close()
        playwright.stop()
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: Network error — {e}", file=sys.stderr)
        browser.close()
        playwright.stop()
        sys.exit(4)

    return page, wechat, playwright, browser, context


def scroll_to_load_images(page) -> None:
    """Scroll progressively to trigger lazy-loaded images, then swap data-src → src."""
    prev_height = 0
    same_count = 0

    while same_count < 2:
        page.evaluate("window.scrollBy(0, 3000)")
        time.sleep(0.8)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == prev_height:
            same_count += 1
        else:
            same_count = 0
            prev_height = new_height

    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.5)

    page.evaluate("""
        document.querySelectorAll('img[data-src]').forEach(img => {
            const src = img.getAttribute('data-src');
            if (src) {
                img.setAttribute('src', src);
                img.removeAttribute('data-src');
            }
        });
        document.querySelectorAll('img[data-original]').forEach(img => {
            const src = img.getAttribute('data-original');
            if (src && !img.getAttribute('src')) {
                img.setAttribute('src', src);
            }
        });
    """)
    time.sleep(1)


def extract_metadata(page, url: str, is_wechat: bool) -> dict:
    """Extract title, author, date, description from the rendered page."""
    meta = {
        "source_url": url,
        "converted_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }

    if is_wechat:
        meta["title"] = _get_text(page, "#activity-name") or ""
        meta["author"] = _get_text(page, "#js_author_name") or ""
        meta["account_name"] = _get_text(page, "#js_name") or ""
        meta["publish_date"] = _get_text(page, "#publish_time") or ""
        meta["description"] = _get_meta(page, "description") or ""
        cover = _get_meta(page, "og:image") or ""
        if cover:
            meta["cover_image"] = cover
    else:
        meta["title"] = page.title() or ""
        og_title = _get_meta(page, "og:title") or ""
        if og_title:
            meta["title"] = og_title
        meta["author"] = _get_meta(page, "author") or ""
        meta["publish_date"] = (
            _get_meta(page, "article:published_time")
            or _get_meta(page, "publish-date")
            or ""
        )
        meta["description"] = (
            _get_meta(page, "description") or _get_meta(page, "og:description") or ""
        )
        cover = _get_meta(page, "og:image") or ""
        if cover:
            meta["cover_image"] = cover

    # Remove empty values
    return {k: v for k, v in meta.items() if v}


def _get_text(page, selector: str) -> str:
    """Get text content of an element, or empty string."""
    try:
        el = page.query_selector(selector)
        return el.inner_text().strip() if el else ""
    except Exception:
        return ""


def _get_meta(page, name: str) -> str:
    """Get meta tag content by name or property."""
    try:
        el = page.query_selector(f'meta[name="{name}"], meta[property="{name}"]')
        return el.get_attribute("content").strip() if el else ""
    except Exception:
        return ""


def extract_content(page, is_wechat: bool) -> str:
    """Extract cleaned article HTML from the rendered page."""
    if is_wechat:
        html = page.evaluate("""
            () => {
                const content = document.getElementById('js_content');
                if (!content) return '';
                const removals = content.querySelectorAll(
                    '.rich_media_meta_list, .reward_area, .like_media_thumb, '
                    + '.rich_media_tool, .rich_media_area_extra, '
                    + 'mp-common-clipboard, .mp-common-profile'
                );
                removals.forEach(el => el.remove());
                content.querySelectorAll('[style]').forEach(el => {
                    el.removeAttribute('style');
                });
                return content.outerHTML;
            }
        """)
        if not html:
            print("ERROR: Could not find article content (#js_content).", file=sys.stderr)
            sys.exit(2)
        return html
    else:
        raw_html = page.content()
        doc = Document(raw_html)
        summary_html = doc.summary()
        if not summary_html:
            print("WARNING: readability could not extract main content, using full body.", file=sys.stderr)
            return page.evaluate("() => document.body.innerHTML")
        return summary_html


def download_images(html: str, img_dir: Path, img_rel_prefix: str, page_url: str) -> tuple:
    """Download all images in the HTML, deduplicate by URL, rewrite src attributes.
    Returns (modified_html, image_count)."""
    soup = BeautifulSoup(html, "html.parser")
    img_dir.mkdir(parents=True, exist_ok=True)

    seen_urls = {}
    img_tags = soup.find_all("img")
    count = 0

    headers = {
        "Referer": page_url,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
    }

    for tag in img_tags:
        src = tag.get("src") or tag.get("data-src") or tag.get("data-original") or ""
        if not src:
            continue

        src = urljoin(page_url, src)

        if not src.startswith("http"):
            continue

        if src in seen_urls:
            tag["src"] = img_rel_prefix + seen_urls[src]
            _clean_img_attrs(tag)
            continue

        try:
            resp = requests.get(src, headers=headers, timeout=15, stream=True)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            ext = _ext_from_content_type(content_type) or _ext_from_url(src) or ".jpg"

            count += 1
            filename = f"img_{count:03d}{ext}"
            filepath = img_dir / filename

            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            seen_urls[src] = filename
            tag["src"] = img_rel_prefix + filename
            _clean_img_attrs(tag)

        except Exception as e:
            print(f"WARNING: Failed to download image {src}: {e}", file=sys.stderr)
            tag.replace_with(f"[Image: {src}]")

    return str(soup), count


def _ext_from_content_type(content_type: str) -> str:
    """Map MIME type to file extension."""
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
    }
    for mime, ext in mapping.items():
        if mime in content_type:
            return ext
    return ""


def _ext_from_url(url: str) -> str:
    """Extract file extension from URL path."""
    parsed = urlparse(url)
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"):
        return ".jpg" if ext == ".jpeg" else ext
    return ""


def _clean_img_attrs(tag) -> None:
    """Remove data-* and other non-essential attributes from an img tag."""
    for attr in list(tag.attrs):
        if attr not in ("src", "alt"):
            del tag[attr]


def convert_to_markdown(html: str) -> str:
    """Convert cleaned HTML to Markdown."""
    md = md_convert(
        html,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "iframe"],
        strong_em_symbol="**",
        newline_style="BACKSLASH",
        escape_asterisks=False,
        escape_underscores=False,
    )
    md = re.sub(r"\n{4,}", "\n\n\n", md)
    return md.strip()


def main() -> None:
    args = parse_args()

    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https"):
        print("ERROR: URL must start with http:// or https://", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir).resolve()
    if not output_dir.exists():
        try:
            output_dir.mkdir(parents=True)
        except OSError as e:
            print(f"ERROR: Cannot write to {output_dir} — {e}", file=sys.stderr)
            sys.exit(5)

    page, is_wechat, playwright, browser, context = fetch_page(args.url, args.timeout)
    article_type = "公众号文章" if is_wechat else "网页"
    print(f"正在转换{article_type}...", file=sys.stderr)

    try:
        scroll_to_load_images(page)

        meta = extract_metadata(page, args.url, is_wechat)
        html = extract_content(page, is_wechat)

        title = meta.get("title", "untitled")
        safe_title = sanitize_filename(title)

        # Auto-sort into type-specific subdirectory under web2md/
        wrapper_dir = output_dir / "web2md"
        type_dir = "公众号文章" if is_wechat else "网页文章"
        article_dir = wrapper_dir / type_dir
        article_dir.mkdir(parents=True, exist_ok=True)

        # .md file directly in type dir
        md_path = article_dir / f"{safe_title}.md"

        # Images go to web2md/images/, organized by article title
        img_dir = wrapper_dir / "images" / safe_title
        img_rel_prefix = f"../images/{safe_title}/"

        if not args.no_images:
            html, img_count = download_images(html, img_dir, img_rel_prefix, args.url)
            # Remove empty image directory if no images were downloaded
            if img_count == 0 and img_dir.exists():
                try:
                    img_dir.rmdir()
                except OSError:
                    pass
        else:
            img_count = 0

        md = convert_to_markdown(html)

        if "cover_image" in meta and not args.no_images and img_count > 0:
            del meta["cover_image"]

        with open(md_path, "w", encoding="utf-8") as f:
            frontmatter = yaml.dump(
                meta, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
            f.write("---\n")
            f.write(frontmatter.strip())
            f.write("\n---\n\n")
            f.write(md)
            f.write("\n")

        print(f"DONE: {md_path}", file=sys.stderr)
        print(f"Title: {meta.get('title', 'N/A')}", file=sys.stderr)
        if meta.get("author"):
            print(f"Author: {meta['author']}", file=sys.stderr)
        if meta.get("publish_date"):
            print(f"Date: {meta['publish_date']}", file=sys.stderr)
        if not args.no_images:
            print(f"Images: {img_count} downloaded", file=sys.stderr)

    finally:
        browser.close()
        playwright.stop()


if __name__ == "__main__":
    main()
