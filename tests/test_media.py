"""Pruebas unitarias y de integración para el módulo `course2book.media`."""

from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path

import pytest

from course2book.media import (
    discover_resources,
    discover_videos,
    extract_audio_wav,
    format_timestamp_hms,
    parse_timestamp_to_seconds,
    probe_media,
    remove_existing_subtitles,
)

SUPPORTED_TEST_FORMATS: tuple[str, ...] = (".mp4", ".mkv", ".avi", ".webm", ".mov", ".wmv")


class TestTimestamps:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("00:00:00", 0.0),
            ("00:01:30", 90.0),
            ("01:02:03,450", 3723.45),
            ("01:02:03.25", 3723.25),
            ("05:12", 312.0),
            ("05:12.500", 312.5),
            ("05:12,750", 312.75),
            ("42", 42.0),
            ("42.5", 42.5),
            ("42,5", 42.5),
            (15, 15.0),
            (15.25, 15.25),
        ],
    )
    def test_parse_timestamp_to_seconds_valid(
        self, raw: str | int | float, expected: float
    ) -> None:
        assert parse_timestamp_to_seconds(raw) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            "   ",
            "abc",
            "-10",
            -5.0,
            "00:60:00",
            "00:00:60",
            "10:60",
            "1:2:3:4",
            True,
            float("nan"),
            float("inf"),
        ],
    )
    def test_parse_timestamp_to_seconds_invalid(self, invalid: object) -> None:
        with pytest.raises(ValueError):
            parse_timestamp_to_seconds(invalid)  # type: ignore[arg-type]

    def test_format_timestamp_hms(self) -> None:
        assert format_timestamp_hms(0.0) == "00:00:00"
        assert format_timestamp_hms(-3.0) == "00:00:00"
        assert format_timestamp_hms(65.9) == "00:01:05"
        assert format_timestamp_hms(3661.2) == "01:01:01"


