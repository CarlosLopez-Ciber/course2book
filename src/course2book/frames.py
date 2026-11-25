"""Extracción inteligente de capturas clave (keyframes) afinada para cursos técnicos de TI."""

from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

from course2book.config import ExtractedFrame, FrameConfig
from course2book.media import find_binary

_ANALYSIS_WIDTH = 320
_ANALYSIS_HEIGHT = 180
_EDGE_GRADIENT_THRESHOLD = 22.0
_SLUG_MAX_LENGTH = 60
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class _FrameSignature:
    """Firma perceptual, espacial y de densidad de bordes de un fotograma candidato."""

    phash: imagehash.ImageHash
    dhash: imagehash.ImageHash
    gray: np.ndarray  # uint8 (H, W)
    edges: np.ndarray  # bool (H, W)
    cell_means: np.ndarray  # float32 (grid_rows, grid_cols)
    cell_edge_density: np.ndarray  # float32 (grid_rows, grid_cols)
    std_luminance: float
    mean_edge_density: float


def slugify_name(name: str) -> str:
    """Normaliza un nombre a un slug ASCII seguro para carpetas de assets y archivos."""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_ALNUM_RE.sub("_", ascii_text).strip("_")
    if len(slug) > _SLUG_MAX_LENGTH:
        slug = slug[:_SLUG_MAX_LENGTH].rstrip("_")
    return slug or "lesson"


def format_frame_timestamp_tag(seconds: float) -> str:
    """Formatea segundos como etiqueta compacta (`01m12s` o `1h02m15s`)."""
    total_seconds = max(0, int(round(seconds)))
    if total_seconds >= 3600:
        hours = total_seconds // 3600
        remainder = total_seconds % 3600
        minutes = remainder // 60
        secs = remainder % 60
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    minutes = total_seconds // 60
    secs = total_seconds % 60
    return f"{minutes:02d}m{secs:02d}s"


def _grid_boundaries(size: int, parts: int) -> np.ndarray:
    """Calcula los índices de corte de una cuadrícula uniforme."""
    count = max(1, int(parts))
    return np.linspace(0, size, count + 1, dtype=int)


def _compute_frame_signature(
    img_path: Path,
    grid_rows: int = 4,
    grid_cols: int = 4,
) -> _FrameSignature:
    """Calcula hashes perceptuales, luminancia por celda y mapa de bordes/texto."""
    rows = max(1, int(grid_rows))
    cols = max(1, int(grid_cols))

    with Image.open(img_path) as img:
        gray_img = img.convert("L")
        phash = imagehash.phash(gray_img)
        dhash = imagehash.dhash(gray_img)
        resized = gray_img.resize(
            (_ANALYSIS_WIDTH, _ANALYSIS_HEIGHT),
            Image.Resampling.BILINEAR,
        )
        gray_u8 = np.asarray(resized, dtype=np.uint8)

    gray_f32 = gray_u8.astype(np.float32)
    gx = np.abs(np.diff(gray_f32, axis=1, prepend=gray_f32[:, :1]))
    gy = np.abs(np.diff(gray_f32, axis=0, prepend=gray_f32[:1, :]))
    edges = (gx + gy) >= _EDGE_GRADIENT_THRESHOLD

    row_bounds = _grid_boundaries(gray_u8.shape[0], rows)
    col_bounds = _grid_boundaries(gray_u8.shape[1], cols)

    cell_means = np.empty((rows, cols), dtype=np.float32)
    cell_edge_density = np.empty((rows, cols), dtype=np.float32)

    for r in range(rows):
        r0, r1 = int(row_bounds[r]), int(row_bounds[r + 1])
        for c in range(cols):
            c0, c1 = int(col_bounds[c]), int(col_bounds[c + 1])
            cell_means[r, c] = float(gray_f32[r0:r1, c0:c1].mean())
            cell_edge_density[r, c] = float(edges[r0:r1, c0:c1].mean())

    return _FrameSignature(
        phash=phash,
        dhash=dhash,
        gray=gray_u8,
        edges=edges,
        cell_means=cell_means,
        cell_edge_density=cell_edge_density,
        std_luminance=float(gray_f32.std()),
        mean_edge_density=float(edges.mean()),
    )


def _is_blank_signature(sig: _FrameSignature) -> bool:
    """Detecta fotogramas prácticamente vacíos (negro/blanco sólido de fade-in)."""
    return sig.std_luminance < 2.0 and sig.mean_edge_density < 0.0005


