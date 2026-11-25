"""Pruebas para generación Markdown estándar de Obsidian, índice maestro y limpieza segura."""

from __future__ import annotations

from pathlib import Path

import pytest

from course2book.config import ExtractedFrame, MediaInfo, SrtEntry
from course2book.obsidian import (
    build_lesson_artifacts,
    cleanup_directory,
    cleanup_lesson_artifacts,
    encode_md_path,
    extract_referenced_image_paths,
    generate_course_index,
    write_draft_markdown,
)


def test_encode_md_path_standard_markdown() -> None:
    encoded = encode_md_path("01 Modulo/01 Intro (x64).md")
    assert encoded == "01%20Modulo/01%20Intro%20%28x64%29.md"
    assert "\\" not in encoded

    # Rutas Windows con barras invertidas se normalizan a POSIX
    encoded_win = encode_md_path(r"02 Análisis\03 Stack (x86_64).md")
    assert encoded_win == "02%20An%C3%A1lisis/03%20Stack%20%28x86_64%29.md"

    # Rutas saneadas de assets se mantienen limpias
    assert encode_md_path("assets/01_intro/01_intro_00m12s.png") == (
        "assets/01_intro/01_intro_00m12s.png"
    )


def test_build_lesson_artifacts_readable_hierarchy_and_sanitized_assets(tmp_path: Path) -> None:
    video = tmp_path / "curso" / "01 Módulo (Fundamentos)" / "02 Stack Frames (x86_64).mkv"
    output_root = tmp_path / "libro"

    artifacts = build_lesson_artifacts(
        video_path=video,
        rel_dir=Path("01 Módulo (Fundamentos)"),
        output_root=output_root,
    )

    assert artifacts.output_dir == output_root / "01 Módulo (Fundamentos)"
    assert artifacts.final_md_path == output_root / "01 Módulo (Fundamentos)" / "02 Stack Frames (x86_64).md"
    assert artifacts.draft_md_path == (
        output_root / "01 Módulo (Fundamentos)" / "02 Stack Frames (x86_64).draft.md"
    )
    assert artifacts.assets_dir == (
        output_root / "01 Módulo (Fundamentos)" / "assets" / "02_stack_frames_x86_64"
    )
    assert artifacts.checkpoint_path == (
        output_root / "01 Módulo (Fundamentos)" / ".02_stack_frames_x86_64.progress.json"
    )
    assert artifacts.srt_path == video.with_suffix(".srt")

    # Lección en raíz del curso (`Path(".")`)
    root_video = tmp_path / "curso" / "00 Bienvenida.mp4"
    root_artifacts = build_lesson_artifacts(
        video_path=root_video,
        rel_dir=Path("."),
        output_root=output_root,
    )
    assert root_artifacts.output_dir == output_root
    assert root_artifacts.final_md_path == output_root / "00 Bienvenida.md"


