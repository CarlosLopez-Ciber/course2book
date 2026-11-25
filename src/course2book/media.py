"""Utilidades multimedia con FFmpeg/FFprobe, descubrimiento de archivos y apertura en VLC."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from course2book.config import (
    RESOURCE_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MediaInfo,
)

_NATURAL_SPLIT_RE = re.compile(r"(\d+)")
_HMS_RE = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2}(?:[.,]\d+)?)$")
_MS_RE = re.compile(r"^(\d+):(\d{1,2}(?:[.,]\d+)?)$")
_NUM_RE = re.compile(r"^\d+(?:[.,]\d+)?$")

# Etiquetas de idioma válidas para subtítulos sidecar:
# - Con punto: .en, .es, .eng, .en-US, .en_US, .es-419
# - Con guion bajo: _en, _es, _en-US, _pt-BR (solo ISO 639-1 de 2 letras para evitar colisiones como _lab o _part2)
_DOT_LANG_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_](?:[A-Za-z]{2}|[0-9]{3}))?$")
_UNDERSCORE_LANG_TAG_RE = re.compile(r"^[A-Za-z]{2}(?:[-_][A-Za-z]{2})?$")


def _natural_key(text: str) -> list[tuple[int, int | str]]:
    """Genera una clave de ordenamiento natural insensible a mayúsculas."""
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in _NATURAL_SPLIT_RE.split(text)
        if part
    ]


def _rel_dir_sort_key(rel_dir: Path) -> tuple[int, list[tuple[int, int | str]]]:
    """Ordena el directorio raíz '.' primero y el resto de forma natural."""
    if rel_dir == Path("."):
        return (0, [])
    return (1, _natural_key(rel_dir.as_posix()))


def find_binary(name: str) -> str:
    """Localiza un binario ejecutable (como ffmpeg o ffprobe) en PATH o rutas de WinGet en Windows.

    Raises:
        FileNotFoundError: Si el binario no se encuentra disponible en el sistema.
    """
    found = shutil.which(name)
    if found:
        return found

    if sys.platform == "win32":
        exe_name = name if name.lower().endswith(".exe") else f"{name}.exe"
        local_app_data = os.environ.get("LOCALAPPDATA")
        candidates: list[Path] = []

        if local_app_data:
            winget_root = Path(local_app_data) / "Microsoft" / "WinGet"
            links_candidate = winget_root / "Links" / exe_name
            candidates.append(links_candidate)

            packages_dir = winget_root / "Packages"
            if packages_dir.is_dir():
                # Priorizar paquetes Gyan.FFmpeg* y luego explorar el resto de paquetes WinGet
                for pkg_dir in sorted(packages_dir.glob("Gyan.FFmpeg*")):
                    if pkg_dir.is_dir():
                        candidates.extend(sorted(pkg_dir.rglob(exe_name)))
                for pkg_dir in sorted(packages_dir.iterdir()):
                    if pkg_dir.is_dir() and not pkg_dir.name.startswith("Gyan.FFmpeg"):
                        for pattern in (f"bin/{exe_name}", f"*/bin/{exe_name}", exe_name):
                            candidates.extend(sorted(pkg_dir.glob(pattern)))

        # Rutas frecuentes adicionales en Windows
        candidates.extend(
            [
                Path(r"C:\ffmpeg\bin") / exe_name,
                Path(r"C:\Program Files\ffmpeg\bin") / exe_name,
                Path(r"C:\ProgramData\chocolatey\bin") / exe_name,
            ]
        )

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

    raise FileNotFoundError(
        f"No se encontró el binario '{name}' en PATH ni en las rutas estándar de instalación "
        f"(WinGet/FFmpeg). Instala FFmpeg o agrega su carpeta 'bin' al PATH del sistema."
    )


def find_vlc() -> str | None:
    """Busca el ejecutable de VLC Media Player en PATH o rutas estándar de Windows."""
    found = shutil.which("vlc")
    if found:
        return found

    standard_paths = (
        Path(r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
        Path(r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"),
    )
    for candidate in standard_paths:
        if candidate.is_file():
            return str(candidate)
    return None


def parse_timestamp_to_seconds(ts: str | float | int) -> float:
    """Convierte una marca de tiempo (HH:MM:SS[,/.mmm], MM:SS o segundos) a segundos flotantes.

    Raises:
        ValueError: Si la marca de tiempo es negativa o tiene un formato inválido.
    """
    if isinstance(ts, bool):
        raise ValueError(f"Marca de tiempo inválida: {ts!r}")

    if isinstance(ts, (int, float)):
        value = float(ts)
        if value < 0 or value != value or value == float("inf"):
            raise ValueError(f"La marca de tiempo debe ser un número finito no negativo: {ts!r}")
        return value

    if not isinstance(ts, str):
        raise ValueError(f"Tipo de marca de tiempo no soportado: {type(ts).__name__}")

    cleaned = ts.strip()
    if not cleaned:
        raise ValueError("La marca de tiempo no puede estar vacía.")

    if _NUM_RE.match(cleaned):
        return float(cleaned.replace(",", "."))

    hms_match = _HMS_RE.match(cleaned)
    if hms_match:
        hours = int(hms_match.group(1))
        minutes = int(hms_match.group(2))
        seconds = float(hms_match.group(3).replace(",", "."))
        if minutes >= 60 or seconds >= 60.0:
            raise ValueError(f"Minutos o segundos fuera de rango en '{ts}'.")
        return hours * 3600.0 + minutes * 60.0 + seconds

    ms_match = _MS_RE.match(cleaned)
    if ms_match:
        minutes = int(ms_match.group(1))
        seconds = float(ms_match.group(2).replace(",", "."))
        if seconds >= 60.0:
            raise ValueError(f"Segundos fuera de rango en '{ts}'.")
        return minutes * 60.0 + seconds

    raise ValueError(f"Formato de marca de tiempo inválido: {ts!r}")


def format_timestamp_hms(seconds: float) -> str:
    """Formatea segundos no negativos como HH:MM:SS."""
    total_seconds = int(max(0.0, float(seconds)))
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def open_in_vlc(video_path: Path, timestamp: str | float) -> subprocess.Popen[bytes]:
    """Abre un video en VLC Media Player en el segundo exacto indicado, de forma desacoplada.

    Raises:
        FileNotFoundError: Si no existe el archivo de video o no se encuentra VLC.
    """
    resolved_video = Path(video_path)
    if not resolved_video.is_file():
        raise FileNotFoundError(f"No se encontró el archivo de video: {resolved_video}")

    vlc_bin = find_vlc()
    if not vlc_bin:
        raise FileNotFoundError(
            "No se encontró VLC Media Player en PATH ni en C:\\Program Files\\VideoLAN\\VLC."
        )

    start_sec = int(parse_timestamp_to_seconds(timestamp))
    cmd = [vlc_bin, f"--start-time={start_sec}", str(resolved_video)]

    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        popen_kwargs["close_fds"] = True
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]


def probe_media(video_path: Path) -> MediaInfo:
    """Inspecciona un archivo multimedia mediante ffprobe y devuelve su MediaInfo."""
    target = Path(video_path)
    if not target.is_file():
        raise FileNotFoundError(f"No se encontró el archivo multimedia: {target}")

    ffprobe_bin = find_binary("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(target),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe falló al inspeccionar '{target}': {result.stderr.strip()}")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Salida JSON inválida de ffprobe para '{target}': {exc}") from exc

    streams: list[dict[str, object]] = payload.get("streams", []) or []
    fmt: dict[str, object] = payload.get("format", {}) or {}

    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    video_streams = [
        s
        for s in streams
        if s.get("codec_type") == "video"
        and not bool((s.get("disposition") or {}).get("attached_pic"))  # type: ignore[union-attr]
    ]
    has_video = bool(video_streams)

    width = int(video_streams[0].get("width", 0) or 0) if video_streams else 0
    height = int(video_streams[0].get("height", 0) or 0) if video_streams else 0

    duration = 0.0
    raw_duration = fmt.get("duration")
    if raw_duration is not None and str(raw_duration) != "N/A":
        try:
            duration = float(raw_duration)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            duration = 0.0

    if duration <= 0.0:
        stream_durations: list[float] = []
        for s in streams:
            sd = s.get("duration")
            if sd is not None and str(sd) != "N/A":
                try:
                    stream_durations.append(float(sd))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    pass
        if stream_durations:
            duration = max(stream_durations)

    format_name = str(fmt.get("format_name", "") or "")
    return MediaInfo(
        path=target.resolve(),
        duration=duration,
        width=width,
        height=height,
        has_audio=has_audio,
        has_video=has_video,
        format_name=format_name,
    )


def extract_audio_wav(video_path: Path, dest_wav: Path) -> Path:
    """Extrae la pista de audio como WAV PCM 16-bit mono a 16 kHz ignorando video y subtítulos."""
    src = Path(video_path)
    dst = Path(dest_wav)
    if not src.is_file():
        raise FileNotFoundError(f"No se encontró el archivo de video: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = find_binary("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-i",
        str(src),
        "-vn",
        "-sn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(dst),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0 or not dst.is_file():
        raise RuntimeError(
            f"ffmpeg falló al extraer el audio WAV de '{src}': {result.stderr.strip()}"
        )
    return dst


def _is_video_by_probe(candidate: Path) -> bool:
    """Comprueba con ffprobe si un archivo contiene una pista de video válida."""
    try:
        info = probe_media(candidate)
    except (FileNotFoundError, RuntimeError, OSError):
        return False
    return info.has_video


def discover_videos(input_path: Path, probe_all: bool = False) -> list[tuple[Path, Path]]:
    """Descubre archivos de video en un archivo o árbol de directorios.

    Ignora directorios ocultos (prefijo '.') y carpetas llamadas 'assets'.
    Devuelve una lista de pares `(video_path_resuelto, rel_dir)` con ordenamiento natural.
    """
    root = Path(input_path)
    if not root.exists():
        raise FileNotFoundError(f"La ruta de entrada no existe: {root}")

    if root.is_file():
        ext = root.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return [(root.resolve(), Path("."))]
        if _is_video_by_probe(root):
            return [(root.resolve(), Path("."))]
        return []

    found: list[tuple[Path, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d.lower() != "assets"
        ]
        current_dir = Path(dirpath)
        rel_dir = current_dir.relative_to(root)

        for filename in filenames:
            if filename.startswith("."):
                continue
            file_path = current_dir / filename
            ext = file_path.suffix.lower()
            is_video = ext in VIDEO_EXTENSIONS
            if not is_video and probe_all:
                if ext not in SUBTITLE_EXTENSIONS and ext != ".md":
                    is_video = _is_video_by_probe(file_path)
            if is_video:
                found.append((file_path.resolve(), rel_dir))

    found.sort(key=lambda item: (_rel_dir_sort_key(item[1]), _natural_key(item[0].name)))
    return found


def discover_resources(input_root: Path) -> dict[Path, list[Path]]:
    """Descubre archivos complementarios del curso agrupados por directorio relativo.

    Ignora archivos/directorios ocultos, subtítulos, videos, borradores `.draft.md`
    y archivos índice `00_Indice.md`.
    """
    root = Path(input_root)
    if not root.exists() or root.is_file():
        return {}

    grouped: dict[Path, list[Path]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        current_dir = Path(dirpath)
        rel_dir = current_dir.relative_to(root)

        for filename in filenames:
            if filename.startswith("."):
                continue
            lower_name = filename.lower()
            if lower_name.endswith(".draft.md") or lower_name == "00_indice.md":
                continue

            file_path = current_dir / filename
            ext = file_path.suffix.lower()
            if ext in SUBTITLE_EXTENSIONS or ext in VIDEO_EXTENSIONS:
                continue
            if ext not in RESOURCE_EXTENSIONS:
                continue

            grouped.setdefault(rel_dir, []).append(file_path.resolve())

    sorted_result: dict[Path, list[Path]] = {}
    for rel_dir in sorted(grouped.keys(), key=_rel_dir_sort_key):
        sorted_result[rel_dir] = sorted(grouped[rel_dir], key=lambda p: _natural_key(p.name))
    return sorted_result


def remove_existing_subtitles(video_path: Path) -> list[Path]:
    """Elimina todos los subtítulos externos asociados exactamente a `video_path`.

    Borra `<stem>.<ext>` y variantes de idioma como `<stem>.en.vtt`, `<stem>.es.srt`,
    `<stem>.en-US.srt` o `<stem>_en.vtt`, sin afectar subtítulos de otros videos
    hermanos (por ejemplo `01_intro_part2.srt` al procesar `01_intro.mp4`).
    """
    target = Path(video_path)
    parent = target.parent
    if not parent.is_dir():
        return []

    target_stem_lower = target.stem.lower()

    # Registrar stems de otros videos en la misma carpeta para no borrar sus subtítulos
    sibling_video_stems: set[str] = set()
    for entry in parent.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() in VIDEO_EXTENSIONS:
            stem_low = entry.stem.lower()
            if stem_low != target_stem_lower:
                sibling_video_stems.add(stem_low)

    deleted: list[Path] = []
    for candidate in sorted(parent.iterdir(), key=lambda p: _natural_key(p.name)):
        if not candidate.is_file():
            continue
        ext_lower = candidate.suffix.lower()
        if ext_lower not in SUBTITLE_EXTENSIONS:
            continue

        cand_stem = candidate.stem
        cand_stem_lower = cand_stem.lower()

        if cand_stem_lower in sibling_video_stems:
            continue

        should_delete = False
        if cand_stem_lower == target_stem_lower:
            should_delete = True
        elif cand_stem_lower.startswith(target_stem_lower):
            remainder = cand_stem[len(target.stem) :]
            if remainder.startswith("."):
                lang_tag = remainder[1:]
                if _DOT_LANG_TAG_RE.match(lang_tag):
                    should_delete = True
            elif remainder.startswith("_"):
                lang_tag = remainder[1:]
                if _UNDERSCORE_LANG_TAG_RE.match(lang_tag):
                    should_delete = True

        if should_delete:
            candidate.unlink(missing_ok=True)
            deleted.append(candidate)

    return deleted
