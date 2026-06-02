---
name: web2md
description: Convert web pages and WeChat official account articles to Markdown files with local images and YAML frontmatter
allowed-tools:
  - Bash
  - Read
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

## 使用流程

1. 从用户消息中提取 URL
2. 识别类型 — `mp.weixin.qq.com` = 公众号文章，其余 = 通用网页
3. 运行转换：

```bash
python3 ~/.claude/skills/web2md/scripts/web2md.py "<url>" --output-dir "<当前工作目录>"
```

4. 汇报结果：输出路径、标题、作者、日期、图片数量

## CLI 参数

```
web2md.py <url> [--output-dir DIR] [--no-images] [--timeout SEC]
```

| 参数 | 说明 |
|------|------|
| `url` | 必填，文章/网页链接 |
| `--output-dir DIR` | 输出目录，默认当前目录 |
| `--no-images` | 跳过图片下载，保留原始 URL |
| `--timeout SEC` | 页面加载超时，默认 30 秒 |

## 功能

1. 无头 Chromium 打开页面
2. 滚动触发懒加载图片（`data-src` → `src`）
3. 提取元数据：标题、作者、日期、描述、封面图
4. 清洗内联样式、广告、非内容元素
5. 微信：精确抓取 `#js_content`；通用：readability 算法提取正文
6. 下载所有图片到 `images/` 子目录（按 URL 去重）
7. HTML → Markdown（保留标题层级、代码块、引用、列表、链接、表格）
8. 输出带 YAML frontmatter 的 `.md` 文件

## 输出结构

`.md` 文件直接存放在类型目录下，`images/` 与类型目录平级，按文章标题关联：

```
./
├── 公众号文章/
│   ├── 文章A.md
│   └── 文章B.md
├── 网页文章/
│   └── 文章C.md
└── images/
    ├── 文章A/
    │   ├── img_001.jpg
    │   └── img_002.png
    ├── 文章B/
    │   └── img_001.jpg
    └── 文章C/
        └── img_001.jpg
```

Markdown 中图片引用：`../images/文章标题/img_001.jpg`

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
