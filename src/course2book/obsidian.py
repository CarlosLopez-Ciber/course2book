"""Generación de Markdown estándar para Obsidian, índice maestro y limpieza segura de artefactos."""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

from course2book.config import ExtractedFrame, LessonArtifacts, MediaInfo, SrtEntry
from course2book.frames import slugify_name

_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".webp", ".jpg", ".jpeg"})
_HTML_IMG_SRC_RE = re.compile(
    r"""<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s>]+))""",
    re.IGNORECASE,
)
_NATURAL_SPLIT_RE = re.compile(r"(\d+)")
_MIN_FINAL_MD_CHARS = 40
_INDEX_FILENAME = "00_Indice.md"


def _yaml_quote(value: str) -> str:
    """Escapa y entrecomilla una cadena para YAML frontmatter."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'


def _format_hms(seconds: float) -> str:
    """Formatea segundos a `HH:MM:SS`."""
    total = max(0, int(round(seconds)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _natural_sort_key(text: str) -> list[int | str]:
    """Clave de ordenamiento natural para módulos y lecciones numeradas."""
    return [
        int(chunk) if chunk.isdigit() else chunk.casefold()
        for chunk in _NATURAL_SPLIT_RE.split(text)
    ]


def _normalize_rel_dir(rel_dir: Path) -> Path:
    """Normaliza `rel_dir` asegurando que el directorio raíz se represente como `Path('.')`."""
    raw = str(rel_dir).strip()
    if raw in ("", ".", "./", ".\\"):
        return Path(".")
    return Path(rel_dir)


def encode_md_path(rel_path: str | Path) -> str:
    """Convierte una ruta relativa a POSIX `/` y codifica cada segmento para Markdown estándar."""
    posix_str = str(rel_path).replace("\\", "/")
    encoded_parts: list[str] = []
    for part in posix_str.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            encoded_parts.append("..")
        else:
            encoded_parts.append(quote(part, safe="-_.~"))
    return "/".join(encoded_parts)


def build_lesson_artifacts(
    video_path: Path,
    rel_dir: Path,
    output_root: Path,
    srt_path: Path | None = None,
) -> LessonArtifacts:
    """Construye las rutas espejo legibles y los directorios de assets/checkpoints saneados."""
    video_path = Path(video_path)
    output_root = Path(output_root)
    clean_rel_dir = _normalize_rel_dir(Path(rel_dir))

    output_dir = output_root if clean_rel_dir == Path(".") else output_root / clean_rel_dir
    stem = video_path.stem
    slug = slugify_name(stem)

    final_md_path = output_dir / f"{stem}.md"
    draft_md_path = output_dir / f"{stem}.draft.md"
    assets_dir = output_dir / "assets" / slug
    checkpoint_path = output_dir / f".{slug}.progress.json"
    resolved_srt = Path(srt_path) if srt_path is not None else video_path.with_suffix(".srt")

    return LessonArtifacts(
        video_path=video_path,
        rel_dir=clean_rel_dir,
        srt_path=resolved_srt,
        output_dir=output_dir,
        assets_dir=assets_dir,
        draft_md_path=draft_md_path,
        final_md_path=final_md_path,
        checkpoint_path=checkpoint_path,
    )


def write_draft_markdown(
    artifacts: LessonArtifacts,
    entries: list[SrtEntry],
    frames: list[ExtractedFrame],
    media_info: MediaInfo | None = None,
) -> Path:
    """Escribe el borrador `.draft.md` intercalando transcripción y capturas en Markdown estándar."""
    artifacts.draft_md_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_entries = sorted(entries, key=lambda e: (e.start, e.index))
    sorted_frames = sorted(frames, key=lambda f: f.timestamp)

    if media_info is not None and media_info.duration > 0:
        duration_sec = media_info.duration
    elif sorted_entries:
        duration_sec = max(e.end for e in sorted_entries)
    elif sorted_frames:
        duration_sec = max(f.timestamp for f in sorted_frames)
    else:
        duration_sec = 0.0

    title = artifacts.video_path.stem
    lines: list[str] = [
        "---",
        f"title: {_yaml_quote(title)}",
        f"source_video: {_yaml_quote(artifacts.video_path.name)}",
        f"duration: {_yaml_quote(_format_hms(duration_sec))}",
        "language: en",
        "status: draft",
        "---",
        "",
        f"# {title}",
        "",
    ]

    frame_idx = 0
    num_frames = len(sorted_frames)

    para_start: float | None = None
    para_parts: list[str] = []
    para_chars = 0
    prev_end: float | None = None

    def flush_paragraph() -> None:
        nonlocal para_start, para_parts, para_chars
        if para_parts and para_start is not None:
            text = " ".join(para_parts).strip()
            if text:
                lines.append(f"⏱️ {_format_hms(para_start)} — {text}")
                lines.append("")
        para_start = None
        para_parts = []
        para_chars = 0

    def emit_frame(frame: ExtractedFrame) -> None:
        ts_str = _format_hms(frame.timestamp)
        encoded_rel = encode_md_path(frame.relative_md_path)
        lines.append(f"![Captura {ts_str}]({encoded_rel})")
        lines.append("")

    for entry in sorted_entries:
        cleaned = " ".join(entry.text.split()).strip()
        if not cleaned:
            continue

        # Emitir capturas cuyo timestamp llega antes o al inicio de esta entrada
        if frame_idx < num_frames and sorted_frames[frame_idx].timestamp <= entry.start:
            flush_paragraph()
            while frame_idx < num_frames and sorted_frames[frame_idx].timestamp <= entry.start:
                emit_frame(sorted_frames[frame_idx])
                frame_idx += 1

        # Separar párrafo ante pausas largas o extensión acumulada tras cierre de oración
        if para_parts and prev_end is not None:
            pause_gap = entry.start - prev_end
            ends_sentence = para_parts[-1].endswith((".", "?", "!"))
            if pause_gap >= 2.5 or (para_chars >= 400 and ends_sentence) or len(para_parts) >= 8:
                flush_paragraph()

        if para_start is None:
            para_start = entry.start
        para_parts.append(cleaned)
        para_chars += len(cleaned) + 1
        prev_end = entry.end

    flush_paragraph()

    while frame_idx < num_frames:
        emit_frame(sorted_frames[frame_idx])
        frame_idx += 1

    content = "\n".join(lines).rstrip() + "\n"
    artifacts.draft_md_path.write_text(content, encoding="utf-8")
    return artifacts.draft_md_path


def _relative_posix(target: Path, base: Path) -> str:
    """Devuelve la ruta relativa de `target` respecto a `base` en formato POSIX."""
    try:
        return target.relative_to(base).as_posix()
    except ValueError:
        return Path(os.path.relpath(target, base)).as_posix()


def generate_course_index(
    output_root: Path,
    course_title: str,
    lessons: list[LessonArtifacts],
    resources: dict[Path, list[Path]] | None = None,
    target_language: str = "es",
) -> Path:
    """Genera el índice del curso (`00_Indice.md` o `00_Index.md`) agrupado por módulo."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    is_en = target_language.strip().lower().startswith("en")
    index_path = output_root / ("00_Index.md" if is_en else _INDEX_FILENAME)

    lessons_by_module: dict[Path, list[LessonArtifacts]] = {}
    for lesson in lessons:
        mod = _normalize_rel_dir(lesson.rel_dir)
        lessons_by_module.setdefault(mod, []).append(lesson)

    resources_by_module: dict[Path, list[Path]] = {}
    if resources:
        for mod_key, res_files in resources.items():
            norm_mod = _normalize_rel_dir(Path(mod_key))
            if res_files:
                resources_by_module.setdefault(norm_mod, []).extend(
                    Path(p) for p in res_files
                )

    all_modules = set(lessons_by_module.keys()) | set(resources_by_module.keys())
    sorted_modules = sorted(
        all_modules,
        key=lambda p: (0, []) if p == Path(".") else (1, _natural_sort_key(p.as_posix())),
    )

    lines: list[str] = [
        "---",
        f"title: {_yaml_quote(course_title)}",
        "type: course-index",
        f"lessons: {len(lessons)}",
        "---",
        "",
        f"# {course_title}",
        "",
    ]

    for module_dir in sorted_modules:
        heading = "General" if module_dir == Path(".") else module_dir.as_posix()
        lines.append(f"## {heading}")
        lines.append("")

        module_lessons = sorted(
            lessons_by_module.get(module_dir, []),
            key=lambda l: _natural_sort_key(l.video_path.stem),
        )
        for lesson in module_lessons:
            lesson_title = lesson.video_path.stem
            if lesson.final_md_path.is_file():
                rel_link = encode_md_path(_relative_posix(lesson.final_md_path, output_root))
                lines.append(f"- [{lesson_title}]({rel_link})")
            else:
                rel_link = encode_md_path(_relative_posix(lesson.draft_md_path, output_root))
                draft_label = "draft" if is_en else "borrador"
                lines.append(f"- [{lesson_title}]({rel_link}) ({draft_label})")

        if module_lessons:
            lines.append("")

        module_resources = sorted(
            resources_by_module.get(module_dir, []),
            key=lambda p: _natural_sort_key(p.name),
        )
        if module_resources:
            res_heading = (
                "### Supplementary resources and labs" if is_en else "### Recursos y laboratorios detectados"
            )
            lines.append(res_heading)
            lines.append("")
            for res_path in module_resources:
                lines.append(f"- `{res_path.name}`")
            lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    index_path.write_text(content, encoding="utf-8")
    return index_path


