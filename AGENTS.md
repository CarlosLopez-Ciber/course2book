# AGENTS.md — Guía editorial de `course2book` para OpenCode

Este archivo define el flujo de trabajo y el estándar editorial obligatorio cuando redactes capítulos de libro en Obsidian a partir de borradores (`.draft.md`) y capturas PNG generados por `course2book`.

## 1. Preparación previa (si el usuario parte de videos sin procesar)

Si todavía no existen los archivos `.draft.md` ni las capturas en `assets/`, ejecuta primero la fase de preparación:

```powershell
uv run course2book prepare "<ruta_curso_o_video>" -o "<carpeta_libro_obsidian>"
```

## 2. Estándar editorial obligatorio para cada capítulo

Cuando transformes un `<leccion>.draft.md` en su capítulo definitivo `<leccion>.md`, cumple TODAS las reglas siguientes sin excepción:

1. **Idioma y exhaustividad técnica absoluta:**
   - Redacta el capítulo completo en **español técnico profesional** con profundidad de libro de referencia.
   - **PROHIBIDO resumir en exceso:** conserva cada comando, bandera (`flag`), parámetro, dirección de memoria, offset, registro, paso intermedio, caso borde, error mostrado y advertencia del instructor.
   - Preserva **exactamente en inglés**: código fuente, comandos CLI, flags, rutas de archivos, instrucciones ensamblador (`MOV`, `LEA`, `CALL`, etc.), registros (`RAX`, `RSP`, `RBP`, `RIP`, etc.), APIs, mensajes de error literales y terminología estándar de TI.

2. **Markdown estándar ESTRICTO (CERO wikilinks):**
   - Usa **ÚNICAMENTE Markdown estándar**: `![Descripción técnica](assets/<slug>/archivo.png)` y `[Título](ruta.md)`.
   - **NUNCA** utilices wikilinks de Obsidian (`[[...]]` ni `![[...]]`).

3. **Inspección visual, OCR y curación de capturas PNG:**
   - Inspecciona **todas** las capturas PNG ubicadas en `assets/<slug>/`.
   - **Transcribe TODO** código fuente, comando de terminal, salida CLI, configuración, hex dump o desensamblado visible en las imágenes dentro de bloques fenced con etiqueta de lenguaje (` ```bash `, ` ```python `, ` ```c `, ` ```asm `, ` ```powershell `, etc.).
   - **Incrusta ÚNICAMENTE** las capturas que aporten valor visual real (diagramas, arquitectura, interfaces gráficas complejas como Ghidra, IDA Pro, Binary Ninja, x64dbg, Wireshark, Burp Suite, mapas de memoria, gráficas) usando su ruta relativa exacta en Markdown estándar.
   - **NO incrustes** capturas de código/texto ya transcrito, planos del instructor (talking head), transiciones vacías ni capturas redundantes.

4. **Procesamiento por bloques cronológicos para lecciones largas:**
   - Si la lección supera los 10 minutos o contiene muchas capturas, procésala en **bloques cronológicos** (~8 minutos y ≤ 6 capturas por bloque) para garantizar que no se pierda ni un solo detalle técnico al final del video.

5. **Marcas de tiempo, callouts y estructura de capítulo:**
   - Incluye marcas de tiempo exactas (`⏱️ HH:MM:SS`) en encabezados (`## Tema (⏱️ 00:08:15)`) y procedimientos clave para saltar directamente en VLC con `course2book open "<video>" HH:MM:SS`.
   - Usa callouts técnicos de Obsidian: `> [!tip]`, `> [!warning]`, `> [!important]`, `> [!note]`.
   - Estructura obligatoria de `<leccion>.md`:
     - Frontmatter YAML limpio:
       ```yaml
       ---
       title: "Título de la lección"
       source_video: "nombre_del_video.mp4"
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
     - Breve contexto técnico + `## Objetivos de aprendizaje`
     - Secciones técnicas exhaustivas cronológicas (`## ... (⏱️ HH:MM:SS)`, `### ...`)
     - `## Práctica Guiada (Manos a la Obra)` con pasos reproducibles y checklist `- [ ]`
     - `## Preguntas de Repaso` usando **exclusivamente** callouts desplegables de Obsidian:
       ```markdown
       > [!question]- ¿Pregunta técnica concreta?
       > Respuesta técnica detallada en español.
       ```

## 3. Paso final OBLIGATORIO: limpieza automática

Una vez guardado el archivo definitivo `<final_md_path>`, ejecuta **siempre** el comando de limpieza para eliminar automáticamente `.draft.md`, `.progress.json` y todas las capturas PNG que no fueron incrustadas en el capítulo:

```powershell
uv run course2book cleanup "<final_md_path>"
```

Si procesaste una carpeta completa de curso, ejecuta también la limpieza a nivel de carpeta para verificar que no queden borradores ni imágenes huérfanas:

```powershell
uv run course2book cleanup "<carpeta_libro_obsidian>"
```
