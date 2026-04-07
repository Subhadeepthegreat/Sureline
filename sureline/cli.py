"""
Sureline — Interactive CLI Test Runner

Test the full conversation engine from the command line (no voice).
This lets you verify the query engine, RAG, and LLM response generation
without needing STT/TTS API keys.

Usage:
    python -m sureline.cli

Features:
    - Hardware detection & model recommendation on startup
    - Interactive Q&A with Mahakash data
    - Shows SQL/Pandas queries generated
    - Shows timing breakdown
    - Session memory for multi-turn conversations
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sureline.observability.logger import setup_logging
from sureline.hardware.detector import detect_hardware
from sureline.hardware.model_selector import select_model, ensure_model_pulled, get_recommendation_report
from sureline.conversation.conversation_engine import ConversationEngine
from sureline.config import DB_PATH, DATA_DIR


def main():
    """Run the interactive CLI test."""
    setup_logging("INFO")

    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   🚀  SURELINE — Enterprise Voice Agent (CLI Mode)           ║
║                                                              ║
║   Mahakash Space Pvt. Ltd. — Data Assistant                  ║
║   "Reaching for the stars. Occasionally hitting pigeons."    ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # 1. Detect hardware
    print("🔍 Detecting hardware...")
    hw = detect_hardware()
    print(hw.summary())
    print()

    # 2. Select & prepare model
    print("🤖 Selecting optimal model...")
    model = select_model(hw)
    print(get_recommendation_report(hw))
    print()

    if not ensure_model_pulled(model):
        print(f"❌ Failed to pull model '{model.name}'. Is Ollama running?")
        print("   Start Ollama with:  ollama serve")
        sys.exit(1)

    # 3. Generate database if needed
    if not DB_PATH.exists():
        print("📊 Generating Mahakash database...")
        from data.seed_database import create_database
        create_database()
        print()

    # 4. Initialize conversation engine
    print("🧠 Initializing conversation engine...")
    engine = ConversationEngine(model_name=model.name)
    print("✅ Ready!\n")

    # 5. Interactive loop
    print("=" * 60)
    print("Ask questions about Mahakash Space. Type 'quit' to exit.")
    print("Try: 'What were our total sales?' or 'What is our leave policy?'")
    print("=" * 60)

    session_id = "cli_session"

    while True:
        try:
            question = input("\n❓ You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 Goodbye!")
            break

        if not question:
            continue

        if question.lower() in ("quit", "exit", "q"):
            print("\n👋 Goodbye! May your rockets always point up. 🚀")
            break

        # Process question
        result = engine.process_question(question, session_id=session_id)

        # Display answer
        print(f"\n🗣️  Sureline: {result['answer']}")

        # Show timing
        timing = result['timing']
        print(f"\n   ⏱️  Total: {timing['total_ms']:.0f}ms "
              f"(RAG: {timing['rag_ms']:.0f}ms, "
              f"Query: {timing['query_ms']:.0f}ms, "
              f"Response: {timing['response_gen_ms']:.0f}ms)")

        # Show query details if a DB query was made
        qr = result['query_result']
        if qr.generated_query:
            print(f"   📝 {qr.query_type.upper()}: {qr.generated_query[:150]}")
        if qr.row_count:
            print(f"   📊 Rows returned: {qr.row_count}")


if __name__ == "__main__":
    main()