def test_write_draft_markdown_interleaves_chronologically_without_wikilinks(
    tmp_path: Path,
) -> None:
    video = tmp_path / "01 Intro (GDB).mp4"
    artifacts = build_lesson_artifacts(video, Path("01 Modulo"), tmp_path / "out")

    entries = [
        SrtEntry(index=1, start=1.0, end=4.0, text="First we inspect the ELF header."),
        SrtEntry(index=2, start=12.0, end=16.0, text="Now we set a breakpoint at main."),
        SrtEntry(index=3, start=25.0, end=29.0, text="Look at the RSP register value."),
    ]
    frames = [
        ExtractedFrame(
            path=artifacts.assets_dir / "01_intro_gdb_00m00s.png",
            relative_md_path="assets/01_intro_gdb/01_intro_gdb_00m00s.png",
            timestamp=0.0,
            filename="01_intro_gdb_00m00s.png",
        ),
        ExtractedFrame(
            path=artifacts.assets_dir / "01_intro_gdb_00m10s.png",
            relative_md_path="assets/01_intro_gdb/01_intro_gdb_00m10s.png",
            timestamp=10.0,
            filename="01_intro_gdb_00m10s.png",
        ),
        ExtractedFrame(
            path=artifacts.assets_dir / "01_intro_gdb_00m30s.png",
            relative_md_path="assets/01_intro_gdb/01_intro_gdb_00m30s.png",
            timestamp=30.0,
            filename="01_intro_gdb_00m30s.png",
        ),
    ]
    media_info = MediaInfo(
        path=video,
        duration=35.0,
        width=1280,
        height=720,
        has_audio=True,
        has_video=True,
        format_name="mp4",
    )

    draft_path = write_draft_markdown(artifacts, entries, frames, media_info)
    content = draft_path.read_text(encoding="utf-8")

    assert "[[" not in content
    assert "![[" not in content
    assert 'source_video: "01 Intro (GDB).mp4"' in content
    assert 'duration: "00:00:35"' in content
    assert "language: en" in content
    assert "status: draft" in content

    # Orden cronológico: frame 00:00:00 -> entrada 00:00:01 -> frame 00:00:10 -> entrada 00:00:12 -> frame 00:00:30
    idx_f0 = content.index("![Captura 00:00:00](assets/01_intro_gdb/01_intro_gdb_00m00s.png)")
    idx_e1 = content.index("⏱️ 00:00:01")
    idx_f1 = content.index("![Captura 00:00:10](assets/01_intro_gdb/01_intro_gdb_00m10s.png)")
    idx_e2 = content.index("⏱️ 00:00:12")
    idx_f2 = content.index("![Captura 00:00:30](assets/01_intro_gdb/01_intro_gdb_00m30s.png)")
    assert idx_f0 < idx_e1 < idx_f1 < idx_e2 < idx_f2


def test_generate_course_index_standard_markdown_and_resources(tmp_path: Path) -> None:
    output_root = tmp_path / "vault_book"

    lesson_1 = build_lesson_artifacts(
        Path("01 Intro (x64).mp4"),
        Path("01 Modulo Inicial"),
        output_root,
    )
    lesson_2 = build_lesson_artifacts(
        Path("02 Debugging (GDB).mp4"),
        Path("01 Modulo Inicial"),
        output_root,
    )
    lesson_3 = build_lesson_artifacts(
        Path("01 Buffer Overflow.mp4"),
        Path("02 Explotacion (Heap & Stack)"),
        output_root,
    )

    # Lección 1 tiene capítulo final; lección 2 y 3 solo borrador
    lesson_1.final_md_path.parent.mkdir(parents=True, exist_ok=True)
    lesson_1.final_md_path.write_text("# Capítulo final de introducción\n" + "x" * 60, encoding="utf-8")
    lesson_2.draft_md_path.write_text("# Borrador\n", encoding="utf-8")
    lesson_3.draft_md_path.parent.mkdir(parents=True, exist_ok=True)
    lesson_3.draft_md_path.write_text("# Borrador\n", encoding="utf-8")

    resources = {
        Path("01 Modulo Inicial"): [Path("lab01.py"), Path("cheatsheet.pdf")],
        Path("02 Explotacion (Heap & Stack)"): [Path("vuln.c")],
    }

    index_path = generate_course_index(
        output_root=output_root,
        course_title="Ingeniería Inversa Práctica",
        lessons=[lesson_2, lesson_3, lesson_1],
        resources=resources,
    )

    assert index_path == output_root / "00_Indice.md"
    content = index_path.read_text(encoding="utf-8")

    assert "[[" not in content
    assert "![[" not in content
    assert "## 01 Modulo Inicial" in content
    assert "## 02 Explotacion (Heap & Stack)" in content
    assert "- [01 Intro (x64)](01%20Modulo%20Inicial/01%20Intro%20%28x64%29.md)" in content
    assert "- [02 Debugging (GDB)](01%20Modulo%20Inicial/02%20Debugging%20%28GDB%29.draft.md) (borrador)" in content
    assert "- [01 Buffer Overflow](02%20Explotacion%20%28Heap%20%26%20Stack%29/01%20Buffer%20Overflow.draft.md) (borrador)" in content
    assert "### Recursos y laboratorios detectados" in content
    assert "- `lab01.py`" in content
    assert "- `cheatsheet.pdf`" in content
    assert "- `vuln.c`" in content


