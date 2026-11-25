# course2book: convierte cursos técnicos en video en libros para Obsidian

[![CI](https://github.com/CarlosLopez-Ciber/course2book/actions/workflows/ci.yml/badge.svg)](https://github.com/CarlosLopez-Ciber/course2book/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[English](README.md) | **Español**

`course2book` convierte cursos técnicos en video en capítulos completos en español o inglés (`--book-lang es|en`) para Obsidian. Funciona en local con Python 3.11 o 3.12 (`uv`), `faster-whisper` (CUDA o CPU) y `ffmpeg`. Acepta cualquier formato que soporte FFmpeg y genera archivos en Markdown estándar (sin wikilinks) con capturas PNG limpias.

## Cómo funciona

`course2book` no es un plugin de Obsidian ni de VLC. El proyecto tiene dos partes que trabajan juntas:

1. La herramienta de terminal (`course2book`), que ejecuta todo el procesamiento local: `ffmpeg`, transcripción con `faster-whisper` (`large-v3`, `int8_float16`), extracción de capturas PNG, limpieza de archivos temporales, conexión con Ollama o APIs y apertura directa en VLC.
2. El skill `course-to-book` (`skills/course-to-book/SKILL.md`) para Antigravity y la guía `AGENTS.md` para OpenCode. Ambos definen el formato editorial cuando prefieres que un agente redacte los capítulos a partir de los borradores y las capturas.

Obsidian no necesita ningún plugin adicional. Solo abres la carpeta de salida porque todo se guarda en Markdown estándar con imágenes `.png`.

## Requisitos mínimos

- Sistema operativo: Windows 10/11 (x64), Linux (x86_64) o macOS (modo CPU) con Python 3.11 o 3.12, `uv` y `ffmpeg` en el `PATH`.
- GPU (recomendado): GPU NVIDIA compatible con CUDA 12 y al menos 8 GB de VRAM para ejecutar `faster-whisper` (`large-v3`, `int8_float16`) y modelos locales en Ollama como `gemma4:12b`.
- Sin GPU NVIDIA: puedes transcribir en CPU con `--device cpu` (más lento) y delegar la redacción a `--engine agent`, `--engine gemini` o `--engine openai-compat`.
- Memoria RAM: 16 GB recomendados para procesar videos largos y modelos locales.
- Opcional: VLC si quieres usar `course2book open` para saltar al segundo citado en tus apuntes.

## Instalación rápida y comprobación del entorno

Instala la última release globalmente con `uv` (sin clonar el repositorio):

```powershell
uv tool install git+https://github.com/CarlosLopez-Ciber/course2book@v0.1.0
course2book check-env
```

O clona el repositorio para ejecutarlo desde el código fuente:

```powershell
git clone https://github.com/CarlosLopez-Ciber/course2book.git
cd course2book
uv run course2book check-env
```

## Tres formas de usarlo

### 1. Pipeline completo en local con Ollama (`gemma4:12b`)

Este modo corre todo el proceso con un solo comando, sin abrir Antigravity ni OpenCode. Usa `--book-lang es` (por defecto) para español o `--book-lang en` para inglés:

```powershell
uv run course2book build "D:\Cursos\MiCurso" -o "D:\Obsidian\MiCurso" --engine ollama --model gemma4:12b --domain cybersec
```

El comando ejecuta cinco pasos en orden:

1. Transcripción en GPU (Whisper): busca todos los videos del curso (`.mp4`, `.mkv`, `.wmv`, `.avi`, `.mov`, `.webm`, `.flv`, `.ts` y otros formatos de FFmpeg). Borra subtítulos previos con errores (`.srt`, `.vtt`, `.ass`, `.en.vtt`) e ignora pistas embebidas (`-sn`). Luego usa `large-v3` (`int8_float16`) para crear solo `<video>.srt` en inglés junto a cada video.
2. Liberación de VRAM: descarga Whisper de la GPU para dejar la memoria libre para Ollama (probado en GPUs NVIDIA de 8 GB de VRAM).
3. Capturas en CPU: combina `ffmpeg` con detección en rejilla 4x4 para extraer capturas PNG limpias y crear un borrador temporal. El detector está ajustado para terminales oscuras, IDEs, desensambladores y ventanas gráficas, e ignora webcams en las esquinas.
4. Redacción en GPU (Ollama `gemma4:12b`): procesa cada clase en bloques cronológicos de unos 8 minutos. Envía la transcripción y las capturas con razonamiento activado (`think=True`) y redacta el capítulo con marcas de tiempo `⏱️ HH:MM:SS`, práctica guiada y preguntas plegables `> [!question]-`.
5. Limpieza final: borra `.draft.md`, el checkpoint temporal y las imágenes `.png` sin referencias. Luego actualiza `00_Indice.md` (`00_Index.md` en inglés) con los recursos y laboratorios que encontró en cada módulo.

Si interrumpes un curso largo con `Ctrl+C`, puedes retomar desde el último punto guardado sin repetir capítulos ni bloques terminados:

```powershell
uv run course2book build "D:\Cursos\MiCurso" -o "D:\Obsidian\MiCurso" --engine ollama --model gemma4:12b --domain cybersec --resume
```

### 2. Modo agente con Antigravity u OpenCode

Usa este modo cuando quieres que Antigravity (con Gemini) u OpenCode redacte los capítulos finales.

1. Genera los subtítulos `.srt`, las capturas PNG en `assets/` y los borradores `.draft.md` con la misma estructura de módulos del curso:
   ```powershell
   uv run course2book build "D:\Cursos\MiCurso" -o "D:\Obsidian\MiCurso" --engine agent --domain reveng
   ```
2. Pide al agente que redacte los capítulos:
   - En Antigravity, copia `skills/course-to-book/SKILL.md` en `~/.gemini/config/skills/course-to-book/SKILL.md`. Luego escribe en el chat: *"Procesa los borradores en `D:\Obsidian\MiCurso` con el skill `course-to-book`"*. El agente lee cada `.draft.md` y revisa las capturas PNG. Luego escribe el capítulo `.md` final y ejecuta `course2book cleanup`.
   - En OpenCode, abre la carpeta del proyecto, sigue las reglas de `AGENTS.md` y ejecuta `course2book cleanup` al finalizar.
3. Si se agotan los tokens de Gemini a mitad del curso, termina los `.draft.md` pendientes con `gemma4:12b` en local:
   ```powershell
   uv run course2book synthesize "D:\Obsidian\MiCurso" --engine ollama --model gemma4:12b --resume
   ```

### 3. Solo subtítulos `.srt` limpios

Si solo quieres subtítulos precisos en inglés para ver un curso en VLC, este comando borra subtítulos viejos con errores y transcribe desde cero con `large-v3` (`int8_float16`):

```powershell
uv run course2book transcribe "D:\Cursos\MiCurso" --domain dev
```

Si interrumpes un lote, añade `--resume` para omitir los archivos `.srt` ya creados.

## Ejemplos de prompts para Antigravity

Una vez copiado `skills/course-to-book/SKILL.md` en `~/.gemini/config/skills/course-to-book/SKILL.md`, puedes iniciar el flujo desde cualquier chat sin escribir comandos:

1. Procesar un curso entero desde los videos originales (para cursos largos, puedes anteponer `/goal` para que recorra toda la carpeta sin detenerse):
   > *"Usa el skill `course-to-book` para convertir el curso en `D:\Cursos\ReverseEngineering` a libro en Obsidian dentro de `D:\Obsidian\ReverseEngineering` (dominio `reveng`)."*
2. Probar con un solo video:
   > *"Usa `course-to-book` con este video `D:\Cursos\MiCurso\01_intro.mkv` y guárdalo en `D:\Obsidian\MiCurso`."*
3. Delegar la ejecución a Ollama local (`gemma4:12b`) sin gastar tokens de Gemini:
   > *"Ejecuta `course2book build` con `--engine ollama` (`gemma4:12b`) sobre `D:\Cursos\MiCurso` y guarda la salida en `D:\Obsidian\MiCurso` (dominio `cybersec`)."*
4. Redactar capítulos a partir de borradores `.draft.md` ya generados:
   > *"Usa `course-to-book` para convertir los `.draft.md` que están en `D:\Obsidian\MiCurso` en capítulos finales y limpia los sobrantes."*

## Perfiles de vocabulario técnico (`--domain`) y glosario

Usa `--domain` para ayudar a `faster-whisper` a transcribir mejor herramientas, comandos y acrónimos técnicos en inglés:

| Perfil (`--domain`) | Enfoque técnico |
| :--- | :--- |
| `it` *(por defecto)* | TI general (Linux, Windows, CLI, Git, Docker, redes, programación, ciberseguridad) |
| `reveng` | Ingeniería inversa (Ghidra, IDA Pro, x64dbg, gdb, pwndbg, x86_64, registros, ASLR, ROP, ELF/PE) |
| `cybersec` | Ciberseguridad / Pentesting (nmap, Burp Suite, Wireshark, Metasploit, Active Directory, OWASP) |
| `dev` | Desarrollo de software (Python, Rust, C/C++, Go, TypeScript, Docker, SQL, APIs) |
| `infra` | DevOps / Redes / SysAdmin (Kubernetes, kubectl, Terraform, Ansible, nginx, iptables, BGP, TLS) |
| `none` | Sin `initial_prompt` |

También puedes añadir términos propios de un curso con `--glossary`:

```powershell
uv run course2book transcribe "D:\Cursos\Malware" --domain reveng --glossary "PEB, TEB, NtAllocateVirtualMemory, Flare-VM"
```

## Otros comandos útiles

```powershell
# Abrir VLC en el segundo exacto citado en tus apuntes (mantiene la bóveda portable)
uv run course2book open "D:\Cursos\MiCurso\01_Modulo\01_Intro.mkv" 00:14:25

# Limpiar .draft.md y PNGs sin referencias después de redactar en modo agente
uv run course2book cleanup "D:\Obsidian\MiCurso"

# Usar la API de Gemini o un endpoint compatible con OpenAI en lugar de Ollama
uv run course2book build "D:\Cursos\MiCurso" -o "D:\Obsidian\MiCurso" --engine gemini --model gemini-2.5-flash
uv run course2book build "D:\Cursos\MiCurso" -o "D:\Obsidian\MiCurso" --engine openai-compat --base-url "http://localhost:1234" --model "tu-modelo"

# Extraer solo capturas PNG deduplicadas de un curso
uv run course2book frames "D:\Cursos\MiCurso" -o "D:\Obsidian\MiCurso"
```

## Pruebas automatizadas

```powershell
uv run pytest -v
```

## Licencia

Distribuido bajo la [Licencia MIT](LICENSE).
