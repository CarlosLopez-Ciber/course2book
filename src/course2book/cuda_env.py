"""Registro automático de DLLs de CUDA 12 / cuDNN 9 en Windows para faster-whisper / CTranslate2."""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path

_DLL_HANDLES: list[object] = []
_REGISTERED = False


def register_cuda_dlls() -> list[Path]:
    """Registra las carpetas `bin` de los wheels NVIDIA en site-packages antes de importar CTranslate2.

    En Windows, `ctranslate2` necesita localizar `cublas64_12.dll`, `cublasLt64_12.dll`,
    `cudart64_12.dll` y `cudnn64_9.dll`. Registrar estas carpetas mediante
    `os.add_dll_directory()` y prefijarlas en `PATH` evita copias manuales de DLLs
    y sobrevive a cualquier `uv sync`.
    """
    global _REGISTERED
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if sys.platform != "win32":
        return []

    added: list[Path] = []
    nvidia_subdirs = ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc")

    candidate_roots: list[Path] = []
    for sp in site.getsitepackages():
        candidate_roots.append(Path(sp))

    # También inspeccionar sys.prefix por si el entorno virtual se ejecuta sin site.getsitepackages completo
    venv_sp = Path(sys.prefix) / "Lib" / "site-packages"
    if venv_sp not in candidate_roots:
        candidate_roots.append(venv_sp)

    seen: set[Path] = set()
    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []

    for sp_root in candidate_roots:
        nvidia_root = sp_root / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for sub in nvidia_subdirs:
            bin_dir = (nvidia_root / sub / "bin").resolve()
            if bin_dir.is_dir() and bin_dir not in seen:
                seen.add(bin_dir)
                added.append(bin_dir)
                if not _REGISTERED:
                    try:
                        _DLL_HANDLES.append(os.add_dll_directory(str(bin_dir)))
                    except OSError:
                        pass
                bin_str = str(bin_dir)
                if bin_str not in path_parts:
                    path_parts.insert(0, bin_str)

    if added:
        os.environ["PATH"] = os.pathsep.join(path_parts)
        _REGISTERED = True

    return added
