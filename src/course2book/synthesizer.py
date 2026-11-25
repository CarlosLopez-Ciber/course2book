"""Síntesis por bloques en español (Ollama/Gemini/OpenAI-compat) con visión, thinking mode y checkpoints."""

from __future__ import annotations

import base64
import io
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from course2book.config import ExtractedFrame, LessonArtifacts, MediaInfo, SrtEntry, SynthesisConfig
from course2book.frames import slugify_name
from course2book.media import format_timestamp_hms
from course2book.obsidian import build_lesson_artifacts, cleanup_lesson_artifacts
from course2book.prompts import (
    SYSTEM_PROMPT_EXHAUSTIVE_ES,
    build_block_user_prompt,
    build_wrapup_user_prompt,
)
from course2book.transcriber import parse_srt

_THINK_TAG_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINKING_TAG_RE = re.compile(r"<thinking\b[^>]*>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HMS_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?(?!\d)")
_MS_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_FNAME_HMS_RE = re.compile(r"(?:(\d{1,2})h)?(\d{1,2})m(\d{1,2})s", re.IGNORECASE)
_FNAME_SEP_HMS_RE = re.compile(r"(\d{2})[_-](\d{2})[_-](\d{2})")
_FNAME_SEC_RE = re.compile(r"(?:^|[_-]|t)(\d{2,6})(?:\.(\d+))?s?(?:[_.-]|$)", re.IGNORECASE)
_TRANSCRIPT_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*|`)?\[?(\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?)\]?(?:\*\*|`)?\s*[:\-–—]?\s*(.+?)\s*$"
)
_OPENING_MARKER_RE = re.compile(
    r"<!--\s*OPENING_START\s*-->(.*?)<!--\s*OPENING_END\s*-->", re.DOTALL | re.IGNORECASE
)
_CLOSING_MARKER_RE = re.compile(
    r"<!--\s*CLOSING_START\s*-->(.*?)<!--\s*CLOSING_END\s*-->", re.DOTALL | re.IGNORECASE
)

_MAX_PNG_BYTES_BEFORE_JPEG = 2 * 1024 * 1024  # 2 MiB


@dataclass(slots=True)
class ContentBlock:
    """Bloque cronológico acotado en duración y número de capturas para síntesis exhaustiva."""

    index: int
    start: float
    end: float
    entries: list[SrtEntry]
    frames: list[ExtractedFrame]


def strip_thinking_tags(text: str) -> str:
    """Elimina cualquier bloque `<think>...</think>` residual y limpia saltos de línea sobrantes."""
    cleaned = _THINK_TAG_RE.sub("", text)
    cleaned = _THINKING_TAG_RE.sub("", cleaned)
    # Si quedara una etiqueta de cierre huérfana tras un bloque truncado al inicio, descarta lo anterior.
    lower = cleaned.lower()
    if "</think>" in lower and "<think" not in lower:
        idx = lower.rfind("</think>")
        cleaned = cleaned[idx + len("</think>") :]
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def encode_image_for_llm(image_path: Path, max_width: int = 1280) -> tuple[str, str]:
    """Codifica una captura en base64 en memoria, escalando solo si excede `max_width`.

    Nunca modifica el archivo PNG original sin pérdida en disco.
    """
    with Image.open(image_path) as img:
        working = img
        if max_width > 0 and img.width > max_width:
            ratio = max_width / float(img.width)
            new_height = max(1, int(round(img.height * ratio)))
            working = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        working.save(buf, format="PNG", optimize=True)
        raw_bytes = buf.getvalue()
        mime_type = "image/png"

        if len(raw_bytes) > _MAX_PNG_BYTES_BEFORE_JPEG:
            jpg_buf = io.BytesIO()
            rgb = working.convert("RGB") if working.mode != "RGB" else working
            rgb.save(jpg_buf, format="JPEG", quality=90, optimize=True)
            raw_bytes = jpg_buf.getvalue()
            mime_type = "image/jpeg"

    encoded = base64.b64encode(raw_bytes).decode("ascii")
    return encoded, mime_type


