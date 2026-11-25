"""Pruebas unitarias para el sintetizador por bloques, clientes HTTP y checkpoints."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from course2book.config import ExtractedFrame, LessonArtifacts, MediaInfo, SrtEntry, SynthesisConfig
from course2book.synthesizer import (
    GeminiSynthesizer,
    OllamaSynthesizer,
    OpenAICompatSynthesizer,
    encode_image_for_llm,
    split_into_blocks,
    strip_thinking_tags,
    synthesize_lesson,
)


def _create_png(path: Path, width: int = 800, height: int = 450, color: tuple[int, int, int] = (20, 40, 80)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, format="PNG")
    return path


def test_split_into_blocks_by_duration() -> None:
    # 20 entradas de 1 minuto cada una (20 minutos totales) -> con block_minutes=8.0 deben salir 3 bloques
    entries = [
        SrtEntry(index=i + 1, start=float(i * 60), end=float((i + 1) * 60), text=f"Segmento {i + 1}")
        for i in range(20)
    ]
    frames = [
        ExtractedFrame(
            path=Path(f"assets/lec/frame_{i:02d}.png"),
            relative_md_path=f"assets/lec/frame_{i:02d}.png",
            timestamp=float(i * 120 + 30),
            filename=f"frame_{i:02d}.png",
        )
        for i in range(10)
    ]

    blocks = split_into_blocks(entries, frames, block_minutes=8.0, max_images_per_block=6)

    assert len(blocks) == 3
    assert [b.index for b in blocks] == [1, 2, 3]
    # Todas las entradas y todas las capturas preservadas exactamente una vez
    assert [e.index for b in blocks for e in b.entries] == [e.index for e in entries]
    assert [f.filename for b in blocks for f in b.frames] == [f.filename for f in frames]
    for block in blocks:
        assert (block.end - block.start) <= 8.0 * 60.0 + 1e-6
        assert len(block.frames) <= 6


def test_split_into_blocks_by_max_images() -> None:
    # 15 entradas cortas (10s cada una = 150s < 8 min) con 15 capturas -> cap de 6 imágenes por bloque => 3 bloques
    entries = [
        SrtEntry(index=i + 1, start=float(i * 10), end=float((i + 1) * 10), text=f"Linea {i + 1}")
        for i in range(15)
    ]
    frames = [
        ExtractedFrame(
            path=Path(f"assets/lec/img_{i:02d}.png"),
            relative_md_path=f"assets/lec/img_{i:02d}.png",
            timestamp=float(i * 10 + 5),
            filename=f"img_{i:02d}.png",
        )
        for i in range(15)
    ]

    blocks = split_into_blocks(entries, frames, block_minutes=8.0, max_images_per_block=6)

    assert len(blocks) == 3
    assert all(len(b.frames) <= 6 for b in blocks)
    assert sum(len(b.frames) for b in blocks) == 15
    assert sum(len(b.entries) for b in blocks) == 15


def test_strip_thinking_tags() -> None:
    raw = (
        "<think>\nVoy a analizar el comando nmap y las capturas...\n</think>\n\n"
        "## Escaneo de puertos (⏱️ 00:02:10)\n\n"
        "Usa `nmap -sC -sV 10.10.10.5`.\n"
        "<thinking>detalle interno</thinking>\n\n"
        "> [!warning] Verifica el alcance autorizado."
    )
    cleaned = strip_thinking_tags(raw)

    assert "<think>" not in cleaned
    assert "<thinking>" not in cleaned
    assert "Voy a analizar" not in cleaned
    assert "## Escaneo de puertos (⏱️ 00:02:10)" in cleaned
    assert "`nmap -sC -sV 10.10.10.5`" in cleaned
    assert "> [!warning] Verifica el alcance autorizado." in cleaned


def test_encode_image_for_llm_resizes_in_memory_without_modifying_disk(tmp_path: Path) -> None:
    img_path = _create_png(tmp_path / "screen.png", width=2560, height=1440)

    b64_data, mime_type = encode_image_for_llm(img_path, max_width=1280)

    assert mime_type == "image/png"
    decoded = Image.open(io.BytesIO(base64.b64decode(b64_data)))
    assert decoded.width == 1280
    assert decoded.height == 720

    # El PNG original en disco debe permanecer intacto en 2560x1440
    with Image.open(img_path) as original:
        assert original.size == (2560, 1440)


def test_ollama_synthesizer_sends_think_num_ctx_images_and_hides_thinking(tmp_path: Path) -> None:
    img_path = _create_png(tmp_path / "ghidra.png", width=640, height=360)
    captured_requests: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append((str(request.url), body))
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "thinking": "RAZONAMIENTO PRIVADO QUE NO DEBE FILTRARSE",
                        "content": "<think>otro pensamiento</think>\n## Análisis en Ghidra (⏱️ 00:01:05)\n\nContenido limpio.",
                    }
                },
            )
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = SynthesisConfig(
        engine="ollama",
        model="gemma4:12b",
        base_url="http://localhost:11434",
        vision=True,
        think=True,
        num_ctx=16384,
        keep_alive="0",
    )
    synth = OllamaSynthesizer(cfg, client=client)

    result = synth.generate("SYSTEM", "USER", images=[img_path])
    synth.unload_model()

    assert len(captured_requests) == 2
    chat_url, chat_payload = captured_requests[0]
    assert chat_url == "http://localhost:11434/api/chat"
    assert chat_payload["model"] == "gemma4:12b"
    assert chat_payload["stream"] is False
    assert chat_payload["think"] is True
    assert chat_payload["options"]["num_ctx"] == 16384
    assert len(chat_payload["messages"][-1]["images"]) == 1
    assert isinstance(chat_payload["messages"][-1]["images"][0], str)

    assert "RAZONAMIENTO PRIVADO" not in result
    assert "otro pensamiento" not in result
    assert "## Análisis en Ghidra (⏱️ 00:01:05)" in result

    unload_url, unload_payload = captured_requests[1]
    assert unload_url == "http://localhost:11434/api/generate"
    assert unload_payload == {"model": "gemma4:12b", "keep_alive": 0}


def test_gemini_synthesizer_payload_and_response(tmp_path: Path) -> None:
    img_path = _create_png(tmp_path / "wireshark.png", width=640, height=360)
    captured: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (str(request.url), dict(request.headers), json.loads(request.content.decode("utf-8")))
        )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "pensamiento interno", "thought": True},
                                {"text": "## Captura TLS en Wireshark (⏱️ 00:03:12)\n\nDetalle técnico."},
                            ]
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = SynthesisConfig(
        engine="gemini",
        model="gemini-2.5-pro",
        api_key="test-gemini-key",
        vision=True,
    )
    synth = GeminiSynthesizer(cfg, client=client)

    out = synth.generate("SYSTEM", "USER", images=[img_path])

    assert len(captured) == 1
    url, headers, payload = captured[0]
    assert "models/gemini-2.5-pro:generateContent" in url
    assert "test-gemini-key" not in url
    assert headers.get("x-goog-api-key") == "test-gemini-key"
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"text": "USER"}
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert "pensamiento interno" not in out
    assert "## Captura TLS en Wireshark" in out


def test_openai_compat_synthesizer_payload_and_response(tmp_path: Path) -> None:
    img_path = _create_png(tmp_path / "terminal.png", width=640, height=360)
    captured: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (str(request.url), dict(request.headers), json.loads(request.content.decode("utf-8")))
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "## Uso de gdb y pwndbg (⏱️ 00:04:00)\n\n```bash\ngdb ./vuln\n```",
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = SynthesisConfig(
        engine="openai-compat",
        model="gpt-4o",
        base_url="https://api.example.com",
        api_key="sk-secret",
        vision=True,
    )
    synth = OpenAICompatSynthesizer(cfg, client=client)

    out = synth.generate("SYSTEM", "USER", images=[img_path])

    assert len(captured) == 1
    url, headers, payload = captured[0]
    assert url == "https://api.example.com/v1/chat/completions"
    assert headers.get("authorization") == "Bearer sk-secret"
    user_content = payload["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "## Uso de gdb y pwndbg" in out


def test_synthesize_lesson_multiblock_cleanup_and_resume(tmp_path: Path) -> None:
    output_dir = tmp_path / "curso"
    assets_dir = output_dir / "assets" / "01-stack-frames"
    f1 = _create_png(assets_dir / "frame_001_00m30s.png")
    f2 = _create_png(assets_dir / "frame_002_01m30s.png")
    f3 = _create_png(assets_dir / "frame_003_10m30s.png")

    draft_path = output_dir / "01-stack-frames.draft.md"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text("# Borrador temporal\n", encoding="utf-8")

    artifacts = LessonArtifacts(
        video_path=tmp_path / "01-stack-frames.mp4",
        rel_dir=Path("."),
        srt_path=tmp_path / "01-stack-frames.srt",
        output_dir=output_dir,
        assets_dir=assets_dir,
        draft_md_path=draft_path,
        final_md_path=output_dir / "01-stack-frames.md",
        checkpoint_path=output_dir / ".01-stack-frames.progress.json",
    )

    entries = [
        SrtEntry(index=1, start=0.0, end=60.0, text="Intro to stack frames and RSP/RBP."),
        SrtEntry(index=2, start=60.0, end=120.0, text="Inspecting registers in gdb."),
        SrtEntry(index=3, start=600.0, end=660.0, text="Exploiting buffer overflow safely."),
    ]
    frames = [
        ExtractedFrame(f1, "assets/01-stack-frames/frame_001_00m30s.png", 30.0, f1.name),
        ExtractedFrame(f2, "assets/01-stack-frames/frame_002_01m30s.png", 90.0, f2.name),
        ExtractedFrame(f3, "assets/01-stack-frames/frame_003_10m30s.png", 630.0, f3.name),
    ]
    media_info = MediaInfo(
        path=artifacts.video_path,
        duration=660.0,
        width=1920,
        height=1080,
        has_audio=True,
        has_video=True,
        format_name="mp4",
    )

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        body = json.loads(request.content.decode("utf-8"))
        prompt = body["messages"][-1]["content"]
        if "bloque cronológico 1 de 2" in prompt:
            content = (
                "## Arquitectura de Stack Frames (⏱️ 00:00:00)\n\n"
                "El registro `RSP` apunta al tope de la pila.\n\n"
                "![Disposición de la pila en memoria](assets/01-stack-frames/frame_001_00m30s.png)"
            )
        elif "bloque cronológico 2 de 2" in prompt:
            content = (
                "## Desbordamiento controlado en gdb (⏱️ 00:10:00)\n\n"
                "```bash\ngdb -q ./demo\n```"
            )
        else:
            content = (
                "<!-- OPENING_START -->\n"
                "Esta lección analiza la anatomía completa de un stack frame en x86_64.\n\n"
                "## Objetivos de aprendizaje\n"
                "- Comprender el prólogo y epílogo de función (`PUSH RBP`, `MOV RBP, RSP`).\n"
                "<!-- OPENING_END -->\n\n"
                "<!-- CLOSING_START -->\n"
                "## Práctica Guiada (Manos a la Obra)\n"
                "- [ ] Compila `demo.c` con `-fno-stack-protector` y depura en `gdb`.\n\n"
                "## Preguntas de Repaso\n"
                "> [!question]- ¿Qué función cumple el registro `RBP` en un stack frame clásico?\n"
                "> Actúa como puntero base estable para referenciar variables locales y argumentos.\n"
                "<!-- CLOSING_END -->"
            )
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "thinking": "oculto", "content": content}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = SynthesisConfig(
        engine="ollama",
        model="gemma4:12b",
        block_minutes=8.0,
        max_images_per_block=6,
        auto_cleanup=True,
        resume=False,
    )
    synth = OllamaSynthesizer(cfg, client=client)

    seen_progress: list[tuple[int, int]] = []
    final_path = synthesize_lesson(
        artifacts=artifacts,
        entries=entries,
        frames=frames,
        config=cfg,
        media_info=media_info,
        on_block=lambda idx, total: seen_progress.append((idx, total)),
        synthesizer=synth,
    )

    assert call_count == 3  # 2 bloques + 1 wrapup
    assert seen_progress == [(1, 2), (2, 2)]
    assert final_path == artifacts.final_md_path
    assert final_path.is_file()

    final_md = final_path.read_text(encoding="utf-8")
    assert "language: es" in final_md
    assert 'model: "gemma4:12b"' in final_md
    assert "## Objetivos de aprendizaje" in final_md
    assert "## Arquitectura de Stack Frames (⏱️ 00:00:00)" in final_md
    assert "## Desbordamiento controlado en gdb (⏱️ 00:10:00)" in final_md
    assert "## Práctica Guiada (Manos a la Obra)" in final_md
    assert "> [!question]- ¿Qué función cumple el registro `RBP`" in final_md
    # Markdown estándar estricto: cero wikilinks
    assert "![[" not in final_md
    assert "[[" not in final_md

    # Auto-cleanup: borrador, checkpoint y PNGs no referenciados eliminados; PNG usado conservado
    assert not draft_path.exists()
    assert not artifacts.checkpoint_path.exists()
    assert f1.exists()
    assert not f2.exists()
    assert not f3.exists()

    # --resume sobre capítulo ya finalizado: 0 llamadas LLM
    call_count_before = call_count
    cfg_resume = SynthesisConfig(
        engine="ollama",
        model="gemma4:12b",
        block_minutes=8.0,
        max_images_per_block=6,
        auto_cleanup=True,
        resume=True,
    )
    resumed_path = synthesize_lesson(
        artifacts=artifacts,
        entries=entries,
        frames=frames,
        config=cfg_resume,
        media_info=media_info,
        synthesizer=synth,
    )
    assert resumed_path == final_path
    assert call_count == call_count_before


def test_synthesize_lesson_resumes_from_partial_checkpoint(tmp_path: Path) -> None:
    output_dir = tmp_path / "curso_resume"
    assets_dir = output_dir / "assets" / "02-heap"
    f1 = _create_png(assets_dir / "frame_001_00m20s.png")
    f2 = _create_png(assets_dir / "frame_002_10m20s.png")

    artifacts = LessonArtifacts(
        video_path=tmp_path / "02-heap.mp4",
        rel_dir=Path("."),
        srt_path=tmp_path / "02-heap.srt",
        output_dir=output_dir,
        assets_dir=assets_dir,
        draft_md_path=output_dir / "02-heap.draft.md",
        final_md_path=output_dir / "02-heap.md",
        checkpoint_path=output_dir / ".02-heap.progress.json",
    )
    artifacts.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.checkpoint_path.write_text(
        json.dumps(
            {
                "total_blocks": 2,
                "engine": "ollama",
                "model": "gemma4:12b",
                "blocks": {
                    "1": "## Chunks en glibc malloc (⏱️ 00:00:00)\n\nBloque recuperado del checkpoint."
                },
            }
        ),
        encoding="utf-8",
    )

    entries = [
        SrtEntry(index=1, start=0.0, end=60.0, text="Malloc chunks."),
        SrtEntry(index=2, start=600.0, end=660.0, text="Tcache poisoning."),
    ]
    frames = [
        ExtractedFrame(f1, "assets/02-heap/frame_001_00m20s.png", 20.0, f1.name),
        ExtractedFrame(f2, "assets/02-heap/frame_002_10m20s.png", 620.0, f2.name),
    ]

    prompts_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = body["messages"][-1]["content"]
        prompts_seen.append(prompt)
        if "bloque cronológico 2 de 2" in prompt:
            text = "## Tcache Poisoning (⏱️ 00:10:00)\n\nExplicación exhaustiva del bloque 2."
        else:
            text = (
                "<!-- OPENING_START -->\n## Objetivos de aprendizaje\n- Dominar chunks.\n<!-- OPENING_END -->\n"
                "<!-- CLOSING_START -->\n## Práctica Guiada (Manos a la Obra)\n- [ ] Inspeccionar heap.\n"
                "## Preguntas de Repaso\n> [!question]- ¿Qué es tcache?\n> Caché por hilo en glibc.\n<!-- CLOSING_END -->"
            )
        return httpx.Response(200, json={"message": {"role": "assistant", "content": text}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = SynthesisConfig(engine="ollama", model="gemma4:12b", auto_cleanup=True, resume=True)
    synth = OllamaSynthesizer(cfg, client=client)

    final_path = synthesize_lesson(
        artifacts=artifacts,
        entries=entries,
        frames=frames,
        config=cfg,
        synthesizer=synth,
    )

    # Solo debió invocar al LLM para bloque 2 + wrapup (NO para el bloque 1 ya en checkpoint)
    assert len(prompts_seen) == 2
    assert "bloque cronológico 2 de 2" in prompts_seen[0]
    assert "Chunks en glibc malloc" in prompts_seen[0]  # Continuidad de encabezados preservada

    text = final_path.read_text(encoding="utf-8")
    assert "Bloque recuperado del checkpoint." in text
    assert "## Tcache Poisoning (⏱️ 00:10:00)" in text
    assert not artifacts.checkpoint_path.exists()


def test_synthesize_lesson_english_target_language(tmp_path: Path) -> None:
    from course2book.config import SrtEntry
    from course2book.obsidian import build_lesson_artifacts
    from course2book.synthesizer import BaseSynthesizer, synthesize_lesson

    captured_prompts: list[tuple[str, str]] = []

    class _RecordingSynth(BaseSynthesizer):
        def generate(self, system_prompt: str, user_prompt: str, images: list[Path] | None = None) -> str:
            captured_prompts.append((system_prompt, user_prompt))
            if "OPENING_START" in user_prompt:
                return (
                    "<!-- OPENING_START -->\n"
                    "## Learning Objectives\n- Run `gdb`.\n"
                    "<!-- OPENING_END -->\n"
                    "<!-- CLOSING_START -->\n"
                    "## Hands-On Practice\n- [ ] Run `gdb ./app`\n"
                    "<!-- CLOSING_END -->"
                )
            return "## Debugging Basics (⏱️ 00:00:10)\n\nUse `gdb ./app`."

    output_root = tmp_path / "book_en"
    artifacts = build_lesson_artifacts(Path("01_gdb.mp4"), Path("."), output_root)
    entries = [SrtEntry(index=1, start=10.0, end=25.0, text="Run gdb on the binary.")]
    cfg = SynthesisConfig(
        engine="ollama",
        model="gemma4:12b",
        vision=False,
        auto_cleanup=False,
        target_language="en",
    )

    final_path = synthesize_lesson(
        artifacts=artifacts,
        entries=entries,
        frames=[],
        config=cfg,
        synthesizer=_RecordingSynth(cfg),
    )

    text = final_path.read_text(encoding="utf-8")
    assert "language: en" in text
    assert "> [!info] Lesson metadata" in text
    assert "**Source video:**" in text
    assert "  - course" in text
    assert len(captured_prompts) == 2
    assert "clear professional technical English" in captured_prompts[0][0]
    assert "chronological block 1 of 1" in captured_prompts[0][1]