def _normalize_image_ref(raw_target: str) -> str | None:
    """Limpia, decodifica URL y normaliza a POSIX una referencia de imagen relativa."""
    cleaned = raw_target.strip()
    if not cleaned:
        return None

    lower = cleaned.lower()
    if lower.startswith(("http://", "https://", "data:", "mailto:", "ftp://")):
        return None

    # Eliminar query string o fragmento (#...)
    cleaned = cleaned.split("#", 1)[0].split("?", 1)[0].strip()
    if not cleaned:
        return None

    decoded = unquote(cleaned).replace("\\", "/")
    while decoded.startswith("./"):
        decoded = decoded[2:]

    normalized = posixpath.normpath(decoded)
    if normalized in ("", "."):
        return None
    return normalized


def _iter_markdown_image_targets(md_text: str) -> list[str]:
    """Extrae destinos de imágenes Markdown estándar `![alt](dest "title")` manejando `<...>` y paréntesis."""
    targets: list[str] = []
    n = len(md_text)
    i = 0

    while i < n:
        start = md_text.find("![", i)
        if start == -1:
            break

        # Avanzar sobre el texto alternativo [...] respetando escapes y corchetes anidados
        j = start + 2
        bracket_depth = 1
        while j < n and bracket_depth > 0:
            ch = md_text[j]
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == "[":
                bracket_depth += 1
            elif ch == "]":
                bracket_depth -= 1
            j += 1

        if bracket_depth != 0 or j >= n or md_text[j] != "(":
            i = start + 2
            continue

        # Leer el destino dentro de (...)
        k = j + 1
        while k < n and md_text[k].isspace():
            k += 1

        if k >= n:
            break

        if md_text[k] == "<":
            end_angle = md_text.find(">", k + 1)
            if end_angle == -1:
                i = k + 1
                continue
            dest = md_text[k + 1 : end_angle]
            close_paren = md_text.find(")", end_angle + 1)
            targets.append(dest)
            i = (close_paren + 1) if close_paren != -1 else (end_angle + 1)
            continue

        dest_chars: list[str] = []
        paren_depth = 0
        while k < n:
            ch = md_text[k]
            if ch == "\\" and k + 1 < n:
                dest_chars.append(md_text[k + 1])
                k += 2
                continue
            if ch == "(":
                paren_depth += 1
                dest_chars.append(ch)
                k += 1
                continue
            if ch == ")":
                if paren_depth == 0:
                    k += 1
                    break
                paren_depth -= 1
                dest_chars.append(ch)
                k += 1
                continue
            if ch.isspace() and paren_depth == 0:
                # Omitir título opcional hasta el ')' de cierre
                close_paren = md_text.find(")", k)
                k = (close_paren + 1) if close_paren != -1 else n
                break
            dest_chars.append(ch)
            k += 1

        if dest_chars:
            targets.append("".join(dest_chars))
        i = k

    return targets


