"""Hardware and Ollama checks used by setup.bat and run.py.

Usage:  python -m src.system_check gpu       print GPU / CPU expectations
        python -m src.system_check ollama    check Ollama and pull missing models
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import load_models
from .platform_text import setup_hint

OLLAMA_DOWNLOAD = "https://ollama.com/download"
EXIT_NO_OLLAMA = 3  # setup.bat treats this as "stop cleanly, nothing broken"

# Approximate GPU memory (GB) each model needs at its default quantization.
MODEL_VRAM_GB = {
    "qwen2.5:7b": 5.5, "qwen2.5:14b": 10, "qwen2.5:32b": 21,
    "llama3.1:8b": 6, "llama3.1:70b": 43, "nomic-embed-text": 0.5,
}
# Radeon cards Ollama supports through ROCm (RX 6800 and newer, Radeon PRO W6800+/W7000).
AMD_SUPPORTED = re.compile(r"Radeon (?:RX\s?(?:68|69|7\d|9\d)\d\d|PRO W(?:68|7\d)\d\d)", re.I)


@dataclass
class Gpu:
    """One detected graphics card."""
    name: str
    vendor: str
    vram_gb: float | None
    ollama_can_use: bool


# ---------------------------------------------------------------- GPU detection

def nvidia_gpus() -> list[Gpu]:
    """Ask nvidia-smi for NVIDIA cards. Empty list if the tool is not installed."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        name, _, mem = line.partition(",")
        vram = round(float(mem) / 1024, 1) if mem.strip().isdigit() else None
        gpus.append(Gpu(name.strip(), "NVIDIA", vram, True))
    return gpus


def windows_gpus() -> list[Gpu]:
    """Read display adapters from the Windows registry (gives true VRAM, unlike WMI)."""
    if sys.platform != "win32":
        return []
    import winreg
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    gpus = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return []
    for i in range(winreg.QueryInfoKey(root)[0]):
        try:
            key = winreg.OpenKey(root, winreg.EnumKey(root, i))
            name = winreg.QueryValueEx(key, "DriverDesc")[0]
        except OSError:
            continue
        try:
            vram = round(int(winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]) / 1024**3, 1)
        except (OSError, ValueError, TypeError):
            vram = None
        vendor = ("NVIDIA" if "nvidia" in name.lower() else
                  "AMD" if re.search(r"amd|radeon", name, re.I) else
                  "Intel" if "intel" in name.lower() else "Other")
        usable = vendor == "NVIDIA" or (vendor == "AMD" and bool(AMD_SUPPORTED.search(name)))
        gpus.append(Gpu(name, vendor, vram, usable))
    return gpus


def apple_silicon_gpu() -> Gpu | None:
    """Unified memory, via sysctl. None on Intel Macs and everywhere else."""
    if sys.platform != "darwin":
        return None
    try:
        machine = os.uname().machine
    except AttributeError:
        return None
    if machine != "arm64":
        return None
    vram = None
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                             text=True, timeout=5, check=True).stdout
        vram = round(int(out.strip()) / 1024**3, 1)
    except (OSError, subprocess.SubprocessError, ValueError):
        vram = None
    return Gpu("Apple Silicon", "Apple", vram, True)


def detect_gpus() -> list[Gpu]:
    """All GPUs found, best candidates for Ollama first."""
    found = {g.name: g for g in windows_gpus()}
    for gpu in nvidia_gpus():  # nvidia-smi has the most accurate memory figure
        found[gpu.name] = gpu
    apple = apple_silicon_gpu()
    if apple:
        found[apple.name] = apple
    return sorted(found.values(), key=lambda g: (not g.ollama_can_use, -(g.vram_gb or 0)))


def model_vram(model: str) -> float | None:
    """Rough GPU memory needed for a model name, if known."""
    return MODEL_VRAM_GB.get(model) or MODEL_VRAM_GB.get(model.split(":")[0])


def gpu_report(model: str) -> tuple[str, list[str]]:
    """Return ('GPU'|'CPU', explanation lines) for the given LLM."""
    gpus = detect_gpus()
    best = next((g for g in gpus if g.ollama_can_use), None)
    need = model_vram(model)
    if not best:
        names = ", ".join(g.name for g in gpus) or "none found"
        return "CPU", [
            f"Graphics card(s): {names}",
            "No GPU that Ollama can use was found, so the model will run on the CPU.",
            f"Expect roughly 30-90 seconds per job with {model}. For faster ranking, set",
            '"model": "qwen2.5:7b" in config/models.json.',
        ]
    vram = f"{best.vram_gb:g} GB" if best.vram_gb else "unknown memory"
    lines = [f"GPU: {best.name} ({vram})"]
    if need and best.vram_gb and best.vram_gb < need:
        lines += [
            f"{model} needs about {need:g} GB of GPU memory, more than this card has.",
            "Ollama will split it between GPU and CPU, which is noticeably slower.",
            "A smaller model such as qwen2.5:7b would fit fully on the GPU.",
        ]
        return "GPU+CPU", lines
    lines.append(f"{model} should run fully on the GPU: expect a few seconds per job.")
    if best.vendor == "AMD":
        lines.append("(AMD cards use ROCm. Keep your Radeon driver up to date.)")
    return "GPU", lines


