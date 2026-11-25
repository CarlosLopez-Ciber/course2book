"""Fixtures compartidas de pytest para generar videos sintéticos con FFmpeg y Pillow."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from course2book.media import find_binary

SUPPORTED_TEST_FORMATS: tuple[str, ...] = (".mp4", ".mkv", ".avi", ".webm", ".mov", ".wmv")

_FORMAT_CODEC_ARGS: dict[str, list[str]] = {
    ".mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"],
    ".mkv": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"],
    ".mov": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"],
    ".avi": ["-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"],
    ".webm": [
        "-c:v",
        "libvpx",
        "-deadline",
        "realtime",
        "-cpu-used",
        "8",
        "-c:a",
        "libvorbis",
    ],
    ".wmv": ["-c:v", "wmv2", "-c:a", "wmav2"],
}


def create_synthetic_video(
    dest: Path,
    *,
    duration: float = 1.5,
    width: int = 320,
    height: int = 240,
    fps: int = 10,
    audio_freq: int = 440,
    color: str = "navy",
    ffmpeg_path: str | None = None,
) -> Path:
    """Genera un video sintético diminuto con audio y video reales usando FFmpeg."""
    ffmpeg_bin = ffmpeg_path or find_binary("ffmpeg")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ext = dest.suffix.lower()
    codec_args = _FORMAT_CODEC_ARGS.get(ext, _FORMAT_CODEC_ARGS[".mp4"])

    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}:d={duration}:r={fps}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={audio_freq}:sample_rate=44100:duration={duration}",
        *codec_args,
        "-shortest",
        str(dest),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"Error generando video sintético '{dest}': {result.stderr.strip()}")
    return dest


def create_terminal_pip_video(
    dest: Path,
    *,
    width: int = 640,
    height: int = 360,
    fps: int = 2,
    seconds_per_slide: int = 4,
    ffmpeg_path: str | None = None,
) -> Path:
    """Genera un video sintético de terminal oscura con 3 diapositivas y PiP animado en la esquina inferior derecha.

    Útil para validar tanto media/ffmpeg como la detección inteligente de capturas y exclusión PiP.
    """
    ffmpeg_bin = ffmpeg_path or find_binary("ffmpeg")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    slides = [
        (
            "SLIDE 1: LINUX SHELL & FILESYSTEM",
            [
                "$ ls -la /opt/course/lab01",
                "drwxr-xr-x 2 root root 4096 vuln_app",
                "-rw-r--r-- 1 root root 1824 Makefile",
                "$ checksec --file=./vuln_app",
                "RELRO: Partial | Canary: No | NX: Disabled",
            ],
            (70, 210, 120),
        ),
        (
            "SLIDE 2: GDB DISASSEMBLY & STACK",
            [
                "pwndbg> disassemble main",
                "0x00401136 <+0>:  push   rbp",
                "0x00401137 <+1>:  mov    rbp, rsp",
                "0x0040113a <+4>:  sub    rsp, 0x40",
                "0x0040113e <+8>:  call   0x401030 <gets@plt>",
            ],
            (95, 185, 255),
        ),
        (
            "SLIDE 3: PYTHON EXPLOIT EXECUTION",
            [
                "$ python3 exploit.py LOCAL",
                "[*] Sending 72-byte cyclic payload...",
                "[+] Overwrote RIP -> 0x00401199 <win>",
                "$ id",
                "uid=0(root) gid=0(root) groups=0(root)",
            ],
            (255, 200, 90),
        ),
    ]

    total_frames = len(slides) * seconds_per_slide * fps
    total_duration = len(slides) * seconds_per_slide

    pip_x0 = int(width * 0.78)
    pip_y0 = int(height * 0.78)
    pip_x1 = width - 8
    pip_y1 = height - 8

    with tempfile.TemporaryDirectory(prefix="course2book_synth_frames_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for frame_idx in range(total_frames):
            slide_idx = min(frame_idx // (seconds_per_slide * fps), len(slides) - 1)
            title, lines, accent = slides[slide_idx]

            img = Image.new("RGB", (width, height), color=(18, 22, 28))
            draw = ImageDraw.Draw(img)

            # Barra superior estilo terminal
            draw.rectangle([(0, 0), (width, 38)], fill=(32, 38, 48))
            draw.rectangle([(16, 48), (width - 16, 52)], fill=accent)
            draw.text((20, 12), title, fill=(235, 240, 245))

            # Bloques de texto y geometría distintiva por diapositiva en el área principal
            y = 74
            for line_i, text_line in enumerate(lines):
                draw.text((28, y), text_line, fill=accent if line_i == 0 else (215, 222, 230))
                # Pequeño indicador visual para alto contraste de bordes
                bar_w = 140 + ((slide_idx * 90 + line_i * 45) % 240)
                draw.rectangle([(28, y + 16), (28 + bar_w, y + 20)], fill=(55, 65, 80))
                y += 42

            # Caja PiP animada en esquina inferior derecha (cambia en cada fotograma)
            r = (frame_idx * 67 + 40) % 256
            g = (frame_idx * 113 + 90) % 256
            b = (frame_idx * 151 + 130) % 256
            draw.rectangle([(pip_x0, pip_y0), (pip_x1, pip_y1)], fill=(r, g, b))
            inner_offset = 6 + (frame_idx % 12)
            draw.rectangle(
                [
                    (pip_x0 + inner_offset, pip_y0 + inner_offset),
                    (pip_x1 - inner_offset, pip_y1 - inner_offset),
                ],
                fill=(255 - r, 255 - g, 255 - b),
            )

            img.save(tmp_path / f"frame_{frame_idx:04d}.png")

        cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-framerate",
            str(fps),
            "-i",
            str(tmp_path / "frame_%04d.png"),
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={total_duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(dest),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0 or not dest.is_file():
            raise RuntimeError(
                f"Error generando video sintético de terminal '{dest}': {result.stderr.strip()}"
            )

    return dest


@pytest.fixture(scope="session")
def ffmpeg_bin() -> str:
    """Devuelve la ruta de ffmpeg o salta las pruebas multimedia si no está instalado."""
    try:
        return find_binary("ffmpeg")
    except FileNotFoundError:
        pytest.skip("ffmpeg no está disponible en el entorno de pruebas.")


@pytest.fixture(scope="session")
def ffprobe_bin() -> str:
    """Devuelve la ruta de ffprobe o salta las pruebas multimedia si no está instalado."""
    try:
        return find_binary("ffprobe")
    except FileNotFoundError:
        pytest.skip("ffprobe no está disponible en el entorno de pruebas.")


@pytest.fixture()
def make_synthetic_video(ffmpeg_bin: str) -> Callable[..., Path]:
    """Fábrica para crear videos sintéticos reales con FFmpeg en cualquier formato soportado."""

    def _factory(dest: Path, **kwargs: object) -> Path:
        return create_synthetic_video(dest, ffmpeg_path=ffmpeg_bin, **kwargs)  # type: ignore[arg-type]

    return _factory


@pytest.fixture()
def multiformat_videos(
    tmp_path: Path, make_synthetic_video: Callable[..., Path]
) -> dict[str, Path]:
    """Genera videos sintéticos diminutos en .mp4, .mkv, .avi, .webm, .mov y .wmv."""
    out_dir = tmp_path / "formats"
    return {
        ext: make_synthetic_video(out_dir / f"sample{ext}", duration=1.2)
        for ext in SUPPORTED_TEST_FORMATS
    }


@pytest.fixture()
def make_terminal_pip_video(ffmpeg_bin: str) -> Callable[..., Path]:
    """Fábrica para crear videos sintéticos de terminal oscura con PiP animado."""

    def _factory(dest: Path, **kwargs: object) -> Path:
        return create_terminal_pip_video(dest, ffmpeg_path=ffmpeg_bin, **kwargs)  # type: ignore[arg-type]

    return _factory


@pytest.fixture()
def terminal_pip_video(
    tmp_path: Path, make_terminal_pip_video: Callable[..., Path]
) -> Path:
    """Genera un video de terminal oscura con 3 diapositivas y PiP animado en esquina inferior derecha."""
    return make_terminal_pip_video(tmp_path / "terminal_pip.mp4")
