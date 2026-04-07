"""
Sureline — Ollama LLM Service

Wraps the Ollama LLM for use in the Pipecat pipeline.
Handles hardware-adaptive model selection on startup.
"""

import logging
from typing import Optional

from sureline.hardware.detector import detect_hardware
from sureline.hardware.model_selector import select_model, ensure_model_pulled, get_recommendation_report
from sureline.config import OLLAMA_BASE_URL

logger = logging.getLogger(__name__)


def create_llm_service(
    force_model: Optional[str] = None,
    prefer_family: Optional[str] = None,
):
    """
    Create an Ollama LLM service with hardware-adaptive model selection.

    This detects hardware, selects the optimal model, ensures it's pulled,
    and returns a Pipecat-compatible OLLamaLLMService instance.

    Args:
        force_model: Override auto-selection with a specific model name.
        prefer_family: Prefer a model family ("qwen", "phi", "gemma").

    Returns:
        Tuple of (pipecat_service, model_name) or (None, model_name) if
        Pipecat is not available.
    """
    # 1. Detect hardware
    hw = detect_hardware()
    logger.info(f"Hardware detected:\n{hw.summary()}")

    # 2. Select optimal model
    model = select_model(hw, prefer_family=prefer_family, force_model=force_model)
    logger.info(f"Selected model: {model.display_name} ({model.name})")

    # 3. Print recommendation report
    print(get_recommendation_report(hw))

    # 4. Ensure model is pulled
    if not ensure_model_pulled(model):
        logger.error(f"Failed to pull model {model.name}. Is Ollama running?")
        raise RuntimeError(
            f"Could not pull model '{model.name}'. "
            f"Make sure Ollama is running: ollama serve"
        )

    # 5. Try to create Pipecat service
    try:
        from pipecat.services.ollama import OLLamaLLMService

        llm_service = OLLamaLLMService(
            base_url=OLLAMA_BASE_URL + "/v1",
            settings=OLLamaLLMService.Settings(
                model=model.name,
            ),
        )
        logger.info(f"Pipecat OLLamaLLMService created for {model.name}")
        return llm_service, model.name

    except ImportError:
        logger.warning("Pipecat Ollama service not available. Returning model name only.")
        return None, model.name


def get_selected_model_name(
    force_model: Optional[str] = None,
    prefer_family: Optional[str] = None,
) -> str:
    """
    Just select and return the model name without creating a Pipecat service.
    Useful for standalone components like QueryEngine.
    """
    hw = detect_hardware()
    model = select_model(hw, prefer_family=prefer_family, force_model=force_model)
    return model.name
