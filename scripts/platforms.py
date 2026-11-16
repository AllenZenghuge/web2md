"""小红书 / 抖音 平台解析。

设计原则：本模块只做「输入文本/JSON → Post」的纯函数解析，不碰浏览器、
不发网络请求，方便用真实抓包做单测。抓取部分留在 web2md.py。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

PLATFORM_WECHAT = "wechat"
PLATFORM_XHS = "xiaohongshu"
PLATFORM_DOUYIN = "douyin"
PLATFORM_GENERIC = "generic"

# 图集项类型，与 App 端 GalleryItemKind 对齐
KIND_STILL = "still"
KIND_LIVE_PHOTO = "livePhoto"
KIND_VIDEO_CLIP = "videoClip"

MAX_IMAGES = 20


@dataclass
class MediaItem:
    url: str
    kind: str = KIND_STILL
    motion_url: Optional[str] = None


@dataclass
class Post:
    platform: str
    title: str = ""
    author: str = ""
    text: str = ""
    publish_date: str = ""
    images: list[MediaItem] = field(default_factory=list)
    video_url: str = ""
    video_cover: str = ""


# ── URL / 文本工具 ──────────────────────────────────────────────────


def detect_platform(url: str) -> str:
    """按域名判断平台。不认识的一律 generic。"""
    url = extract_first_url(url) or url.strip()
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("mp.weixin.qq.com"):
        return PLATFORM_WECHAT
    if host.endswith("xiaohongshu.com") or host.endswith("xhslink.com"):
        return PLATFORM_XHS
    if host.endswith("douyin.com") or host.endswith("iesdouyin.com"):
        return PLATFORM_DOUYIN
    return PLATFORM_GENERIC


def extract_first_url(text: str) -> str:
    """小红书/抖音分享文案是「文字 + 链接」混合，抠出其中第一个 URL。

    排除中文，避免把「链接后紧跟的汉字」吞进 URL。
    """
    m = re.search(r"https?://[^\s一-鿿]+", text)
    return m.group(0) if m else ""


def normalize_to_https(url: str) -> str:
    """平台 CDN 常返回 http:// 或协议相对 //，统一成 https。"""
    if not url:
        return ""
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    if url.startswith("//"):
        return "https:" + url
    return url


def unescape_json_str(s: str) -> str:
    """还原 JSON 转义（小红书常见 \\u002F\\u002F）。还原失败就原样返回。"""
    if not s or "\\" not in s:
        return s
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s


def _bracket_slice(text: str, start: int, open_ch: str) -> Optional[str]:
    """从 start 处的开括号起，按括号配对截取完整 JSON 片段。

    正则在嵌套对象上会提前截断，所以这里必须手工配对。
    """
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _loads_lenient(raw: str) -> Any:
    """小红书页面状态里混有 JS 的 undefined，直接 json.loads 会失败。"""
    cleaned = re.sub(r"\bundefined\b", "null", raw)
    return json.loads(cleaned)


# ── 小红书 ─────────────────────────────────────────────────────────

_XHS_STATE_MARKERS = ("window.__INITIAL_STATE__", "__INITIAL_STATE__")


def parse_xhs(html: str, source_url: str) -> Optional[Post]:
    """从小红书笔记页 HTML 解析。取不到任何媒体时返回 None（登录墙/壳页）。"""
    note = _xhs_note_object(html)
    if not note:
        return None

    post = Post(platform=PLATFORM_XHS)
    post.title = (note.get("title") or "").strip()
    post.text = (note.get("desc") or "").strip()
    user = note.get("user") or {}
    post.author = (user.get("nickname") or "").strip()
    post.publish_date = _ms_to_date(note.get("time"))

    post.images = _xhs_images(note.get("imageList") or [])
    post.video_url, post.video_cover = _xhs_note_video(note, post.images)

    if not post.images and not post.video_url:
        return None
    if not post.title:
        # 标题为空时退到正文首行，避免文件名变成 untitled
        post.title = (post.text.splitlines()[0][:40] if post.text else "小红书笔记")
    return post