def split_into_blocks(
    entries: list[SrtEntry],
    frames: list[ExtractedFrame],
    block_minutes: float = 8.0,
    max_images_per_block: int = 6,
) -> list[ContentBlock]:
    """Divide la lección en bloques cronológicos acotando duración y número máximo de imágenes.

    Garantiza que:
    - Cada `SrtEntry` aparece exactamente una vez en orden cronológico.
    - Cada `ExtractedFrame` aparece exactamente una vez en orden cronológico.
    - Ningún bloque supera `max_images_per_block` imágenes.
    - Se respetan los límites de entrada SRT salvo en bloques puramente visuales excedentes.
    """
    sorted_entries = sorted(entries, key=lambda e: (e.start, e.end, e.index))
    sorted_frames = sorted(frames, key=lambda f: (f.timestamp, f.filename))

    if not sorted_entries and not sorted_frames:
        return []

    max_duration = max(1.0, float(block_minutes) * 60.0)
    max_images = max(1, int(max_images_per_block))

    raw_blocks: list[tuple[list[SrtEntry], list[ExtractedFrame]]] = []

    if not sorted_entries:
        # Solo capturas visuales sin transcripción
        cur_frames: list[ExtractedFrame] = []
        for frame in sorted_frames:
            if cur_frames and (
                len(cur_frames) >= max_images
                or (frame.timestamp - cur_frames[0].timestamp) > max_duration
            ):
                raw_blocks.append(([], cur_frames))
                cur_frames = []
            cur_frames.append(frame)
        if cur_frames:
            raw_blocks.append(([], cur_frames))
    else:
        cur_entries: list[SrtEntry] = []
        cur_frames = []
        frame_cursor = 0
        total_frames = len(sorted_frames)

        for entry in sorted_entries:
            # Frames aún no asignados que ocurren hasta el final de esta entrada
            next_cursor = frame_cursor
            while next_cursor < total_frames and sorted_frames[next_cursor].timestamp <= entry.end:
                next_cursor += 1
            candidate_frames = sorted_frames[frame_cursor:next_cursor]

            if cur_entries:
                block_start = cur_entries[0].start
                exceeds_duration = (entry.end - block_start) > max_duration
                exceeds_images = (len(cur_frames) + len(candidate_frames)) > max_images
                if exceeds_duration or exceeds_images:
                    raw_blocks.append((cur_entries, cur_frames))
                    cur_entries = []
                    cur_frames = []

            if len(candidate_frames) > max_images:
                # Si se acumularon más imágenes de las permitidas antes/durante una sola entrada,
                # emitimos bloques visuales intermedios para no violar nunca `max_images_per_block`.
                pre_frames = [f for f in candidate_frames if f.timestamp < entry.start]
                in_frames = [f for f in candidate_frames if f.timestamp >= entry.start]

                for start_i in range(0, len(pre_frames), max_images):
                    chunk = pre_frames[start_i : start_i + max_images]
                    if len(chunk) == max_images and (not cur_entries and not cur_frames):
                        raw_blocks.append(([], chunk))
                    else:
                        if cur_frames and len(cur_frames) + len(chunk) > max_images:
                            raw_blocks.append((cur_entries, cur_frames))
                            cur_entries = []
                            cur_frames = []
                        cur_frames.extend(chunk)

                if cur_frames and len(cur_frames) + min(len(in_frames), max_images) > max_images:
                    raw_blocks.append((cur_entries, cur_frames))
                    cur_entries = []
                    cur_frames = []

                cur_entries.append(entry)
                room = max(0, max_images - len(cur_frames))
                cur_frames.extend(in_frames[:room])
                overflow = in_frames[room:]
                if overflow:
                    raw_blocks.append((cur_entries, cur_frames))
                    cur_entries = []
                    cur_frames = []
                    for start_i in range(0, len(overflow), max_images):
                        raw_blocks.append(([], overflow[start_i : start_i + max_images]))
            else:
                cur_entries.append(entry)
                cur_frames.extend(candidate_frames)

            frame_cursor = next_cursor

        if cur_entries or cur_frames:
            raw_blocks.append((cur_entries, cur_frames))

        # Capturas posteriores a la última entrada SRT
        remaining_frames = sorted_frames[frame_cursor:]
        if remaining_frames:
            if raw_blocks:
                last_entries, last_frames = raw_blocks[-1]
                room = max_images - len(last_frames)
                last_start = (
                    last_entries[0].start
                    if last_entries
                    else (last_frames[0].timestamp if last_frames else remaining_frames[0].timestamp)
                )
                while remaining_frames and room > 0:
                    nxt = remaining_frames[0]
                    if (nxt.timestamp - last_start) > max_duration:
                        break
                    last_frames.append(remaining_frames.pop(0))
                    room -= 1
            for start_i in range(0, len(remaining_frames), max_images):
                raw_blocks.append(([], remaining_frames[start_i : start_i + max_images]))

    blocks: list[ContentBlock] = []
    for idx, (b_entries, b_frames) in enumerate(raw_blocks, start=1):
        starts: list[float] = []
        ends: list[float] = []
        if b_entries:
            starts.append(b_entries[0].start)
            ends.append(b_entries[-1].end)
        if b_frames:
            starts.append(b_frames[0].timestamp)
            ends.append(b_frames[-1].timestamp)
        b_start = min(starts) if starts else 0.0
        b_end = max(ends) if ends else b_start
        blocks.append(
            ContentBlock(
                index=idx,
                start=b_start,
                end=b_end,
                entries=b_entries,
                frames=b_frames,
            )
        )

    return blocks


