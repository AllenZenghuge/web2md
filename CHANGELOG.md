# web2md 版本记录

> 网页/公众号文章转 Markdown 技能 — 版本更新历史

| 时间 | 版本 | 修订内容 |
|------|------|---------|
| 2026-06-02 11:00 | v1.0.0 | 初始版本：基于 Playwright + headless Chromium，支持微信公众号 `#js_content` DOM 提取、通用网页 readability 算法、图片下载到本地、YAML frontmatter 元数据 |
| 2026-06-04 11:56 | v1.0.1 | 修复输出目录结构：所有内容统一归入 `web2md/` 父文件夹，按类型分子目录（`公众号文章/`、`网页文章/`），图片归入 `web2md/images/` |
| 2026-06-22 15:40 | v1.1.0 | **新增 defuddle 后端自动路由**：普通网页默认使用 defuddle CLI 提取内容（更快更轻），微信公众号继续走 Playwright。新增 `--backend` 参数（auto/playwright/defuddle）支持手动指定后端 |
| 2026-06-22 15:55 | v1.1.1 | **defuddle 路径增加图片下载**：解析 Markdown 输出中的 `![alt](url)` 和 `<img>` 标签，下载图片到本地并重写引用路径，与 Playwright 路径保持一致的本地化体验 |
| 2026-06-22 16:10 | v1.1.2 | **抽取共享下载核心**：`_download_single_image()` 统一两个后端的图片下载逻辑（headers、timeout、Content-Type 检测、ext 映射、chunk write），消除代码重复 |
| 2026-06-23 21:57 | v1.2.0 | **Obsidian 属性规范**：frontmatter 新增 `tags`/`aliases`/`backend` 字段；新增 `--conversion-log` 参数 + `append_conversion_record()` 函数，自动维护 vault 内转换记录 |
| 2026-06-23 22:26 | v1.3.0 | **输出扁平化**：取消类型子目录，`.md` 直接存 `web2md/`；新增 AI 后处理流程（6 tags + 2 aliases） |
| 2026-06-23 22:49 | v1.3.1 | **aliases 至少 2 个**：参考六大技能模式，提炼版标题 + 极简关键词别名；新增 `test_web2md.py` TDD 测试 |
| 2026-06-23 23:19 | v1.3.2 | **图片路径修正**：改为 vault 根相对路径 `web2md/images/`，Obsidian 可直接渲染 |
| 2026-06-23 23:35 | v1.3.3 | **必要属性强制存在**：新增 `normalize_meta()` + `META_DEFAULTS`，9 个属性缺失自动补空值/域名推导；TDD 扩展至 15 用例 |
| 2026-06-24 09:42 | v1.3.4 | **wikilink 表格兼容**：转换记录 wikilink 从 `[[文件名\|显示文本]]` 改为 `[[文件名]]`，修复表格中 `\|` 冲突 |
| 2026-06-24 10:42 | v1.3.5 | **description 属性 + 全局 6 属性铁律**：frontmatter 新增 `description`；全局规则从 5→6 属性；planning-with-files 同步更新 |
| 2026-06-24 15:01 | v1.3.6 | **YAML 写入校验**：`normalize_meta()` 增加 aliases/tags 非空保护；SKILL.md 新增 AI 后处理校验步骤（引号冲突、重复 key、空值检查）；README 输出结构同步 |
| 2026-11-16 17:40 | v1.4.0 | **小红书 / 抖音支持**：新增 `platforms.py`（纯解析）、`social.py`（浏览器抓取）、`asr.py`（DashScope 转写）、`ocr.py` + `ocr.swift`（macOS Vision 图片文字识别）；自动识别平台并抓取图文/视频；抖音短链 `/note/` 自动改写 `/video/` 触发详情接口；图集按实况/片段/静态图三分类；TDD 扩展至 28 用例 |
| 2026-11-16 18:05 | v1.4.1 | **小红书视频与 CDN 修复**：支持 `xhslink.cn` 短链；移动端分享页视频从全文 `masterUrl` 回退提取；图片下载按平台传 UA 与 Referer（小红书 `!h5_` 变体需移动 UA + 主域 Referer）；彻底移除千问摘要，摘要改由对话模型写入 |
| 2026-11-17 | — | **回退文件名下划线方案**：曾把标题空格统一改为下划线，按龙哥要求回退；文件名仍保留空格，图片路径继续用 `%20` 编码 |
| 2026-11-17 | v1.5.0 | **图片说明与摘要改由模型生成**：移除 macOS Vision OCR，脚本在每张图下留占位由对话模型读图补写；抖音 BGM 写入 frontmatter `bgm`；明确摘要按内容类型（做饭/感悟/攻略）组织；抖音短链先 HTTP 解析真实地址 |
| 2026-11-17 | v1.5.1 | **正文细节规则修正**：内容类型规则约束的是「正文主要内容」（做饭要材料用量与步骤、感悟要背景与原文、攻略要路线名称与心得、其他类型同样保留细节），摘要另由模型概括，不再把两者混为一谈 |

---

## 架构演进

```
v1.0.x                              v1.1.x
─────────                           ─────────
  用户调用                             用户调用
     │                                    │
     ▼                                    ▼
  web2md.py                          web2md.py
     │                                    │
     ▼                              ┌─ 公众号? ──┐
  Playwright                        ▼            ▼
  (唯一后端)                    Playwright    defuddle
     │                            │            │
     ▼                            ▼            ▼
  HTML 清洗                     DOM 提取     CLI 提取
  BS4 <img> 解析                → Markdown   → Markdown
     │                            │            │
     └──────────┬─────────────────┘            │
                │               BS4 <img>    正则 MD
                ▼                解析        图片解析
     _download_single_image()     │            │
     (共享下载核心)                └─────┬──────┘
                                        ▼
                               _download_single_image()
                               (两个路径共用同一核心)
```

## 后端对比

| 维度 | Playwright | defuddle |
|------|-----------|----------|
| 启动速度 | 慢（~2s 启动浏览器） | 快（直接 CLI） |
| 内存占用 | 高（Chromium ~200MB） | 低（Node.js ~30MB） |
| 微信适配 | ✅ `#js_content` 精确提取 | ❌ 不支持 |
| 懒加载图片 | ✅ 滚动触发 | ❌ 依赖服务端渲染 |
| 去广告 | ⚠️ readability 算法 | ✅ 内置 |
| Token 消耗 | 高（全 HTML） | 低（仅正文） |
| 图片下载 | BS4 HTML `<img>` 解析 | Markdown `![alt](url)` 正则解析 |
| 下载核心 | `_download_single_image()` | `_download_single_image()`（共用） |
| 适用场景 | 公众号、需本地图片 | 博客、文档、新闻 |

---

## v1.2.x–v1.3.x 管道增强

```
                    _download_single_image()
                    (共享下载核心)
                           │
                           ▼
                    normalize_meta()
                    (9 属性补全：缺失→空值，
                     account_name→域名推导，
                     aliases/tags 非空保护)
                           │
                           ▼
                    .md 输出 (tags/aliases 占位)
                           │
                           ▼
                    AI 后处理
                    (4 个 AI tag + 2 个精炼 aliases)
                           │
                           ▼
                    YAML 校验（v1.3.6 新增）
                    (引号冲突 / 重复 key / 空值检查)
                           │
                           ▼
                    append_conversion_record()
                    (追加到 vault 转换记录)
                           │
                           ▼
                    web2md/
                    ├── 文章.md
                    ├── 转换记录.md
                    └── images/文章/
```
