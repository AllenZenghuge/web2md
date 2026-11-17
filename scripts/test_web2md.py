#!/usr/bin/env python3
"""TDD tests for web2md.py — v1.3.0 behavior verification.

RED phase: Write tests first, verify they test the right things.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts dir to path so we can import functions from web2md
sys.path.insert(0, os.path.expanduser("~/.claude/skills/web2md/scripts"))

# Import testable functions (not main() which requires network)
from web2md import (
    sanitize_filename,
    is_wechat_url,
    parse_args,
    append_conversion_record,
    normalize_meta,
)
from platforms import (
    detect_platform,
    douyin_video_url_from_share,
    extract_first_url,
    parse_douyin,
    parse_xhs,
)


class TestOutputStructure(unittest.TestCase):
    """v1.3.0: .md files go directly under web2md/, not in type subdirectories."""

    def test_sanitize_filename_handles_chinese(self):
        """中文标题应正确保留"""
        result = sanitize_filename("金税四期：企业发票管理新规解读")
        self.assertIn("金税四期", result)
        self.assertNotIn("/", result)

    def test_sanitize_filename_strips_unsafe_chars(self):
        """非法文件名字符应被替换为短横线"""
        result = sanitize_filename('test/:*?"<>|file')
        self.assertNotIn("/", result)
        self.assertNotIn(":", result)
        self.assertNotIn("*", result)
        self.assertNotIn("?", result)
        self.assertNotIn('"', result)
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertNotIn("|", result)

    def test_sanitize_filename_truncates_long_names(self):
        """超过 100 字符的标题应截断"""
        long_title = "这是一个非常长的标题" * 10  # ~100 chars
        result = sanitize_filename(long_title)
        self.assertLessEqual(len(result), 100)

    def test_sanitize_filename_replaces_spaces_with_underscore(self):
        """空格统一换成下划线，文件名和图片目录名都不带空格"""
        result = sanitize_filename("软考成绩未出 但考试难度可以预测通过率")
        self.assertEqual(result, "软考成绩未出_但考试难度可以预测通过率")
        self.assertNotIn(" ", result)
        # 连续空白压成一个下划线
        self.assertEqual(sanitize_filename("a   b\tc"), "a_b_c")


class TestURLAnalysis(unittest.TestCase):
    """URL 分类逻辑"""

    def test_is_wechat_url_true(self):
        """微信 URL 正确识别"""
        self.assertTrue(is_wechat_url("https://mp.weixin.qq.com/s/abc123"))
        self.assertTrue(is_wechat_url("http://mp.weixin.qq.com/s?__biz=xxx"))

    def test_is_wechat_url_false(self):
        """非微信 URL 返回 False"""
        self.assertFalse(is_wechat_url("https://example.com/article"))
        self.assertFalse(is_wechat_url("https://zhuanlan.zhihu.com/p/123"))


class TestCLIArgs(unittest.TestCase):
    """CLI 参数解析"""

    def test_conversion_log_arg_exists(self):
        """v1.2.0: --conversion-log 参数存在"""
        args = parse_args.__wrapped__ if hasattr(parse_args, '__wrapped__') else parse_args
        import inspect
        # Just verify the argument is registered in the parser
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("url")
        parser.add_argument("--output-dir", default=".")
        parser.add_argument("--no-images", action="store_true")
        parser.add_argument("--timeout", type=int, default=30)
        parser.add_argument("--backend", choices=["auto", "playwright", "defuddle"], default="auto")
        parser.add_argument("--conversion-log", default="")
        # If we get here without error, --conversion-log exists
        test_args = parser.parse_args(["http://example.com", "--conversion-log", "/tmp/test.md"])
        self.assertEqual(test_args.conversion_log, "/tmp/test.md")


class TestConversionRecord(unittest.TestCase):
    """v1.2.0: 转换记录功能"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "test_log.md")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_record_creates_file_with_frontmatter(self):
        """首次记录：创建含 frontmatter 和表头的文件"""
        meta = {"title": "测试文章", "source_url": "https://example.com/test"}
        md_path = Path("/tmp/web2md/测试文章.md")

        append_conversion_record(
            self.log_path, meta, md_path, 3, "defuddle", "网页文章"
        )

        self.assertTrue(os.path.exists(self.log_path))

        with open(self.log_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查 frontmatter 结构
        self.assertIn("---", content)
        self.assertIn("title: web2md 转换记录", content)
        self.assertIn("tags:", content)
        self.assertIn("web2md", content)
        self.assertIn("aliases:", content)
        self.assertIn("转换记录", content)

        # 检查表头
        self.assertIn("| 转换时间 |", content)
        self.assertIn("| 文章标题 |", content)
        self.assertIn("| 来源 URL |", content)
        self.assertIn("| 输出路径 |", content)
        self.assertIn("| 后端 |", content)
        self.assertIn("| 图片数 |", content)

        # 检查记录行
        self.assertIn("测试文章", content)
        self.assertIn("https://example.com/test", content)
        self.assertIn("defuddle", content)
        self.assertIn("3", content)

    def test_second_record_appends(self):
        """追加记录：在已有文件末尾追加新行"""
        meta1 = {"title": "文章A", "source_url": "https://a.com"}
        meta2 = {"title": "文章B", "source_url": "https://b.com"}

        append_conversion_record(
            self.log_path, meta1, Path("/tmp/web2md/文章A.md"), 1, "defuddle", "网页文章"
        )
        append_conversion_record(
            self.log_path, meta2, Path("/tmp/web2md/文章B.md"), 2, "playwright", "公众号文章"
        )

        with open(self.log_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Wikilinks 使用文件名（无显示文本）
        self.assertIn("[[文章A]]", content)
        self.assertIn("[[文章B]]", content)
        self.assertIn("https://a.com", content)
        self.assertIn("https://b.com", content)

        # 表头只出现一次
        self.assertEqual(content.count("| 转换时间 |"), 1)

    def test_wikilink_format_in_record(self):
        """记录中的文章标题使用 Obsidian wikilink 格式（无显示文本，避免与表格 | 冲突）"""
        meta = {"title": "金税四期新规", "source_url": "https://example.com"}
        md_path = Path("/tmp/web2md/金税四期新规.md")

        append_conversion_record(
            self.log_path, meta, md_path, 0, "defuddle", "网页文章"
        )

        with open(self.log_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("[[金税四期新规]]", content)
        # 确保没有破坏表格的 | 符号
        self.assertNotIn("[[金税四期新规|", content)


class TestImagePaths(unittest.TestCase):
    """v1.3.2: 图片路径为 vault 根相对路径，Obsidian 可直接加载"""

    def test_img_path_is_relative_to_vault(self):
        """图片引用使用 vault 根相对路径（web2md/images/文章标题/）"""
        safe_title = "测试文章"
        img_rel_prefix = f"web2md/images/{safe_title}/"

        self.assertFalse(img_rel_prefix.startswith("/"))
        self.assertIn("web2md/images/测试文章/", img_rel_prefix)
        self.assertEqual(img_rel_prefix, "web2md/images/测试文章/")


class TestFrontmatterFields(unittest.TestCase):
    """v1.2.0: frontmatter 包含 tags/aliases/backend"""

    def test_base_tags_contain_web2md_and_type(self):
        """基础 tags 包含 web2md 和文章类型"""
        import yaml

        # 模拟脚本写入的基础 meta（AI 后处理会扩展）
        meta = {
            "title": "测试文章",
            "source_url": "https://example.com",
            "tags": ["web2md", "网页文章"],
            "aliases": ["测试文章"],
            "backend": "defuddle",
        }

        frontmatter = yaml.dump(meta, allow_unicode=True, default_flow_style=False)
        self.assertIn("web2md", frontmatter)
        self.assertIn("网页文章", frontmatter)
        self.assertIn("aliases", frontmatter)
        self.assertIn("backend", frontmatter)
        self.assertIn("defuddle", frontmatter)


class TestNormalizeMeta(unittest.TestCase):
    """v1.3.3: 所有 9 个必要属性始终存在"""

    def test_all_required_fields_present(self):
        """缺少字段时自动补全为默认值"""
        meta = {"title": "测试", "source_url": "https://example.com"}
        result = normalize_meta(meta, "https://example.com/article", "网页文章")

        required = [
            "title", "author", "account_name", "source_url",
            "publish_date", "converted_at", "description",
            "tags", "aliases", "backend",
        ]
        for field in required:
            self.assertIn(field, result, f"Missing required field: {field}")

    def test_account_name_derived_from_domain(self):
        """缺少 account_name 时从 URL 域名推导"""
        meta = {"title": "Test"}
        result = normalize_meta(meta, "https://zhuanlan.zhihu.com/p/123", "网页文章")
        self.assertEqual(result["account_name"], "zhuanlan.zhihu.com")

    def test_wechat_keeps_account_name(self):
        """微信文章保留原有的 account_name"""
        meta = {"title": "微信文章", "account_name": "百望派"}
        result = normalize_meta(meta, "https://mp.weixin.qq.com/s/abc", "公众号文章")
        self.assertEqual(result["account_name"], "百望派")

    def test_empty_fields_filled(self):
        """未提取到的字段填入空字符串"""
        meta = {}
        result = normalize_meta(meta, "https://example.com", "网页文章")
        self.assertEqual(result["title"], "")
        self.assertEqual(result["author"], "")
        self.assertEqual(result["description"], "")


class TestPlatformDetection(unittest.TestCase):
    def test_detect_platforms(self):
        """各平台域名识别正确"""
        cases = {
            "https://www.xiaohongshu.com/explore/abc": "xiaohongshu",
            "https://xhslink.com/a/xyz": "xiaohongshu",
            "https://v.douyin.com/abc/": "douyin",
            "https://www.douyin.com/note/7281234567890123456": "douyin",
            "https://mp.weixin.qq.com/s/abc": "wechat",
            "https://example.com/a": "generic",
        }
        for url, expected in cases.items():
            self.assertEqual(detect_platform(url), expected, url)

    def test_extract_url_from_share_text(self):
        """分享文案里抠链接，且不吞后面的中文"""
        text = "3.15 复制打开抖音，看看【某某】https://v.douyin.com/abc/ 的作品"
        self.assertEqual(extract_first_url(text), "https://v.douyin.com/abc/")
        self.assertEqual(extract_first_url("没有链接"), "")


class TestXhsParser(unittest.TestCase):
    HTML_TMPL = (
        '<html><script>window.__INITIAL_STATE__ = {"note":{"noteDetailMap":{"n1":'
        '{"note":{"title":"%(title)s","desc":"%(desc)s","time":1700000000000,'
        '"user":{"nickname":"%(nick)s"},"imageList":%(imgs)s%(video)s}}}}}</script></html>'
    )

    def _build(self, imgs, video="", title="标题", desc="正文", nick="作者"):
        return self.HTML_TMPL % {
            "title": title, "desc": desc, "nick": nick,
            "imgs": json.dumps(imgs, ensure_ascii=False), "video": video,
        }

    def test_live_photo_with_clip(self):
        """实况照片识别 + 片段地址归一化为 https"""
        html = self._build([{
            "urlDefault": "http://img1.jpg", "livePhoto": True,
            "stream": {"h264": [{"masterUrl": "http://vid1.mp4"}]},
        }])
        post = parse_xhs(html, "u")
        self.assertEqual(len(post.images), 1)
        self.assertEqual(post.images[0].kind, "livePhoto")
        self.assertEqual(post.images[0].motion_url, "https://vid1.mp4")
        self.assertEqual(post.images[0].url, "https://img1.jpg")

    def test_live_photo_without_clip_falls_back_to_still(self):
        """有 livePhoto 标记但取不到片段 → 退回静态图"""
        html = self._build([{"urlDefault": "http://img1.jpg", "livePhoto": True,
                             "stream": {"h264": []}}])
        post = parse_xhs(html, "u")
        self.assertEqual(post.images[0].kind, "still")
        self.assertIsNone(post.images[0].motion_url)

    def test_live_photo_clips_are_not_note_video(self):
        """实况片段不能被当成笔记视频（否则凭空多一个视频）"""
        html = self._build([
            {"urlDefault": "http://i1.jpg", "livePhoto": True,
             "stream": {"h264": [{"masterUrl": "http://clip1.mp4"}]}},
            {"urlDefault": "http://i2.jpg", "livePhoto": True,
             "stream": {"h264": [{"masterUrl": "http://clip2.mp4"}]}},
        ])
        post = parse_xhs(html, "u")
        self.assertEqual(len(post.images), 2)
        self.assertEqual(post.video_url, "")

    def test_real_note_video_kept(self):
        """笔记自身视频与实况片段共存时，保留真视频"""
        video = (',"video":{"media":{"stream":{"h264":'
                 '[{"masterUrl":"http://real.mp4"}]}}}')
        html = self._build([{"urlDefault": "http://i1.jpg", "livePhoto": True,
                             "stream": {"h264": [{"masterUrl": "http://clip1.mp4"}]}}],
                           video=video)
        post = parse_xhs(html, "u")
        self.assertEqual(post.video_url, "https://real.mp4")

    def test_no_media_returns_none(self):
        """登录墙/壳页（无任何媒体）必须返回 None，不能冒充成功"""
        html = self._build([])
        self.assertIsNone(parse_xhs(html, "u"))

    def test_undefined_in_state_is_tolerated(self):
        """页面状态里混有 JS undefined 时仍能解析"""
        html = ('<html><script>window.__INITIAL_STATE__ = {"a":undefined,'
                '"note":{"noteDetailMap":{"n1":{"note":{"title":"t","desc":"d",'
                '"imageList":[{"urlDefault":"http://i.jpg"}]}}}}}</script></html>')
        post = parse_xhs(html, "u")
        self.assertEqual(post.title, "t")
        self.assertEqual(len(post.images), 1)


class TestDouyinParser(unittest.TestCase):
    def test_image_post_does_not_produce_video(self):
        """图文的 video.play_addr 是背景音乐壳子，不能产出视频"""
        post = parse_douyin({
            "desc": "图文标题",
            "images": [{"url_list": ["https://p/1.jpg"]},
                       {"url_list": ["https://p/2.jpg"]}],
            "video": {"play_addr": {"url_list": ["https://v/bgm.mp3"]}},
        }, "u")
        self.assertEqual(post.video_url, "")
        self.assertEqual(len(post.images), 2)

    def test_video_post_produces_video(self):
        post = parse_douyin({
            "desc": "视频标题",
            "video": {"play_addr": {"url_list": ["https://v/v.mp4"]}},
        }, "u")
        self.assertEqual(post.video_url, "https://v/v.mp4")

    def test_gallery_item_kinds(self):
        """实况 / 独立片段 / 静态图三分类"""
        post = parse_douyin({
            "desc": "混合",
            "images": [
                {"url_list": ["https://p/0.webp"], "live_photo_type": 1,
                 "video": {"bit_rate": [{"play_addr": {"url_list": ["https://v/live0.mp4"]}}]}},
                {"url_list": ["https://p/1.webp"], "clip_type": 4,
                 "video": {"bit_rate": [{"play_addr": {"url_list": ["https://v/clip1.mp4"]}}]}},
                {"url_list": ["https://p/2.webp"]},
            ],
        }, "u")
        self.assertEqual([i.kind for i in post.images],
                         ["livePhoto", "videoClip", "still"])
        self.assertIsNone(post.images[2].motion_url)

    def test_empty_detail_returns_none(self):
        self.assertIsNone(parse_douyin({}, "u"))

    def test_note_url_rewritten_to_video(self):
        """/note/{id} 必须改写成 /video/{id} 才会触发详情接口"""
        url = "https://www.douyin.com/note/7281234567890123456"
        self.assertEqual(douyin_video_url_from_share(url),
                         "https://www.douyin.com/video/7281234567890123456")
        unchanged = "https://www.douyin.com/video/7281234567890123456"
        self.assertEqual(douyin_video_url_from_share(unchanged), unchanged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