def test_extract_referenced_image_paths() -> None:
    md = """
    ![Captura 1](assets/01_intro/img_01.png)
    ![Captura 2](assets/my%20slug/img%20%282%29.png "Título opcional")
    ![Captura 3](<assets/01_intro/con espacios.webp>)
    <img src="./assets/01_intro/html_img.jpg" width="600" />
    ![Remota](https://example.com/logo.png)
    """
    refs = extract_referenced_image_paths(md)
    assert refs == {
        "assets/01_intro/img_01.png",
        "assets/my slug/img (2).png",
        "assets/01_intro/con espacios.webp",
        "assets/01_intro/html_img.jpg",
    }


def test_cleanup_lesson_artifacts_and_safety_guard(tmp_path: Path) -> None:
    output_root = tmp_path / "book"
    artifacts = build_lesson_artifacts(
        Path("01 Análisis Estático (Ghidra).mp4"),
        Path("01 Modulo"),
        output_root,
    )
    artifacts.assets_dir.mkdir(parents=True, exist_ok=True)

    kept_1 = artifacts.assets_dir / "01_analisis_estatico_ghidra_00m04s.png"
    kept_2 = artifacts.assets_dir / "01_analisis_estatico_ghidra_00m18s.png"
    unused_1 = artifacts.assets_dir / "01_analisis_estatico_ghidra_00m08s.png"
    unused_2 = artifacts.assets_dir / "01_analisis_estatico_ghidra_00m24s.webp"
    for p in (kept_1, kept_2, unused_1, unused_2):
        p.write_bytes(b"fake-image-bytes")

    artifacts.draft_md_path.write_text("Borrador temporal", encoding="utf-8")
    artifacts.checkpoint_path.write_text('{"completed": [0, 1]}', encoding="utf-8")

    # Guardia de seguridad 1: el capítulo final aún no existe -> debe rechazar sin borrar nada
    with pytest.raises(ValueError):
        cleanup_lesson_artifacts(
            artifacts.final_md_path,
            assets_dir=artifacts.assets_dir,
            draft_md_path=artifacts.draft_md_path,
            checkpoint_path=artifacts.checkpoint_path,
        )
    assert unused_1.exists()
    assert artifacts.draft_md_path.exists()
    assert artifacts.checkpoint_path.exists()

    # Guardia de seguridad 2: el capítulo final existe pero está vacío/corrupto (< 40 chars)
    artifacts.final_md_path.write_text("# Corto", encoding="utf-8")
    with pytest.raises(ValueError):
        cleanup_lesson_artifacts(
            artifacts.final_md_path,
            assets_dir=artifacts.assets_dir,
            draft_md_path=artifacts.draft_md_path,
            checkpoint_path=artifacts.checkpoint_path,
        )
    assert unused_1.exists()
    assert artifacts.draft_md_path.exists()
    assert artifacts.checkpoint_path.exists()

    # Capítulo final válido referenciando solo kept_1 y kept_2
    artifacts.final_md_path.write_text(
        "# 01 Análisis Estático (Ghidra)\n\n"
        "Explicación detallada paso a paso del desensamblado inicial en Ghidra.\n\n"
        f"![Captura 1](assets/{artifacts.assets_dir.name}/{kept_1.name})\n\n"
        f"![Captura 2](assets/{artifacts.assets_dir.name}/{kept_2.name})\n",
        encoding="utf-8",
    )

    stats = cleanup_lesson_artifacts(
        artifacts.final_md_path,
        assets_dir=artifacts.assets_dir,
        draft_md_path=artifacts.draft_md_path,
        checkpoint_path=artifacts.checkpoint_path,
    )
    assert stats == {
        "deleted_images": 2,
        "kept_images": 2,
        "deleted_draft": 1,
        "deleted_checkpoint": 1,
    }
    assert kept_1.exists()
    assert kept_2.exists()
    assert not unused_1.exists()
    assert not unused_2.exists()
    assert not artifacts.draft_md_path.exists()
    assert not artifacts.checkpoint_path.exists()


