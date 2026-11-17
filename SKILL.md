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
| `xiaohongshu.com` / `xhslink.com` / `xhslink.cn` | **Playwright** | 移动 UA + 解析分享页状态取图集/视频 |
| `douyin.com` / `iesdouyin.com` | **Playwright** | 需桌面 UA + 拦截 `aweme/detail` 接口 |
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

5. **YAML 校验（必须）** — AI 后处理完成后，逐项检查：
   - ❌ **重复 key**：同一个 key 出现两次（如 `description` 同时有脚本生成值和 AI 后处理值）→ 删除多余的，只保留一个
   - ❌ **aliases 空值**：`aliases:` 下面必须有至少一个列表项，不能空行
   - ❌ **引号冲突**：title 值含弯引号 `"` `"` 时，外层用单引号 `'...'` 包裹，不能用直双引号 `"..."`，否则 YAML 解析失败
   - ✅ 三项检查通过后，才汇报转换成功

6. 汇报结果：输出路径、标题、后端、图片数量、生成的 4 个 tag、2 个 aliases、description


## CLI 参数

```
web2md.py <url> [--output-dir DIR] [--no-images] [--timeout SEC] [--backend auto|playwright|defuddle] [--conversion-log PATH]
```

| 参数 | 说明 |
|------|------|
| `url` | 必填，文章/网页链接；小红书、抖音可直接贴整段分享文案 |
| `--output-dir DIR` | 输出目录，默认当前目录 |
| `--no-images` | 跳过图片下载，保留原始 URL |
| `--timeout SEC` | 页面加载超时，默认 30 秒 |
| `--backend` | 后端选择：`auto`（默认自动路由）、`playwright`（强制浏览器）、`defuddle`（强制轻量 CLI） |
| `--conversion-log PATH` | 转换记录文件路径（追加写入）。固定指向 vault 中的 `web2md/转换记录.md` |
| `--save-video` | 小红书/抖音：把视频与实况片段一起下载到本地 |
| `--no-transcript` | 小红书/抖音：跳过语音转写（不生成逐字稿） |
| `--asr-model` | DashScope 转写模型，默认 `paraformer-v2` |
| `--language` | 转写语言提示，默认 `zh` |

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

### 小红书 / 抖音 后端
1. **小红书**：**必须用移动端 UA**（桌面 UA 会被重定向到登录页）。
   优先直连 HTTP 取 HTML，失败再用浏览器。两种页面结构都要兼容：
   - 桌面/状态对象：`note.noteDetailMap[].note`，图片字段 `urlDefault`，作者 `user.nickname`
   - 移动分享页：字段平铺，图片字段 `url`，作者 `user.nickName`（大写 N）
   注意页面状态里混有 JS 的 `undefined`，`json.loads` 前要替换成 `null`。
2. **抖音**：必须用桌面 Chrome UA（移动 UA 只会拿到「打开 App」引导页）。
   注入 JS hook `fetch` / `XMLHttpRequest` 拦截 `aweme/detail` 响应；
   短链跳转后若落在 `/note/{id}`，要按作品 ID 改写成 `/video/{id}` 才会触发详情接口。
   图集项按 `live_photo_type == 1` → 实况，`video.bit_rate[0].play_addr` → 独立片段，
   其余为静态图；**图集非空时绝不产出顶层视频**（`video.play_addr` 是背景音乐壳子）。
3. 下载图片/视频
   - **小红书 CDN**：`!h5_` 变体只认**移动 UA**，且 Referer 必须是
     `https://www.xiaohongshu.com/`（传短链域名 `xhslink.cn` 会 403）
   - **抖音 CDN**：Referer 必须是 `https://www.douyin.com/`，否则 403
