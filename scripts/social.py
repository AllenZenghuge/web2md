"""小红书 / 抖音的浏览器抓取层。

只负责「打开页面 → 拿到可解析的数据 → 返回 Post」，不涉及落盘、YAML、
转写。落盘编排留在 web2md.py，避免和它循环依赖。
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from platforms import (
    DOUYIN_DESKTOP_UA,
    DOUYIN_HOOK_JS,
    PLATFORM_XHS,
    Post,
    XHS_MOBILE_UA,
    douyin_video_url_from_share,
    parse_douyin,
    parse_xhs,
)

LABELS = {"xiaohongshu": "小红书笔记", "douyin": "抖音作品"}


def _launch_browser(user_agent: str):
    """起一个桌面 UA 的 Chromium，返回 (playwright, browser, page)。"""
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
    context = browser.new_context(user_agent=user_agent, locale="zh-CN")
    return playwright, browser, context.new_page()


def fetch_social_post(url: str, platform: str, timeout: int = 40,
                      page_timeout: int = 30) -> Optional[Post]:
    """打开页面并解析成 Post。抓不到返回 None。"""
    if platform == PLATFORM_XHS:
        return _fetch_xhs(url, page_timeout)
    return _fetch_douyin(url, timeout, page_timeout)


def _fetch_xhs(url: str, page_timeout: int) -> Optional[Post]:
    """小红书：优先直连 HTTP（移动 UA 会直接返回带数据的分享页），失败再用浏览器。

    桌面 UA 会被重定向到登录页，所以全程用移动 UA。
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": XHS_MOBILE_UA, "Referer": "https://www.xiaohongshu.com/"},
            timeout=page_timeout, allow_redirects=True,
        )
        if resp.status_code == 200:
            post = parse_xhs(resp.text, url)
            if post:
                return post
            if "仅支持在小红书" in resp.text or "打开 APP 查看" in resp.text:
                print("ERROR: 该笔记仅支持在小红书 App 内查看，网页端无法获取。",
                      file=sys.stderr)
                return None
            if "/login" in resp.url or "登录后推荐更懂你的笔记" in resp.text:
                print("ERROR: 小红书要求登录后才能查看该笔记（被重定向到登录页）。",
                      file=sys.stderr)
                return None
    except requests.RequestException as e:
        print(f"WARNING: 小红书直连失败（{e}），改用浏览器重试", file=sys.stderr)

    playwright, browser, page = _launch_browser(XHS_MOBILE_UA)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=page_timeout * 1000)
        page.wait_for_timeout(3000)
        if "/login" in page.url:
            print("ERROR: 小红书要求登录后才能查看该笔记（页面被重定向到登录页）。\n"
                  "       当前无登录态，无法抓取。", file=sys.stderr)
            return None
        html = page.content()
        if "仅支持在小红书" in html or "打开 APP 查看" in html:
            print("ERROR: 该笔记仅支持在小红书 App 内查看，网页端无法获取。",
                  file=sys.stderr)
            return None
        post = parse_xhs(html, url)
        if post is None:
            print(f"ERROR: 小红书页面未提取到任何媒体（最终 URL: {page.url[:120]}）。\n"
                  "       可能是笔记已删除、需要登录，或页面结构已变更。", file=sys.stderr)
        return post
    finally:
        browser.close()
        playwright.stop()


def _resolve_short_link(url: str, timeout: int) -> str:
    """短链先用 HTTP 跟随重定向拿到真实地址，避免浏览器跳转不稳。"""
    try:
        resp = requests.get(url, headers={"User-Agent": DOUYIN_DESKTOP_UA},
                            timeout=timeout, allow_redirects=True, stream=True)
        return resp.url or url
    except requests.RequestException:
        return url


def _fetch_douyin(url: str, timeout: int, page_timeout: int) -> Optional[Post]:
    """短链先重定向；落点是 /note/ 的话改写成 /video/ 才会触发详情接口。"""
    target = douyin_video_url_from_share(_resolve_short_link(url, page_timeout))
    playwright, browser, page = _launch_browser(DOUYIN_DESKTOP_UA)
    try:
        page.add_init_script(DOUYIN_HOOK_JS)
        page.goto(target, wait_until="domcontentloaded", timeout=page_timeout * 1000)
        page.wait_for_timeout(1500)

        # 短链会跳到 /note/{id}；PC 端该页面不请求 aweme/detail，
        # 必须按作品 ID 换成 /video/{id} 再加载一次。
        landed = page.url
        rewritten = douyin_video_url_from_share(landed)
        if rewritten != landed:
            page.goto(rewritten, wait_until="domcontentloaded",
                      timeout=page_timeout * 1000)

        try:
            page.wait_for_function("() => window.__dyCapture !== null",
                                   timeout=timeout * 1000)
        except PlaywrightTimeout:
            _report_failure(page, rewritten, timeout)
            return None

        raw = page.evaluate("() => window.__dyCapture")
        if not raw:
            _report_failure(page, rewritten, timeout)
            return None
        try:
            root = json.loads(raw)
        except ValueError:
            print(f"ERROR: 抖音 detail 响应非合法 JSON（{len(raw)} 字节）", file=sys.stderr)
            return None

        detail = root.get("aweme_detail")
        if not isinstance(detail, dict):
            keys = ",".join(sorted(root.keys())[:6])
            print(f"ERROR: 抖音 detail 响应无 aweme_detail 字段（顶层键: {keys}）",
                  file=sys.stderr)
            return None
        return parse_douyin(detail, url)
    finally:
        browser.close()
        playwright.stop()


def _report_failure(page, target: str, timeout: int) -> None:
    """超时时打印页面状态，区分「没发请求」和「发了没回来」。"""
    try:
        state = page.evaluate("""() => ({
            url: location.href,
            title: document.title || '',
            body: (document.body && document.body.innerText || '').length,
            detailRequested: (window.__dyProbe || {}).detailRequested || 0,
            paths: ((window.__dyProbe || {}).paths || []).slice(-5)
        })""")
        print(f"ERROR: {timeout} 秒内未捕获到抖音 aweme/detail 响应（目标 {target}）"
              f"；页面状态 {json.dumps(state, ensure_ascii=False)}", file=sys.stderr)
    except Exception:
        print(f"ERROR: {timeout} 秒内未捕获到抖音 aweme/detail 响应（目标 {target}）",
              file=sys.stderr)


def download_media(url: str, dest_dir, name: str, referer: str) -> tuple:
    """下载视频/实况片段。抖音 CDN 必须带 Referer，否则 403。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": DOUYIN_DESKTOP_UA, "Referer": referer}
    try:
        resp = requests.get(url, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()
        ext = ".mov" if "quicktime" in resp.headers.get("Content-Type", "") else ".mp4"
        path = dest_dir / f"{name}{ext}"
        with open(path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return path.name, True
    except requests.RequestException as e:
        print(f"WARNING: 媒体下载失败 {url}: {e}", file=sys.stderr)
        return "", False