def _is_significant_change(
    prev_sig: _FrameSignature,
    curr_sig: _FrameSignature,
    config: FrameConfig,
) -> bool:
    """Evalúa si entre dos fotogramas hubo un cambio técnico real, filtrando PiP en esquinas."""
    rows = max(1, int(config.grid_rows))
    cols = max(1, int(config.grid_cols))

    h, w = prev_sig.gray.shape
    row_bounds = _grid_boundaries(h, rows)
    col_bounds = _grid_boundaries(w, cols)

    abs_gray_diff = np.abs(
        prev_sig.gray.astype(np.int16) - curr_sig.gray.astype(np.int16)
    )
    edge_xor = prev_sig.edges ^ curr_sig.edges

    cell_diff = np.empty((rows, cols), dtype=np.float32)
    edge_diff = np.empty((rows, cols), dtype=np.float32)

    for r in range(rows):
        r0, r1 = int(row_bounds[r]), int(row_bounds[r + 1])
        for c in range(cols):
            c0, c1 = int(col_bounds[c]), int(col_bounds[c + 1])
            cell_diff[r, c] = float(abs_gray_diff[r0:r1, c0:c1].mean())
            edge_diff[r, c] = float(edge_xor[r0:r1, c0:c1].mean())

    changed_mask = (cell_diff >= config.grid_cell_diff_threshold) | (
        edge_diff >= config.edge_diff_threshold
    )
    changed_cells = {
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if bool(changed_mask[r, c])
    }

    corners = {(0, 0), (0, cols - 1), (rows - 1, 0), (rows - 1, cols - 1)}
    corner_changed = changed_cells & corners
    non_corner_changed = changed_cells - corners

    phash_dist = int(prev_sig.phash - curr_sig.phash)
    dhash_dist = int(prev_sig.dhash - curr_sig.dhash)
    global_hash_changed = (
        phash_dist >= config.phash_threshold or dhash_dist >= config.dhash_threshold
    )

    can_filter_pip = config.ignore_corner_pip and rows >= 2 and cols >= 2 and (rows * cols > 4)

    if can_filter_pip:
        # Caso 1: únicamente cambió una celda de esquina (webcam PiP) y las otras 15 están estáticas
        if len(changed_cells) == 1 and len(corner_changed) == 1:
            only_corner = next(iter(corner_changed))
            other_mask = np.ones((rows, cols), dtype=bool)
            other_mask[only_corner] = False
            other_mean_diff = float(cell_diff[other_mask].mean())
            other_mean_edge = float(edge_diff[other_mask].mean())
            if (
                other_mean_diff < config.grid_cell_diff_threshold * 0.5
                and other_mean_edge < config.edge_diff_threshold * 0.5
            ):
                return False

        # Caso 2: ninguna celda superó umbral, pero el movimiento sutil en una esquina disparó hash global
        if not changed_cells and global_hash_changed:
            norm_lum = cell_diff / max(config.grid_cell_diff_threshold, 1e-6)
            norm_edge = edge_diff / max(config.edge_diff_threshold, 1e-6)
            combined_score = norm_lum + norm_edge
            max_idx = tuple(int(v) for v in np.unravel_index(int(np.argmax(combined_score)), (rows, cols)))
            if max_idx in corners:
                other_mask = np.ones((rows, cols), dtype=bool)
                other_mask[max_idx] = False
                if (
                    float(cell_diff[other_mask].max()) < min(2.0, config.grid_cell_diff_threshold * 0.35)
                    and float(edge_diff[other_mask].max()) < min(0.004, config.edge_diff_threshold * 0.35)
                ):
                    return False

    if len(non_corner_changed) >= 1 or len(changed_cells) >= 2:
        return True

    if not config.ignore_corner_pip and len(changed_cells) >= 1:
        return True

    return global_hash_changed


def _select_settle_frame_indices(
    signatures: list[_FrameSignature],
    timestamps: list[float],
    config: FrameConfig,
) -> list[int]:
    """Selecciona índices de fotogramas estabilizados (settle frames) respetando min_interval y max_frames."""
    total = len(signatures)
    if total == 0:
        return []

    anchor_idx = 0
    for idx, sig in enumerate(signatures):
        if not _is_blank_signature(sig):
            anchor_idx = idx
            break

    selected: list[int] = [anchor_idx]
    last_saved_sig = signatures[anchor_idx]
    last_saved_ts = timestamps[anchor_idx]

    in_burst = False
    burst_last_idx: int | None = None

    for idx in range(anchor_idx + 1, total):
        curr_sig = signatures[idx]
        prev_sig = signatures[idx - 1]

        if not in_burst:
            if _is_significant_change(last_saved_sig, curr_sig, config):
                in_burst = True
                burst_last_idx = idx
        else:
            if _is_significant_change(prev_sig, curr_sig, config):
                # Sigue habiendo escritura/scroll activo; avanzamos al último frame de la ráfaga
                burst_last_idx = idx
            else:
                # La pantalla se estabilizó: evaluamos el settle frame
                assert burst_last_idx is not None
                if _is_significant_change(last_saved_sig, signatures[burst_last_idx], config):
                    if timestamps[burst_last_idx] - last_saved_ts >= config.min_interval_seconds:
                        chosen_idx = burst_last_idx
                    elif timestamps[idx] - last_saved_ts >= config.min_interval_seconds:
                        chosen_idx = idx
                    else:
                        chosen_idx = None

                    if chosen_idx is not None:
                        selected.append(chosen_idx)
                        last_saved_sig = signatures[chosen_idx]
                        last_saved_ts = timestamps[chosen_idx]

                in_burst = False
                burst_last_idx = None

    # Si el video termina durante una ráfaga activa, guardamos su último frame si cumple criterios
    if in_burst and burst_last_idx is not None:
        if (
            _is_significant_change(last_saved_sig, signatures[burst_last_idx], config)
            and timestamps[burst_last_idx] - last_saved_ts >= config.min_interval_seconds
        ):
            selected.append(burst_last_idx)

    if config.max_frames > 0 and len(selected) > config.max_frames:
        if config.max_frames == 1:
            return [selected[0]]
        positions = np.linspace(0, len(selected) - 1, config.max_frames)
        downsampled: list[int] = []
        seen: set[int] = set()
        for pos in positions:
            candidate = selected[int(round(float(pos)))]
            if candidate not in seen:
                seen.add(candidate)
                downsampled.append(candidate)
        return downsampled

    return selected


