"""
Sureline — Adaptive Model Selector

Automatically selects the best Ollama model for the current hardware.
Prioritises small, fast models with tool-calling support for voice agent
latency requirements (< 800 ms LLM inference).

Strategy:
  - Use TOOL CALLING instead of raw LLM generation (structured output is
    faster to generate and far more reliable on small models).
  - Prefer models with native function-calling support.
  - Rank by: speed > tool-call reliability > general quality.

Model tiers (all support Ollama tool calling):
  Tier 1 (ultra-fast, < 2 GB):  qwen2.5:1.5b, phi3.5:3.8b-mini
  Tier 2 (fast, 2-5 GB):        qwen2.5:3b, phi4-mini, gemma3:4b
  Tier 3 (balanced, 5-10 GB):   qwen2.5:7b, gemma3:12b
  Tier 4 (quality, 10-20 GB):   qwen2.5:14b
"""

import subprocess
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from sureline.hardware.detector import HardwareProfile

logger = logging.getLogger(__name__)


@dataclass
class ModelOption:
    """An Ollama model candidate with metadata."""
    name: str                  # Ollama model tag (e.g. "qwen2.5:3b")
    display_name: str          # Human-friendly name
    size_gb: float             # Approximate download / VRAM size
    min_ram_gb: float          # Minimum RAM to run comfortably
    tool_calling: bool         # Native tool-calling support
    speed_tier: int            # 1=fastest, 4=slowest
    quality_tier: int          # 1=basic, 4=best
    family: str                # "qwen", "phi", "gemma"
    notes: str = ""


# ─── Model Registry ─────────────────────────────────────────────
# Ordered by speed (fastest first). All support tool calling.

MODEL_REGISTRY: list[ModelOption] = [
    # === Tier 1: Ultra-fast (< 2 GB) ===
    ModelOption(
        name="qwen2.5:1.5b",
        display_name="Qwen 2.5 1.5B",
        size_gb=1.0,
        min_ram_gb=4,
        tool_calling=True,
        speed_tier=1,
        quality_tier=1,
        family="qwen",
        notes="Smallest Qwen with tool support. Very fast on CPU.",
    ),
    ModelOption(
        name="qwen2.5:0.5b",
        display_name="Qwen 2.5 0.5B",
        size_gb=0.4,
        min_ram_gb=2,
        tool_calling=True,
        speed_tier=1,
        quality_tier=1,
        family="qwen",
        notes="Tiny. Good for routing/tool selection only.",
    ),

    # === Tier 2: Fast (2–5 GB) ===
    ModelOption(
        name="qwen2.5:3b",
        display_name="Qwen 2.5 3B",
        size_gb=2.0,
        min_ram_gb=6,
        tool_calling=True,
        speed_tier=2,
        quality_tier=2,
        family="qwen",
        notes="Best balance of speed and quality for tool calling.",
    ),
    ModelOption(
        name="phi4-mini",
        display_name="Phi-4 Mini (3.8B)",
        size_gb=2.5,
        min_ram_gb=6,
        tool_calling=True,
        speed_tier=2,
        quality_tier=2,
        family="phi",
        notes="Microsoft's compact model. Excellent function calling.",
    ),
    ModelOption(
        name="gemma3:4b",
        display_name="Gemma 3 4B",
        size_gb=3.0,
        min_ram_gb=8,
        tool_calling=True,
        speed_tier=2,
        quality_tier=2,
        family="gemma",
        notes="Google's small model. Good instruction following.",
    ),

    # === Tier 3: Balanced (5–10 GB) ===
    ModelOption(
        name="qwen2.5:7b",
        display_name="Qwen 2.5 7B",
        size_gb=4.5,
        min_ram_gb=12,
        tool_calling=True,
        speed_tier=3,
        quality_tier=3,
        family="qwen",
        notes="Strong all-rounder. Good SQL generation.",
    ),

    # === Tier 4: Quality (10–20 GB) ===
    ModelOption(
        name="qwen2.5:14b",
        display_name="Qwen 2.5 14B",
        size_gb=9.0,
        min_ram_gb=20,
        tool_calling=True,
        speed_tier=4,
        quality_tier=4,
        family="qwen",
        notes="Highest quality. May be too slow for real-time voice on CPU.",
    ),
]


