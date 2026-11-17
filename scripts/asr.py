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

# 本地转写（可选依赖 faster-whisper）默认值
DEFAULT_LOCAL_MODEL = "small"
LOCAL_MODEL_ROOT = os.path.expanduser("~/.local/share/whisper-models/faster")


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


# --------------------------------------------------------------- 本地转写（可选）

_LOCAL_ASR_HINT = """未安装本地转写环境。装法：
  python3 -m venv ~/.local/share/whisper-env
  ~/.local/share/whisper-env/bin/pip install faster-whisper
装完用该环境的解释器跑本脚本。模型首次运行自动下载到 ~/.local/share/whisper-models/faster，
国内直连 huggingface.co 不通，脚本已默认走 hf-mirror.com。"""


class LocalAsrUnavailable(AsrError):
    """本地转写依赖缺失，message 里带安装命令。"""


def local_asr_available() -> tuple[bool, str]:
    """本地转写依赖是否就绪。返回 (可用, 说明)。"""
    for mod in ("av", "numpy", "faster_whisper"):
        try:
            __import__(mod)
        except ImportError as e:
            return False, f"{mod}: {e}"
    return True, ""


def _decode_16k_mono(media_path: str):
    """用 PyAV 把音/视频解成 16k 单声道 float32 numpy 数组。

    不走 faster-whisper 的 decode_audio()：它内部调 av.open(metadata_errors=...)，
    而 av 19 已移除该参数（faster-whisper 1.2.1 会 TypeError），降 av 又缺 py3.13 的
    wheel。自己解码反而稳，且 PyAV 的 wheel 自带解码库，不需要系统 ffmpeg。
    """
    import av
    import numpy as np

    chunks = []
    with av.open(media_path, mode="r") as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise AsrError("本地转写失败：文件里没有音轨")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(stream):
            for rf in resampler.resample(frame):
                chunks.append(rf.to_ndarray().reshape(-1))
        for rf in resampler.resample(None):  # flush
            chunks.append(rf.to_ndarray().reshape(-1))

    if not chunks:
        raise AsrError("本地转写失败：没有解出音频")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe_local(media_path: str, *, model_size: str = DEFAULT_LOCAL_MODEL,
                     language: str = "zh", model_root: str = "",
                     initial_prompt: str = "") -> Transcript:
    """本地 faster-whisper 转写。media_path 是本地音/视频文件。

    CPU + int8 跑，3 分半的音频在 2018 款 i5 上约 3.5 分钟，别指望实时。
    """
    ok, why = local_asr_available()
    if not ok:
        raise LocalAsrUnavailable(f"{_LOCAL_ASR_HINT}\n（缺：{why}）")

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    if not os.path.exists(media_path):
        raise AsrError(f"本地转写失败：文件不存在 {media_path}")

    from faster_whisper import WhisperModel

    audio = _decode_16k_mono(media_path)
    model = WhisperModel(model_size, device="cpu", compute_type="int8",
                         download_root=model_root or LOCAL_MODEL_ROOT)
    segments, _info = model.transcribe(
        audio, language=language, vad_filter=True, beam_size=5,
        initial_prompt=initial_prompt or None,
    )

    texts, sentences = [], []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        texts.append(text)
        sentences.append({"begin": _fmt_sec(seg.start), "text": text})

    return Transcript(
        text="\n".join(texts),
        sentences=sentences,
        source_url=media_path,
        model=f"faster-whisper/{model_size}",
    )


def _fmt_sec(sec: Any) -> str:
    try:
        total = int(float(sec))
    except (TypeError, ValueError):
        return ""
    return f"{total // 60:02d}:{total % 60:02d}"


def render_transcript(t: Transcript) -> str:
    """逐字稿成 Markdown。有分句带时间戳，没有就整段落。"""
    if t.sentences:
        return "\n\n".join(f"**[{s['begin']}]** {s['text']}" for s in t.sentences)
    return t.text
