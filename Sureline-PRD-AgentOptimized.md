# AI-Agent Optimized PRD — Sureline Voice Agent

## Execution Instruction for AI Agent

1. Read this PRD fully.
2. Break tasks into executable steps.
3. Implement tasks sequentially.
4. After each task, run validation.
5. Update task status.

---

## 1. Product Overview

**Product Name:** Sureline — Enterprise Voice Agent  
**Version:** 0.1  
**Owner:** Subhadeep M  
**Last Updated:** 2026-04-04  

**Summary:**  
Sureline is a real-time voice agent that operates on enterprise documents and data. Clients speak to the agent over a telephone or web interface; the agent converts speech to text, executes queries (SQL, Python/Pandas) against company databases and Excel files, retrieves results, and responds with natural-sounding speech — all within a low-latency, streaming pipeline. The system is built on the Pipecat framework to enable audio streaming and user interruption (barge-in), and targets minimal recurring cost by leveraging pay-as-you-go services and existing Azure infrastructure.

**Primary Objective:**  
Build a production-grade voice agent that converts client speech → text → intelligent data query → spoken answer with end-to-end latency < 2 seconds, at the lowest possible per-interaction cost.

---

## 2. Problem Statement

**User Problem:**  
Enterprise clients need quick, conversational answers from internal company data (databases, Excel files, documents). Today this requires a human analyst to receive the request, manually query the data, and respond — introducing delays of minutes to hours.

**Current Alternatives:**  
- Manual analyst queries via email, chat, or phone callback.
- Static dashboard/BI tools that require the client to learn a UI and self-serve.
- Generic chatbot solutions that cannot execute live data queries or support voice.

**Why Existing Solutions Are Insufficient:**  
- Manual workflows are slow and do not scale.
- BI dashboards require training and are not accessible via phone.
- Off-the-shelf voice assistants (Alexa, Google Assistant) cannot query private enterprise data and lack SQL/Pandas execution capabilities.
- Most voice-AI solutions have high latency (> 5 s) or high recurring costs, making them impractical for sustained enterprise use.

---

## 3. Success Criteria (Measurable)

| # | Metric | Definition | Target |
|---|--------|-----------|--------|
| 1 | End-to-end latency | Time from end of user utterance to start of agent speech response | < 2 seconds |
| 2 | Speech-to-text accuracy | Word-error rate on English business speech | > 92 % |
| 3 | Query success rate | Percentage of natural-language questions correctly translated to SQL/Pandas and returning correct results | > 85 % |
| 4 | System uptime | Availability during business hours | > 99 % |
| 5 | Cost per interaction | Average cost of one question-answer exchange (STT + LLM + TTS) | < ₹1 (≈ $0.012) |

---

## 4. System Capabilities

| # | Capability | Description | Priority |
|---|-----------|-------------|----------|
| 1 | Speech Recognition (STT) | Convert incoming client audio to text using AssemblyAI, Groq Whisper, or Azure Speech Services | High |
| 2 | Natural-Language Data Querying | Translate transcribed text into SQL or Pandas queries against company databases/Excel/CSV files | High |
| 3 | LLM-Powered Conversation Engine | Generate contextual, human-friendly answers from query results using a local or hosted LLM with vector-DB RAG | High |
| 4 | Text-to-Speech Synthesis (TTS) | Convert the LLM response to natural-sounding speech via ElevenLabs or Azure TTS | High |
| 5 | Streaming & Barge-In | Use the Pipecat framework to stream audio/text through the pipeline and allow the user to interrupt the agent mid-response | High |
| 6 | Telephony Integration | Connect the voice pipeline to a phone number via Plivo, Exotel, or similar provider so clients can call in | Medium |
| 7 | Session Memory | Maintain conversation context within a call for multi-turn interactions | Medium |
| 8 | Azure Service Integration | Leverage the company's existing Azure subscription for STT/TTS to reduce external costs | Medium |
| 9 | High-Performance Backend (Rust) | Explore Rust for latency-critical parts of the agentic backend to achieve lower overhead than pure Python | Low |

