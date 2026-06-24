#!/usr/bin/env python3
"""web2md — Convert web pages and WeChat articles to Markdown with local images."""

import argparse
import os
import re
import shutil
import subprocess
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


# ── defuddle integration ──────────────────────────────────────────────

def defuddle_available() -> bool:
    """Check if defuddle CLI is installed."""
    return shutil.which("defuddle") is not None


def should_use_defuddle(url: str, backend: str) -> bool:
    """Decide whether to use defuddle for this URL.

    Routing logic:
    - backend=playwright → never use defuddle
    - backend=defuddle → always use defuddle
    - backend=auto:
        - WeChat (mp.weixin.qq.com) → playwright (needs DOM selectors)
        - .md URLs → playwright (defuddle says don't use for .md)
        - Everything else → defuddle (faster, cleaner)
    """
    if backend == "playwright":
        return False
    if backend == "defuddle":
        if not defuddle_available():
            print("WARNING: defuddle not installed, falling back to playwright.", file=sys.stderr)
            return False
        return True
    # auto
    if is_wechat_url(url):
        return False
    if url.rstrip("/").endswith(".md"):
        return False
    if not defuddle_available():
        return False
    return True


def fetch_via_defuddle(url: str, timeout: int) -> tuple:
    """Extract content and metadata using defuddle CLI.
    Returns (markdown_text, metadata_dict).
    """
    try:
        result = subprocess.run(
            ["defuddle", "parse", url, "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            print(f"ERROR: defuddle failed — {result.stderr.strip()}", file=sys.stderr)
            sys.exit(2)

        import json
        data = json.loads(result.stdout)
        content = data.get("markdown", data.get("content", ""))
        if not content:
            print("ERROR: defuddle returned empty content.", file=sys.stderr)
            sys.exit(2)

        meta = {
            "source_url": url,
            "converted_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "backend": "defuddle",
        }
        if data.get("title"):
            meta["title"] = data["title"]
        if data.get("description"):
            meta["description"] = data["description"]
        if data.get("domain"):
            meta["account_name"] = data["domain"]
        if data.get("author"):
            meta["author"] = data["author"]
        if data.get("date") or data.get("published"):
            meta["publish_date"] = data.get("date") or data.get("published")

        return content, meta

    except subprocess.TimeoutExpired:
        print("ERROR: defuddle timed out.", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError:
        print("ERROR: defuddle returned invalid JSON.", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError:
        print("ERROR: defuddle CLI not found. Install with: npm install -g defuddle", file=sys.stderr)
        sys.exit(3)


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
    parser.add_argument(
        "--backend",
        choices=["auto", "playwright", "defuddle"],
        default="auto",
        help="Content extraction backend: auto (route by URL type), "
        "playwright (full browser, best for WeChat and image download), "
        "defuddle (lightweight CLI, best for standard web pages)",
    )
    parser.add_argument(
        "--conversion-log",
        default="",
        help="Path to conversion record file (appends entry after successful conversion)",
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

    # Always return all known keys; normalize_meta will fill the rest
    return meta


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

    seen_urls = {}  # remote URL → local filename
    img_tags = soup.find_all("img")
    count = 0

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

        count += 1
        filename, ok = _download_single_image(src, page_url, img_dir, count)
        if ok:
            seen_urls[src] = filename
            tag["src"] = img_rel_prefix + filename
            _clean_img_attrs(tag)
        else:
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


# ── Shared image download core ──────────────────────────────────────

def _download_single_image(img_url: str, page_url: str, img_dir: Path,
                           count: int) -> tuple:
    """Download one image, return (local_filename, succeeded).
    Shared by both HTML and Markdown image pipelines.
    """
    headers = {
        "Referer": page_url,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
    }
    try:
        resp = requests.get(img_url, headers=headers, timeout=15, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        ext = _ext_from_content_type(content_type) or _ext_from_url(img_url) or ".jpg"

        filename = f"img_{count:03d}{ext}"
        filepath = img_dir / filename

        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        return filename, True

    except Exception as e:
        print(f"WARNING: Failed to download image {img_url}: {e}", file=sys.stderr)
        return "", False


# ── Markdown image download (for defuddle path) ─────────────────────

_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


def _extract_image_urls_from_markdown(md_text: str) -> list:
    """Extract unique image URLs from markdown text.
    Handles both ![alt](url) and <img src="url"> patterns.
    """
    urls = []
    seen = set()

    # Markdown images: ![alt](url)
    for match in _MD_IMAGE_RE.finditer(md_text):
        url = match.group(2)
        if url not in seen:
            seen.add(url)
            urls.append(url)

    # HTML <img> tags
    for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', md_text):
        url = match.group(1)
        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


def download_images_from_markdown(md_text: str, img_dir: Path,
                                  img_rel_prefix: str, page_url: str) -> tuple:
    """Download images referenced in markdown, rewrite URLs to local paths.
    Uses the same _download_single_image() core as the HTML pipeline.
    Returns (modified_markdown, image_count).
    """
    img_dir.mkdir(parents=True, exist_ok=True)

    url_map = {}  # remote URL → local filename
    count = 0
    result = md_text

    # Collect all image URLs (deduped, order preserved)
    image_urls = _extract_image_urls_from_markdown(md_text)

    for img_url in image_urls:
        full_url = urljoin(page_url, img_url)
        if not full_url.startswith("http"):
            continue

        count += 1
        filename, ok = _download_single_image(full_url, page_url, img_dir, count)
        if ok:
            url_map[img_url] = filename

    # Rewrite all image references in markdown
    for remote_url, local_file in url_map.items():
        local_ref = img_rel_prefix + local_file
        # Escape special regex chars in URL
        escaped = re.escape(remote_url)
        # Replace in ![alt](url) pattern
        result = re.sub(
            r'!\[([^\]]*)\]\(' + escaped + r'(?:\s+"[^"]*")?\)',
            r'![\1](' + re.escape(local_ref) + ')',
            result
        )
        # Replace in <img src="url"> pattern
        result = re.sub(
            r'(<img[^>]*src=["\'])' + escaped + r'(["\'])',
            r'\1' + re.escape(local_ref) + r'\2',
            result
        )

    return result, count


# ── Required frontmatter fields ──────────────────────────────────────

META_DEFAULTS = {
    "title": "",
    "author": "",
    "account_name": "",
    "source_url": "",
    "publish_date": "",
    "converted_at": "",
    "description": "",
    "tags": [],
    "aliases": [],
    "backend": "",
}


def normalize_meta(meta: dict, url: str, article_type: str) -> dict:
    """Ensure all required frontmatter fields exist, filling missing with sensible defaults."""
    from urllib.parse import urlparse

    result = dict(META_DEFAULTS)
    result.update(meta)
    result["source_url"] = url or result["source_url"]

    # Derive account_name from domain if not set
    if not result["account_name"]:
        parsed = urlparse(url)
        result["account_name"] = parsed.netloc or ""

    # Ensure aliases is always a non-empty list
    if not result.get("aliases"):
        title = result.get("title", "")
        result["aliases"] = [title] if title else ["未命名"]

    # Ensure tags is always a non-empty list
    if not result.get("tags"):
        result["tags"] = ["web2md", article_type or "article"]

    return result


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


def append_conversion_record(log_path: str, meta: dict, md_path: Path,
                             img_count: int, backend: str, article_type: str) -> None:
    """Append a conversion entry to the record file (Obsidian-compatible)."""
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).astimezone()
    date_str = now.strftime("%Y-%m-%d %H:%M")
    title = meta.get("title", "untitled")
    source_url = meta.get("source_url", "")

    wikilink = f"[[{md_path.stem}]]"
    entry = (
        f"| {date_str} | {wikilink} | {source_url} | "
        f"`{md_path}` | {backend} | {img_count} |\n"
    )

    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            existing = f.read()
        # Append after the table body (last line of the file)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        # Create new record file with frontmatter + table header
        frontmatter = f"""---
title: web2md 转换记录
date: {now.strftime("%Y-%m-%d")}
tags:
  - web2md
  - conversion-log
aliases:
  - 转换记录
  - web2md-log
---

# web2md 转换记录

> [!info] 说明
> 所有通过 web2md 转换的文章记录。无论从哪个项目调用，均统一记录于此。

| 转换时间 | 文章标题 | 来源 URL | 输出路径 | 后端 | 图片数 |
|----------|---------|----------|----------|------|--------|
"""
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(frontmatter)
            f.write(entry)

    print(f"LOG: Appended to {log_path}", file=sys.stderr)


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

    use_defuddle = should_use_defuddle(args.url, args.backend)

    # ── defuddle path (fast extraction + local image download) ────
    if use_defuddle:
        article_type = "网页文章"
        print(f"正在转换{article_type}（defuddle 后端）...", file=sys.stderr)

        md_content, meta = fetch_via_defuddle(args.url, args.timeout)

        title = meta.get("title", "untitled")
        safe_title = sanitize_filename(title)

        wrapper_dir = output_dir / "web2md"
        wrapper_dir.mkdir(parents=True, exist_ok=True)

        md_path = wrapper_dir / f"{safe_title}.md"

        # Download images referenced in markdown
        img_dir = wrapper_dir / "images" / safe_title
        img_rel_prefix = f"web2md/images/{safe_title}/"

        if not args.no_images:
            md_content, img_count = download_images_from_markdown(
                md_content, img_dir, img_rel_prefix, args.url
            )
            if img_count == 0 and img_dir.exists():
                try:
                    img_dir.rmdir()
                except OSError:
                    pass
        else:
            img_count = 0

        # Normalize: ensure all 9 required properties exist
        meta = normalize_meta(meta, args.url, article_type)

        # Add obsidian-markdown properties
        meta["tags"] = ["web2md", article_type]
        meta["aliases"] = [title]
        meta["backend"] = "defuddle"

        with open(md_path, "w", encoding="utf-8") as f:
            frontmatter = yaml.dump(
                meta, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
            f.write("---\n")
            f.write(frontmatter.strip())
            f.write("\n---\n\n")
            f.write(md_content)
            f.write("\n")

        if args.conversion_log:
            append_conversion_record(
                args.conversion_log, meta, md_path, img_count, "defuddle", article_type
            )

        print(f"DONE: {md_path}", file=sys.stderr)
        print(f"Title: {meta.get('title', 'N/A')}", file=sys.stderr)
        print(f"Backend: defuddle", file=sys.stderr)
        if not args.no_images:
            print(f"Images: {img_count} downloaded", file=sys.stderr)

        return

    # ── playwright path (full browser, for WeChat / image download) ──
    is_wechat_flag = is_wechat_url(args.url)
    page, is_wechat, playwright, browser, context = fetch_page(args.url, args.timeout)
    article_type = "公众号文章" if is_wechat else "网页文章"
    print(f"正在转换{article_type}（Playwright 后端）...", file=sys.stderr)

    try:
        scroll_to_load_images(page)

        meta = extract_metadata(page, args.url, is_wechat)
        html = extract_content(page, is_wechat)

        title = meta.get("title", "untitled")
        safe_title = sanitize_filename(title)

        # Output .md directly under web2md/
        wrapper_dir = output_dir / "web2md"
        wrapper_dir.mkdir(parents=True, exist_ok=True)

        md_path = wrapper_dir / f"{safe_title}.md"

        # Images go to web2md/images/, organized by article title
        img_dir = wrapper_dir / "images" / safe_title
        img_rel_prefix = f"{img_dir}/"

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

        # Normalize: ensure all 9 required properties exist
        meta = normalize_meta(meta, args.url, article_type)

        # Add obsidian-markdown properties
        meta["tags"] = ["web2md", article_type]
        meta["aliases"] = [title]
        meta["backend"] = "playwright"

        with open(md_path, "w", encoding="utf-8") as f:
            frontmatter = yaml.dump(
                meta, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
            f.write("---\n")
            f.write(frontmatter.strip())
            f.write("\n---\n\n")
            f.write(md)
            f.write("\n")

        if args.conversion_log:
            append_conversion_record(
                args.conversion_log, meta, md_path, img_count, "playwright", article_type
            )

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