def _get_ollama_models() -> list[str]:
    """Get list of models already pulled in Ollama."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")[1:]  # skip header
            return [line.split()[0] for line in lines if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        logger.warning(f"Could not list Ollama models: {e}")
    return []


def select_model(
    hw: HardwareProfile,
    prefer_family: Optional[str] = None,
    max_speed_tier: int = 3,
    force_model: Optional[str] = None,
) -> ModelOption:
    """
    Select the optimal Ollama model based on hardware capabilities.

    Args:
        hw: Hardware profile from detector.
        prefer_family: Prefer a specific model family ("qwen", "phi", "gemma").
        max_speed_tier: Maximum speed tier to consider (1=fastest only, 4=all).
        force_model: Override — use this exact model name.

    Returns:
        The best ModelOption for this hardware.
    """
    if force_model:
        for m in MODEL_REGISTRY:
            if m.name == force_model:
                return m
        # If forced model not in registry, create a stub entry
        return ModelOption(
            name=force_model,
            display_name=force_model,
            size_gb=0,
            min_ram_gb=0,
            tool_calling=True,
            speed_tier=2,
            quality_tier=2,
            family="custom",
            notes="User-specified model override.",
        )

    available_ram_gb = hw.ram_available_gb

    # Filter: fits in RAM and within speed tier
    candidates = [
        m for m in MODEL_REGISTRY
        if m.min_ram_gb <= available_ram_gb and m.speed_tier <= max_speed_tier
    ]

    if not candidates:
        # Fallback to absolute smallest
        logger.warning("No model fits available RAM. Falling back to smallest model.")
        return MODEL_REGISTRY[1]  # qwen2.5:0.5b

    # Prefer already-pulled models (avoid download wait)
    pulled = _get_ollama_models()
    pulled_candidates = [m for m in candidates if m.name in pulled]

    pool = pulled_candidates if pulled_candidates else candidates

    # If user prefers a family, filter for it
    if prefer_family:
        family_pool = [m for m in pool if m.family == prefer_family]
        if family_pool:
            pool = family_pool

    # For voice agent: prioritise SPEED, then QUALITY
    # Pick the model with best (lowest) speed tier, then highest quality
    pool.sort(key=lambda m: (m.speed_tier, -m.quality_tier))

    # But for voice agent latency, prefer the fastest model that has
    # quality >= 2 (to avoid the 0.5B model for SQL generation)
    good_pool = [m for m in pool if m.quality_tier >= 2]
    selected = good_pool[0] if good_pool else pool[0]

    logger.info(
        f"Model selected: {selected.display_name} "
        f"(speed={selected.speed_tier}, quality={selected.quality_tier}, "
        f"size={selected.size_gb}GB, family={selected.family})"
    )
    return selected


def ensure_model_pulled(model: ModelOption) -> bool:
    """
    Ensure the selected model is pulled in Ollama.
    Returns True if model is ready, False if pull failed.
    """
    pulled = _get_ollama_models()
    if model.name in pulled:
        logger.info(f"Model '{model.name}' is already available.")
        return True

    logger.info(f"Pulling model '{model.name}' (~{model.size_gb} GB)... this may take a few minutes.")
    try:
        result = subprocess.run(
            ["ollama", "pull", model.name],
            capture_output=False, timeout=600  # 10 min timeout
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error(f"Failed to pull model '{model.name}': {e}")
        return False


def get_recommendation_report(hw: HardwareProfile) -> str:
    """Generate a human-readable report of model recommendations for this hardware."""
    available_ram = hw.ram_available_gb
    pulled = _get_ollama_models()

    lines = [
        "╔══════════════════════════════════════════════════╗",
        "║     SURELINE — Model Recommendation Report      ║",
        "╠══════════════════════════════════════════════════╣",
        f"║  Available RAM: {available_ram:.1f} GB",
        f"║  GPU: {hw.gpu.name} ({hw.gpu.vendor})",
        f"║  CPU: {hw.cpu_name}",
        "╠══════════════════════════════════════════════════╣",
        "║  Model Options (sorted by speed):                ║",
        "╠══════════════════════════════════════════════════╣",
    ]

    for m in MODEL_REGISTRY:
        fits = "✅" if m.min_ram_gb <= available_ram else "❌"
        pulled_tag = " [PULLED]" if m.name in pulled else ""
        lines.append(
            f"║  {fits} {m.display_name:<22} "
            f"~{m.size_gb:.1f}GB  speed={m.speed_tier}  qual={m.quality_tier}"
            f"{pulled_tag}"
        )

    selected = select_model(hw)
    lines.extend([
        "╠══════════════════════════════════════════════════╣",
        f"║  ➤ Recommended: {selected.display_name}",
        f"║    {selected.notes}",
        "╚══════════════════════════════════════════════════╝",
    ])

    return "\n".join(lines)


if __name__ == "__main__":
    from sureline.hardware.detector import detect_hardware

    hw = detect_hardware()
    print(hw.summary())
    print()
    print(get_recommendation_report(hw))
