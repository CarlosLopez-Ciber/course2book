"""Configuración, constantes de formatos y modelos de datos compartidos de course2book."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
        ".flv",
        ".wmv",
        ".m4v",
        ".ts",
        ".mts",
        ".m2ts",
        ".mpg",
        ".mpeg",
        ".3gp",
        ".ogv",
        ".vob",
        ".asf",
        ".rmvb",
        ".f4v",
        ".divx",
        ".f4p",
    }
)

SUBTITLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".srt",
        ".vtt",
        ".ass",
        ".ssa",
        ".sub",
        ".sbv",
        ".idx",
    }
)

RESOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".txt",
        ".md",
        ".py",
        ".c",
        ".cpp",
        ".cc",
        ".h",
        ".hpp",
        ".rs",
        ".go",
        ".js",
        ".jsx",
        ".tsx",
        ".java",
        ".cs",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".cmd",
        ".sql",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".html",
        ".css",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".7z",
        ".rar",
        ".pptx",
        ".ppt",
        ".docx",
        ".xlsx",
        ".csv",
        ".pcap",
        ".pcapng",
        ".elf",
        ".exe",
        ".dll",
        ".so",
        ".bin",
        ".asm",
        ".s",
        ".ipynb",
        ".conf",
        ".cfg",
        ".ini",
    }
)

DomainProfile = Literal["it", "reveng", "cybersec", "dev", "infra", "none"]
EngineType = Literal["agent", "ollama", "gemini", "openai-compat"]

DOMAIN_PROMPTS: dict[str, str] = {
    "it": (
        "Technical IT lecture covering programming, Linux, Windows, CLI, Git, Docker, "
        "networking, TCP/IP, HTTP, APIs, JSON, YAML, SQL, SSH, Python, C, C++, Rust, "
        "Bash, PowerShell, debugging, cybersecurity, and reverse engineering."
    ),
    "reveng": (
        "Reverse engineering and binary analysis lecture covering x86, x64, x86_64, ARM64, "
        "assembly, Ghidra, IDA Pro, Binary Ninja, radare2, Cutter, x64dbg, WinDbg, gdb, "
        "pwndbg, GEF, ELF, PE, Mach-O, registers RAX RBX RCX RDX RSI RDI RSP RBP RIP, "
        "stack frame, heap, calling conventions, syscalls, ASLR, DEP, NX, PIE, Canary, "
        "ROP, GOT, PLT, Frida, decompiler, hex dump, opcodes, shellcode, and malware analysis."
    ),
    "cybersec": (
        "Cybersecurity and penetration testing lecture covering nmap, Burp Suite, Wireshark, "
        "Metasploit, sqlmap, ffuf, Gobuster, BloodHound, Active Directory, Kerberos, NTLM, "
        "SMB, LDAP, XSS, SQLi, SSRF, CSRF, RCE, LFI, JWT, OAuth, CVE, OWASP, Kali Linux, "
        "reverse shell, privilege escalation, SIEM, SOC, MITRE ATT&CK, and cryptography."
    ),
    "dev": (
        "Software engineering lecture covering Python, Rust, C, C++, Go, TypeScript, JavaScript, "
        "Git, pytest, Docker, REST, GraphQL, PostgreSQL, Redis, async/await, pointers, memory "
        "management, concurrency, algorithms, data structures, design patterns, CI/CD, and APIs."
    ),
    "infra": (
        "DevOps, networking, and systems administration lecture covering Linux, systemd, "
        "iptables, nftables, nginx, HAProxy, Docker, Kubernetes, kubectl, Helm, Terraform, "
        "Ansible, AWS, GCP, Azure, VPC, DNS, BGP, OSPF, VLAN, TLS, SSL, Prometheus, and Grafana."
    ),
    "none": "",
}


def build_initial_prompt(domain: DomainProfile = "it", glossary: str = "") -> str | None:
    """Combina el perfil de dominio técnico con el glosario personalizado del usuario."""
    parts: list[str] = []
    base = DOMAIN_PROMPTS.get(domain, "")
    if base:
        parts.append(base)
    cleaned_glossary = glossary.strip()
    if cleaned_glossary:
        parts.append(f"Key technical terms: {cleaned_glossary}.")
    combined = " ".join(parts).strip()
    return combined or None


@dataclass(slots=True)
class WhisperConfig:
    """Configuración para transcripción GPU con faster-whisper."""

    model_size: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "int8_float16"
    language: str | None = "en"
    vad_filter: bool = True
    min_silence_duration_ms: int = 500
    beam_size: int = 5
    domain: DomainProfile = "it"
    glossary: str = ""
    clean_existing_subtitles: bool = True
    resume: bool = False


@dataclass(slots=True)
class FrameConfig:
    """Configuración del detector de capturas afinado para cursos técnicos de TI."""

    interval_seconds: float = 2.0
    phash_threshold: int = 6
    dhash_threshold: int = 7
    grid_rows: int = 4
    grid_cols: int = 4
    grid_cell_diff_threshold: float = 8.0
    edge_diff_threshold: float = 0.012
    ignore_corner_pip: bool = True
    min_interval_seconds: float = 4.0
    max_frames: int = 120
    image_format: Literal["png", "webp"] = "png"
    workers: int = 0  # 0 = automático según núcleos disponibles


@dataclass(slots=True)
class SynthesisConfig:
    """Configuración de síntesis por bloques en español."""

    engine: EngineType = "ollama"
    model: str = "gemma4:12b"
    base_url: str = "http://localhost:11434"
    api_key: str | None = None
    vision: bool = True
    think: bool = True
    num_ctx: int = 16384
    max_image_width: int = 1280
    block_minutes: float = 8.0
    max_images_per_block: int = 6
    keep_alive: str = "0"
    timeout_seconds: float = 600.0
    auto_cleanup: bool = True
    resume: bool = False
    target_language: str = "es"


@dataclass(slots=True)
class MediaInfo:
    """Metadatos obtenidos mediante ffprobe para un archivo de video."""

    path: Path
    duration: float
    width: int
    height: int
    has_audio: bool
    has_video: bool
    format_name: str


@dataclass(slots=True)
class SrtEntry:
    """Entrada individual de un archivo SubRip (.srt)."""

    index: int
    start: float
    end: float
    text: str


@dataclass(slots=True)
class ExtractedFrame:
    """Captura PNG deduplicada con su ruta relativa Markdown y marca de tiempo."""

    path: Path
    relative_md_path: str
    timestamp: float
    filename: str


@dataclass(slots=True)
class LessonArtifacts:
    """Rutas y artefactos generados para una lección dentro de la jerarquía espejo."""

    video_path: Path
    rel_dir: Path
    srt_path: Path
    output_dir: Path
    assets_dir: Path
    draft_md_path: Path
    final_md_path: Path
    checkpoint_path: Path
    frames: list[ExtractedFrame] = field(default_factory=list)
