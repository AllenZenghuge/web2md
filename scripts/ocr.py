"""本地图片文字提取（macOS Vision OCR）。

不依赖任何在线模型：用系统自带的 Vision 框架，中文识别质量好、离线、免费。
首次调用会把 Swift 源码编译成二进制并缓存，之后直接复用。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SWIFT_SRC = HERE / "ocr.swift"
BIN_PATH = HERE / ".ocr_bin"


def available() -> bool:
    """非 macOS 或缺 swift 时直接判定不可用。"""
    return sys.platform == "darwin" and shutil.which("swiftc") is not None


def _ensure_binary() -> Optional[Path]:
    """编译一次，源文件未变就复用。编译失败返回 None。"""
    if not SWIFT_SRC.exists():
        return None
    if BIN_PATH.exists() and BIN_PATH.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return BIN_PATH
    try:
        subprocess.run(
            ["swiftc", "-O", "-o", str(BIN_PATH), str(SWIFT_SRC)],
            check=True, capture_output=True, timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"WARNING: OCR 编译失败，跳过图片文字提取（{e}）", file=sys.stderr)
        return None
    return BIN_PATH


def ocr_images(paths: list[Path]) -> dict[str, str]:
    """返回 {图片文件名: 识别出的文字}。识别失败或为空的不出现在结果里。"""
    if not paths or not available():
        return {}
    binary = _ensure_binary()
    if binary is None:
        return {}

    try:
        result = subprocess.run(
            [str(binary)] + [str(p) for p in paths],
            capture_output=True, text=True, timeout=300,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"WARNING: OCR 执行失败（{e}）", file=sys.stderr)
        return {}

    try:
        rows = json.loads(result.stdout)
    except ValueError:
        print("WARNING: OCR 输出不是合法 JSON，跳过图片文字提取", file=sys.stderr)
        return {}

    out: dict[str, str] = {}
    for row in rows:
        name = Path(row.get("file", "")).name
        text = (row.get("text") or "").strip()
        if name and text:
            out[name] = text
    return out


def render_markdown(ocr_text: str) -> str:
    """把 OCR 文本块包成引用块，避免长图文字把正文结构冲散。"""
    lines = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]
    return "\n".join("> " + ln for ln in lines)
