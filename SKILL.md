---
name: web2md
description: Convert web pages and WeChat official account articles to Markdown files with local images and YAML frontmatter
allowed-tools:
  - Bash
  - Read
  - Edit
triggers:
  - 转公众号
  - 转网页
  - 保存文章
  - 文章转markdown
  - 网页转markdown
  - 下载这篇文章
  - convert article
  - save this page
  - 转markdown
---

# web2md — 网页/公众号文章转 Markdown

将微信公众号文章或任意网页转换为干净的 Markdown 文件。下载所有图片到本地，添加 YAML frontmatter 元数据。

## 触发

用户消息包含以下任一关键词时触发：

- 中文：转公众号 / 转网页 / 保存文章 / 文章转 markdown / 网页转 markdown / 下载这篇文章 / 转 markdown
- 英文：convert article / save this page / save as markdown

**未提供 URL 时反问用户索要链接。**

## 后端自动路由

web2md 支持两个后端，**根据 URL 类型自动选择**，无需用户指定：

| URL 类型 | 后端 | 原因 |
|----------|------|------|
| `mp.weixin.qq.com` | **Playwright** | 需要 `#js_content` DOM 提取 + 滚动懒加载 |
| `.md` 结尾的 URL | **Playwright** | 原始 Markdown 文件无需清洗 |
| 其他所有网页 | **defuddle** | 更快、更轻、token 更少 |

**手动覆盖：** 需要强制指定后端时，使用 `--backend` 参数：
- `--backend playwright`：强制使用浏览器
- `--backend defuddle`：强制使用 defuddle（仅普通网页生效）
- `--backend auto`：默认，自动路由

> [!tip] defuddle vs Playwright
> defuddle 速度更快、更省资源。两个后端均下载图片到本地。需要 WeChat 页面懒加载图片时用 Playwright。

## 使用流程

1. 从用户消息中提取 URL
2. 运行转换（后端自动选择，**始终带上转换记录路径**）：

```bash
python3 ~/.claude/skills/web2md/scripts/web2md.py "<url>" \
  --output-dir "<当前工作目录>" \
  --conversion-log "/Users/allenzeng/Library/Mobile Documents/iCloud~md~obsidian/Documents/龙龙的黑曜石/web2md/转换记录.md"
```

3. 如需强制 Playwright：

```bash
python3 ~/.claude/skills/web2md/scripts/web2md.py "<url>" \
  --output-dir "<当前工作目录>" \
  --backend playwright \
  --conversion-log "/Users/allenzeng/Library/Mobile Documents/iCloud~md~obsidian/Documents/龙龙的黑曜石/web2md/转换记录.md"
```

4. **AI 后处理（必须）** — 脚本输出的 `tags`、`aliases`、`description` 为基础值，需 AI 增强：
   - 读取转换后的 `.md` 文件内容
   - **分析文章主题，生成 4 个最相关的 tag**（精确、有区分度的关键词，用英文或中文视文章内容而定）
   - **精炼 aliases（至少 2 个）**：参考 [[Obsidian_六大技能完整指南]] 的 aliases 模式：
     - **别名 1**：标题的提炼版（≤20 字）
     - **别名 2**：极简关键词（2-8 字）
   - **生成 description**：AI 根据文章内容整理一段中文摘要描述（2-3 句话，概括核心内容）
   - 用 Edit 工具更新 frontmatter：`tags` + `aliases` + `description`

5. 汇报结果：输出路径、标题、后端、图片数量、生成的 4 个 tag、2 个 aliases、description


## CLI 参数

```
web2md.py <url> [--output-dir DIR] [--no-images] [--timeout SEC] [--backend auto|playwright|defuddle] [--conversion-log PATH]
```

| 参数 | 说明 |
|------|------|
| `url` | 必填，文章/网页链接 |
| `--output-dir DIR` | 输出目录，默认当前目录 |
| `--no-images` | 跳过图片下载，保留原始 URL |
| `--timeout SEC` | 页面加载超时，默认 30 秒 |
| `--backend` | 后端选择：`auto`（默认自动路由）、`playwright`（强制浏览器）、`defuddle`（强制轻量 CLI） |
| `--conversion-log PATH` | 转换记录文件路径（追加写入）。固定指向 vault 中的 `web2md/转换记录.md` |

## Frontmatter 字段

