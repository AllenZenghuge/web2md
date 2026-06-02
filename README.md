# web2md

Convert WeChat official account articles (微信公众号) and web pages to clean Markdown files. Downloads all images locally, preserves formatting, and adds YAML frontmatter.

## Quick Start

```bash
git clone https://github.com/<your-username>/web2md.git ~/.claude/skills/web2md/
pip install -r ~/.claude/skills/web2md/scripts/requirements.txt
```

**Requirements:** Python 3.9+, Google Chrome

## How It Works

1. Opens the page in headless Chrome via Playwright
2. Scrolls to trigger lazy-loaded images
3. Extracts metadata (title, author, date, description)
4. Cleans inline styles, ads, and non-content elements
5. Downloads all images to a local `images/` directory (deduplicated)
6. Converts HTML to Markdown preserving headings, code blocks, quotes, lists, tables
7. Writes `.md` file with YAML frontmatter

### WeChat Strategy

Precisely extracts `#js_content`, swaps `data-src` → `src` for lazy images, removes reward areas and comment sections.

### General Web Strategy

Uses Mozilla's readability algorithm to extract main content, strips navigation and sidebars.

## Output Structure

```
./
├── 公众号文章/
│   ├── article-a.md
│   └── article-b.md
├── 网页文章/
│   └── page-c.md
└── images/
    └── article-a/
        ├── img_001.jpg
        └── img_002.png
```

## CLI Usage

```bash
python3 scripts/web2md.py <url> [--output-dir DIR] [--no-images] [--timeout SEC]
```

## Claude Code Usage

The skill triggers on natural language:
- "转一下这个公众号" + URL
- "把这个网页保存为 markdown" + URL
- "convert this article" + URL

## License

MIT