def _xhs_note_object(html: str) -> Optional[dict]:
    """定位 __INITIAL_STATE__ 并取出第一条笔记。"""
    idx = -1
    for marker in _XHS_STATE_MARKERS:
        idx = html.find(marker)
        if idx >= 0:
            break
    state = None
    if idx >= 0:
        brace = html.find("{", idx)
        raw = _bracket_slice(html, brace, "{") if brace >= 0 else None
        if raw:
            try:
                state = _loads_lenient(raw)
            except Exception:
                state = None

    # 兜底：退化为正则捞关键字段（页面结构变化时不至于整体失败）
    if not isinstance(state, dict):
        return _xhs_note_from_regex(html)

    detail_map = (state.get("note") or {}).get("noteDetailMap") or {}
    for entry in detail_map.values():
        note = entry.get("note") if isinstance(entry, dict) else None
        if isinstance(note, dict):
            return note
    return _xhs_note_from_regex(html)


def _xhs_note_from_regex(html: str) -> Optional[dict]:
    """状态对象取不到时的兜底：只用平铺字段拼一个伪 note。"""
    desc = _first_json_string(html, "desc")
    nickname = _first_json_string(html, "nickname")
    title = _first_json_string(html, "title")
    imgs = _slice_after_key(html, "imageList", "[")
    image_list = []
    if imgs:
        try:
            image_list = _loads_lenient(imgs)
        except Exception:
            image_list = []
    if not any((desc, nickname, title, image_list)):
        return None
    return {"title": title, "desc": desc, "user": {"nickname": nickname},
            "imageList": image_list}


def _first_json_string(text: str, key: str) -> str:
    m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % re.escape(key), text)
    return unescape_json_str(m.group(1)) if m else ""


def _slice_after_key(text: str, key: str, open_ch: str) -> Optional[str]:
    pos = text.find('"%s":' % key)
    if pos < 0:
        return None
    begin = text.find(open_ch, pos)
    return _bracket_slice(text, begin, open_ch) if begin >= 0 else None