---

## 5. Functional Requirements

### REQ-001 — Speech to Text
**Description:** Capture client audio and transcribe to text in real time.

| Field | Detail |
|-------|--------|
| **Input** | Audio stream (16 kHz, mono) from telephony or web client |
| **Output** | Transcribed text string |

**Steps:**
1. Receive audio chunks via Pipecat audio input pipeline.
2. Stream chunks to the configured STT provider (AssemblyAI / Groq Whisper / Azure Speech).
3. Return partial and final transcription results.

**Acceptance Criteria:**
- Transcription latency < 500 ms from end of utterance.
- Word-error rate < 8 % on clear English speech.
- Gracefully handle silence and background noise.

---

### REQ-002 — Data Query Execution
**Description:** Convert the transcribed natural-language question into a data query and execute it.

| Field | Detail |
|-------|--------|
| **Input** | Transcribed text + schema/metadata of connected data sources |
| **Output** | Structured query result (rows, values, summaries) |

**Steps:**
1. Send transcribed text + data schema to LLM to generate SQL or Pandas code.
2. Execute the generated query against the connected database or loaded DataFrame.
3. Return results to the conversation engine.

**Acceptance Criteria:**
- Correct SQL/Pandas generated for ≥ 85 % of test questions.
- Read-only queries only — no mutations allowed.
- Execution timeout of 5 s with graceful error message on failure.

---

### REQ-003 — LLM Response Generation
**Description:** Synthesise a natural-language answer from the query results.

| Field | Detail |
|-------|--------|
| **Input** | Query result + conversation history |
| **Output** | Natural-language text response |

**Steps:**
1. Format query results into a prompt with conversation context.
2. Call the LLM (local or API) to generate a concise spoken answer.
3. Return text to the TTS module.

**Acceptance Criteria:**
- Response is factual and sourced from the query result.
- Response length is suitable for speech (1–3 sentences by default).
- LLM inference latency < 800 ms.

---

### REQ-004 — Text to Speech
**Description:** Convert the LLM text response into speech audio.

| Field | Detail |
|-------|--------|
| **Input** | Text string |
| **Output** | Audio stream (PCM/WAV) |

**Steps:**
1. Send text to the configured TTS provider (ElevenLabs / Azure TTS).
2. Stream audio chunks back to the client in real time.

**Acceptance Criteria:**
- Time-to-first-byte of audio < 300 ms.
- Natural, human-like prosody.
- Support streaming output for long responses.

---

### REQ-005 — User Interruption (Barge-In)
**Description:** Allow the client to interrupt the agent while it is speaking.

| Field | Detail |
|-------|--------|
| **Input** | New audio from user while TTS output is playing |
| **Output** | TTS playback stops; new STT cycle begins |

**Steps:**
1. Pipecat VAD (voice activity detection) detects user speech during TTS playback.
2. Immediately cancel TTS audio output.
3. Route new audio to STT and restart the pipeline.

**Acceptance Criteria:**
- Interruption detected within 300 ms of user speech onset.
- No overlapping audio artifacts.

---

### REQ-006 — Telephony Integration
**Description:** Connect the voice pipeline to a rented phone number.

| Field | Detail |
|-------|--------|
| **Input** | Inbound phone call |
| **Output** | Bidirectional audio stream to/from the voice pipeline |

**Steps:**
1. Rent a phone number via Plivo / Exotel (≈ ₹250–300/month).
2. Configure SIP/WebSocket bridge to Pipecat.
3. Route inbound call audio to STT and return TTS audio to the caller.

**Acceptance Criteria:**
- Calls connect within 3 seconds.
- Audio quality is clear at 8 kHz G.711 or higher.
- Graceful handling of call drop and reconnect.

---

## 6. Task Decomposition (Agent Execution Plan)

### TASK-01 — Environment Setup
| Field | Detail |
|-------|--------|
| **Dependencies** | None |
| **Inputs** | None |
| **Outputs** | Configured project directory, virtual environment, installed base packages |