**每篇转换文章至少包含以下 10 个属性**（缺失字段自动补空值，`account_name` 无值时从域名推导）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | ✅ | 文章标题 |
| `author` | string | ✅ | 作者（微信可提取；其他源为空） |
| `account_name` | string | ✅ | 公众号名称（微信）/ 域名（网页） |
| `source_url` | string | ✅ | 原始 URL |
| `publish_date` | string | ✅ | 发布日期（取不到为空） |
| `converted_at` | string | ✅ | 转换时间（ISO 格式） |
| `description` | string | ✅ | 页面 meta 提取；**AI 后处理生成精炼中文摘要**（每次更新同步刷新） |
| `tags` | list | ✅ | 6 个 tag：`[web2md, 类型, ai_tag1...4]`（AI 后处理补全） |
| `aliases` | list | ✅ | 至少 2 个：提炼版标题 + 关键词别名（参考六大技能模式） |
| `backend` | string | ✅ | 转换后端（playwright / defuddle） |

> [!important] tags 和 aliases 的最终值由 AI 后处理生成
> 脚本写入的是占位基础值（`tags: [web2md, 类型]`、`aliases: [标题]`），AI 后处理步骤会读取文章内容并更新为精确值。

## 转换记录

每次转换成功后，自动追加一条记录到固定路径的 `转换记录.md`（位于 Obsidian vault 内）：

- **固定路径：** `龙龙的黑曜石/web2md/转换记录.md`
- **首次自动创建**（带 frontmatter + 表格头）
- **后续追加**（表格行）
- **记录内容：** 转换时间、文章标题（wikilink）、来源 URL、输出绝对路径、后端、图片数

## 功能

### Playwright 后端（公众号 / 强制指定时）
1. 无头 Chromium 打开页面
2. 滚动触发懒加载图片（`data-src` → `src`）
3. 提取元数据：标题、作者、日期、描述、封面图
4. 清洗内联样式、广告、非内容元素
5. 微信：精确抓取 `#js_content`；通用：readability 算法提取正文
6. 下载所有图片到 `images/` 子目录（按 URL 去重）
7. HTML → Markdown（保留标题层级、代码块、引用、列表、链接、表格）
8. 输出带 YAML frontmatter 的 `.md` 文件
9. AI 后处理：生成 4 个相关 tag + 精炼 alias，更新 frontmatter

### defuddle 后端（普通网页默认）
1. `defuddle parse <url> --json` 提取正文 + 元数据
2. 自动去广告、导航、侧栏等干扰内容
3. 解析 Markdown 中的图片 URL，下载到本地 `images/` 目录
4. 重写图片引用为本地相对路径（Obsidian 可直接渲染）
5. 输出带 YAML frontmatter 的 `.md` 文件
6. AI 后处理：生成 4 个相关 tag + 精炼 alias，更新 frontmatter

## 前置依赖

- **Playwright 后端**：Python 3.9+、Google Chrome、Playwright（`pip install playwright`）
- **defuddle 后端**：Node.js、`npm install -g defuddle`

## 输出结构

`.md` 文件直接存放在 `web2md/` 下，不再按类型分子目录：

```
./
└── web2md/
    ├── 文章A.md
    ├── 文章B.md
    ├── 文章C.md
    ├── 转换记录.md
    └── images/
        ├── 文章A/
        │   ├── img_001.jpg
        │   └── img_002.png
        ├── 文章B/
        │   └── img_001.jpg
        └── 文章C/
            └── img_001.jpg
```

Markdown 中图片引用使用 **vault 根相对路径**：`web2md/images/文章标题/img_001.jpg`

## 错误码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | URL 格式无效 |
| 2 | 页面加载超时 / 正文未找到 |
| 3 | Chrome/Playwright 不可用 |
| 4 | 网络不可达 |
| 5 | 输出目录写入失败 |

## 安装（首次使用）

**前置要求：** Python 3.9+、Google Chrome

```bash
# 1. 克隆或复制 skill 到 ~/.claude/skills/web2md/
git clone <repo-url> ~/.claude/skills/web2md/

# 2. 安装 Python 依赖
pip install -r ~/.claude/skills/web2md/scripts/requirements.txt

# 3. 验证
python3 ~/.claude/skills/web2md/scripts/web2md.py --help
```

Playwright 会自动检测系统 Chrome，无需额外下载 Chromium。
