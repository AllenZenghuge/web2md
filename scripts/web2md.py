#!/usr/bin/env python3
"""web2md — Convert web pages and WeChat articles to Markdown with local images."""

import argparse
import json
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

from asr import (AsrError, local_asr_available, render_transcript, transcribe,
                 transcribe_local)
from platforms import (
    DOUYIN_DESKTOP_UA,
    PLATFORM_DOUYIN,
    PLATFORM_XHS,
    XHS_MOBILE_UA,
    detect_platform,
    extract_first_url,
)
from social import LABELS, download_media, fetch_social_post


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
        - 小红书 / 抖音 → playwright (需浏览器渲染 + 接口拦截)
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
    if detect_platform(url) in (PLATFORM_XHS, PLATFORM_DOUYIN):
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
    parser.add_argument(
        "--filename",
        default="",
        help="提炼后的短文件名（不含扩展名）。不传则直接用原标题",
    )
    parser.add_argument(
        "--save-video",
        action="store_true",
        help="小红书/抖音：把视频（及实况片段）一起下载到本地",
    )
    parser.add_argument(
        "--no-transcript",
        action="store_true",
        help="小红书/抖音：跳过语音转写，不生成逐字稿与摘要",
    )
    parser.add_argument(
        "--asr-model",
        default="paraformer-v2",
        help="DashScope 转写模型（默认 paraformer-v2）",
    )
    parser.add_argument(
        "--language",
        default="zh",
        help="转写语言提示（默认 zh）",
    )
    parser.add_argument(
        "--local-asr",
        action="store_true",
        help="云转写失败（或未配 key）时降级到本地 faster-whisper。需要额外装可选依赖，"
             "见 scripts/requirements-local-asr.txt。不加此参数时行为与原来完全一致",
    )
    parser.add_argument(
        "--local-asr-model",
        default="small",
        help="本地转写的 whisper 模型尺寸：tiny/base/small/medium（默认 small）",
    )
    parser.add_argument(
        "--vad",
        action="store_true",
        help="本地转写时开启 VAD 静音过滤。默认关闭——vlog 的口播常垫在音乐上，"
             "VAD 会把整片判成非语音，只吐出几个字的噪音",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="跳过「已转换过」检查，强制重新转换（默认会按 source_url 查已有笔记并跳过）",
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
        # Newer WeChat article layouts no longer render #activity-name;
        # h1.rich_media_title / og:title still carry the title.
        meta["title"] = (
            _get_text(page, "#activity-name")
            or _get_text(page, "h1.rich_media_title")
            or _get_meta(page, "og:title")
            or page.title()
            or ""
        )
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
                // 图片消息（#js_image_content）的主图在 swiper 容器里，不在 #js_content 内，
                // 需手动搬到正文，否则会丢失全部配图。
                if (document.getElementById('js_image_content')) {
                    const seen = new Set();
                    const imgs = [];
                    document.querySelectorAll('#img_list img, #img_swiper img').forEach(el => {
                        const src = el.getAttribute('src') || el.getAttribute('data-src') || '';
                        if (src && !seen.has(src)) { seen.add(src); imgs.push(src); }
                    });
                    const anchor = content.querySelector('#js_image_desc') || content.firstChild;
                    const parent = (anchor && anchor.parentNode) || content;
                    imgs.reverse().forEach(src => {
                        const img = document.createElement('img');
                        img.setAttribute('src', src);
                        img.setAttribute('alt', '图片');
                        parent.insertBefore(img, anchor);
                    });
                }
                const removals = content.querySelectorAll(
                    'h1.rich_media_title, '
                    + '.rich_media_meta_list, .reward_area, .like_media_thumb, '
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
                           count: int, user_agent: str = "",
                           referer: str = "") -> tuple:
    """Download one image, return (local_filename, succeeded).
    Shared by both HTML and Markdown image pipelines.

    user_agent: 小红书 CDN 的 !h5_ 变体只认移动 UA，桌面 UA 会 403。
    referer: 小红书/抖音 CDN 只认自家主域，传短链域名会 403。
    """
    headers = {
        "Referer": referer or page_url,
        "User-Agent": user_agent or (
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


# ── 小红书 / 抖音 ────────────────────────────────────────────────────


def convert_social_post(args, platform: str, output_dir: Path) -> None:
    """小红书/抖音转换主流程：取 Post → 下载媒体 → （可选）转写 → 写 md。"""
    label = LABELS[platform]
    print(f"正在转换{label}（{platform} 后端）...", file=sys.stderr)

    post = fetch_social_post(args.url, platform, timeout=max(args.timeout, 40),
                             page_timeout=max(args.timeout, 30))
    if post is None:
        # 具体原因由抓取层打印（登录墙 / 仅 App 内查看 / 结构变更 / 已删除）
        sys.exit(6)

    title = post.title or "未命名"
    # 抓取前只按 URL 查过；短链每次分享都可能不一样，抓到标题和作者后再兜一次
    if not args.force:
        hit = find_converted(args.url, output_dir, title=title, author=post.author or "")
        if hit and hit.get("by") == "title+author":
            _report_duplicate(hit)
            return
    safe_title = sanitize_filename(args.filename or title)
    wrapper_dir = output_dir / "web2md"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    md_path = wrapper_dir / f"{safe_title}.md"
    img_dir = wrapper_dir / "images" / safe_title
    img_rel_prefix = f"images/{safe_title.replace(' ', '%20')}/"

    parts: list[str] = []
    if post.text:
        parts.append(post.text)

    img_count = 0
    downloaded: list[tuple[int, str, Path]] = []
    platform_ua = XHS_MOBILE_UA if platform == PLATFORM_XHS else DOUYIN_DESKTOP_UA
    platform_referer = ("https://www.xiaohongshu.com/" if platform == PLATFORM_XHS
                        else "https://www.douyin.com/")
    if post.images:
        if args.no_images:
            parts.extend(f"![图片]({i.url})" for i in post.images)
        else:
            img_dir.mkdir(parents=True, exist_ok=True)
            for idx, item in enumerate(post.images, 1):
                filename, ok = _download_single_image(
                    item.url, args.url, img_dir, idx,
                    user_agent=platform_ua, referer=platform_referer)
                if ok:
                    img_count += 1
                    downloaded.append((idx, filename, img_dir / filename))
                else:
                    parts.append(f"[Image: {item.url}]")

    # 图片说明由对话模型读图后补写，脚本只留占位
    for idx, filename, _ in downloaded:
        parts.append(f"![图片]({img_rel_prefix}{filename})")
        parts.append(f"> **图 {idx}**：（图片说明待补）")

    # 实况照片的动态片段：不下载时至少把地址留在笔记里
    clips = [i for i in post.images if i.motion_url]
    if clips and not args.save_video:
        parts.append("**实况片段**：" + " / ".join(i.motion_url for i in clips))

    transcript = None
    if post.video_url:
        parts.append(f"**视频地址**：{post.video_url}")
        video_dir = img_dir if not args.no_images else wrapper_dir
        local_video = ""
        # --save-video 要留档；--local-asr 要本地文件才能解码，两种情况都得先下
        if args.save_video or args.local_asr:
            name, ok = download_media(post.video_url, video_dir,
                                       f"{safe_title}-视频", platform_referer)
            if ok:
                local_video = str(video_dir / name)
                if args.save_video:
                    parts.append(f"**本地视频**：{img_rel_prefix}{name}")
        if not args.no_transcript:
            transcript = _try_transcribe(post.video_url, args, local_video)

    if transcript is not None:
        parts.append(transcript)

    meta = {
        "title": title,
        "author": post.author,
        "account_name": post.author,
        "source_url": args.url,
        "publish_date": post.publish_date,
        "converted_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "description": _social_description(post),
        "platform": platform,
        "video_url": post.video_url,
        "bgm": post.bgm,
        "backend": platform,
    }
    meta = normalize_meta(meta, args.url, label)
    meta["tags"] = ["web2md", label]
    meta["aliases"] = [title]

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(meta, allow_unicode=True, default_flow_style=False,
                          sort_keys=False).strip())
        f.write("\n---\n\n")
        f.write("\n\n".join(p for p in parts if p.strip()))
        f.write("\n")

    if args.conversion_log:
        append_conversion_record(args.conversion_log, meta, md_path, img_count,
                                 platform, label)

    print(f"DONE: {md_path}", file=sys.stderr)
    print(f"Title: {title}", file=sys.stderr)
    if post.author:
        print(f"Author: {post.author}", file=sys.stderr)
    print(f"Images: {img_count} downloaded", file=sys.stderr)
    if post.video_url:
        print("Video: 1", file=sys.stderr)


def _social_description(post: "Post") -> str:
    text = re.sub(r"\s+", " ", post.text).strip()
    return text[:120]


def _try_transcribe(video_url: str, args, local_media: str = "") -> str:
    """转写。云失败时若开了 --local-asr 则降级本地；都失败只跳过，不中断转换。"""
    t = None
    try:
        t = transcribe(video_url, model=args.asr_model, language=args.language,
                       timeout=max(args.timeout * 20, 300))
    except AsrError as e:
        print(f"WARNING: 云转写失败 — {e}", file=sys.stderr)

    if t is None or not t.ok:
        if not args.local_asr:
            print("WARNING: 转写跳过（加 --local-asr 可改用本地转写）", file=sys.stderr)
            return ""
        t = _local_fallback(local_media, args)

    if t is None or not t.ok:
        print("WARNING: 转写结果为空", file=sys.stderr)
        return ""

    blocks = ["## 逐字稿", render_transcript(t)]
    print(f"Transcript: {len(t.text)} 字（{t.model}）", file=sys.stderr)
    return "\n\n".join(blocks)


def _local_fallback(local_media: str, args):
    """本地 faster-whisper 兜底。依赖缺失时把安装命令打给用户，然后返回 None。"""
    ok, why = local_asr_available()
    if not ok:
        print(f"WARNING: 本地转写依赖缺失（{why}）\n"
              "  装法：python3 -m venv ~/.local/share/whisper-env && "
              "~/.local/share/whisper-env/bin/pip install faster-whisper\n"
              "  然后用该环境的解释器重跑本脚本", file=sys.stderr)
        return None
    if not local_media:
        print("WARNING: 本地转写需要视频文件，但没下载到，跳过", file=sys.stderr)
        return None

    print(f"INFO: 改用本地 faster-whisper/{args.local_asr_model} 转写"
          "（首次会下载模型，音频长则数分钟）", file=sys.stderr)
    try:
        return transcribe_local(local_media, model_size=args.local_asr_model,
                                language=args.language, vad_filter=args.vad)
    except AsrError as e:
        print(f"WARNING: 本地转写失败 — {e}", file=sys.stderr)
        return None


def _norm_url(u: str) -> str:
    """URL 归一化：去协议、去尾斜杠、去 query/fragment。"""
    u = (u or "").strip().strip("'\"").rstrip("/")
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"[?#].*$", "", u)
    return u.lower()


def _read_existing_notes(output_dir: Path):
    """遍历 web2md/ 下已有笔记，yield (Path, frontmatter 文本)。"""
    wrapper = output_dir / "web2md"
    if not wrapper.is_dir():
        return
    for md in sorted(wrapper.glob("*.md")):
        if md.name.startswith("转换记录"):
            continue
        try:
            yield md, md.read_text(encoding="utf-8", errors="ignore")[:3000]
        except OSError:
            continue


def _note_meta(head: str) -> dict:
    """从 frontmatter 文本里取标题、作者、来源、转换时间。"""
    def get(key):
        m = re.search(rf"^{key}:\s*(.+)$", head, re.M)
        return m.group(1).strip().strip("'\"") if m else ""

    title = re.search(r"^title:\s*(.+)$", head, re.M)
    return {
        "title": title.group(1).strip().strip("'\"") if title else "",
        "author": get("author"),
        "source_url": get("source_url"),
        "converted_at": get("converted_at"),
    }


def find_converted(url: str, output_dir: Path, *, title: str = "", author: str = "") -> dict:
    """查已转换笔记：先按 source_url，再按「标题+作者」兜短链换皮。

    URL 查重拦得住同链接重复；抖音短链每次分享都可能不一样，所以抓到
    post 之后再拿 title+author 查一次，避免同一作品转两遍。两者都没命中
    才返回 {}。返回 {title, author, path, converted_at, source_url, by}。
    """
    target = _norm_url(url)
    for md, head in _read_existing_notes(output_dir):
        meta = _note_meta(head)
        if target and meta["source_url"] and _norm_url(meta["source_url"]) == target:
            by = "source_url"
        elif (title and author and meta["title"] and meta["author"]
              and _norm_text(title) == _norm_text(meta["title"])
              and _norm_text(author) == _norm_text(meta["author"])):
            by = "title+author"
        else:
            continue
        return {**meta, "path": str(md), "title": meta["title"] or md.stem, "by": by}
    return {}


def _norm_text(s: str) -> str:
    """标题/作者归一化：去空白、标点和 emoji，只留中文/字母/数字。"""
    return re.sub(r"[\s\W_]+", "", s or "").lower()


def _report_duplicate(hit: dict) -> None:
    """统一打印「已转换过」回显，抓取前后两处查重都用它。"""
    reason = "（同一作品的另一个链接）" if hit.get("by") == "title+author" else ""
    print(f"已转换过，本次跳过{reason}。", file=sys.stderr)
    print(f"  标题：{hit['title']}", file=sys.stderr)
    print(f"  文件：{hit['path']}", file=sys.stderr)
    if hit.get("converted_at"):
        print(f"  时间：{hit['converted_at']}", file=sys.stderr)
    if hit.get("source_url"):
        print(f"  来源：{hit['source_url']}", file=sys.stderr)
    print("  确认要重转，加 --force", file=sys.stderr)


def main() -> None:
    args = parse_args()

    # 小红书/抖音分享文案是「文字 + 链接」混合，先抠出链接
    args.url = extract_first_url(args.url) or args.url.strip()

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

    if not args.force:
        hit = find_converted(args.url, output_dir)
        if hit:
            _report_duplicate(hit)
            sys.exit(0)

    platform = detect_platform(args.url)
    if platform in (PLATFORM_XHS, PLATFORM_DOUYIN) and args.backend != "defuddle":
        convert_social_post(args, platform, output_dir)
        return
    use_defuddle = should_use_defuddle(args.url, args.backend)

    # ── defuddle path (fast extraction + local image download) ────
    if use_defuddle:
        article_type = "网页文章"
        print(f"正在转换{article_type}（defuddle 后端）...", file=sys.stderr)

        md_content, meta = fetch_via_defuddle(args.url, args.timeout)

        title = meta.get("title", "untitled")
        safe_title = sanitize_filename(args.filename or title)

        wrapper_dir = output_dir / "web2md"
        wrapper_dir.mkdir(parents=True, exist_ok=True)

        md_path = wrapper_dir / f"{safe_title}.md"

        # Download images referenced in markdown
        img_dir = wrapper_dir / "images" / safe_title
        img_rel_prefix = f"images/{safe_title.replace(' ', '%20')}/"

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
        # 标题提炼只用于非微信链接；公众号保留原标题
        safe_title = sanitize_filename(
            title if is_wechat else (args.filename or title))

        # Output .md directly under web2md/
        wrapper_dir = output_dir / "web2md"
        wrapper_dir.mkdir(parents=True, exist_ok=True)

        md_path = wrapper_dir / f"{safe_title}.md"

        # Images go to web2md/images/, organized by article title
        img_dir = wrapper_dir / "images" / safe_title
        img_rel_prefix = f"images/{safe_title.replace(' ', '%20')}/"

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

        if "cover_image" in meta and not args.no_images:
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