def test_cleanup_directory_single_file_and_recursive(tmp_path: Path) -> None:
    output_root = tmp_path / "curso_out"
    l1 = build_lesson_artifacts(Path("01 Clase Uno.mp4"), Path("01 Modulo"), output_root)
    l2 = build_lesson_artifacts(Path("02 Clase Dos.mp4"), Path("02 Modulo"), output_root)

    # Crear índice maestro (no debe limpiarse ni fallar)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "00_Indice.md").write_text("# Indice\n", encoding="utf-8")

    # Lección 1: referencia 0 imágenes -> se deben eliminar sus assets y carpetas vacías
    l1.assets_dir.mkdir(parents=True, exist_ok=True)
    (l1.assets_dir / "01_clase_uno_00m00s.png").write_bytes(b"img")
    l1.draft_md_path.write_text("draft 1", encoding="utf-8")
    l1.checkpoint_path.write_text("{}", encoding="utf-8")
    l1.final_md_path.write_text(
        "# 01 Clase Uno\n\nContenido completo y verificado del primer capítulo del libro técnico.\n",
        encoding="utf-8",
    )

    # Lección 2: referencia 1 de 2 imágenes
    l2.assets_dir.mkdir(parents=True, exist_ok=True)
    l2_keep = l2.assets_dir / "02_clase_dos_00m05s.png"
    l2_drop = l2.assets_dir / "02_clase_dos_00m15s.png"
    l2_keep.write_bytes(b"img")
    l2_drop.write_bytes(b"img")
    l2.draft_md_path.write_text("draft 2", encoding="utf-8")
    l2.checkpoint_path.write_text("{}", encoding="utf-8")
    l2.final_md_path.write_text(
        "# 02 Clase Dos\n\nContenido completo del segundo capítulo con captura útil.\n\n"
        f"![Captura](assets/{l2.assets_dir.name}/{l2_keep.name})\n",
        encoding="utf-8",
    )

    # Limpieza sobre un archivo individual (l1)
    single_stats = cleanup_directory(l1.final_md_path)
    assert single_stats == {
        "deleted_images": 1,
        "kept_images": 0,
        "deleted_draft": 1,
        "deleted_checkpoint": 1,
    }
    assert not l1.assets_dir.exists()
    assert not l1.assets_dir.parent.exists()

    # Limpieza recursiva sobre todo el directorio (l1 ya limpio + l2 pendiente)
    dir_stats = cleanup_directory(output_root)
    assert dir_stats == {
        "deleted_images": 1,
        "kept_images": 1,
        "deleted_draft": 1,
        "deleted_checkpoint": 1,
    }
    assert l2_keep.exists()
    assert not l2_drop.exists()
    assert not l2.draft_md_path.exists()
    assert not l2.checkpoint_path.exists()
    assert (output_root / "00_Indice.md").exists()


def test_generate_course_index_english_localization(tmp_path: Path) -> None:
    output_root = tmp_path / "vault_en"
    lesson = build_lesson_artifacts(
        Path("01 Setup.mp4"),
        Path("01 Basics"),
        output_root,
    )
    lesson.draft_md_path.parent.mkdir(parents=True, exist_ok=True)
    lesson.draft_md_path.write_text("# Draft\n", encoding="utf-8")

    index_path = generate_course_index(
        output_root=output_root,
        course_title="Practical Reverse Engineering",
        lessons=[lesson],
        resources={Path("01 Basics"): [Path("notes.pdf")]},
        target_language="en",
    )

    assert index_path == output_root / "00_Index.md"
    content = index_path.read_text(encoding="utf-8")
    assert "(draft)" in content
    assert "### Supplementary resources and labs" in content
    assert "`notes.pdf`" in content