**Steps:**
1. Create project directory structure.
2. Set up Python 3.10+ virtual environment.
3. Install Pipecat framework and core dependencies.
4. Configure `.env` with API keys for STT, TTS, LLM.

**Validation:** `python -c "import pipecat"` succeeds; `.env` file present with all required keys.

---

### TASK-02 — Implement STT Module
| Field | Detail |
|-------|--------|
| **Dependencies** | TASK-01 |
| **Inputs** | Audio file / stream |
| **Outputs** | `stt_module.py` — working transcription module |

**Steps:**
1. Integrate AssemblyAI streaming SDK (or Groq Whisper / Azure Speech as alternatives).
2. Write `transcribe_stream(audio_chunks) → text` function.
3. Test with sample `.wav` file.

**Validation:** Transcription of `test_audio.wav` returns expected text with WER < 8 %.

---

### TASK-03 — Implement Data Query Engine
| Field | Detail |
|-------|--------|
| **Dependencies** | TASK-01 |
| **Inputs** | Natural-language question, data source metadata |
| **Outputs** | `query_engine.py` — NL-to-SQL/Pandas module |

**Steps:**
1. Load database schema / Excel metadata.
2. Construct LLM prompt with schema context.
3. Generate and execute SQL or Pandas query.
4. Return result rows.

**Validation:** 5 sample questions return correct results against test dataset.

---

### TASK-04 — Implement LLM Conversation Engine
| Field | Detail |
|-------|--------|
| **Dependencies** | TASK-01 |
| **Inputs** | Query results, conversation history |
| **Outputs** | `conversation_engine.py` — response generation module |

**Steps:**
1. Set up local vector DB (ChromaDB / FAISS) for document RAG context.
2. Implement LLM call with conversation history and query results.
3. Format response for speech output (concise, spoken style).

**Validation:** LLM generates accurate, concise answers for 5 test queries.

---

### TASK-05 — Implement TTS Module
| Field | Detail |
|-------|--------|
| **Dependencies** | TASK-01 |
| **Inputs** | Text string |
| **Outputs** | `tts_module.py` — speech synthesis module |

**Steps:**
1. Integrate ElevenLabs streaming API (or Azure TTS as alternative).
2. Write `synthesize_stream(text) → audio_chunks` function.
3. Test with sample text.

**Validation:** Generated audio plays correctly; TTFB < 300 ms.

---

### TASK-06 — Integrate Pipecat Streaming Pipeline
| Field | Detail |
|-------|--------|
| **Dependencies** | TASK-02, TASK-03, TASK-04, TASK-05 |
| **Inputs** | All modules |
| **Outputs** | `pipeline.py` — end-to-end voice agent |

**Steps:**
1. Wire STT → Query Engine → Conversation Engine → TTS in a Pipecat pipeline.
2. Implement barge-in / interruption handling.
3. Add session memory for multi-turn conversations.
4. Run end-to-end test.

**Validation:** Speak a data question → receive a correct spoken answer within 2 s.

---

### TASK-07 — Telephony Integration
| Field | Detail |
|-------|--------|
| **Dependencies** | TASK-06 |
| **Inputs** | Working pipeline |
| **Outputs** | Telephony-connected voice agent |

**Steps:**
1. Sign up for Plivo / Exotel and rent a number.
2. Configure SIP/WebSocket bridge.
3. Test inbound call → pipeline → response.

**Validation:** Successful phone call with correct spoken answer.

---

### TASK-08 — Azure STT/TTS Evaluation
| Field | Detail |
|-------|--------|
| **Dependencies** | TASK-02, TASK-05 |
| **Inputs** | Company Azure subscription credentials |
| **Outputs** | Benchmark report comparing Azure vs external providers |

**Steps:**
1. Set up Azure Speech Services (STT + TTS).
2. Run the same test suite used for AssemblyAI/ElevenLabs.
3. Compare latency, accuracy, and cost.