def extract_referenced_image_paths(md_text: str) -> set[str]:
    """Extrae todas las rutas relativas de imágenes referenciadas en Markdown estándar o `<img>`."""
    referenced: set[str] = set()

    for raw in _iter_markdown_image_targets(md_text):
        norm = _normalize_image_ref(raw)
        if norm is not None:
            referenced.add(norm)

    for match in _HTML_IMG_SRC_RE.finditer(md_text):
        raw_src = match.group(1) or match.group(2) or match.group(3) or ""
        norm = _normalize_image_ref(raw_src)
        if norm is not None:
            referenced.add(norm)

    return referenced


def cleanup_lesson_artifacts(
    final_md_path: Path,
    assets_dir: Path | None = None,
    draft_md_path: Path | None = None,
    checkpoint_path: Path | None = None,
) -> dict[str, int]:
    """Elimina capturas no referenciadas, `.draft.md` y `.progress.json` si el `.md` final es válido."""
    final_md_path = Path(final_md_path)
    if not final_md_path.is_file():
        raise ValueError(
            f"Limpieza abortada: no existe el capítulo final '{final_md_path}'."
        )

    md_text = final_md_path.read_text(encoding="utf-8")
    if len(md_text.strip()) < _MIN_FINAL_MD_CHARS:
        raise ValueError(
            f"Limpieza abortada: el capítulo final '{final_md_path}' está vacío o incompleto "
            f"({len(md_text.strip())} caracteres; mínimo {_MIN_FINAL_MD_CHARS})."
        )

    referenced_paths = extract_referenced_image_paths(md_text)
    ref_paths_lower = {p.casefold() for p in referenced_paths}
    ref_bare_names_lower = {
        PurePosixPath(p).name.casefold() for p in referenced_paths if "/" not in p
    }

    deleted_images = 0
    kept_images = 0
    deleted_draft = 0
    deleted_checkpoint = 0

    if assets_dir is not None:
        assets_dir = Path(assets_dir)
        if assets_dir.is_dir():
            for item in list(assets_dir.iterdir()):
                if not item.is_file() or item.suffix.lower() not in _IMAGE_EXTENSIONS:
                    continue

                is_referenced = False
                try:
                    rel_to_md = item.relative_to(final_md_path.parent).as_posix()
                    if rel_to_md.casefold() in ref_paths_lower:
                        is_referenced = True
                except ValueError:
                    pass

                if not is_referenced:
                    short_rel = f"{assets_dir.name}/{item.name}".casefold()
                    if (
                        short_rel in ref_paths_lower
                        or item.name.casefold() in ref_bare_names_lower
                    ):
                        is_referenced = True

                if is_referenced:
                    kept_images += 1
                else:
                    item.unlink()
                    deleted_images += 1

            # Eliminar carpetas vacías sobrantes (`assets/<slug>/` y `assets/`)
            if not any(assets_dir.iterdir()):
                assets_dir.rmdir()
                parent_assets = assets_dir.parent
                if (
                    parent_assets.name == "assets"
                    and parent_assets.is_dir()
                    and not any(parent_assets.iterdir())
                ):
                    parent_assets.rmdir()

    if draft_md_path is not None:
        draft_md_path = Path(draft_md_path)
        if draft_md_path.is_file():
            draft_md_path.unlink()
            deleted_draft = 1

    if checkpoint_path is not None:
        checkpoint_path = Path(checkpoint_path)
        if checkpoint_path.is_file():
            checkpoint_path.unlink()
            deleted_checkpoint = 1

    return {
        "deleted_images": deleted_images,
        "kept_images": kept_images,
        "deleted_draft": deleted_draft,
        "deleted_checkpoint": deleted_checkpoint,
    }


