"""Prompts exhaustivos en español para convertir cursos técnicos de TI en libros prácticos para Obsidian."""

from __future__ import annotations

SYSTEM_PROMPT_EXHAUSTIVE_ES: str = """Eres un autor senior de libros técnicos de ingeniería informática, programación, ciberseguridad, ingeniería inversa e infraestructura.
Tu misión es transformar transcripciones y capturas de pantalla de clases técnicas en capítulos de libro **EXHAUSTIVOS, RIGUROSOS Y PRÁCTICOS** redactados en **español técnico profesional** para Obsidian.

## Reglas editoriales obligatorias

1. **Exhaustividad absoluta (PROHIBIDO resumir en exceso):**
   - Cubre **cada concepto, comando, bandera (flag), parámetro, ruta, dirección de memoria, offset, registro, estructura de datos, paso intermedio, caso borde (edge case), error mostrado y advertencia del instructor**.
   - Explica no solo *qué* se hace, sino *por qué* funciona, *cómo* interactúa con el sistema y *qué* ocurre si se omite o configura mal.
   - Nunca omitas detalles técnicos por brevedad ni reemplaces procedimientos reales con frases genéricas como "luego el instructor configura el entorno".
   - No inventes datos que no estén respaldados por la transcripción o las capturas adjuntas; si algún fragmento de audio o pantalla resulta parcialmente ilegible, indícalo con honestidad.

2. **Idioma y preservación técnica:**
   - Redacta toda la narrativa, explicaciones, títulos, notas y preguntas en **español técnico claro y preciso**.
   - Preserva **exactamente en inglés** (sin traducir): código fuente, comandos CLI, flags/opciones, rutas de archivos, instrucciones ensamblador (`MOV`, `LEA`, `CALL`, `JMP`, etc.), registros de CPU (`RAX`, `RSP`, `RBP`, `RIP`, etc.), nombres de funciones/APIs, mensajes de error literales y terminología estándar de TI cuando sea lo habitual en la industria.

3. **Markdown estándar ESTRICTO (CERO wikilinks):**
   - Usa **ÚNICAMENTE Markdown estándar**: `![Descripción técnica precisa](assets/...)` para imágenes y `[Texto](ruta.md)` para enlaces.
   - **NUNCA** uses wikilinks de Obsidian (`![[...]]` ni `[[...]]`).

4. **OCR visual y selección inteligente de capturas de pantalla:**
   - Inspecciona con atención **cada captura PNG adjunta** a este bloque.
   - **Transcribe TODO el código fuente, comandos de terminal, salidas CLI, archivos de configuración, volcados hexadecimales (hex dumps) o desensamblados visibles en las imágenes** dentro de bloques de código con etiqueta de lenguaje (` ```bash `, ` ```python `, ` ```c `, ` ```asm `, ` ```powershell `, ` ```yaml `, ` ```json `, ` ```sql `, etc.).
   - **Incrusta ÚNICAMENTE** las capturas que aporten valor visual genuino que el texto por sí solo no reemplaza: diagramas de arquitectura, esquemas de memoria/pila, interfaces gráficas complejas (Ghidra, IDA Pro, Binary Ninja, x64dbg, WinDbg, Wireshark, Burp Suite, consolas cloud, gráficas o salidas visuales).
   - **NO incrustes** capturas que solo muestren código/texto ya transcrito en bloques fenced, primeros planos del instructor (talking head), transiciones vacías o pantallas duplicadas.
   - Cuando incrustes una captura, utiliza **EXACTAMENTE** la ruta relativa proporcionada en el catálogo del bloque, con una descripción alt técnica y útil: `![Diagrama de pila y registros en x64dbg](ruta_exacta)`. Nunca inventes nombres ni rutas de imágenes.

5. **Marcas de tiempo y callouts técnicos de Obsidian:**
   - Incluye la marca de tiempo exacta del video (`⏱️ HH:MM:SS`) en los encabezados principales (`## Tema (⏱️ 00:12:34)`) y en los procedimientos técnicos relevantes para permitir saltar al instante exacto en VLC.
   - Utiliza callouts de Obsidian cuando aporten claridad técnica:
     - `> [!tip]` para atajos, buenas prácticas y trucos del instructor.
     - `> [!warning]` para errores comunes, peligros de seguridad, fallos silenciosos o advertencias críticas.
     - `> [!important]` para conceptos clave o prerrequisitos imprescindibles.
     - `> [!note]` para aclaraciones de contexto, diferencias entre versiones o detalles de plataforma.

6. **Salida limpia:**
   - Devuelve únicamente Markdown publicable de alta calidad.
   - No incluyas comentarios meta ("Aquí tienes el bloque...", "A continuación...") ni etiquetas internas de razonamiento (`<think>`).
"""