**Validation:** Comparison table with latency, accuracy, and cost-per-request.

---

### TASK-09 — Rust Backend Exploration (Stretch)
| Field | Detail |
|-------|--------|
| **Dependencies** | TASK-06 |
| **Inputs** | Working Python pipeline |
| **Outputs** | Feasibility report + prototype of latency-critical path in Rust |

**Steps:**
1. Research Perplexity's published voice-agent architecture.
2. Identify pipeline bottlenecks suitable for Rust rewrite.
3. Prototype a Rust module for one bottleneck.
4. Benchmark against Python equivalent.

**Validation:** Rust module runs faster with equivalent output.

---

## 7. System Architecture

### Components
- **Telephony Layer** — Plivo / Exotel SIP bridge
- **Pipecat Streaming Framework** — orchestrates audio-in → text → response → audio-out
- **STT Service** — AssemblyAI / Groq Whisper / Azure Speech
- **Query Engine** — NL-to-SQL/Pandas against company databases & Excel
- **LLM Service** — Local or API-based LLM with vector-DB RAG
- **TTS Service** — ElevenLabs / Azure TTS
- **Memory Store** — Conversation history + vector DB (ChromaDB / FAISS)

### Architecture Diagram

```
  Client (Phone / Web)
         ↓  audio
  ┌──────────────────┐
  │  Telephony Layer  │  Plivo / Exotel
  └────────┬─────────┘
           ↓  audio stream
  ┌──────────────────┐
  │   Pipecat Core   │  streaming orchestration + barge-in
  │                  │
  │  ┌─────┐  ┌──────────┐  ┌──────────────┐  ┌─────┐  │
  │  │ STT │→ │ Query    │→ │ Conversation │→ │ TTS │  │
  │  │     │  │ Engine   │  │ Engine (LLM) │  │     │  │
  │  └─────┘  └──────────┘  └──────────────┘  └─────┘  │
  │                  ↕                                  │
  │          ┌───────────────┐                          │
  │          │  Data Sources │  SQL DB / Excel / Vector DB│
  │          └───────────────┘                          │
  └──────────────────┘
           ↓  audio stream
       Client hears response
```

---

## 8. Interfaces

### STT Service Interface
| Field | Detail |
|-------|--------|
| **Endpoint** | Streaming WebSocket or REST |
| **Input** | `{ "audio": "<base64 PCM chunks>" }` |
| **Output** | `{ "transcript": "string", "is_final": bool }` |

### Query Engine Interface
| Field | Detail |
|-------|--------|
| **Function** | `execute_query(question: str, schema: dict) → dict` |
| **Input** | `{ "question": "What were Q3 sales?", "schema": {...} }` |
| **Output** | `{ "result": [...], "query": "SELECT ...", "success": bool }` |

### LLM Service Interface
| Field | Detail |
|-------|--------|
| **Function** | `generate_response(query_result: dict, history: list) → str` |
| **Input** | `{ "query_result": {...}, "history": [...] }` |
| **Output** | `{ "response": "Q3 sales were ₹4.2 crore, up 12% from Q2." }` |

### TTS Service Interface
| Field | Detail |
|-------|--------|
| **Endpoint** | Streaming WebSocket or REST |
| **Input** | `{ "text": "string", "voice_id": "string" }` |
| **Output** | `{ "audio": "<streaming PCM chunks>" }` |

---

## 9. Data Structures

### ConversationTurn
```json
{
  "id": "string (UUID)",
  "session_id": "string (UUID)",
  "user_transcript": "string",
  "generated_query": "string (SQL or Pandas code)",
  "query_result": "object",
  "assistant_response": "string",
  "latency_ms": "integer",
  "timestamp": "datetime (ISO 8601)"
}
```

### DataSourceConfig
```json
{
  "source_type": "sql | excel | csv",
  "connection_string": "string",
  "schema_metadata": {
    "tables": [
      {
        "name": "string",
        "columns": [
          { "name": "string", "type": "string" }
        ]
      }
    ]
  }
}
```

