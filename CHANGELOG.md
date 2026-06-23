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
                    (9 属性补全：缺失→空值，account_name→域名推导)
                           │
                           ▼
                    .md 输出 (tags/aliases 占位)
                           │
                           ▼
                    AI 后处理
                    (4 个 AI tag + 2 个精炼 aliases)
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