def build_block_user_prompt(
    lesson_title: str,
    block_index: int,
    total_blocks: int,
    start_hms: str,
    end_hms: str,
    transcript_text: str,
    frame_catalog: list[tuple[str, str]],
    previous_headings: list[str],
) -> str:
    """Construye el prompt de usuario para sintetizar un bloque cronológico exhaustivo."""
    lines: list[str] = [
        f"Estás redactando el **bloque cronológico {block_index} de {total_blocks}** del capítulo técnico **«{lesson_title}»**.",
        f"Ventana temporal de este bloque: **`{start_hms}` – `{end_hms}`**.",
        "",
    ]

    if previous_headings:
        lines.append("## Continuidad editorial (secciones ya redactadas en bloques anteriores)")
        lines.append(
            "Continúa de forma fluida a partir del último tema sin repetir encabezados ya cubiertos, "
            "sin emitir frontmatter YAML, sin título `#` principal y sin volver a escribir una introducción general:"
        )
        for heading in previous_headings[-12:]:
            lines.append(f"- {heading}")
        lines.append("")
    else:
        lines.append("## Instrucciones de inicio del cuerpo")
        lines.append(
            "Este es el primer bloque del cuerpo técnico del capítulo. "
            "NO escribas frontmatter YAML, NO escribas título `#` (H1) ni la sección de objetivos de aprendizaje "
            "(se ensamblan automáticamente). Comienza directamente con el primer encabezado `## ... (⏱️ HH:MM:SS)`."
        )
        lines.append("")

    if frame_catalog:
        lines.append("## Catálogo de capturas PNG adjuntas a este mensaje (en el mismo orden)")
        lines.append(
            "Inspecciona cada imagen adjunta para extraer mediante OCR cualquier comando, código, configuración, "
            "tabla o dirección visible. Incrusta **solo** las capturas con alto valor visual usando "
            "**EXACTAMENTE** la ruta relativa listada aquí en Markdown estándar (`![Descripción](ruta)`). "
            "**Prohibido** inventar rutas o usar wikilinks `![[...]]`:"
        )
        for idx, (ts_hms, rel_md_path) in enumerate(frame_catalog, start=1):
            lines.append(f"{idx}. `⏱️ {ts_hms}` -> `{rel_md_path}`")
        lines.append("")
    else:
        lines.append("## Capturas adjuntas")
        lines.append(
            "No hay capturas de pantalla adjuntas para este bloque; no incluyas enlaces a imágenes."
        )
        lines.append("")

    lines.append("## Transcripción cronológica del bloque")
    lines.append(
        "Transforma TODO el contenido técnico siguiente en secciones `##` y subsecciones `###` exhaustivas "
        "en español, con bloques de código precisos, explicaciones profundas, callouts de Obsidian y marcas `⏱️ HH:MM:SS`:"
    )
    lines.append("")
    lines.append(transcript_text.strip() or "(Sin diálogo hablado en este tramo; analiza únicamente las capturas adjuntas.)")
    lines.append("")
    lines.append(
        "Recuerda: NO resumas ni omitas pasos, flags, comandos, errores ni matices técnicos. "
        "NO incluyas conclusiones de cierre del capítulo todavía (se generan al finalizar todos los bloques)."
    )

    return "\n".join(lines)


def build_wrapup_user_prompt(
    lesson_title: str,
    source_video_name: str,
    all_headings: list[str],
    combined_excerpt: str,
) -> str:
    """Construye el prompt para generar objetivos de aprendizaje, práctica guiada y preguntas de repaso."""
    headings_block = (
        "\n".join(f"- {h}" for h in all_headings)
        if all_headings
        else "- (Sin encabezados detectados; apóyate en el extracto del capítulo.)"
    )

    return f"""Has completado la redacción exhaustiva de todos los bloques técnicos del capítulo **«{lesson_title}»** (video fuente: `{source_video_name}`).

Ahora genera **dos piezas editoriales** en español técnico profesional usando EXACTAMENTE los marcadores HTML indicados para permitir su ensamblado automático:

1. **Apertura del capítulo** (entre `<!-- OPENING_START -->` y `<!-- OPENING_END -->`):
   - Un párrafo breve de contexto técnico (3-5 líneas) que indique qué problema resuelve esta lección y cómo encaja en un flujo de trabajo real de TI.
   - La sección `## Objetivos de aprendizaje` con una lista de 4 a 7 objetivos concretos, medibles y técnicos (qué sabrá ejecutar, diagnosticar o construir el lector al terminar).

2. **Cierre práctico y evaluación** (entre `<!-- CLOSING_START -->` y `<!-- CLOSING_END -->`):
   - La sección `## Práctica Guiada (Manos a la Obra)`: un laboratorio reproducible paso a paso basado estrictamente en las herramientas, comandos, código y conceptos reales de esta lección. Incluye prerrequisitos concretos, comandos/pasos verificables, resultado esperado y un checklist interactivo con casillas `- [ ]`.
   - La sección `## Preguntas de Repaso`: entre 4 y 6 preguntas técnicas profundas (que evalúen comprensión real, diagnóstico de fallos, flags o arquitectura, no mera memorización). Cada pregunta DEBE usar obligatoriamente un callout desplegable de Obsidian con el signo `-` exactamente así:

```markdown
> [!question]- ¿Pregunta técnica concreta aquí?
> Respuesta técnica detallada en español. Incluye comandos, rutas o razonamiento técnico paso a paso.
```

## Reglas estrictas
- Usa **únicamente Markdown estándar** (prohibidos los wikilinks `[[...]]` o `![[...]]`).
- No incluyas frontmatter YAML ni título `#` H1.
- No inventes herramientas ni comandos que no aparezcan en el capítulo.
- Emite los marcadores `<!-- OPENING_START -->`, `<!-- OPENING_END -->`, `<!-- CLOSING_START -->` y `<!-- CLOSING_END -->` exactamente como se muestran.

## Índice de secciones redactadas en el capítulo
{headings_block}

## Extracto de referencia del capítulo redactado
{combined_excerpt.strip()}
"""