class TestDiscovery:
    def test_discover_videos_nested_multiformat_and_filtering(self, tmp_path: Path) -> None:
        root = tmp_path / "curso"
        (root / "01_intro").mkdir(parents=True)
        (root / "02_labs" / "sub").mkdir(parents=True)
        (root / "10_avanzado").mkdir(parents=True)
        (root / ".hidden_dir").mkdir(parents=True)
        (root / "01_intro" / "assets").mkdir(parents=True)

        # Videos válidos en múltiples formatos y capitalizaciones
        v_root = root / "00_bienvenida.mp4"
        v_mod1_a = root / "01_intro" / "01_setup.MKV"
        v_mod1_b = root / "01_intro" / "02_demo.avi"
        v_mod2_a = root / "02_labs" / "01_lab.webm"
        v_mod2_sub = root / "02_labs" / "sub" / "01_extra.mov"
        v_mod10 = root / "10_avanzado" / "01_final.wmv"

        for p in (v_root, v_mod1_a, v_mod1_b, v_mod2_a, v_mod2_sub, v_mod10):
            p.write_bytes(b"stub")

        # Archivos que deben ignorarse
        (root / ".hidden_video.mp4").write_bytes(b"stub")
        (root / ".hidden_dir" / "secret.mp4").write_bytes(b"stub")
        (root / "01_intro" / "assets" / "clip.mp4").write_bytes(b"stub")
        (root / "01_intro" / "01_setup.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        (root / "01_intro" / "notes.md").write_text("# notas\n")

        discovered = discover_videos(root)
        assert discovered == [
            (v_root.resolve(), Path(".")),
            (v_mod1_a.resolve(), Path("01_intro")),
            (v_mod1_b.resolve(), Path("01_intro")),
            (v_mod2_a.resolve(), Path("02_labs")),
            (v_mod2_sub.resolve(), Path("02_labs") / "sub"),
            (v_mod10.resolve(), Path("10_avanzado")),
        ]

    def test_discover_videos_single_file(self, tmp_path: Path) -> None:
        video = tmp_path / "clase.mkv"
        video.write_bytes(b"stub")
        assert discover_videos(video) == [(video.resolve(), Path("."))]

    def test_discover_resources_groups_and_filters(self, tmp_path: Path) -> None:
        root = tmp_path / "curso"
        (root / "01_intro").mkdir(parents=True)
        (root / "02_labs").mkdir(parents=True)
        (root / ".git").mkdir(parents=True)

        # Recursos válidos
        r_root = root / "syllabus.pdf"
        r_mod1_py = root / "01_intro" / "exploit.py"
        r_mod1_md = root / "01_intro" / "guia.md"
        r_mod2_zip = root / "02_labs" / "archivos.zip"
        for p in (r_root, r_mod1_py, r_mod1_md, r_mod2_zip):
            p.write_text("data", encoding="utf-8")

        # Archivos a ignorar
        (root / "00_Indice.md").write_text("# indice", encoding="utf-8")
        (root / "01_intro" / "clase.draft.md").write_text("# draft", encoding="utf-8")
        (root / "01_intro" / "clase.srt").write_text("srt", encoding="utf-8")
        (root / "01_intro" / "clase.vtt").write_text("vtt", encoding="utf-8")
        (root / "01_intro" / "clase.mp4").write_bytes(b"video")
        (root / "01_intro" / ".oculto.txt").write_text("hidden", encoding="utf-8")
        (root / ".git" / "config.ini").write_text("git", encoding="utf-8")

        resources = discover_resources(root)
        assert resources == {
            Path("."): [r_root.resolve()],
            Path("01_intro"): [r_mod1_py.resolve(), r_mod1_md.resolve()],
            Path("02_labs"): [r_mod2_zip.resolve()],
        }

        # Archivo individual devuelve diccionario vacío
        assert discover_resources(r_root) == {}


class TestRemoveExistingSubtitles:
    def test_deletes_only_target_video_subtitles(self, tmp_path: Path) -> None:
        video = tmp_path / "01_intro.mp4"
        video.write_bytes(b"video")
        sibling_video = tmp_path / "01_intro_part2.mp4"
        sibling_video.write_bytes(b"video2")

        target_subs = [
            tmp_path / "01_intro.vtt",
            tmp_path / "01_intro.en.vtt",
            tmp_path / "01_intro.es.srt",
            tmp_path / "01_intro.en-US.srt",
            tmp_path / "01_intro_en.vtt",
            tmp_path / "01_intro.srt",
            tmp_path / "01_intro.ass",
        ]
        for sub in target_subs:
            sub.write_text("subtitle", encoding="utf-8")

        untouched_files = [
            video,
            sibling_video,
            tmp_path / "01_intro_part2.srt",
            tmp_path / "01_intro_part2.en.vtt",
            tmp_path / "01_intro_lab.srt",
            tmp_path / "02_otro.srt",
            tmp_path / "02_otro.en.vtt",
            tmp_path / "01_intro.md",
        ]
        for keep in untouched_files:
            if not keep.exists():
                keep.write_text("keep", encoding="utf-8")

        deleted = remove_existing_subtitles(video)
        assert set(deleted) == set(target_subs)
        for sub in target_subs:
            assert not sub.exists()
        for keep in untouched_files:
            assert keep.exists(), f"No debió eliminarse: {keep.name}"


class TestProbeAndExtractMultiformat:
    @pytest.mark.parametrize("ext", SUPPORTED_TEST_FORMATS)
    def test_probe_media_and_extract_wav_all_formats(
        self,
        ext: str,
        tmp_path: Path,
        ffmpeg_bin: str,
        ffprobe_bin: str,
        make_synthetic_video: Callable[..., Path],
    ) -> None:
        video = make_synthetic_video(tmp_path / f"clip{ext}", duration=1.2)

        info = probe_media(video)
        assert info.path == video.resolve()
        assert info.has_video is True
        assert info.has_audio is True
        assert info.width == 320
        assert info.height == 240
        assert info.duration > 0.5
        assert info.format_name != ""

        wav_dir = tmp_path / "wav_out"
        dest_wav = wav_dir / f"clip_{ext.lstrip('.')}.wav"
        extracted = extract_audio_wav(video, dest_wav)
        assert extracted == dest_wav
        assert dest_wav.is_file()

        with wave.open(str(dest_wav), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 16000
            assert wf.getsampwidth() == 2
            assert wf.getnframes() > 8000

        # Solo existe el WAV esperado, sin archivos temporales residuales
        assert list(wav_dir.iterdir()) == [dest_wav]