def extract_markdown_headings(markdown_text: str) -> list[str]:
    """Extrae los encabezados H2/H3 de un bloque Markdown para mantener continuidad."""
    headings: list[str] = []
    for match in _HEADING_RE.finditer(markdown_text):
        hashes, title = match.group(1), match.group(2).strip()
        headings.append(f"{hashes} {title}")
    return headings


def parse_wrapup_response(text: str) -> tuple[str, str]:
    """Separa la respuesta de cierre en `(apertura_con_objetivos, practica_y_preguntas)`."""
    cleaned = strip_thinking_tags(text)
    open_match = _OPENING_MARKER_RE.search(cleaned)
    close_match = _CLOSING_MARKER_RE.search(cleaned)

    if open_match or close_match:
        opening = open_match.group(1).strip() if open_match else ""
        closing = close_match.group(1).strip() if close_match else ""
        return opening, closing

    # Respaldo robusto si el modelo omitió los marcadores HTML
    split_match = re.search(
        r"(?m)^##\s+(?:Pr[aá]ctica\s+Guiada|Preguntas\s+de\s+Repaso)\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if split_match:
        opening = cleaned[: split_match.start()].strip()
        closing = cleaned[split_match.start() :].strip()
        return opening, closing

    return "", cleaned


class BaseSynthesizer(ABC):
    """Interfaz base para motores de síntesis multimodal (Ollama, Gemini, OpenAI-compat)."""

    def __init__(self, config: SynthesisConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=config.timeout_seconds)

    def __enter__(self) -> BaseSynthesizer:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def unload_model(self) -> None:
        """Libera VRAM si el backend soporta descarga explícita del modelo."""

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[Path] | None = None,
    ) -> str:
        """Genera Markdown limpio para un prompt de sistema, usuario e imágenes opcionales."""


class OllamaSynthesizer(BaseSynthesizer):
    """Cliente nativo de Ollama (`/api/chat`) con soporte de visión y `think=True`."""

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[Path] | None = None,
    ) -> str:
        url = f"{self.config.base_url.rstrip('/')}/api/chat"
        user_message: dict[str, Any] = {"role": "user", "content": user_prompt}
        if self.config.vision and images:
            user_message["images"] = [
                encode_image_for_llm(img, max_width=self.config.max_image_width)[0]
                for img in images
            ]

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
            "stream": False,
            "think": self.config.think,
            "options": {
                "num_ctx": self.config.num_ctx,
                "temperature": 0.3,
            },
            "keep_alive": self.config.keep_alive,
        }

        response = self._client.post(url, json=payload, timeout=self.config.timeout_seconds)
        response.raise_for_status()
        data = response.json()

        # Ignora explícitamente `data["message"]["thinking"]` para no filtrar razonamiento interno.
        message = data.get("message") or {}
        content = message.get("content") or ""
        return strip_thinking_tags(str(content))

    def unload_model(self) -> None:
        """Envía `keep_alive: 0` a Ollama para liberar VRAM inmediatamente."""
        url = f"{self.config.base_url.rstrip('/')}/api/generate"
        payload = {"model": self.config.model, "keep_alive": 0}
        try:
            self._client.post(url, json=payload, timeout=30.0)
        except httpx.HTTPError:
            pass