def _xhs_images(image_list: list) -> list[MediaItem]:
    """图集解析。必须先解析 JSON 再关联字段，正则无法把 urlDefault /
    livePhoto / masterUrl 三个字段对应到同一张图。"""
    items: list[MediaItem] = []
    seen: set[str] = set()
    for entry in image_list[:MAX_IMAGES]:
        if not isinstance(entry, dict):
            continue
        url = normalize_to_https(unescape_json_str(entry.get("urlDefault") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        clip = _xhs_live_clip(entry) if entry.get("livePhoto") else None
        items.append(MediaItem(url=url,
                               kind=KIND_LIVE_PHOTO if clip else KIND_STILL,
                               motion_url=clip))
    return items


def _xhs_live_clip(entry: dict) -> Optional[str]:
    """实况照片的动态片段：stream.h264[0].masterUrl；取不到就退回静态图。"""
    stream = entry.get("stream") or {}
    h264 = stream.get("h264") or []
    if not isinstance(h264, list) or not h264:
        return None
    raw = h264[0].get("masterUrl") if isinstance(h264[0], dict) else None
    return normalize_to_https(unescape_json_str(raw)) if raw else None


def _xhs_note_video(note: dict, images: list[MediaItem]) -> tuple[str, str]:
    """取笔记自身的视频。

    图集里每张实况照片的伴随片段也是 masterUrl，直接取第一个会把实况片段
    误当成笔记视频，凭空多出一个视频。所以必须排除掉图集自带的片段。
    """
    clip_urls = {i.motion_url for i in images if i.motion_url}
    video = note.get("video") or {}
    media = video.get("media") or {}
    for codec in ("h264", "h265", "av1", "h266"):
        variants = media.get("stream", {}).get(codec) or []
        for v in variants:
            if not isinstance(v, dict):
                continue
            raw = normalize_to_https(unescape_json_str(v.get("masterUrl") or ""))
            if raw and raw not in clip_urls:
                return raw, _xhs_cover(note, images)
    return "", ""


def _xhs_cover(note: dict, images: list[MediaItem]) -> str:
    if images:
        return images[0].url
    return normalize_to_https(unescape_json_str(
        (note.get("imageList") or [{}])[0].get("urlDefault", ""))) if note.get("imageList") else ""


def _ms_to_date(value: Any) -> str:
    """毫秒时间戳 → YYYY-MM-DD。取不到返回空串。"""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    if ms > 10 ** 12:  # 毫秒
        ms //= 1000
    from datetime import datetime
    try:
        return datetime.fromtimestamp(ms).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        return ""


# ── 抖音 ───────────────────────────────────────────────────────────

def parse_douyin(detail: dict, source_url: str) -> Optional[Post]:
    """把 aweme_detail 转成 Post。detail 来自页面拦截到的接口响应。"""
    if not isinstance(detail, dict):
        return None

    post = Post(platform=PLATFORM_DOUYIN)
    post.text = (detail.get("desc") or "").strip()
    author = detail.get("author") or {}
    post.author = (author.get("nickname") or "").strip()
    post.publish_date = _ms_to_date(detail.get("create_time"))

    video = detail.get("video") or {}
    cover = _first_url((video.get("cover") or {}))

    post.images = _douyin_images(detail.get("images") or [])

    # 图文作品的 video.play_addr 是背景音乐 mp3 的壳子（bit_rate 为 null），
    # 不是视频流。只要图集非空就绝不产出顶层视频，否则下载会得到无效媒体。
    if not post.images:
        play = _first_url(video.get("play_addr") or {})
        if play:
            post.video_url = play
            post.video_cover = cover
    if not post.images and cover:
        post.images = [MediaItem(url=cover)]

    if not post.images and not post.video_url:
        return None

    if not post.title:
        post.title = _douyin_title(post.text)
    return post


def _douyin_title(text: str) -> str:
    """抖音没有独立标题，取文案首行；去掉话题标签和首尾空白。"""
    first = (text.splitlines()[0] if text else "").strip()
    first = re.sub(r"#\S+", "", first).strip()
    return first[:40] or "抖音作品"


def _douyin_images(raw_images: list) -> list[MediaItem]:
    items: list[MediaItem] = []
    seen: set[str] = set()
    for img in raw_images[:MAX_IMAGES]:
        if not isinstance(img, dict):
            continue
        url = _first_url(img)
        if not url or url in seen:
            continue
        seen.add(url)
        clip = _douyin_clip(img)
        if _is_live_photo_type(img.get("live_photo_type")):
            kind = KIND_LIVE_PHOTO
        elif clip:
            kind = KIND_VIDEO_CLIP
        else:
            kind = KIND_STILL
        items.append(MediaItem(url=url, kind=kind if clip else KIND_STILL,
                               motion_url=clip))
    return items


def _is_live_photo_type(value: Any) -> bool:
    """接口里出现过数字和字符串两种形态，都认。"""
    return value == 1 or value == "1"


def _douyin_clip(img: dict) -> Optional[str]:
    """图集项的动态片段：video.bit_rate[0].play_addr.url_list[0]。"""
    video = img.get("video") or {}
    bit_rate = video.get("bit_rate") or []
    if not isinstance(bit_rate, list) or not bit_rate:
        return None
    return _first_url((bit_rate[0] or {}).get("play_addr") or {})


def _first_url(container: dict) -> str:
    urls = container.get("url_list") or []
    return urls[0] if isinstance(urls, list) and urls else ""


# ── 抖音浏览器侧：拦截 aweme/detail 响应 ───────────────────────────

DOUYIN_DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)

# 桌面 UA 才会进 PC 版页面并调用 aweme/detail；移动 UA 只会落到
# iesdouyin.com 的「打开抖音 App」引导页，该页永远不请求详情接口。
DOUYIN_HOOK_JS = """
(function () {
    window.__dyCapture = null;
    window.__dyProbe = { paths: [], detailRequested: 0 };
    function note(url) {
        try {
            var s = String(url);
            if (s.indexOf('/aweme/') < 0) return;
            var clean = s.split('?')[0];
            window.__dyProbe.paths.push(clean);
            if (clean.indexOf('aweme/detail') >= 0) window.__dyProbe.detailRequested++;
        } catch (e) {}
    }
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
        this.__dyUrl = url; note(url); return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
        var self = this;
        this.addEventListener('load', function () {
            if (self.__dyUrl && String(self.__dyUrl).indexOf('aweme/detail') >= 0) {
                window.__dyCapture = self.responseText;
            }
        });
        return origSend.apply(this, arguments);
    };
    var origFetch = window.fetch;
    window.fetch = function (input) {
        var url = (typeof input === 'string') ? input : ((input && input.url) || '');
        note(url);
        return origFetch.apply(this, arguments).then(function (resp) {
            if (url.indexOf('aweme/detail') >= 0) {
                resp.clone().text().then(function (t) { window.__dyCapture = t; }).catch(function () {});
            }
            return resp;
        });
    };
})();
"""

DOUYIN_NOTE_ID_RE = re.compile(r"/(\d{15,25})")


def douyin_video_url_from_share(url: str) -> str:
    """/note/{id} 在 PC 端既不请求详情也不渲染，必须改写成 /video/{id}。"""
    if "/note/" not in url:
        return url
    m = DOUYIN_NOTE_ID_RE.search(url)
    return f"https://www.douyin.com/video/{m.group(1)}" if m else url