# ---------------------------------------------------------------- Ollama

def find_ollama() -> str | None:
    """Path to the ollama executable, checking PATH and the default install folder."""
    path = shutil.which("ollama")
    if path:
        return path
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    if default.exists():
        return str(default)
    if sys.platform != "darwin":
        return None
    mac_candidates = [
        Path("/Applications/Ollama.app/Contents/Resources/ollama"),
        Path.home() / "Applications/Ollama.app/Contents/Resources/ollama",
        Path("/usr/local/bin/ollama"),
        Path("/opt/homebrew/bin/ollama"),
    ]
    for candidate in mac_candidates:
        if candidate.exists():
            return str(candidate)
    return None


def ollama_running(host: str) -> bool:
    """True if the Ollama server answers on `host`."""
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=3) as resp:
            return resp.status == 200
    except OSError:
        return False


def start_ollama(exe: str, host: str, wait_seconds: int = 20) -> bool:
    """Start the Ollama server in the background and wait until it answers."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags)
    elif sys.platform == "darwin" and (
        Path("/Applications/Ollama.app").exists() or (Path.home() / "Applications/Ollama.app").exists()
    ):
        subprocess.Popen(["open", "-a", "Ollama"])
    else:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if ollama_running(host):
            return True
        time.sleep(1)
    return False


def installed_models(host: str) -> set[str]:
    """Names of models already downloaded, always with a tag (e.g. 'x:latest')."""
    with urllib.request.urlopen(f"{host}/api/tags", timeout=10) as resp:
        data = json.load(resp)
    return {m["name"] for m in data.get("models", [])}


def with_tag(name: str) -> str:
    """Ollama treats a bare model name as ':latest'."""
    return name if ":" in name else f"{name}:latest"


def models_to_pull() -> list[str]:
    """Ollama models named in models.json."""
    models = load_models()
    names = [models["llm"]["model"]]
    if models["embedding"]["provider"] == "ollama":
        names.append(models["embedding"]["model"])
    if documents_model_fits(models["documents"]["model"]):
        names.append(models["documents"]["model"])  # larger model that writes resumes and cover letters
    return list(dict.fromkeys(names))


def documents_model_fits(model: str) -> bool:
    """Skip the big writing model on machines whose GPU cannot hold it (documents then use the ranking model)."""
    need = model_vram(model)
    best = next((g for g in detect_gpus() if g.ollama_can_use), None)
    if need is None or need <= 10:
        return True
    return bool(best and (best.vram_gb is None or best.vram_gb >= need))


def print_install_help() -> None:
    """Explain how to install Ollama."""
    print("Ollama is not installed. It runs the AI model on your own computer (free, no account).")
    print()
    print(f"  1. Download it from {OLLAMA_DOWNLOAD}")
    print("  2. Run the installer and keep the defaults.")
    print(f"  3. Run {setup_hint()} again. It will download the models for you.")


def setup_ollama() -> int:
    """Make sure Ollama is installed and running, then pull any missing models."""
    exe = find_ollama()
    if not exe:
        print_install_help()
        return EXIT_NO_OLLAMA
    host = load_models()["llm"]["host"].rstrip("/")
    print(f"Ollama found: {exe}")
    if not ollama_running(host):
        print("Starting the Ollama server...")
        if not start_ollama(exe, host):
            if sys.platform == "win32":
                print("Could not start Ollama. Open the Ollama app from the Start menu, "
                      "then run setup.bat again.")
            else:
                print(f"Could not start Ollama. Open the Ollama app, then run {setup_hint()} again.")
            return 1
    have = installed_models(host)
    writer = load_models()["documents"]["model"]
    if not documents_model_fits(writer) and with_tag(writer) not in have:
        print(f"Skipping {writer} (writes tailored resumes): it needs about {model_vram(writer):g} GB of GPU "
              f"memory. Documents will be written with the ranking model instead. To get it anyway: "
              f"ollama pull {writer}")
    for name in models_to_pull():
        if with_tag(name) in have:
            print(f"Model {name}: already downloaded")
            continue
        size = f" (about {model_vram(name):g} GB)" if model_vram(name) else ""
        print(f"Model {name}: downloading{size}. This can take a while...")
        if subprocess.run([exe, "pull", name]).returncode != 0:
            print(f"Download of {name} failed. Check your internet connection and the model "
                  f"name in config/models.json, then run {setup_hint()} again.")
            return 1
    print("All models are ready.")
    return 0


def main(argv: list[str]) -> int:
    """Command line entry point."""
    command = argv[0] if argv else "gpu"
    if command == "gpu":
        mode, lines = gpu_report(load_models()["llm"]["model"])
        print(f"The AI model will run on: {mode}")
        for line in lines:
            print(f"  {line}")
        return 0
    if command == "ollama":
        return setup_ollama()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main(sys.argv[1:]))