def _is_final_lesson_md(path: Path) -> bool:
    """Determina si un archivo `.md` corresponde a un capítulo final (no borrador ni índice)."""
    return (
        path.suffix.lower() == ".md"
        and not path.name.lower().endswith(".draft.md")
        and path.name != _INDEX_FILENAME
    )


def _infer_companion_paths(final_md_path: Path) -> tuple[Path, Path, Path]:
    """Infiere `(assets_dir, draft_md_path, checkpoint_path)` a partir de un capítulo `.md` final."""
    stem = final_md_path.stem
    slug = slugify_name(stem)
    parent = final_md_path.parent
    return (
        parent / "assets" / slug,
        parent / f"{stem}.draft.md",
        parent / f".{slug}.progress.json",
    )


def cleanup_directory(target_path: Path) -> dict[str, int]:
    """Ejecuta limpieza segura sobre un capítulo `.md` individual o recursivamente en un directorio."""
    target_path = Path(target_path)
    if not target_path.exists():
        raise FileNotFoundError(f"La ruta objetivo no existe: {target_path}")

    if target_path.is_file():
        if not _is_final_lesson_md(target_path):
            raise ValueError(
                f"El archivo '{target_path.name}' no es un capítulo final limpiable."
            )
        assets_dir, draft_md_path, checkpoint_path = _infer_companion_paths(target_path)
        return cleanup_lesson_artifacts(
            final_md_path=target_path,
            assets_dir=assets_dir,
            draft_md_path=draft_md_path,
            checkpoint_path=checkpoint_path,
        )

    totals = {
        "deleted_images": 0,
        "kept_images": 0,
        "deleted_draft": 0,
        "deleted_checkpoint": 0,
    }
    for md_path in sorted(target_path.rglob("*.md")):
        if not md_path.is_file() or not _is_final_lesson_md(md_path):
            continue
        assets_dir, draft_md_path, checkpoint_path = _infer_companion_paths(md_path)
        result = cleanup_lesson_artifacts(
            final_md_path=md_path,
            assets_dir=assets_dir,
            draft_md_path=draft_md_path,
            checkpoint_path=checkpoint_path,
        )
        for key in totals:
            totals[key] += result[key]

    return totals
