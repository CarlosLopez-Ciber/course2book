---
name: course-to-book
description: Convierte borradores `.draft.md` y capturas PNG generados por `course2book` (o cursos técnicos en video de TI) en capítulos de libro técnico exhaustivos en español para Obsidian, usando Markdown estándar estricto, inspección visual/OCR de capturas, práctica guiada, preguntas de repaso desplegables y limpieza automática final.
---

# Skill `course-to-book` (Antigravity)

Esta skill guía la redacción de capítulos técnicos exhaustivos en español para Obsidian a partir de los artefactos preparados por `course2book` (`.draft.md`, `.srt` y `assets/<slug>/*.png`).

## Cuándo usar esta skill

- El usuario quiere convertir un curso técnico de TI (programación, ciberseguridad, ingeniería inversa, redes, DevOps, sistemas) en un libro práctico para Obsidian.
- El usuario proporciona uno o varios archivos `.draft.md` generados por `course2book prepare` / `course2book build --engine agent`, o una carpeta de videos sin procesar.

## Flujo de trabajo paso a paso

### Paso 1: Preparar artefactos (solo si el usuario parte de videos crudos)

Si aún no existen los `.draft.md` y las capturas en `assets/`, ejecuta:

```powershell
uv run course2book prepare "<ruta_video_o_curso>" -o "<carpeta_libro_obsidian>"
```

Esto transcribe en GPU con `faster-whisper` (guardando solo `.srt` junto a cada video y liberando VRAM), extrae capturas PNG sin pérdida afinadas para contenido técnico de TI y crea los `.draft.md` y `00_Indice.md`.

### Paso 2: Leer el borrador e inspeccionar TODAS las capturas PNG

Para cada `<leccion>.draft.md`:

1. Lee el borrador `.draft.md` (y el `.srt` fuente si hace falta revisar marcas de tiempo exactas).
2. Lista e **inspecciona visualmente cada PNG** en `assets/<slug>/` usando `read_file`.
3. Clasifica cada captura:
   - **OCR obligatorio:** Si la imagen muestra código fuente, comandos CLI, salidas de terminal, configuraciones, hex dumps o desensamblados, **transcribe todo fielmente** a bloques de código fenced (` ```bash `, ` ```python `, ` ```c `, ` ```asm `, ` ```powershell `, ` ```sql `, ` ```yaml `, etc.).
   - **Incrustar en el capítulo:** Conserva **ÚNICAMENTE** capturas que aporten valor visual genuino que el texto no sustituye (diagramas, esquemas de arquitectura/memoria, GUIs complejas como Ghidra, IDA Pro, Binary Ninja, x64dbg, Wireshark, Burp Suite, consolas cloud o salidas gráficas).
   - **Descartar (no referenciar):** No incrustes capturas de puro texto/código ya transcrito, planos del instructor (talking head), transiciones en negro o pantallas redundantes. El comando final `cleanup` las eliminará automáticamente del disco.

### Paso 3: Redactar el capítulo por bloques cronológicos

Si la lección es extensa (> 10 minutos o > 6 capturas), redáctala mentalmente o por tramos en **bloques cronológicos** (~8 minutos por bloque) para garantizar **exhaustividad absoluta** de principio a fin sin comprimir ni omitir los tramos finales.

### Paso 4: Estándar editorial obligatorio del archivo `<leccion>.md`

Guarda el capítulo definitivo en la misma carpeta que el borrador, reemplazando `.draft.md` por `.md`, cumpliendo estrictamente:

1. **Idioma y rigor técnico:**
   - Redacción en **español técnico profesional** con profundidad de libro de referencia.
   - **NUNCA resumas omitiendo detalles:** documenta cada comando, flag, parámetro, ruta, dirección de memoria, offset, registro, paso intermedio, caso borde, error mostrado y advertencia del instructor.
   - Preserva **exactamente en inglés**: código fuente, comandos CLI, flags, rutas, instrucciones ensamblador (`MOV`, `LEA`, `CALL`, etc.), registros (`RAX`, `RSP`, `RBP`, `RIP`, etc.), nombres de APIs/funciones, errores literales y terminología técnica estándar.

2. **Markdown estándar ESTRICTO (CERO wikilinks):**
   - Imágenes: `![Descripción técnica precisa](assets/<slug>/archivo.png)` usando la ruta relativa exacta.
   - Enlaces: `[Título](ruta.md)`.
   - **PROHIBIDO** usar wikilinks (`[[...]]` o `![[...]]`).

3. **Marcas de tiempo y callouts de Obsidian:**
   - Incluye la marca exacta del video (`⏱️ HH:MM:SS`) en cada encabezado principal (`## Tema (⏱️ 00:12:40)`) y bloque procedimental relevante (el lector puede saltar al instante exacto en VLC con `course2book open "<video>" HH:MM:SS`).
   - Usa callouts técnicos de Obsidian (`> [!tip]`, `> [!warning]`, `> [!important]`, `> [!note]`).

4. **Estructura obligatoria:**
   - Frontmatter YAML limpio:
     ```yaml
     ---
     title: "Título de la lección"
     source_video: "archivo_video.mp4"
     duration: "HH:MM:SS"
     language: es
     engine: agent
     status: ready
     tags:
       - curso
       - ti
     ---
     ```
   - `# Título del Capítulo`
   - Contexto técnico breve + `## Objetivos de aprendizaje`
   - Secciones técnicas exhaustivas cronológicas (`## ... (⏱️ HH:MM:SS)`, `### ...`)
   - `## Práctica Guiada (Manos a la Obra)` con pasos concretos reproducibles y checklist `- [ ]`
   - `## Preguntas de Repaso` con 4 a 6 preguntas técnicas profundas usando callouts desplegables de Obsidian:
     ```markdown
     > [!question]- ¿Pregunta técnica concreta?
     > Respuesta técnica detallada en español, con comandos o ejemplos si aplica.
     ```

### Paso 5: Limpieza final OBLIGATORIA

Inmediatamente después de escribir `<final_md_path>`, ejecuta **siempre**:

```powershell
uv run course2book cleanup "<final_md_path>"
```

Este comando:
- Elimina automáticamente `.draft.md` y cualquier `.progress.json` temporal.
- Conserva únicamente los PNG referenciados en `<final_md_path>` y borra todas las capturas descartadas de `assets/<slug>/`.

Verifica además que `00_Indice.md` apunte con enlaces Markdown estándar (`[Capítulo](ruta.md)`) a los capítulos finales listos.