4. **图片说明（必须补写）**：脚本只在每张图下留 `> **图 N**：（图片说明待补）` 占位。
   转换完必须由**对话模型读取本地图片**，把占位替换成该图的内容说明
   （不是 OCR 文字，是理解图片后在讲什么）。脚本不做本地 OCR、不调视觉模型。
   **图注标题必须用主题词**（如「睡眠系统」「装备总览」「急救包」），
   不要保留「图 1 / 图 2」这种编号。
   **图片文件名也要同步改成主题词**：`img_001.webp` → `睡眠系统.webp`，
   alt 文本也改成主题词（`![睡眠系统](images/…/睡眠系统.webp)`）。
   重名时加后缀 `-2`。重命名后必须重新校验所有图片引用都能找到文件。
5. 有视频时用 DashScope Paraformer 转写逐字稿（`--no-transcript` 关闭）。
   抖音的 BGM 写进 frontmatter 的 `bgm` 字段。

### 正文与摘要规则（对话模型执行，脚本不调 LLM）

转完必须补两部分，**不能只留原始文案和逐字稿**：

**1. 摘要**：用模型概括这条内容讲什么，一段话即可，放在 `## 摘要` 下。

**2. 正文主要内容**：按内容类型组织，**保留细节而不是套框架**：

| 内容类型 | 正文必须保留的细节 |
|----------|--------------------|
| **做饭 / 教程类** | 菜名或成品、材料清单（含用量）、分步骤做法（火候、时间、顺序） |
| **感悟 / 道理类** | 触发背景（怎么引起的话题）、核心观点、**感悟原文**（原话摘录，不改写成书面语） |
| **攻略 / 经验类** | 路线或流程、具体名称（地点、装备、App、工具）、关键参数（重量/价格/时长）、作者心得与避坑 |
| **其他类型** | 同样保留该类内容的关键细节（清单、参数、步骤、对比项、结论），不要强行套「主题/主要内容/关键结论」 |

原则：信息要能直接拿来用——照着清单能买齐、照着步骤能做出来、照着路线能走。缺细节就是没做完。

**3. 矛盾必须写进正文**：整理过程中发现原文内部或图文不一致（数字对不上、
前后说法冲突、图注与正文不符），不能只在对话里提一句，要在正文对应位置用
callout 写清楚：矛盾点、两边数值、判断依据、以哪个为准。

> [!warning] 登录态
> 小红书对未登录请求会重定向到 `/login`，此时脚本会明确报错并退出码 6。
> 目前不支持 Cookie 注入，需要登录态的笔记无法抓取。

## 前置依赖

- **Playwright 后端**：Python 3.9+、Google Chrome、Playwright（`pip install playwright`）
- **defuddle 后端**：Node.js、`npm install -g defuddle`

## 输出结构

`.md` 文件直接存放在 `web2md/` 下，不再按类型分子目录：

> [!important] 文件名规则（仅非微信链接）
> 公众号文章用原标题即可；**小红书 / 抖音 / 普通网页要重新提炼一个短标题**，
> 用 `--filename` 传入：
> - 控制在 20 字以内，能一眼看出是什么内容
> - 不要全角/半角标点（`。：（）！？、` 等），可以有引号、下划线、短横线
> - 例：`国庆亚丁装备清单。基重（不含水、食物和燃料）：8kg` → `亚丁五天四晚_8kg装备清单`
>
> 图片目录名跟随文件名，frontmatter 的 `title` 字段仍保留原标题。

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

Markdown 中图片引用使用**相对笔记的路径**：`images/文章标题/img_001.jpg`。
（笔记本身在 `web2md/` 下，写成 `web2md/images/...` 会被解析成 `web2md/web2md/images/...`，图片不显示。）

## 错误码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | URL 格式无效 |
| 2 | 页面加载超时 / 正文未找到 |
| 3 | Chrome/Playwright 不可用 |
| 4 | 网络不可达 |
| 5 | 输出目录写入失败 |
| 6 | 小红书/抖音未提取到任何内容（登录墙 / 已删除 / 结构变更） |

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
