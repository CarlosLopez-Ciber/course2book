"""Pruebas para el extractor de capturas clave (keyframes) para cursos técnicos de TI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from course2book.config import FrameConfig
from course2book.frames import (
    _compute_frame_signature,
    _is_significant_change,
    extract_keyframes,
    format_frame_timestamp_tag,
    slugify_name,
)
from course2book.media import find_binary


def _load_test_font(size: int = 28) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Obtiene una fuente legible sin depender de rutas del sistema operativo."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _render_dark_terminal_frame(
    dest: Path,
    lines: list[str],
    *,
    pip_tick: int = 0,
    width: int = 1280,
    height: int = 720,
) -> Path:
    """Renderiza una pantalla de terminal oscura con código y una webcam PiP animada en la esquina inferior derecha."""
    img = Image.new("RGB", (width, height), color=(18, 20, 26))
    draw = ImageDraw.Draw(img)
    font = _load_test_font(30)

    # Barra superior sutil de terminal
    draw.rectangle([0, 0, width, 44], fill=(32, 36, 46))
    draw.text((24, 8), "kali@lab:~/reveng/lesson01", fill=(180, 210, 255), font=font)

    # Líneas de texto/código en la zona principal (celdas centrales y superiores)
    y = 90
    for idx, line in enumerate(lines):
        color = (120, 240, 150) if idx == 0 else (235, 240, 248)
        draw.text((56, y), line, fill=color, font=font)
        y += 58

    # Webcam PiP animada confinada estrictamente dentro de la celda esquina inferior derecha (3, 3):
    # En cuadrícula 4x4 de 1280x720, la celda (3, 3) abarca x in [960, 1280], y in [540, 720].
    pip_x0, pip_y0, pip_x1, pip_y1 = 1000, 565, 1245, 700
    bg_palette = [
        (45, 65, 95),
        (95, 55, 65),
        (55, 90, 70),
        (110, 85, 45),
    ]
    pip_bg = bg_palette[pip_tick % len(bg_palette)]
    draw.rectangle([pip_x0, pip_y0, pip_x1, pip_y1], fill=pip_bg, outline=(220, 220, 220), width=3)

    # Silueta animada moviéndose dentro del recuadro PiP en cada tick
    shift_x = (pip_tick * 23) % 120
    shift_y = (pip_tick * 17) % 45
    draw.ellipse(
        [pip_x0 + 20 + shift_x, pip_y0 + 18 + shift_y, pip_x0 + 84 + shift_x, pip_y0 + 82 + shift_y],
        fill=(240, 200, 160),
    )
    draw.rectangle(
        [pip_x0 + 15 + shift_x, pip_y0 + 78 + shift_y, pip_x0 + 95 + shift_x, pip_y1 - 8],
        fill=(220, 90, 90) if pip_tick % 2 == 0 else (70, 160, 230),
    )

    img.save(dest, format="PNG")
    return dest


def test_slugify_name() -> None:
    assert slugify_name("01 Introducción a x86_64 (Ghidra & GDB)!") == (
        "01_introduccion_a_x86_64_ghidra_gdb"
    )
    assert slugify_name("   !!!   ") == "lesson"
    long_name = "modulo_" + "segmento_muy_largo_" * 6
    slug = slugify_name(long_name)
    assert len(slug) <= 60
    assert not slug.endswith("_")
    assert not slug.startswith("_")


def test_format_frame_timestamp_tag() -> None:
    assert format_frame_timestamp_tag(0) == "00m00s"
    assert format_frame_timestamp_tag(72) == "01m12s"
    assert format_frame_timestamp_tag(3599) == "59m59s"
    assert format_frame_timestamp_tag(3600) == "1h00m00s"
    assert format_frame_timestamp_tag(3735) == "1h02m15s"
    assert format_frame_timestamp_tag(-5) == "00m00s"


def test_anti_pip_and_dark_terminal_detection(tmp_path: Path) -> None:
    base_code = [
        "$ gdb -q ./crackme_x64",
        "(gdb) break main",
        "(gdb) run AAAA_TEST_INPUT",
    ]
    changed_code = [
        "$ gdb -q ./crackme_x64",
        "(gdb) break main",
        "(gdb) disassemble check_password",
        "0x0000000000401196 <+0>: push rbp",
    ]

    f_static_a = _render_dark_terminal_frame(tmp_path / "a.png", base_code, pip_tick=0)
    f_pip_only = _render_dark_terminal_frame(tmp_path / "b.png", base_code, pip_tick=3)
    f_code_change = _render_dark_terminal_frame(tmp_path / "c.png", changed_code, pip_tick=0)
    f_both = _render_dark_terminal_frame(tmp_path / "d.png", changed_code, pip_tick=5)

    sig_a = _compute_frame_signature(f_static_a, 4, 4)
    sig_pip = _compute_frame_signature(f_pip_only, 4, 4)
    sig_code = _compute_frame_signature(f_code_change, 4, 4)
    sig_both = _compute_frame_signature(f_both, 4, 4)

    cfg_anti_pip = FrameConfig(ignore_corner_pip=True)
    cfg_no_filter = FrameConfig(ignore_corner_pip=False)

    # Pantalla idéntica: sin cambio
    assert not _is_significant_change(sig_a, sig_a, cfg_anti_pip)

    # Solo movimiento de webcam PiP en esquina inferior derecha: ignorado con anti-PiP
    assert not _is_significant_change(sig_a, sig_pip, cfg_anti_pip)
    # Sin filtro anti-PiP sí debe detectarse
    assert _is_significant_change(sig_a, sig_pip, cfg_no_filter)

    # Cambio de texto en terminal oscura (área principal): detectado
    assert _is_significant_change(sig_a, sig_code, cfg_anti_pip)

    # Cambio de texto + movimiento de webcam simultáneo: detectado
    assert _is_significant_change(sig_a, sig_both, cfg_anti_pip)


def test_extract_keyframes_synthetic_video_with_pip_and_settle_frames(tmp_path: Path) -> None:
    ffmpeg_bin = find_binary("ffmpeg")

    slide_1 = [
        "$ readelf -h ./binary_elf",
        "Class: ELF64 | Machine: Advanced Micro Devices X86-64",
        "Entry point address: 0x401080",
    ]
    slide_2_typing_1 = [
        "$ objdump -d -M intel ./binary_elf | head -n 6",
        "0000000000401146 <vuln>:",
    ]
    slide_2_typing_2 = [
        "$ objdump -d -M intel ./binary_elf | head -n 6",
        "0000000000401146 <vuln>:",
        "  40114a: sub rsp, 0x50",
        "  40114e: lea rdi, [rbp-0x40]",
    ]
    slide_2_final = [
        "$ objdump -d -M intel ./binary_elf | head -n 6",
        "0000000000401146 <vuln>:",
        "  40114a: sub rsp, 0x50",
        "  40114e: lea rdi, [rbp-0x40]",
        "  401152: call 401040 <gets@plt>",
        "  401157: leave ; ret",
    ]
    slide_3 = [
        "$ python3 exploit.py",
        "from pwn import p64",
        "payload = b'A' * 72 + p64(0x401196)",
        "[+] Shell spawned: uid=0(root)",
    ]

    # Secuencia de 20 segundos a 1 fps:
    # t=0..5: Slide 1 estable (solo se mueve la webcam PiP)
    # t=6: ráfaga escribiendo (parcial 1)
    # t=7: ráfaga escribiendo (parcial 2)
    # t=8..13: Slide 2 final estabilizado (solo se mueve la webcam PiP)
    # t=14..19: Slide 3 estabilizado (solo se mueve la webcam PiP)
    timeline: list[list[str]] = (
        [slide_1] * 6
        + [slide_2_typing_1]
        + [slide_2_typing_2]
        + [slide_2_final] * 6
        + [slide_3] * 6
    )

    seq_dir = tmp_path / "seq"
    seq_dir.mkdir()
    for idx, text_lines in enumerate(timeline):
        _render_dark_terminal_frame(
            seq_dir / f"frame_{idx:03d}.png",
            text_lines,
            pip_tick=idx,
        )

    video_path = tmp_path / "01 Stack Overflow (x64).mp4"
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-framerate",
            "1",
            "-i",
            str(seq_dir / "frame_%03d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            str(video_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"ffmpeg error: {proc.stderr}"

    assets_dir = tmp_path / "out" / "assets" / "01_stack_overflow_x64"
    cfg = FrameConfig(
        interval_seconds=1.0,
        min_interval_seconds=3.0,
        ignore_corner_pip=True,
        image_format="png",
    )

    frames = extract_keyframes(video_path, assets_dir, cfg)

    # Debe extraer exactamente 3 capturas limpias (Slide 1, Slide 2 estabilizado, Slide 3)
    assert len(frames) == 3
    for frame in frames:
        assert frame.path.is_file()
        assert frame.filename.startswith("01_stack_overflow_x64_")
        assert frame.filename.endswith(".png")
        assert "\\" not in frame.relative_md_path
        assert frame.relative_md_path == f"assets/01_stack_overflow_x64/{frame.filename}"

    # Verifica settle-frame: la 2a captura corresponde al final de la ráfaga (>= 8s), no a t=6s
    assert frames[0].timestamp == pytest.approx(0.0)
    assert frames[1].timestamp >= 8.0
    assert frames[2].timestamp >= 14.0

    # Verifica max_frames respetando primero y último
    limited_dir = tmp_path / "out_limited" / "assets" / "01_stack_overflow_x64"
    cfg_limited = FrameConfig(
        interval_seconds=1.0,
        min_interval_seconds=3.0,
        ignore_corner_pip=True,
        max_frames=2,
    )
    limited_frames = extract_keyframes(video_path, limited_dir, cfg_limited)
    assert len(limited_frames) == 2
    assert limited_frames[0].timestamp == frames[0].timestamp
    assert limited_frames[-1].timestamp == frames[-1].timestamp
