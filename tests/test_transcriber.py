"""Pruebas unitarias para `course2book.transcriber` y `WhisperTranscriber`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import wave

import numpy as np
import pytest

from course2book import transcriber as transcriber_mod
from course2book.config import SrtEntry, WhisperConfig, build_initial_prompt
from course2book.transcriber import (
    WhisperTranscriber,
    format_srt_timestamp,
    parse_srt,
    write_srt,
)


@dataclass
class _FakeSegment:
    start: float
    end: float
    text: str


class _FakeWhisperModel:
    def __init__(self, segments: list[_FakeSegment]) -> None:
        self._segments = segments
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
        assert isinstance(audio, np.ndarray), "Debe recibir np.ndarray float32 directamente (sin PyAV)."
        assert audio.dtype == np.float32
        assert audio.size > 0
        self.calls.append({"audio_len": int(audio.size), **kwargs})

        def _gen() -> Any:
            for seg in self._segments:
                yield seg

        return _gen(), {"language": "en"}


class TestSrtFormattingAndRoundtrip:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "00:00:00,000"),
            (-5.0, "00:00:00,000"),
            (1.5, "00:00:01,500"),
            (59.9999, "00:01:00,000"),
            (3599.9999, "01:00:00,000"),
            (3661.005, "01:01:01,005"),
        ],
    )
    def test_format_srt_timestamp(self, seconds: float, expected: str) -> None:
        assert format_srt_timestamp(seconds) == expected

    def test_write_and_parse_srt_roundtrip(self, tmp_path: Path) -> None:
        srt_path = tmp_path / "lesson.srt"
        entries = [
            SrtEntry(index=99, start=0.25, end=2.5, text="Introducción a Ghidra y x86_64."),
            SrtEntry(index=100, start=2.5, end=3.0, text="   \n  "),  # debe omitirse
            SrtEntry(
                index=101,
                start=3.125,
                end=7.75,
                text="Línea uno con ñ y acentos.\nLínea dos: ¿qué pasa en el stack?",
            ),
        ]

        written = write_srt(entries, srt_path)
        assert written == srt_path

        raw_bytes = srt_path.read_bytes()
        assert not raw_bytes.startswith(b"\xef\xbb\xbf"), "No debe tener BOM UTF-8"
        assert b"\r\n" not in raw_bytes, "Debe usar saltos de línea Unix \\n"

        parsed = parse_srt(srt_path)
        assert len(parsed) == 2
        assert parsed[0] == SrtEntry(
            index=1, start=0.25, end=2.5, text="Introducción a Ghidra y x86_64."
        )
        assert parsed[1] == SrtEntry(
            index=2,
            start=3.125,
            end=7.75,
            text="Línea uno con ñ y acentos.\nLínea dos: ¿qué pasa en el stack?",
        )

    def test_parse_srt_handles_bom_and_crlf(self, tmp_path: Path) -> None:
        srt_path = tmp_path / "bom_crlf.srt"
        content = (
            "1\r\n"
            "00:00:01,000 --> 00:00:03,500\r\n"
            "Primera línea\r\n"
            "Segunda línea\r\n"
            "\r\n"
            "2\r\n"
            "00:00:04,000 --> 00:00:06,250\r\n"
            "Tercer bloque\r\n"
        )
        srt_path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

        parsed = parse_srt(srt_path)
        assert len(parsed) == 2
        assert parsed[0].text == "Primera línea\nSegunda línea"
        assert parsed[1].start == pytest.approx(4.0)
        assert parsed[1].end == pytest.approx(6.25)


class TestWhisperTranscriber:
    def _patch_audio_extractor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> list[Path]:
        created_wavs: list[Path] = []

        def _fake_extract(video_path: Path, dest_wav: Path) -> Path:
            dest_wav.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(dest_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(np.zeros(1600, dtype=np.int16).tobytes())
            created_wavs.append(dest_wav)
            return dest_wav

        monkeypatch.setattr(transcriber_mod, "extract_audio_wav", _fake_extract)
        return created_wavs

    def test_transcribe_cleans_old_subs_creates_only_srt_and_passes_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created_wavs = self._patch_audio_extractor(monkeypatch)

        video = tmp_path / "01_intro.mp4"
        video.write_bytes(b"dummy-video")

        # Subtítulos previos del video que deben eliminarse antes de transcribir
        old_subs = [
            tmp_path / "01_intro.vtt",
            tmp_path / "01_intro.en.vtt",
            tmp_path / "01_intro.srt",
            tmp_path / "01_intro.ass",
        ]
        for sub in old_subs:
            sub.write_text("old subtitle", encoding="utf-8")

        # Subtítulo de otro video que NO debe tocarse
        sibling_sub = tmp_path / "01_intro_part2.srt"
        sibling_sub.write_text("sibling", encoding="utf-8")

        fake_model = _FakeWhisperModel(
            [
                _FakeSegment(0.0, 2.5, "Welcome to reverse engineering with Ghidra."),
                _FakeSegment(2.5, 3.0, "   "),
                _FakeSegment(3.0, 6.2, "Let us inspect RAX and the stack frame."),
            ]
        )

        config = WhisperConfig(
            domain="reveng",
            glossary="Ghidra, pwndbg, RAX",
            language="en",
            beam_size=5,
            vad_filter=True,
            min_silence_duration_ms=500,
            clean_existing_subtitles=True,
            resume=False,
        )
        transcriber = WhisperTranscriber(config)
        transcriber._model = fake_model

        out_srt = transcriber.transcribe_video(video)

        assert out_srt == video.with_suffix(".srt")
        assert out_srt.is_file()

        # Verificar que los subtítulos antiguos desaparecieron (excepto el nuevo .srt)
        assert not (tmp_path / "01_intro.vtt").exists()
        assert not (tmp_path / "01_intro.en.vtt").exists()
        assert not (tmp_path / "01_intro.ass").exists()
        assert sibling_sub.exists()

        # Verificar que SOLO existe el video, el subtítulo hermano y el nuevo .srt (cero extras)
        assert {p.name for p in tmp_path.iterdir()} == {
            "01_intro.mp4",
            "01_intro.srt",
            "01_intro_part2.srt",
        }

        # Verificar que el WAV temporal fue eliminado automáticamente
        assert len(created_wavs) == 1
        assert not created_wavs[0].exists()
        assert not created_wavs[0].parent.exists()

        # Verificar argumentos enviados a WhisperModel.transcribe
        assert len(fake_model.calls) == 1
        call = fake_model.calls[0]
        assert call["language"] == "en"
        assert call["beam_size"] == 5
        assert call["vad_filter"] is True
        assert call["vad_parameters"] == {"min_silence_duration_ms": 500}
        assert call["initial_prompt"] == build_initial_prompt("reveng", "Ghidra, pwndbg, RAX")

        # Verificar contenido del SRT generado
        parsed = parse_srt(out_srt)
        assert len(parsed) == 2
        assert parsed[0].text == "Welcome to reverse engineering with Ghidra."
        assert parsed[1].text == "Let us inspect RAX and the stack frame."

    def test_resume_skips_existing_nonempty_srt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_audio_extractor(monkeypatch)

        video = tmp_path / "02_lesson.mkv"
        video.write_bytes(b"dummy-video")
        existing_srt = tmp_path / "02_lesson.srt"
        original_text = "1\n00:00:00,000 --> 00:00:02,000\nExistente\n"
        existing_srt.write_text(original_text, encoding="utf-8", newline="\n")

        fake_model = _FakeWhisperModel([_FakeSegment(0.0, 1.0, "Nuevo")])
        config = WhisperConfig(resume=True, clean_existing_subtitles=True)
        transcriber = WhisperTranscriber(config)
        transcriber._model = fake_model

        result = transcriber.transcribe_video(video)
        assert result == existing_srt
        assert existing_srt.read_text(encoding="utf-8") == original_text
        assert fake_model.calls == []

    def test_language_auto_passes_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_audio_extractor(monkeypatch)
        video = tmp_path / "03_auto.mp4"
        video.write_bytes(b"dummy")

        fake_model = _FakeWhisperModel([_FakeSegment(0.0, 1.0, "Hola")])
        transcriber = WhisperTranscriber(WhisperConfig(language="auto"))
        transcriber._model = fake_model

        transcriber.transcribe_video(video)
        assert fake_model.calls[0]["language"] is None

    def test_unload_and_context_manager_clear_model(self) -> None:
        transcriber = WhisperTranscriber(WhisperConfig())
        transcriber._model = _FakeWhisperModel([])
        assert transcriber._model is not None

        transcriber.unload()
        assert transcriber._model is None

        with WhisperTranscriber(WhisperConfig()) as ctx:
            ctx._model = _FakeWhisperModel([])
            assert ctx._model is not None
        assert ctx._model is None

    def test_transcribe_batch_loads_once_mirrors_output_and_unloads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_audio_extractor(monkeypatch)

        in_root = tmp_path / "curso"
        out_root = tmp_path / "salida"
        v1 = in_root / "01_mod" / "a.mp4"
        v2 = in_root / "02_mod" / "b.mkv"
        v1.parent.mkdir(parents=True)
        v2.parent.mkdir(parents=True)
        v1.write_bytes(b"v1")
        v2.write_bytes(b"v2")

        fake_model = _FakeWhisperModel([_FakeSegment(0.0, 1.5, "Segmento")])
        load_count = 0

        transcriber = WhisperTranscriber(WhisperConfig())

        def _fake_load() -> None:
            nonlocal load_count
            load_count += 1
            transcriber._model = fake_model

        monkeypatch.setattr(transcriber, "load", _fake_load)

        events: list[tuple[str, int, int, str]] = []
        results = transcriber.transcribe_batch(
            [v1, v2],
            output_root=out_root,
            input_root=in_root,
            on_progress=lambda p, i, t, s: events.append((p.name, i, t, s)),
        )

        assert load_count == 1
        assert transcriber._model is None
        assert results == [
            out_root / "01_mod" / "a.srt",
            out_root / "02_mod" / "b.srt",
        ]
        assert all(p.is_file() for p in results)
        assert ("a.mp4", 1, 2, "transcribing") in events
        assert ("a.mp4", 1, 2, "done") in events
        assert ("b.mkv", 2, 2, "done") in events
