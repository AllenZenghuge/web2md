"""DashScope 语音转写。

转写走 Paraformer 录音文件识别（异步三步：提交 → 轮询 → 下载结果）。
不使用任何千问（Qwen）模型：摘要由对话模型生成后写回 md。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

ASR_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
TASK_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
DEFAULT_ASR_MODEL = "paraformer-v2"


class AsrError(RuntimeError):
    """转写失败，message 里带平台返回的错误码，便于定位。"""


@dataclass
class Transcript:
    text: str = ""
    sentences: list[dict] = field(default_factory=list)
    source_url: str = ""
    model: str = DEFAULT_ASR_MODEL

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


def _api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise AsrError("未设置 DASHSCOPE_API_KEY，无法转写")
    return key


def transcribe(media_url: str, *, model: str = DEFAULT_ASR_MODEL,
               language: str = "zh", timeout: int = 900,
               poll_interval: float = 3.0) -> Transcript:
    """把公网可访问的音/视频 URL 转成逐字稿。

    Paraformer 只接受 URL，不接受本地文件；mp4/mov/mkv 等视频格式由服务端
    自动抽音轨，本地不需要 ffmpeg。
    """
    key = _api_key()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    submit_body = {
        "model": model,
        "input": {"file_urls": [media_url]},
        "parameters": {"language_hints": [language]},
    }
    try:
        resp = requests.post(ASR_ENDPOINT, headers={**headers, "X-DashScope-Async": "enable"},
                             json=submit_body, timeout=60)
    except requests.RequestException as e:
        raise AsrError(f"转写任务提交失败：{e}") from e

    if resp.status_code != 200:
        raise AsrError(f"转写任务提交失败（HTTP {resp.status_code}）：{resp.text[:300]}")

    task_id = (resp.json().get("output") or {}).get("task_id")
    if not task_id:
        raise AsrError(f"转写任务未返回 task_id：{resp.text[:300]}")

    result = _poll(task_id, headers, timeout=timeout, interval=poll_interval)
    return _collect(result, model=model)


def _poll(task_id: str, headers: dict, *, timeout: int, interval: float) -> dict:
    deadline = time.time() + timeout
    url = TASK_ENDPOINT.format(task_id=task_id)
    while time.time() < deadline:
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as e:
            raise AsrError(f"转写状态查询失败：{e}") from e
        if resp.status_code != 200:
            raise AsrError(f"转写状态查询失败（HTTP {resp.status_code}）")

        output = resp.json().get("output") or {}
        status = output.get("task_status")
        if status == "SUCCEEDED":
            return output
        if status in ("FAILED", "CANCELED"):
            code = output.get("code") or (output.get("results") or [{}])[0].get("code") or status
            msg = output.get("message", "")
            raise AsrError(f"转写失败（{code}）{msg}".strip())
        time.sleep(interval)
    raise AsrError(f"转写超时（{timeout} 秒），task_id={task_id}")


def _collect(output: dict, *, model: str) -> Transcript:
    """从任务结果里取 transcription_url 并下载逐字稿。"""
    items = output.get("results") or []
    for item in items:
        payload = item.get("output") or item
        sub_status = payload.get("subtask_status") or item.get("subtask_status")
        if sub_status == "FAILED":
            code = item.get("code") or payload.get("code") or "SUBTASK_FAILED"
            raise AsrError(f"转写失败（{code}）：文件下载或解码失败")

        url = payload.get("transcription_url") or item.get("transcription_url")
        if not url:
            continue
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise AsrError(f"逐字稿结果下载失败：{e}") from e
        return _parse_transcription(data, model=model)

    raise AsrError("转写任务成功但未返回 transcription_url")


def _parse_transcription(data: dict, *, model: str) -> Transcript:
    transcripts = data.get("transcripts") or []
    texts, sentences = [], []
    for tr in transcripts:
        if tr.get("text"):
            texts.append(tr["text"].strip())
        for s in tr.get("sentences") or []:
            begin = s.get("begin_time")
            sentences.append({
                "begin": _fmt_ms(begin),
                "text": (s.get("text") or "").strip(),
            })
    return Transcript(
        text="\n".join(t for t in texts if t),
        sentences=[s for s in sentences if s["text"]],
        source_url=data.get("file_url", ""),
        model=model,
    )


def _fmt_ms(ms: Any) -> str:
    try:
        total = int(ms) // 1000
    except (TypeError, ValueError):
        return ""
    return f"{total // 60:02d}:{total % 60:02d}"


def render_transcript(t: Transcript) -> str:
    """逐字稿成 Markdown。有分句带时间戳，没有就整段落。"""
    if t.sentences:
        return "\n\n".join(f"**[{s['begin']}]** {s['text']}" for s in t.sentences)
    return t.text