def extract_keyframes(
    video_path: Path,
    assets_dir: Path,
    config: FrameConfig | None = None,
    *,
    lesson_slug: str | None = None,
    rel_assets_prefix: str = "assets",
) -> list[ExtractedFrame]:
    """Extrae capturas deduplicadas de un video técnico usando detección por cuadrícula y settle frames."""
    video_path = Path(video_path)
    assets_dir = Path(assets_dir)
    if not video_path.is_file():
        raise FileNotFoundError(f"No se encontró el archivo de video: {video_path}")

    cfg = config or FrameConfig()
    if cfg.interval_seconds <= 0:
        raise ValueError("interval_seconds debe ser mayor que 0.")

    slug = slugify_name(lesson_slug if lesson_slug is not None else video_path.stem)
    clean_prefix = rel_assets_prefix.replace("\\", "/").strip("/")
    ffmpeg_bin = find_binary("ffmpeg")
    fps_expr = f"fps=1/{cfg.interval_seconds:g}"

    with tempfile.TemporaryDirectory(prefix="course2book_frames_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        raw_pattern = tmp_dir / "raw_%06d.png"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vf",
            fps_expr,
            str(raw_pattern),
        ]
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"código de salida {proc.returncode}"
            raise RuntimeError(
                f"ffmpeg falló al extraer fotogramas de '{video_path.name}': {err_msg}"
            )

        raw_files = sorted(tmp_dir.glob("raw_*.png"))
        if not raw_files:
            return []

        workers = cfg.workers if cfg.workers > 0 else min(8, os.cpu_count() or 4)
        workers = max(1, min(workers, len(raw_files)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            signatures = list(
                executor.map(
                    lambda p: _compute_frame_signature(p, cfg.grid_rows, cfg.grid_cols),
                    raw_files,
                )
            )

        timestamps = [idx * cfg.interval_seconds for idx in range(len(raw_files))]
        selected_indices = _select_settle_frame_indices(signatures, timestamps, cfg)
        if not selected_indices:
            return []

        assets_dir.mkdir(parents=True, exist_ok=True)
        ext = "webp" if cfg.image_format == "webp" else "png"
        tag_counts: dict[str, int] = {}
        extracted: list[ExtractedFrame] = []

        for idx in selected_indices:
            src_path = raw_files[idx]
            ts = timestamps[idx]
            base_tag = format_frame_timestamp_tag(ts)
            count = tag_counts.get(base_tag, 0) + 1
            tag_counts[base_tag] = count
            tag_suffix = base_tag if count == 1 else f"{base_tag}_{count}"

            filename = f"{slug}_{tag_suffix}.{ext}"
            dest_path = assets_dir / filename

            if ext == "png":
                shutil.copy2(src_path, dest_path)
            else:
                with Image.open(src_path) as img:
                    img.save(dest_path, format="WEBP", lossless=True)

            rel_md_path = (
                f"{clean_prefix}/{slug}/{filename}"
                if clean_prefix
                else f"{slug}/{filename}"
            )
            extracted.append(
                ExtractedFrame(
                    path=dest_path,
                    relative_md_path=rel_md_path,
                    timestamp=ts,
                    filename=filename,
                )
            )

    extracted.sort(key=lambda frame: frame.timestamp)
    return extracted


def extract_key_frames(
    video_path: Path,
    assets_dir: Path,
    lesson_slug: str | None = None,
    config: FrameConfig | None = None,
    rel_assets_prefix: str = "assets",
) -> list[ExtractedFrame]:
    """Alias posicional compatible con la interfaz pública `extract_key_frames`."""
    return extract_keyframes(
        video_path=video_path,
        assets_dir=assets_dir,
        config=config,
        lesson_slug=lesson_slug,
        rel_assets_prefix=rel_assets_prefix,
    )