class GeminiSynthesizer(BaseSynthesizer):
    """Cliente REST directo para Google Gemini (`generateContent`) con visión multimodal."""

    _DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def _resolve_api_key(self) -> str:
        api_key = self.config.api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "Falta la clave API de Gemini. Configura `--api-key` o la variable de entorno GEMINI_API_KEY."
            )
        return api_key

    def _resolve_base_url(self) -> str:
        base = self.config.base_url.rstrip("/")
        if not base or "localhost:11434" in base or "127.0.0.1:11434" in base:
            return self._DEFAULT_BASE_URL
        return base

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[Path] | None = None,
    ) -> str:
        api_key = self._resolve_api_key()
        base_url = self._resolve_base_url()
        url = f"{base_url}/models/{self.config.model}:generateContent"

        parts: list[dict[str, Any]] = [{"text": user_prompt}]
        if self.config.vision and images:
            for img in images:
                b64_data, mime_type = encode_image_for_llm(
                    img, max_width=self.config.max_image_width
                )
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_data,
                        }
                    }
                )

        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.3},
        }

        response = self._client.post(
            url,
            headers={"x-goog-api-key": api_key},
            json=payload,
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        content_parts = (candidates[0].get("content") or {}).get("parts") or []
        texts: list[str] = []
        for part in content_parts:
            if part.get("thought"):
                continue
            text_val = part.get("text")
            if isinstance(text_val, str):
                texts.append(text_val)
        return strip_thinking_tags("\n".join(texts))


class OpenAICompatSynthesizer(BaseSynthesizer):
    """Cliente REST compatible con `/v1/chat/completions` (OpenAI, OpenRouter, LM Studio, vLLM)."""

    def _resolve_endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if not base:
            base = "https://api.openai.com"
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[Path] | None = None,
    ) -> str:
        url = self._resolve_endpoint()
        api_key = self.config.api_key or os.environ.get("OPENAI_API_KEY")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        user_content: str | list[dict[str, Any]]
        if self.config.vision and images:
            content_parts: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
            for img in images:
                b64_data, mime_type = encode_image_for_llm(
                    img, max_width=self.config.max_image_width
                )
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
                    }
                )
            user_content = content_parts
        else:
            user_content = user_prompt

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
        }

        response = self._client.post(
            url,
            headers=headers,
            json=payload,
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        return strip_thinking_tags(str(content))


def create_synthesizer(
    config: SynthesisConfig,
    client: httpx.Client | None = None,
) -> BaseSynthesizer:
    """Fábrica de sintetizadores según `config.engine`."""
    if config.engine == "ollama":
        return OllamaSynthesizer(config, client=client)
    if config.engine == "gemini":
        return GeminiSynthesizer(config, client=client)
    if config.engine == "openai-compat":
        return OpenAICompatSynthesizer(config, client=client)
    raise ValueError(
        f"Motor de síntesis no soportado para ejecución automática: {config.engine!r}. "
        "Usa 'ollama', 'gemini' o 'openai-compat' (para 'agent', utiliza Antigravity u OpenCode)."
    )


def _format_block_transcript(entries: list[SrtEntry]) -> str:
    if not entries:
        return ""
    lines = [f"[{format_timestamp_hms(e.start)}] {e.text.strip()}" for e in entries if e.text.strip()]
    return "\n".join(lines)


def _build_combined_excerpt(block_markdowns: list[str], max_chars: int = 12000) -> str:
    combined = "\n\n".join(md.strip() for md in block_markdowns if md.strip())
    if len(combined) <= max_chars:
        return combined
    # Mantiene una muestra balanceada de cada bloque si el capítulo es muy extenso
    per_block = max(600, max_chars // max(1, len(block_markdowns)))
    parts: list[str] = []
    for idx, md in enumerate(block_markdowns, start=1):
        snippet = md.strip()
        if len(snippet) > per_block:
            snippet = snippet[:per_block].rstrip() + "\n[...]"
        parts.append(f"### Extracto bloque {idx}\n{snippet}")
    return "\n\n".join(parts)[:max_chars]


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _load_checkpoint(checkpoint_path: Path, expected_total: int) -> dict[str, str]:
    if not checkpoint_path.is_file():
        return {}
    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("total_blocks") != expected_total:
        return {}
    raw_blocks = data.get("blocks")
    if not isinstance(raw_blocks, dict):
        return {}
    return {str(k): str(v) for k, v in raw_blocks.items() if isinstance(v, str)}


def _save_checkpoint(
    checkpoint_path: Path,
    total_blocks: int,
    completed_blocks: dict[str, str],
    engine: str,
    model: str,
) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "total_blocks": total_blocks,
        "engine": engine,
        "model": model,
        "blocks": completed_blocks,
    }
    tmp_path = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(checkpoint_path)


def synthesize_lesson(
    artifacts: LessonArtifacts,
    entries: list[SrtEntry],
    frames: list[ExtractedFrame],
    config: SynthesisConfig,
    media_info: MediaInfo | None = None,
    on_block: Callable[[int, int], None] | None = None,
    synthesizer: BaseSynthesizer | None = None,
) -> Path:
    """Sintetiza una lección completa por bloques en español, con checkpoint y limpieza automática."""
    if (
        config.resume
        and artifacts.final_md_path.is_file()
        and artifacts.final_md_path.stat().st_size > 100
    ):
        return artifacts.final_md_path

    lesson_title = artifacts.final_md_path.stem.replace("_", " ").strip() or artifacts.video_path.stem
    blocks = split_into_blocks(
        entries,
        frames,
        block_minutes=config.block_minutes,
        max_images_per_block=config.max_images_per_block,
    )

    total_blocks = len(blocks)
    completed_blocks: dict[str, str] = (
        _load_checkpoint(artifacts.checkpoint_path, total_blocks) if config.resume else {}
    )

    owns_synth = synthesizer is None
    synth = synthesizer or create_synthesizer(config)

    block_markdowns: list[str] = []
    previous_headings: list[str] = []

    try:
        for block in blocks:
            key = str(block.index)
            if key in completed_blocks and completed_blocks[key].strip():
                block_md = completed_blocks[key]
            else:
                transcript_text = _format_block_transcript(block.entries)
                frame_catalog = [
                    (format_timestamp_hms(frame.timestamp), frame.relative_md_path)
                    for frame in block.frames
                ]
                user_prompt = build_block_user_prompt(
                    lesson_title=lesson_title,
                    block_index=block.index,
                    total_blocks=total_blocks,
                    start_hms=format_timestamp_hms(block.start),
                    end_hms=format_timestamp_hms(block.end),
                    transcript_text=transcript_text,
                    frame_catalog=frame_catalog,
                    previous_headings=previous_headings,
                )
                image_paths = [frame.path for frame in block.frames] if config.vision else None
                block_md = synth.generate(
                    SYSTEM_PROMPT_EXHAUSTIVE_ES,
                    user_prompt,
                    images=image_paths,
                )
                completed_blocks[key] = block_md
                _save_checkpoint(
                    artifacts.checkpoint_path,
                    total_blocks=total_blocks,
                    completed_blocks=completed_blocks,
                    engine=config.engine,
                    model=config.model,
                )

            block_markdowns.append(block_md)
            previous_headings.extend(extract_markdown_headings(block_md))

            if on_block is not None:
                on_block(block.index, total_blocks)

        wrapup_prompt = build_wrapup_user_prompt(
            lesson_title=lesson_title,
            source_video_name=artifacts.video_path.name,
            all_headings=previous_headings,
            combined_excerpt=_build_combined_excerpt(block_markdowns),
        )
        wrapup_raw = synth.generate(SYSTEM_PROMPT_EXHAUSTIVE_ES, wrapup_prompt, images=None)
        opening_md, closing_md = parse_wrapup_response(wrapup_raw)
    finally:
        if owns_synth:
            synth.close()

    duration_seconds = 0.0
    if media_info is not None and media_info.duration > 0:
        duration_seconds = media_info.duration
    elif entries:
        duration_seconds = max(e.end for e in entries)
    elif frames:
        duration_seconds = max(f.timestamp for f in frames)
    duration_hms = format_timestamp_hms(duration_seconds)

    frontmatter = "\n".join(
        [
            "---",
            f"title: {_yaml_quote(lesson_title)}",
            f"source_video: {_yaml_quote(artifacts.video_path.name)}",
            f"duration: {_yaml_quote(duration_hms)}",
            f"language: {config.target_language}",
            f"engine: {config.engine}",
            f"model: {_yaml_quote(config.model)}",
            "status: ready",
            "tags:",
            "  - curso",
            "  - ti",
            "---",
        ]
    )

    sections: list[str] = [
        frontmatter,
        f"# {lesson_title}",
        (
            "> [!info] Metadatos de la lección\n"
            f"> - **Video fuente:** `{artifacts.video_path.name}`\n"
            f"> - **Duración:** `{duration_hms}`"
        ),
    ]
    if opening_md:
        sections.append(opening_md)
    for b_md in block_markdowns:
        if b_md.strip():
            sections.append(b_md.strip())
    if closing_md:
        sections.append(closing_md)

    final_content = "\n\n".join(sections).rstrip() + "\n"
    artifacts.final_md_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.final_md_path.write_text(final_content, encoding="utf-8")

    if config.auto_cleanup:
        cleanup_lesson_artifacts(
            artifacts.final_md_path,
            artifacts.assets_dir,
            artifacts.draft_md_path,
            artifacts.checkpoint_path,
        )

    return artifacts.final_md_path


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parser ligero de frontmatter YAML simple (`clave: valor`) sin dependencias externas."""
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, val = line.split(":", 1)
        cleaned_val = val.strip().strip('"').strip("'")
        meta[key.strip()] = cleaned_val
    return meta


def _parse_hms_to_seconds(ts_text: str) -> float | None:
    m = _HMS_RE.search(ts_text)
    if m:
        hours = int(m.group(1))
        minutes = int(m.group(2))
        seconds = int(m.group(3))
        millis = int(m.group(4).ljust(3, "0")) if m.group(4) else 0
        return hours * 3600 + minutes * 60 + seconds + millis / 1000.0
    m2 = _MS_RE.search(ts_text)
    if m2:
        minutes = int(m2.group(1))
        seconds = int(m2.group(2))
        return float(minutes * 60 + seconds)
    return None


def _timestamp_from_filename(filename: str) -> float:
    stem = Path(filename).stem
    m = _FNAME_HMS_RE.search(stem)
    if m:
        hours = int(m.group(1)) if m.group(1) else 0
        minutes = int(m.group(2))
        seconds = int(m.group(3))
        return float(hours * 3600 + minutes * 60 + seconds)
    m = _FNAME_SEP_HMS_RE.search(stem)
    if m:
        hours, minutes, seconds = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return float(hours * 3600 + minutes * 60 + seconds)
    m = _FNAME_SEC_RE.search(stem)
    if m:
        sec = int(m.group(1))
        frac = float(f"0.{m.group(2)}") if m.group(2) else 0.0
        return sec + frac
    return 0.0


def _parse_transcript_from_draft(text: str) -> list[SrtEntry]:
    entries: list[SrtEntry] = []
    idx = 1
    for line in text.splitlines():
        m = _TRANSCRIPT_LINE_RE.match(line)
        if not m:
            continue
        ts = _parse_hms_to_seconds(m.group(1))
        body = m.group(2).strip()
        if ts is None or not body or body.startswith("!["):
            continue
        entries.append(SrtEntry(index=idx, start=ts, end=ts + 4.0, text=body))
        idx += 1
    for i in range(len(entries) - 1):
        if entries[i + 1].start > entries[i].start:
            entries[i].end = entries[i + 1].start
    return entries


def parse_draft_for_synthesis(
    draft_md_path: Path,
) -> tuple[LessonArtifacts, list[SrtEntry], list[ExtractedFrame]]:
    """Reconstruye `LessonArtifacts`, entradas SRT y capturas desde un `.draft.md` existente."""
    draft_md_path = draft_md_path.resolve()
    raw_text = draft_md_path.read_text(encoding="utf-8")
    meta = _parse_frontmatter(raw_text)

    if draft_md_path.name.endswith(".draft.md"):
        lesson_stem = draft_md_path.name[: -len(".draft.md")]
    else:
        lesson_stem = draft_md_path.stem

    lesson_slug = slugify_name(lesson_stem)
    output_dir = draft_md_path.parent

    # Resolver video_path y srt_path
    video_raw = meta.get("source_video_path") or meta.get("video_path") or meta.get("source_video")
    if video_raw:
        vp = Path(video_raw)
        video_path = vp if vp.is_absolute() else (output_dir / vp)
    else:
        video_path = output_dir / f"{lesson_stem}.mp4"

    srt_path: Path | None = None
    srt_raw = meta.get("srt_path")
    if srt_raw:
        candidate = Path(srt_raw)
        srt_path = candidate if candidate.is_absolute() else (output_dir / candidate)
    elif video_path.with_suffix(".srt").is_file():
        srt_path = video_path.with_suffix(".srt")
    elif (output_dir / f"{lesson_stem}.srt").is_file():
        srt_path = output_dir / f"{lesson_stem}.srt"

    artifacts = build_lesson_artifacts(
        video_path=video_path,
        rel_dir=Path("."),
        output_root=output_dir,
        srt_path=srt_path,
    )
    # Asegurar que las rutas coinciden exactamente con el `.draft.md` proporcionado
    artifacts.draft_md_path = draft_md_path
    artifacts.final_md_path = output_dir / f"{lesson_stem}.md"

    # Recolectar capturas referenciadas en el draft y en `assets_dir`
    frames_by_path: dict[Path, ExtractedFrame] = {}
    discovered_assets_dir: Path | None = None

    for alt_text, rel_target in _MD_IMAGE_RE.findall(raw_text):
        cleaned_rel = rel_target.strip().strip("<>")
        if cleaned_rel.startswith(("http://", "https://", "data:")):
            continue
        img_path = (output_dir / cleaned_rel).resolve()
        if img_path.is_file():
            discovered_assets_dir = discovered_assets_dir or img_path.parent
            ts = _parse_hms_to_seconds(alt_text)
            if ts is None:
                ts = _timestamp_from_filename(img_path.name)
            rel_posix = img_path.relative_to(output_dir).as_posix()
            frames_by_path[img_path] = ExtractedFrame(
                path=img_path,
                relative_md_path=rel_posix,
                timestamp=ts,
                filename=img_path.name,
            )

    if meta.get("assets_dir"):
        candidate_dir = Path(meta["assets_dir"])
        if not candidate_dir.is_absolute():
            candidate_dir = (output_dir / candidate_dir).resolve()
        if candidate_dir.is_dir():
            discovered_assets_dir = candidate_dir

    if discovered_assets_dir is None:
        slug_dir = output_dir / "assets" / lesson_slug
        if slug_dir.is_dir():
            discovered_assets_dir = slug_dir
        elif artifacts.assets_dir.is_dir():
            discovered_assets_dir = artifacts.assets_dir

    if discovered_assets_dir is not None and discovered_assets_dir.is_dir():
        artifacts.assets_dir = discovered_assets_dir
        is_shared_assets = discovered_assets_dir.name == "assets"
        for img_path in sorted(discovered_assets_dir.iterdir()):
            if not img_path.is_file() or img_path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg"}:
                continue
            if is_shared_assets and not img_path.name.startswith(lesson_slug):
                continue
            resolved = img_path.resolve()
            if resolved in frames_by_path:
                continue
            try:
                rel_posix = resolved.relative_to(output_dir).as_posix()
            except ValueError:
                rel_posix = f"assets/{lesson_slug}/{img_path.name}"
            frames_by_path[resolved] = ExtractedFrame(
                path=resolved,
                relative_md_path=rel_posix,
                timestamp=_timestamp_from_filename(img_path.name),
                filename=img_path.name,
            )

    frames = sorted(frames_by_path.values(), key=lambda f: (f.timestamp, f.filename))
    artifacts.frames = frames

    if artifacts.srt_path.is_file():
        entries = parse_srt(artifacts.srt_path)
    else:
        entries = _parse_transcript_from_draft(raw_text)

    return artifacts, entries, frames
