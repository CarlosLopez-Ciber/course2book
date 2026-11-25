# course2book: turn technical video courses into books for Obsidian

[![CI](https://github.com/CarlosLopez-Ciber/course2book/actions/workflows/ci.yml/badge.svg)](https://github.com/CarlosLopez-Ciber/course2book/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**English** | [Español](README.es.md)

`course2book` turns technical video courses into detailed Markdown book chapters for Obsidian. It runs locally with Python 3.11 or 3.12 (`uv`), `faster-whisper` (CUDA or CPU), and `ffmpeg`. It accepts any video format that FFmpeg supports, generates chapters in English or Spanish (`--book-lang en|es`), and outputs standard Markdown (no wikilinks) with clean PNG screenshots.

## How it works

`course2book` is not an Obsidian or VLC plugin. It consists of two parts that work together:

1. The `course2book` CLI handles local processing: `ffmpeg`, transcription with `faster-whisper` (`large-v3`, `int8_float16`), PNG screenshot extraction, temporary file cleanup, optional Ollama or API synthesis, and timestamp playback in VLC.
2. The `course-to-book` skill (`skills/course-to-book/SKILL.md`) for Antigravity and `AGENTS.md` for OpenCode. Both define the editorial format when you want an AI agent to write chapters from drafts and PNG screenshots.

Obsidian needs no extra plugin. Open the output folder as a vault because every file uses standard Markdown and `.png` images.

## Minimum requirements

- Operating system: Windows 10/11 (x64), Linux (x86_64), or macOS (CPU mode). Requires Python 3.11 or 3.12, `uv`, and `ffmpeg` on your `PATH`.
- GPU (recommended): An NVIDIA GPU with CUDA 12 and at least 8 GB of VRAM to run `faster-whisper` (`large-v3`, `int8_float16`) and local Ollama models such as `gemma4:12b`.
- Without an NVIDIA GPU: Transcribe on CPU with `--device cpu` (slower) and generate chapters with `--engine agent`, `--engine gemini`, or `--engine openai-compat`.
- RAM: 16 GB recommended for long videos and local models.
- Optional: VLC if you want to use `course2book open` to jump to cited timestamps.

## Quick setup

Install the latest release globally with `uv` (no manual clone required):

```bash
uv tool install git+https://github.com/CarlosLopez-Ciber/course2book@v0.1.0
course2book check-env
```

Or clone the repository to run it from source:

```bash
git clone https://github.com/CarlosLopez-Ciber/course2book.git
cd course2book
uv run course2book check-env
```

## Three ways to use it

### 1. Fully local pipeline with Ollama (`gemma4:12b`)

This mode runs the entire pipeline in one command, without opening Antigravity or OpenCode. Pass `--book-lang en` for English chapters or `--book-lang es` (default) for Spanish:

```bash
uv run course2book build "/path/to/MyCourse" -o "/path/to/Obsidian/MyCourse" --engine ollama --model gemma4:12b --domain cybersec --book-lang en
```

The command runs five steps in order:

1. GPU transcription (Whisper): scans all course videos (`.mp4`, `.mkv`, `.wmv`, `.avi`, `.mov`, `.webm`, `.flv`, `.ts`, and other FFmpeg formats). It removes broken subtitle files (`.srt`, `.vtt`, `.ass`, `.en.vtt`) and ignores embedded tracks (`-sn`). Then it runs `large-v3` (`int8_float16`) to create `<video>.srt` next to each video.
2. VRAM release: unloads Whisper from the GPU so Ollama has full access to memory (tested on 8 GB NVIDIA GPUs).
3. CPU screenshots: combines `ffmpeg` with 4x4 grid change detection to extract clean PNG frames and build a draft. The detector is tuned for dark terminals, IDEs, disassemblers, and GUIs, while ignoring corner PiP webcams.
4. GPU synthesis (Ollama `gemma4:12b`): processes each lesson in chronological chunks of about 8 minutes. It sends the transcript and PNGs with reasoning enabled (`think=True`) and writes the chapter with `⏱️ HH:MM:SS` timestamps, hands-on practice, and collapsible review questions (`> [!question]-`).
5. Final cleanup: deletes `.draft.md`, the checkpoint file, and unreferenced `.png` images. Then it updates `00_Index.md` (`00_Indice.md` in Spanish) with any supplementary labs or resources found in each module.

If you cancel a long course with `Ctrl+C`, add `--resume` to continue from the last saved checkpoint without repeating finished chapters or blocks:

```bash
uv run course2book build "/path/to/MyCourse" -o "/path/to/Obsidian/MyCourse" --engine ollama --model gemma4:12b --domain cybersec --book-lang en --resume
```

### 2. Agent mode with Antigravity or OpenCode

Use this mode when you want Antigravity (Gemini) or OpenCode to write the final chapters.

1. Generate `.srt` subtitles, PNG screenshots in `assets/`, and `.draft.md` files that mirror the course module structure:
   ```bash
   uv run course2book build "/path/to/MyCourse" -o "/path/to/Obsidian/MyCourse" --engine agent --domain reveng --book-lang en
   ```
2. Ask the agent to write the chapters:
   - In Antigravity, copy `skills/course-to-book/SKILL.md` into `~/.gemini/config/skills/course-to-book/SKILL.md`. Then ask in chat: *"Process the drafts in `/path/to/Obsidian/MyCourse` with the `course-to-book` skill"*. The agent reads each `.draft.md`, inspects the PNGs, writes the final `.md` chapter, and runs `course2book cleanup`.
   - In OpenCode, open the repository folder, follow `AGENTS.md`, and run `course2book cleanup` when done.
3. If you hit API token limits mid-course, finish the remaining `.draft.md` files with local `gemma4:12b`:
   ```bash
   uv run course2book synthesize "/path/to/Obsidian/MyCourse" --engine ollama --model gemma4:12b --book-lang en --resume
   ```

### 3. Clean `.srt` subtitles only

If you only need accurate English subtitles for VLC, this command removes old broken subtitles and transcribes from scratch with `large-v3` (`int8_float16`):

```bash
uv run course2book transcribe "/path/to/MyCourse" --domain dev
```

Add `--resume` to skip existing `.srt` files after an interrupted batch.

## Antigravity prompt examples

Once you copy `skills/course-to-book/SKILL.md` to `~/.gemini/config/skills/course-to-book/SKILL.md`, you can trigger the workflow from any Antigravity chat:

1. Process a full course from raw videos (prepend `/goal` for large courses so the agent finishes the whole folder):
   > *"Use the `course-to-book` skill to convert `/path/to/ReverseEngineering` into an Obsidian book in `/path/to/Obsidian/ReverseEngineering` (domain `reveng`)."*
2. Test on a single video:
   > *"Use `course-to-book` on `/path/to/MyCourse/01_intro.mkv` and save it to `/path/to/Obsidian/MyCourse`."*
3. Run the local Ollama pipeline (`gemma4:12b`) without spending cloud tokens:
   > *"Run `course2book build` with `--engine ollama` (`gemma4:12b`) on `/path/to/MyCourse` and save the output to `/path/to/Obsidian/MyCourse` (domain `cybersec`)."*
4. Convert existing `.draft.md` files into final chapters:
   > *"Use `course-to-book` to convert the `.draft.md` files in `/path/to/Obsidian/MyCourse` into final chapters and clean up unused files."*

## Domain profiles (`--domain`) and custom glossary

Use `--domain` so `faster-whisper` transcribes technical tools, commands, and acronyms more accurately:

| Profile (`--domain`) | Technical focus |
| :--- | :--- |
| `it` *(default)* | General IT (Linux, Windows, CLI, Git, Docker, networking, programming, cybersecurity) |
| `reveng` | Reverse engineering (Ghidra, IDA Pro, x64dbg, gdb, pwndbg, x86_64, registers, ASLR, ROP, ELF/PE) |
| `cybersec` | Cybersecurity and pentesting (nmap, Burp Suite, Wireshark, Metasploit, Active Directory, OWASP) |
| `dev` | Software development (Python, Rust, C/C++, Go, TypeScript, Docker, SQL, APIs) |
| `infra` | DevOps, networking, and sysadmin (Kubernetes, kubectl, Terraform, Ansible, nginx, iptables, BGP, TLS) |
| `none` | No `initial_prompt` |

Add course-specific terms with `--glossary`:

```bash
uv run course2book transcribe "/path/to/Malware" --domain reveng --glossary "PEB, TEB, NtAllocateVirtualMemory, Flare-VM"
```

## Other useful commands

```bash
# Open VLC at the exact timestamp cited in your notes (keeps your vault portable)
uv run course2book open "/path/to/MyCourse/01_Module/01_Intro.mkv" 00:14:25

# Clean .draft.md files and unreferenced PNGs after editing in agent mode
uv run course2book cleanup "/path/to/Obsidian/MyCourse"

# Use the Gemini API or any OpenAI-compatible endpoint instead of Ollama
uv run course2book build "/path/to/MyCourse" -o "/path/to/Obsidian/MyCourse" --engine gemini --model gemini-2.5-flash --book-lang en
uv run course2book build "/path/to/MyCourse" -o "/path/to/Obsidian/MyCourse" --engine openai-compat --base-url "http://localhost:1234" --model "your-model" --book-lang en

# Extract deduplicated PNG screenshots only
uv run course2book frames "/path/to/MyCourse" -o "/path/to/Obsidian/MyCourse"
```

## Automated tests

```bash
uv run pytest -v
```

## License

Released under the [MIT License](LICENSE).
