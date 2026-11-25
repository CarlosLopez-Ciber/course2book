"""Interfaz de línea de comandos Typer para course2book."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from course2book.config import (
    DOMAIN_PROMPTS,
    BookLanguage,
    DomainProfile,
    EngineType,
    FrameConfig,
    LessonArtifacts,
    MediaInfo,
    SynthesisConfig,
    WhisperConfig,
)
from course2book.cuda_env import register_cuda_dlls
from course2book.frames import extract_key_frames, slugify_name
from course2book.media import (
    discover_resources,
    discover_videos,
    find_binary,
    find_vlc,
    open_in_vlc,
    probe_media,
)
from course2book.obsidian import (
    build_lesson_artifacts,
    cleanup_directory,
    cleanup_lesson_artifacts,
    generate_course_index,
    write_draft_markdown,
)
from course2book.synthesizer import (
    create_synthesizer,
    parse_draft_for_synthesis,
    synthesize_lesson,
)
from course2book.transcriber import WhisperTranscriber, parse_srt

app = typer.Typer(
    name="course2book",
    help=(
        "Convierte cursos técnicos en video a subtítulos .srt (faster-whisper CUDA) "
        "y libros prácticos exhaustivos en español para Obsidian."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


def _validate_domain(domain: str) -> DomainProfile:
    normalized = domain.lower().strip()
    if normalized not in DOMAIN_PROMPTS:
        valid = ", ".join(DOMAIN_PROMPTS.keys())
        raise typer.BadParameter(f"Dominio inválido '{domain}'. Opciones válidas: {valid}")
    return cast(DomainProfile, normalized)


def _validate_engine(engine: str, allow_agent: bool = True) -> EngineType:
    normalized = engine.lower().strip()
    allowed = {"agent", "ollama", "gemini", "openai-compat"} if allow_agent else {"ollama", "gemini", "openai-compat"}
    if normalized not in allowed:
        raise typer.BadParameter(
            f"Motor inválido '{engine}'. Opciones válidas: {', '.join(sorted(allowed))}"
        )
    return cast(EngineType, normalized)


def _validate_book_lang(book_lang: str) -> BookLanguage:
    normalized = book_lang.lower().strip()
    if normalized not in {"es", "en"}:
        raise typer.BadParameter(f"Idioma de libro inválido '{book_lang}'. Opciones válidas: es, en")
    return cast(BookLanguage, normalized)


def _normalize_language(language: str | None) -> str | None:
    if language is None:
        return None
    cleaned = language.strip().lower()
    if not cleaned or cleaned == "auto":
        return None
    return cleaned


def _print_agent_instructions(output_dir: Path) -> None:
    resolved = output_dir.resolve()
    text = (
        f"[bold green]Borradores y capturas listos en:[/bold green] `{resolved}`\n\n"
        "[bold cyan]Opción A — Antigravity (skill `course-to-book`):[/bold cyan]\n"
        f"  Pídele al agente: «Usa la skill `course-to-book` para redactar los capítulos "
        f"exhaustivos en español a partir de los `.draft.md` en `{resolved}`».\n\n"
        "[bold cyan]Opción B — OpenCode (`AGENTS.md`):[/bold cyan]\n"
        "  Abre el repositorio `course2book` en OpenCode y pídele redactar los `.draft.md` "
        f"de `{resolved}` siguiendo `AGENTS.md`.\n\n"
        "[bold yellow]Limpieza final obligatoria:[/bold yellow]\n"
        f"  `uv run course2book cleanup \"{resolved}\"`"
    )
    console.print(Panel(text, title="Siguiente paso: Redacción con Agente", border_style="cyan"))


def _print_cleanup_summary(target: Path, stats: dict[str, int]) -> None:
    table = Table(title=f"Resumen de limpieza — {target.name}")
    table.add_column("Categoría", style="cyan")
    table.add_column("Cantidad", justify="right", style="green")
    for key, val in stats.items():
        table.add_row(str(key), str(val))
    console.print(table)


@app.command("check-env")
def check_env(
    ollama_url: Annotated[
        str,
        typer.Option("--ollama-url", help="URL base del servidor Ollama local."),
    ] = "http://localhost:11434",
) -> None:
    """Verifica FFmpeg, FFprobe, VLC, CUDA/CTranslate2 y Ollama (`gemma4:12b`)."""
    table = Table(title="Diagnóstico de entorno — course2book")
    table.add_column("Componente", style="bold cyan")
    table.add_column("Estado", style="bold")
    table.add_column("Detalle")

    # 1. FFmpeg & FFprobe
    for binary in ("ffmpeg", "ffprobe"):
        try:
            bin_path = find_binary(binary)
            table.add_row(binary, "[green]OK[/green]", bin_path)
        except Exception as exc:
            table.add_row(binary, "[red]FALTA[/red]", str(exc))

    # 2. VLC
    vlc_path = find_vlc()
    if vlc_path:
        table.add_row("VLC", "[green]OK[/green]", vlc_path)
    else:
        table.add_row("VLC", "[yellow]NO ENCONTRADO[/yellow]", "Opcional para `course2book open`")

    # 3. CUDA DLLs + CTranslate2
    dll_dirs = register_cuda_dlls()
    dll_note = f"{len(dll_dirs)} carpetas DLL registradas" if dll_dirs else "Sin carpetas NVIDIA en site-packages"
    try:
        import ctranslate2  # type: ignore[import-untyped]

        cuda_count = int(ctranslate2.get_cuda_device_count())
        if cuda_count > 0:
            table.add_row("CUDA / CTranslate2", "[green]OK[/green]", f"{cuda_count} GPU(s) CUDA — {dll_note}")
        else:
            table.add_row("CUDA / CTranslate2", "[yellow]SIN GPU[/yellow]", f"0 GPUs CUDA detectadas ({dll_note})")
    except Exception as exc:
        table.add_row("CUDA / CTranslate2", "[red]ERROR[/red]", f"{exc} ({dll_note})")

    # 4. Ollama + gemma4:12b
    try:
        resp = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in (resp.json().get("models") or []) if m.get("name")]
        has_gemma = any("gemma4:12b" in m for m in models)
        if models:
            formatted = ", ".join(
                f"[bold green]{m}[/bold green]" if "gemma4:12b" in m else m for m in models
            )
        else:
            formatted = "(sin modelos descargados)"
        if has_gemma:
            table.add_row("Ollama", "[green]OK[/green]", f"gemma4:12b listo — {formatted}")
        else:
            table.add_row(
                "Ollama",
                "[yellow]SIN gemma4:12b[/yellow]",
                f"Ejecuta `ollama pull gemma4:12b` — Instalados: {formatted}",
            )
    except Exception as exc:
        table.add_row("Ollama", "[yellow]INACTIVO[/yellow]", f"No responde en {ollama_url} ({exc})")

    console.print(table)


@app.command("transcribe")
def transcribe_cmd(
    path: Annotated[Path, typer.Argument(exists=True, help="Archivo de video o carpeta raíz del curso.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Carpeta o archivo .srt de salida (por defecto junto a cada video)."),
    ] = None,
    model: Annotated[str, typer.Option("--model", help="Modelo faster-whisper.")] = "large-v3",
    compute_type: Annotated[
        str, typer.Option("--compute-type", help="Precisión CTranslate2 (ej. int8_float16).")
    ] = "int8_float16",
    device: Annotated[str, typer.Option("--device", help="Dispositivo de ejecución (cuda o cpu).")] = "cuda",
    language: Annotated[
        str | None, typer.Option("--language", help="Idioma del audio ('en', 'es' o 'auto').")
    ] = "en",
    domain: Annotated[
        str, typer.Option("--domain", help="Perfil técnico para initial_prompt (it, reveng, cybersec, dev, infra, none).")
    ] = "it",
    glossary: Annotated[
        str, typer.Option("--glossary", help="Glosario extra separado por comas.")
    ] = "",
    resume: Annotated[
        bool, typer.Option("--resume/--no-resume", help="Reutiliza .srt existentes si ya fueron generados.")
    ] = False,
    probe_all: Annotated[
        bool, typer.Option("--probe-all/--no-probe-all", help="Inspecciona archivos sin extensión estándar vía ffprobe.")
    ] = False,
) -> None:
    """Transcribe videos en GPU con faster-whisper, limpiando subtítulos previos y guardando solo .srt."""
    domain_profile = _validate_domain(domain)
    videos = discover_videos(path, probe_all=probe_all)
    if not videos:
        console.print(f"[yellow]No se encontraron videos en:[/yellow] {path}")
        raise typer.Exit(code=1)

    whisper_cfg = WhisperConfig(
        model_size=model,
        device=device,
        compute_type=compute_type,
        language=_normalize_language(language),
        domain=domain_profile,
        glossary=glossary,
        clean_existing_subtitles=True,
        resume=resume,
    )

    transcriber = WhisperTranscriber(whisper_cfg)
    generated: list[Path] = []
    try:
        with console.status(f"[bold cyan]Cargando faster-whisper ({model}, {compute_type}, {device})...[/bold cyan]"):
            transcriber.load()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Transcribiendo videos", total=len(videos))
            for video_path, rel_dir in videos:
                progress.update(task, description=f"Transcribiendo: {video_path.name}")
                out_srt: Path | None = None
                if output is not None:
                    if len(videos) == 1 and output.suffix.lower() == ".srt":
                        out_srt = output
                    else:
                        out_srt = output / rel_dir / f"{video_path.stem}.srt"
                srt_path = transcriber.transcribe_video(video_path, output_srt_path=out_srt)
                generated.append(srt_path)
                progress.advance(task)
    finally:
        transcriber.unload()

    console.print(f"[bold green]Transcripción completada:[/bold green] {len(generated)} archivo(s) .srt listos.")


@app.command("frames")
def frames_cmd(
    path: Annotated[Path, typer.Argument(exists=True, help="Archivo de video o carpeta del curso.")],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Carpeta destino donde crear los assets.")
    ],
    interval: Annotated[float, typer.Option("--interval", help="Intervalo de muestreo en segundos.")] = 2.0,
    phash_threshold: Annotated[
        int, typer.Option("--phash-threshold", help="Umbral de distancia pHash para detectar cambios.")
    ] = 6,
    min_interval: Annotated[
        float, typer.Option("--min-interval", help="Separación mínima en segundos entre capturas.")
    ] = 4.0,
    max_frames: Annotated[
        int, typer.Option("--max-frames", help="Límite máximo de capturas por video.")
    ] = 120,
    image_format: Annotated[
        str, typer.Option("--format", help="Formato de imagen (png o webp).")
    ] = "png",
    ignore_pip: Annotated[
        bool, typer.Option("--ignore-pip/--no-ignore-pip", help="Ignora webcam PiP en esquinas al detectar cambios.")
    ] = True,
    probe_all: Annotated[
        bool, typer.Option("--probe-all/--no-probe-all", help="Inspecciona archivos atípicos vía ffprobe.")
    ] = False,
) -> None:
    """Extrae capturas PNG/WebP deduplicadas afinadas para cursos técnicos de TI."""
    fmt = image_format.lower().strip()
    if fmt not in {"png", "webp"}:
        raise typer.BadParameter("El formato debe ser 'png' o 'webp'.")

    videos = discover_videos(path, probe_all=probe_all)
    if not videos:
        console.print(f"[yellow]No se encontraron videos en:[/yellow] {path}")
        raise typer.Exit(code=1)

    frame_cfg = FrameConfig(
        interval_seconds=interval,
        phash_threshold=phash_threshold,
        min_interval_seconds=min_interval,
        max_frames=max_frames,
        image_format=cast(str, fmt),  # type: ignore[arg-type]
        ignore_corner_pip=ignore_pip,
    )

    table = Table(title="Extracción de capturas TI")
    table.add_column("Video", style="cyan")
    table.add_column("Capturas", justify="right", style="green")
    table.add_column("Carpeta assets")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Extrayendo capturas", total=len(videos))
        for video_path, rel_dir in videos:
            progress.update(task, description=f"Analizando: {video_path.name}")
            artifacts = build_lesson_artifacts(video_path, rel_dir, output)
            lesson_slug = slugify_name(video_path.stem)
            extracted = extract_key_frames(video_path, artifacts.assets_dir, lesson_slug, frame_cfg)
            table.add_row(video_path.name, str(len(extracted)), str(artifacts.assets_dir))
            progress.advance(task)

    console.print(table)


def _run_prepare_phases(
    input_path: Path,
    output_root: Path,
    whisper_cfg: WhisperConfig,
    frame_cfg: FrameConfig,
    course_title: str | None,
    skip_transcribe: bool,
    probe_all: bool,
    skip_existing_final: bool = False,
    target_language: BookLanguage = "es",
) -> list[tuple[LessonArtifacts, list, list, MediaInfo | None]]:
    """Ejecuta Fase 1 (GPU Whisper + unload) y Fase 2 (CPU Frames + Drafts + 00_Indice.md)."""
    videos = discover_videos(input_path, probe_all=probe_all)
    if not videos:
        console.print(f"[yellow]No se encontraron videos en:[/yellow] {input_path}")
        raise typer.Exit(code=1)

    input_root = input_path if input_path.is_dir() else input_path.parent
    resolved_title = course_title or (input_path.name if input_path.is_dir() else input_path.stem)

    # FASE 1 (GPU): Transcripción Whisper -> descarga explícita de VRAM
    srt_map: dict[Path, Path] = {}
    if not skip_transcribe:
        pending_videos = [
            vp
            for vp, _ in videos
            if not (whisper_cfg.resume and vp.with_suffix(".srt").is_file())
        ]
        if pending_videos:
            console.rule("[bold cyan]Fase 1 (GPU): Transcripción con faster-whisper[/bold cyan]")
            transcriber = WhisperTranscriber(whisper_cfg)
            try:
                with console.status(
                    f"[cyan]Cargando faster-whisper ({whisper_cfg.model_size}, {whisper_cfg.compute_type})...[/cyan]"
                ):
                    transcriber.load()

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeElapsedColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("Transcribiendo", total=len(pending_videos))
                    for vp in pending_videos:
                        progress.update(task, description=f"Whisper: {vp.name}")
                        srt_map[vp] = transcriber.transcribe_video(vp, output_srt_path=None)
                        progress.advance(task)
            finally:
                transcriber.unload()
                console.print("[dim]VRAM liberada tras finalizar Whisper.[/dim]")
        else:
            console.print("[green]Fase 1 omitida:[/green] todos los subtítulos .srt ya existen (--resume).")

    for vp, _ in videos:
        if vp not in srt_map:
            srt_map[vp] = vp.with_suffix(".srt")

    # FASE 2 (CPU): Extracción de capturas TI + borradores Markdown + índice
    console.rule("[bold cyan]Fase 2 (CPU): Capturas TI y borradores Markdown[/bold cyan]")
    prepared: list[tuple[LessonArtifacts, list, list, MediaInfo | None]] = []
    all_artifacts: list[LessonArtifacts] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Preparando lecciones", total=len(videos))
        for video_path, rel_dir in videos:
            progress.update(task, description=f"Preparando: {video_path.name}")
            srt_path = srt_map.get(video_path)
            artifacts = build_lesson_artifacts(video_path, rel_dir, output_root, srt_path=srt_path)
            all_artifacts.append(artifacts)

            if (
                skip_existing_final
                and artifacts.final_md_path.is_file()
                and artifacts.final_md_path.stat().st_size > 100
            ):
                prepared.append((artifacts, [], [], None))
                progress.advance(task)
                continue

            entries = parse_srt(artifacts.srt_path) if artifacts.srt_path.is_file() else []
            try:
                media_info: MediaInfo | None = probe_media(video_path)
            except Exception:
                media_info = None

            lesson_slug = slugify_name(video_path.stem)
            frames = extract_key_frames(video_path, artifacts.assets_dir, lesson_slug, frame_cfg)
            artifacts.frames = frames
            write_draft_markdown(artifacts, entries, frames, media_info=media_info)
            prepared.append((artifacts, entries, frames, media_info))
            progress.advance(task)

    resources = discover_resources(input_root) if input_root.is_dir() else None
    index_path = generate_course_index(
        output_root,
        resolved_title,
        all_artifacts,
        resources=resources,
        target_language=target_language,
    )
    console.print(f"[green]Índice generado:[/green] {index_path}")
    return prepared


@app.command("prepare")
def prepare_cmd(
    path: Annotated[Path, typer.Argument(exists=True, help="Archivo de video o carpeta del curso.")],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Carpeta raíz del libro en Obsidian.")
    ],
    model: Annotated[str, typer.Option("--model", help="Modelo faster-whisper.")] = "large-v3",
    compute_type: Annotated[str, typer.Option("--compute-type")] = "int8_float16",
    device: Annotated[str, typer.Option("--device")] = "cuda",
    language: Annotated[str | None, typer.Option("--language")] = "en",
    domain: Annotated[str, typer.Option("--domain")] = "it",
    glossary: Annotated[str, typer.Option("--glossary")] = "",
    interval: Annotated[float, typer.Option("--interval")] = 2.0,
    phash_threshold: Annotated[int, typer.Option("--phash-threshold")] = 6,
    min_interval: Annotated[float, typer.Option("--min-interval")] = 4.0,
    max_frames: Annotated[int, typer.Option("--max-frames")] = 120,
    image_format: Annotated[str, typer.Option("--format")] = "png",
    ignore_pip: Annotated[bool, typer.Option("--ignore-pip/--no-ignore-pip")] = True,
    title: Annotated[str | None, typer.Option("--title", help="Título del curso para el índice.")] = None,
    book_lang: Annotated[
        str, typer.Option("--book-lang", help="Idioma del libro y del índice: es o en.")
    ] = "es",
    skip_transcribe: Annotated[
        bool, typer.Option("--skip-transcribe", help="Omite Whisper y usa .srt ya existentes.")
    ] = False,
    resume: Annotated[bool, typer.Option("--resume/--no-resume")] = False,
    probe_all: Annotated[bool, typer.Option("--probe-all/--no-probe-all")] = False,
) -> None:
    """Ejecuta Fase 1 (Whisper .srt) + Fase 2 (Capturas PNG + .draft.md + índice)."""
    domain_profile = _validate_domain(domain)
    target_lang = _validate_book_lang(book_lang)
    fmt = image_format.lower().strip()
    if fmt not in {"png", "webp"}:
        raise typer.BadParameter("El formato debe ser 'png' o 'webp'.")

    whisper_cfg = WhisperConfig(
        model_size=model,
        device=device,
        compute_type=compute_type,
        language=_normalize_language(language),
        domain=domain_profile,
        glossary=glossary,
        clean_existing_subtitles=True,
        resume=resume,
    )
    frame_cfg = FrameConfig(
        interval_seconds=interval,
        phash_threshold=phash_threshold,
        min_interval_seconds=min_interval,
        max_frames=max_frames,
        image_format=cast(str, fmt),  # type: ignore[arg-type]
        ignore_corner_pip=ignore_pip,
    )

    _run_prepare_phases(
        input_path=path,
        output_root=output,
        whisper_cfg=whisper_cfg,
        frame_cfg=frame_cfg,
        course_title=title,
        skip_transcribe=skip_transcribe,
        probe_all=probe_all,
        target_language=target_lang,
    )
    _print_agent_instructions(output)


@app.command("synthesize")
def synthesize_cmd(
    target: Annotated[
        Path, typer.Argument(exists=True, help="Archivo `.draft.md` o carpeta con borradores.")
    ],
    engine: Annotated[
        str, typer.Option("--engine", help="Motor de síntesis: ollama, gemini o openai-compat.")
    ] = "ollama",
    model: Annotated[str, typer.Option("--model", help="Modelo LLM multimodal.")] = "gemma4:12b",
    base_url: Annotated[
        str, typer.Option("--base-url", help="URL base del servidor Ollama u OpenAI-compat.")
    ] = "http://localhost:11434",
    api_key: Annotated[str | None, typer.Option("--api-key", help="Clave API (Gemini/OpenAI-compat).")] = None,
    vision: Annotated[bool, typer.Option("--vision/--no-vision", help="Adjunta capturas PNG al modelo.")] = True,
    think: Annotated[bool, typer.Option("--think/--no-think", help="Activa modo razonamiento en Ollama.")] = True,
    num_ctx: Annotated[int, typer.Option("--num-ctx", help="Ventana de contexto para Ollama.")] = 16384,
    block_minutes: Annotated[float, typer.Option("--block-minutes", help="Duración máxima por bloque.")] = 8.0,
    max_images_per_block: Annotated[
        int, typer.Option("--max-images-per-block", help="Máximo de capturas adjuntas por bloque.")
    ] = 6,
    cleanup: Annotated[
        bool, typer.Option("--cleanup/--no-cleanup", help="Elimina .draft.md, checkpoint y PNGs no usados al terminar.")
    ] = True,
    resume: Annotated[
        bool, typer.Option("--resume/--no-resume", help="Reanuda desde checkpoints o salta capítulos ya completados.")
    ] = False,
    title: Annotated[str | None, typer.Option("--title", help="Título del curso para actualizar el índice.")] = None,
    book_lang: Annotated[
        str, typer.Option("--book-lang", help="Idioma de salida del libro: es o en.")
    ] = "es",
) -> None:
    """Sintetiza borradores `.draft.md` en capítulos exhaustivos (español o inglés)."""
    engine_type = _validate_engine(engine, allow_agent=False)
    target_lang = _validate_book_lang(book_lang)
    draft_files = [target] if target.is_file() else sorted(target.rglob("*.draft.md"))
    if not draft_files:
        console.print(f"[yellow]No se encontraron archivos `.draft.md` en:[/yellow] {target}")
        raise typer.Exit(code=1)

    synth_cfg = SynthesisConfig(
        engine=engine_type,
        model=model,
        base_url=base_url,
        api_key=api_key,
        vision=vision,
        think=think,
        num_ctx=num_ctx,
        block_minutes=block_minutes,
        max_images_per_block=max_images_per_block,
        auto_cleanup=cleanup,
        resume=resume,
        target_language=target_lang,
    )

    processed_artifacts: list[LessonArtifacts] = []
    synthesizer = create_synthesizer(synth_cfg)
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            overall_task = progress.add_task("Sintetizando capítulos", total=len(draft_files))
            for draft_path in draft_files:
                artifacts, entries, frames = parse_draft_for_synthesis(draft_path)
                processed_artifacts.append(artifacts)
                block_task = progress.add_task(f"  {artifacts.final_md_path.name}", total=1)

                def _on_block(idx: int, total: int, tid: int = block_task) -> None:
                    progress.update(tid, total=max(1, total), completed=idx)

                final_path = synthesize_lesson(
                    artifacts=artifacts,
                    entries=entries,
                    frames=frames,
                    config=synth_cfg,
                    on_block=_on_block,
                    synthesizer=synthesizer,
                )
                progress.update(block_task, visible=False)
                console.print(f"[green]Capítulo listo:[/green] {final_path}")
                progress.advance(overall_task)
    finally:
        synthesizer.unload_model()
        synthesizer.close()

    if target.is_dir():
        course_title = title or target.name
        index_path = generate_course_index(
            target, course_title, processed_artifacts, target_language=target_lang
        )
        console.print(f"[green]Índice actualizado:[/green] {index_path}")


@app.command("build")
def build_cmd(
    path: Annotated[Path, typer.Argument(exists=True, help="Archivo de video o carpeta raíz del curso.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Carpeta raíz del libro en Obsidian.")],
    engine: Annotated[
        str, typer.Option("--engine", help="Motor de síntesis: ollama, agent, gemini u openai-compat.")
    ] = "ollama",
    whisper_model: Annotated[str, typer.Option("--whisper-model", help="Modelo faster-whisper.")] = "large-v3",
    compute_type: Annotated[str, typer.Option("--compute-type")] = "int8_float16",
    device: Annotated[str, typer.Option("--device")] = "cuda",
    language: Annotated[str | None, typer.Option("--language")] = "en",
    domain: Annotated[str, typer.Option("--domain")] = "it",
    glossary: Annotated[str, typer.Option("--glossary")] = "",
    skip_transcribe: Annotated[bool, typer.Option("--skip-transcribe")] = False,
    probe_all: Annotated[bool, typer.Option("--probe-all/--no-probe-all")] = False,
    interval: Annotated[float, typer.Option("--interval")] = 2.0,
    phash_threshold: Annotated[int, typer.Option("--phash-threshold")] = 6,
    min_interval: Annotated[float, typer.Option("--min-interval")] = 4.0,
    max_frames: Annotated[int, typer.Option("--max-frames")] = 120,
    image_format: Annotated[str, typer.Option("--format")] = "png",
    ignore_pip: Annotated[bool, typer.Option("--ignore-pip/--no-ignore-pip")] = True,
    model: Annotated[str, typer.Option("--model", help="Modelo LLM para Fase 3.")] = "gemma4:12b",
    base_url: Annotated[str, typer.Option("--base-url")] = "http://localhost:11434",
    api_key: Annotated[str | None, typer.Option("--api-key")] = None,
    vision: Annotated[bool, typer.Option("--vision/--no-vision")] = True,
    think: Annotated[bool, typer.Option("--think/--no-think")] = True,
    num_ctx: Annotated[int, typer.Option("--num-ctx")] = 16384,
    block_minutes: Annotated[float, typer.Option("--block-minutes")] = 8.0,
    max_images_per_block: Annotated[int, typer.Option("--max-images-per-block")] = 6,
    cleanup: Annotated[bool, typer.Option("--cleanup/--no-cleanup")] = True,
    resume: Annotated[bool, typer.Option("--resume/--no-resume")] = False,
    title: Annotated[str | None, typer.Option("--title")] = None,
    book_lang: Annotated[
        str, typer.Option("--book-lang", help="Idioma de salida del libro: es o en.")
    ] = "es",
) -> None:
    """Pipeline completo por fases: GPU Whisper -> CPU Frames/Drafts -> Síntesis + Limpieza."""
    domain_profile = _validate_domain(domain)
    engine_type = _validate_engine(engine, allow_agent=True)
    target_lang = _validate_book_lang(book_lang)
    fmt = image_format.lower().strip()
    if fmt not in {"png", "webp"}:
        raise typer.BadParameter("El formato debe ser 'png' o 'webp'.")

    whisper_cfg = WhisperConfig(
        model_size=whisper_model,
        device=device,
        compute_type=compute_type,
        language=_normalize_language(language),
        domain=domain_profile,
        glossary=glossary,
        clean_existing_subtitles=True,
        resume=resume,
    )
    frame_cfg = FrameConfig(
        interval_seconds=interval,
        phash_threshold=phash_threshold,
        min_interval_seconds=min_interval,
        max_frames=max_frames,
        image_format=cast(str, fmt),  # type: ignore[arg-type]
        ignore_corner_pip=ignore_pip,
    )

    prepared = _run_prepare_phases(
        input_path=path,
        output_root=output,
        whisper_cfg=whisper_cfg,
        frame_cfg=frame_cfg,
        course_title=title,
        skip_transcribe=skip_transcribe,
        probe_all=probe_all,
        skip_existing_final=resume,
        target_language=target_lang,
    )

    if engine_type == "agent":
        _print_agent_instructions(output)
        return

    console.rule(f"[bold cyan]Fase 3 (Síntesis): {engine_type} / {model}[/bold cyan]")
    synth_cfg = SynthesisConfig(
        engine=engine_type,
        model=model,
        base_url=base_url,
        api_key=api_key,
        vision=vision,
        think=think,
        num_ctx=num_ctx,
        block_minutes=block_minutes,
        max_images_per_block=max_images_per_block,
        auto_cleanup=cleanup,
        resume=resume,
        target_language=target_lang,
    )

    synthesizer = create_synthesizer(synth_cfg)
    all_artifacts = [item[0] for item in prepared]
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            overall = progress.add_task("Sintetizando curso", total=len(prepared))
            for artifacts, entries, frames, media_info in prepared:
                block_task = progress.add_task(f"  {artifacts.final_md_path.name}", total=1)

                def _on_block(idx: int, total: int, tid: int = block_task) -> None:
                    progress.update(tid, total=max(1, total), completed=idx)

                final_path = synthesize_lesson(
                    artifacts=artifacts,
                    entries=entries,
                    frames=frames,
                    config=synth_cfg,
                    media_info=media_info,
                    on_block=_on_block,
                    synthesizer=synthesizer,
                )
                progress.update(block_task, visible=False)
                console.print(f"[green]Capítulo completado:[/green] {final_path}")
                progress.advance(overall)
    finally:
        synthesizer.unload_model()
        synthesizer.close()

    input_root = path if path.is_dir() else path.parent
    resolved_title = title or (path.name if path.is_dir() else path.stem)
    resources = discover_resources(input_root) if input_root.is_dir() else None
    index_path = generate_course_index(
        output, resolved_title, all_artifacts, resources=resources, target_language=target_lang
    )
    console.print(f"[bold green]Libro técnico completado.[/bold green] Índice: {index_path}")


@app.command("cleanup")
def cleanup_cmd(
    target: Annotated[
        Path,
        typer.Argument(exists=True, help="Archivo `.md` final de un capítulo o carpeta del curso."),
    ],
) -> None:
    """Elimina capturas PNG no referenciadas, `.draft.md` y checkpoints `.progress.json`."""
    if target.is_file():
        stem = target.stem
        if target.name.endswith(".draft.md"):
            stem = target.name[: -len(".draft.md")]
        final_md = target.with_name(f"{stem}.md")
        draft_md = target.with_name(f"{stem}.draft.md")
        lesson_slug = slugify_name(stem)
        assets_dir = target.parent / "assets" / lesson_slug
        if not assets_dir.is_dir():
            assets_dir = target.parent / "assets"
        checkpoint = target.with_name(f".{stem}.progress.json")
        if not checkpoint.is_file():
            alt_cp = target.with_name(f"{stem}.progress.json")
            if alt_cp.is_file():
                checkpoint = alt_cp
        stats = cleanup_lesson_artifacts(
            final_md,
            assets_dir=assets_dir if assets_dir.is_dir() else None,
            draft_md_path=draft_md if draft_md.is_file() else None,
            checkpoint_path=checkpoint if checkpoint.is_file() else None,
        )
    else:
        stats = cleanup_directory(target)

    _print_cleanup_summary(target, stats)


@app.command("open")
def open_cmd(
    video: Annotated[Path, typer.Argument(exists=True, help="Ruta del archivo de video.")],
    timestamp: Annotated[
        str, typer.Argument(help="Marca de tiempo (HH:MM:SS, MM:SS o segundos).")
    ] = "00:00:00",
) -> None:
    """Abre el video en VLC directamente en la marca de tiempo indicada."""
    try:
        open_in_vlc(video, timestamp)
    except Exception as exc:
        console.print(f"[red]No se pudo abrir VLC:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Abriendo en VLC:[/green] {video.name} @ [bold]{timestamp}[/bold]")


if __name__ == "__main__":
    app()
