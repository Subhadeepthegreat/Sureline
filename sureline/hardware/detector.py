"""
Sureline — Hardware Detector

Detects the current device's hardware capabilities (RAM, CPU, GPU)
so the model selector can pick the optimal Ollama model.
Works on Windows, macOS, and Linux.
"""

import platform
import subprocess
import json
from dataclasses import dataclass
from typing import Optional

import psutil


@dataclass
class GPUInfo:
    """Detected GPU information."""
    name: str
    vram_mb: int
    vendor: str  # "nvidia", "amd", "intel", "apple", "none"


@dataclass
class HardwareProfile:
    """Complete hardware profile of the current device."""
    cpu_name: str
    cpu_cores_physical: int
    cpu_cores_logical: int
    ram_total_mb: int
    ram_available_mb: int
    gpu: GPUInfo
    os_name: str
    os_version: str

    @property
    def ram_total_gb(self) -> float:
        return self.ram_total_mb / 1024

    @property
    def ram_available_gb(self) -> float:
        return self.ram_available_mb / 1024

    @property
    def has_dedicated_gpu(self) -> bool:
        return self.gpu.vendor in ("nvidia", "amd")

    @property
    def has_apple_silicon(self) -> bool:
        return self.gpu.vendor == "apple"

    def summary(self) -> str:
        """Human-readable summary of the hardware."""
        gpu_str = f"{self.gpu.name} ({self.gpu.vram_mb} MB VRAM)" if self.gpu.vendor != "none" else "No dedicated GPU"
        return (
            f"CPU: {self.cpu_name} ({self.cpu_cores_physical}C/{self.cpu_cores_logical}T)\n"
            f"RAM: {self.ram_total_gb:.1f} GB total, {self.ram_available_gb:.1f} GB available\n"
            f"GPU: {gpu_str}\n"
            f"OS:  {self.os_name} {self.os_version}"
        )


def _detect_gpu() -> GPUInfo:
    """Detect GPU — tries NVIDIA first, then falls back to platform-specific methods."""

    # Try NVIDIA via nvidia-smi
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            name = parts[0].strip()
            vram_mb = int(float(parts[1].strip()))
            return GPUInfo(name=name, vram_mb=vram_mb, vendor="nvidia")
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Try AMD via rocm-smi (Linux)
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            # Parse AMD GPU info from rocm-smi output
            for card_id, card_data in data.items():
                if "VRAM Total Memory" in str(card_data):
                    vram_bytes = int(card_data.get("VRAM Total Memory (B)", 0))
                    return GPUInfo(
                        name=f"AMD GPU ({card_id})",
                        vram_mb=vram_bytes // (1024 * 1024),
                        vendor="amd"
                    )
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass

    # Apple Silicon detection (macOS)
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        # On Apple Silicon, unified memory acts as GPU memory
        total_ram = psutil.virtual_memory().total // (1024 * 1024)
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            cpu_brand = result.stdout.strip() if result.returncode == 0 else "Apple Silicon"
        except Exception:
            cpu_brand = "Apple Silicon"
        return GPUInfo(name=cpu_brand, vram_mb=total_ram, vendor="apple")

    # Intel iGPU detection (Windows)
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                name = result.stdout.strip()
                if "intel" in name.lower():
                    return GPUInfo(name=name, vram_mb=0, vendor="intel")
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass

    return GPUInfo(name="None detected", vram_mb=0, vendor="none")


def _detect_cpu_name() -> str:
    """Get the CPU brand string."""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[1].strip()
        except Exception:
            pass

    return platform.processor() or "Unknown CPU"


def detect_hardware() -> HardwareProfile:
    """
    Detect the full hardware profile of the current device.

    Returns a HardwareProfile with CPU, RAM, GPU, and OS info.
    This runs cross-platform (Windows, macOS, Linux).
    """
    mem = psutil.virtual_memory()

    return HardwareProfile(
        cpu_name=_detect_cpu_name(),
        cpu_cores_physical=psutil.cpu_count(logical=False) or 1,
        cpu_cores_logical=psutil.cpu_count(logical=True) or 1,
        ram_total_mb=mem.total // (1024 * 1024),
        ram_available_mb=mem.available // (1024 * 1024),
        gpu=_detect_gpu(),
        os_name=platform.system(),
        os_version=platform.version(),
    )


if __name__ == "__main__":
    profile = detect_hardware()
    print("=" * 50)
    print("SURELINE — Hardware Detection")
    print("=" * 50)
    print(profile.summary())
    print(f"\nDedicated GPU: {profile.has_dedicated_gpu}")
    print(f"Apple Silicon: {profile.has_apple_silicon}")
