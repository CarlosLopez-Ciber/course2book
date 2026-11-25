"""Transcripción GPU con faster-whisper y lectura/escritura estricta de archivos SubRip (.srt)."""

from __future__ import annotations

import gc
import re
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from course2book.config import SrtEntry, WhisperConfig, build_initial_prompt
from course2book.cuda_env import register_cuda_dlls
from course2book.media import (
    extract_audio_wav,
    parse_timestamp_to_seconds,
    remove_existing_subtitles,
)

_SRT_CUE_LINE_RE = re.compile(
    r"^\s*(\d+:\d{1,2}:\d{1,2}[,.]\d{1,3})\s*-->\s*(\d+:\d{1,2}:\d{1,2}[,.]\d{1,3})"
)


def format_srt_timestamp(seconds: float) -> str:
    """Formatea segundos no negativos en formato estricto SubRip `HH:MM:SS,mmm`."""
    total_ms = round(max(0.0, float(seconds)) * 1000.0)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(entries: list[SrtEntry], srt_path: Path) -> Path:
    """Escribe un archivo SubRip (.srt) válido en UTF-8 sin BOM con saltos de línea Unix (`\\n`)."""
    target = Path(srt_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    blocks: list[str] = []
    seq = 0
    for entry in entries:
        cleaned_text = entry.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned_text:
            continue
        seq += 1
        start_ts = format_srt_timestamp(entry.start)
        end_ts = format_srt_timestamp(max(entry.start, entry.end))
        blocks.append(f"{seq}\n{start_ts} --> {end_ts}\n{cleaned_text}")

    content = ("\n\n".join(blocks) + "\n") if blocks else ""
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return target


def parse_srt(srt_path: Path) -> list[SrtEntry]:
    """Analiza un archivo `.srt` estándar (soportando BOM, CRLF y bloques multilínea)."""
    target = Path(srt_path)
    raw = target.read_text(encoding="utf-8-sig")
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    raw_blocks = re.split(r"\n[ \t]*\n+", normalized)
    entries: list[SrtEntry] = []

    for block in raw_blocks:
        lines = [line for line in block.split("\n")]
        cue_line_idx: int | None = None
        cue_match: re.Match[str] | None = None

        for idx, line in enumerate(lines):
            match = _SRT_CUE_LINE_RE.match(line)
            if match:
                cue_line_idx = idx
                cue_match = match
                break

        if cue_line_idx is None or cue_match is None:
            continue

        start = parse_timestamp_to_seconds(cue_match.group(1))
        end = parse_timestamp_to_seconds(cue_match.group(2))
        text = "\n".join(lines[cue_line_idx + 1 :]).strip()
        if not text:
            continue

        entry_index = len(entries) + 1
        if cue_line_idx > 0:
            maybe_num = lines[cue_line_idx - 1].strip()
            if maybe_num.isdigit():
                entry_index = int(maybe_num)

        entries.append(SrtEntry(index=entry_index, start=start, end=end, text=text))

    return entries


def load_wav_float32(wav_path: Path) -> np.ndarray:
    """Lee un WAV PCM 16-bit mono (16 kHz) directamente a `np.ndarray` float32 en [-1.0, 1.0].

    Evita pasar rutas a `faster_whisper.audio.decode_audio` (evitando incompatibilidades con PyAV 19+).
    """
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(
                f"Se esperaba audio PCM 16-bit en {wav_path}, recibido sampwidth={wf.getsampwidth()}"
            )
        channels = wf.getnchannels()
        raw_frames = wf.readframes(wf.getnframes())

    if not raw_frames:
        return np.zeros(0, dtype=np.float32)

    audio = np.frombuffer(raw_frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


class WhisperTranscriber:
    """Transcriptor de video a subtítulos `.srt` exclusivos usando faster-whisper en GPU/CPU."""

    def __init__(self, config: WhisperConfig) -> None:
        self.config = config
        self._model: Any = None

    def load(self) -> None:
        """Registra las DLLs de CUDA en Windows y carga el modelo WhisperModel."""
        if self._model is not None:
            return
        register_cuda_dlls()
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.config.model_size,
            device=self.config.device,
            compute_type=self.config.compute_type,
        )

    def unload(self) -> None:
        """Libera el modelo y ejecuta garbage collection para devolver VRAM a la GPU."""
        self._model = None
        gc.collect()

    def __enter__(self) -> WhisperTranscriber:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.unload()

    def transcribe_video(
        self,
        video_path: Path,
        output_srt_path: Path | None = None,
    ) -> Path:
        """Transcribe un video a un único archivo `.srt` limpio sin dejar archivos temporales."""
        video = Path(video_path)
        target_srt = (
            Path(output_srt_path) if output_srt_path is not None else video.with_suffix(".srt")
        )

        if self.config.resume and target_srt.is_file() and target_srt.stat().st_size > 0:
            return target_srt

        if self.config.clean_existing_subtitles:
            remove_existing_subtitles(video)
            if target_srt.is_file():
                target_srt.unlink(missing_ok=True)

        if self._model is None:
            self.load()

        language = (
            None
            if self.config.language in (None, "", "auto")
            else self.config.language
        )
        initial_prompt = build_initial_prompt(self.config.domain, self.config.glossary)

        entries: list[SrtEntry] = []
        with tempfile.TemporaryDirectory(prefix="course2book_wav_") as tmp_dir:
            wav_path = Path(tmp_dir) / f"{video.stem}.wav"
            extract_audio_wav(video, wav_path)
            audio_array = load_wav_float32(wav_path)

            segments, _info = self._model.transcribe(
                audio_array,
                language=language,
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter,
                vad_parameters={
                    "min_silence_duration_ms": self.config.min_silence_duration_ms
                },
                initial_prompt=initial_prompt,
            )

            for seg in segments:
                text = (getattr(seg, "text", "") or "").strip()
                if not text:
                    continue
                start = float(seg.start)
                end = max(start, float(seg.end))
                entries.append(
                    SrtEntry(index=len(entries) + 1, start=start, end=end, text=text)
                )

        return write_srt(entries, target_srt)

    def transcribe_batch(
        self,
        videos: list[Path],
        output_root: Path | None = None,
        input_root: Path | None = None,
        on_progress: Callable[[Path, int, int, str], None] | None = None,
    ) -> list[Path]:
        """Transcribe una lista de videos cargando el modelo una sola vez y liberándolo al final."""
        total = len(videos)
        if total == 0:
            return []

        resolved_input_root = Path(input_root).resolve() if input_root is not None else None
        resolved_output_root = Path(output_root) if output_root is not None else None

        targets: list[tuple[Path, Path]] = []
        for raw_video in videos:
            video = Path(raw_video)
            if resolved_output_root is None:
                target_srt = video.with_suffix(".srt")
            else:
                rel_dir = Path(".")
                if resolved_input_root is not None:
                    try:
                        rel_dir = video.resolve().parent.relative_to(resolved_input_root)
                    except ValueError:
                        rel_dir = Path(".")
                target_srt = resolved_output_root / rel_dir / f"{video.stem}.srt"
            targets.append((video, target_srt))

        results: list[Path] = []
        try:
            needs_model = any(
                not (self.config.resume and srt.is_file() and srt.stat().st_size > 0)
                for _vid, srt in targets
            )
            if needs_model:
                self.load()

            for idx, (video, target_srt) in enumerate(targets, start=1):
                if self.config.resume and target_srt.is_file() and target_srt.stat().st_size > 0:
                    if on_progress is not None:
                        on_progress(video, idx, total, "skipped")
                    results.append(target_srt)
                    continue

                if on_progress is not None:
                    on_progress(video, idx, total, "transcribing")
                written = self.transcribe_video(video, output_srt_path=target_srt)
                if on_progress is not None:
                    on_progress(video, idx, total, "done")
                results.append(written)
        finally:
            self.unload()

        return results