---

## 10. Constraints

| Constraint | Detail |
|-----------|--------|
| **Compute** | Must run on standard CPU; GPU optional for faster inference |
| **Latency** | End-to-end < 2 seconds |
| **Cost — fixed** | Telephony number rental ≈ ₹250–300/month; all else pay-as-you-go |
| **Cost — variable** | Minimise per-interaction cost; prefer Azure (company subscription) over paid APIs where quality is comparable |
| **Data security** | All company data stays local; only anonymised text sent to external STT/LLM/TTS APIs |
| **Framework** | Pipecat for streaming orchestration |
| **Language** | Python primary; Rust optional for performance-critical paths |

---

## 11. Edge Cases

| # | Case | Expected Behaviour |
|---|------|--------------------|
| 1 | Empty / silent audio input | Agent prompts: "I didn't catch that, could you repeat?" after 5 s of silence |
| 2 | Network failure to STT/TTS provider | Retry up to 3 times with exponential backoff; fall back to alternate provider if available |
| 3 | Unrecognised or ambiguous question | Agent asks a clarifying question instead of guessing |
| 4 | Query returns no results | Agent responds: "I couldn't find any data matching your question." |
| 5 | User interrupts agent mid-speech | TTS playback stops within 300 ms; new STT cycle begins |
| 6 | Extremely long query result | Summarise top results and offer to provide more detail |
| 7 | Concurrent calls (future) | Queue or reject with a polite message until multi-call support is built |

---

## 12. Observability

**Logs:**
- Pipeline start / end
- STT request sent / transcript received
- Query generated / executed / result returned
- LLM prompt sent / response received
- TTS request sent / audio sent to client
- Errors and retries

**Metrics:**
- End-to-end latency (p50, p95, p99)
- STT latency & word-error rate
- LLM inference latency
- TTS time-to-first-byte
- Query success rate
- Error rate by component
- Cost per interaction

**Alerts:**
- Error rate > 5 % over 5-minute window
- p95 latency > 3 seconds
- STT provider unreachable for > 30 seconds

---

## 13. Acceptance Tests

| # | Test | Input | Expected Output |
|---|------|-------|-----------------|
| 1 | STT accuracy | `test_audio.wav` saying "What were our total sales last quarter?" | Transcript matches with WER < 8 % |
| 2 | Query generation | Text: "What were our total sales last quarter?" | Valid SQL: `SELECT SUM(sales) FROM transactions WHERE quarter = 'Q3'` (or equivalent) |
| 3 | LLM response | Query result: `{ "total_sales": 4200000 }` | "Total sales last quarter were ₹42 lakh." |
| 4 | TTS output | Text: "Total sales last quarter were 42 lakh rupees." | Clear, natural audio; TTFB < 300 ms |
| 5 | End-to-end pipeline | Spoken question about company data | Correct spoken answer within 2 seconds |
| 6 | Barge-in | User interrupts mid-response | Agent stops speaking and processes new input |

---

## 14. Definition of Done

- [ ] All TASK-01 through TASK-07 completed and validated.
- [ ] All acceptance tests (§13) pass.
- [ ] End-to-end latency consistently < 2 seconds on test hardware.
- [ ] Cost per interaction confirmed < ₹1.
- [ ] Telephony integration working with a live phone number.
- [ ] Documentation generated for setup, configuration, and usage.
- [ ] Observability (logs, metrics, alerts) operational.

---

## 15. Future Enhancements

- **Multi-language support** — Hindi, regional languages via multilingual STT/TTS.
- **Multi-tenant telephony** — Support concurrent inbound calls.
- **Persistent memory** — Cross-session memory for returning callers.
- **Rust rewrite** — High-performance backend for latency-critical pipeline stages.
- **Write-back operations** — Allow the agent to update records (with auth & approval flow).
- **Web/mobile UI** — Visual dashboard alongside voice for rich data display.
- **Fine-tuned domain LLM** — Company-specific fine-tuning for higher query accuracy.
