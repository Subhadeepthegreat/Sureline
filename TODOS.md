# Sureline — Deferred Work

Last updated: 2026-04-07 (from /plan-ceo-review)

---

## P2: Phase 1.5 (After WebRTC demo validates the platform)

### TODO-1: Telephony Integration (Plivo / Exotel)
**What:** SIP trunk integration for a real Indian phone number.
**Why:** The greenlight demo goal is "a real phone call in, correct answer out." WebRTC validates
the platform first; then Plivo makes it dialable from any phone.
**Effort:** S→M (CC: ~1 day) | **Cost:** ₹250-300/month for a number
**How to start:** Pipecat has built-in WebRTC transport. Add Plivo SIP transport after WebRTC validates.
Reference: pipecat-sarvam-handshake.md has integration notes.
**Blocked by:** Platform config layer working (Phase 1) + WebRTC Phase 1.5 demo.

### TODO-2: Lotus UI (Demo Surface + Voice Animation)
**What:** Web page with animated lotus SVG that expands/contracts with voice activity (Web Audio API).
**Why:** The visual demo makes the technology tangible for prospective clients.
States: Idle → Listening (grows with voice) → Thinking (gentle rotation) → Speaking (pulses with TTS).
**Design:** Reference image: `ChatGPT Image Apr 4, 2026, 05_10_49 PM.png` in project root.
**Effort:** M (CC: ~4 hours) | **Stack:** HTML/CSS/JS or React. WebRTC connects to Python backend.
**How to start:** `/design-consultation` or `/frontend-design` skill for the lotus animation.
**Blocked by:** WebRTC transport working (Phase 1.5).
**Note:** UI doubles as WhatsApp/Discord screen-share surface for remote demos.

---

## P3: Phase 2+ (Post-pilot)

### TODO-3: Rust Backend for Hot Path
**What:** Rewrite STT→context→TTS hot path in Rust for latency optimization.
**Why:** P95 < 1.5s may be unreachable in Python + cloud LLM after parallel optimization.
**Effort:** XL (human: ~3 months / CC: ~1 week)
**Blocked by:** P95 baseline measured on real call data. Only justified if P95 > 1.5s
persists after cloud LLM migration + parallel RAG+SQL.
**Context:** Perplexity released a voice architecture paper describing their approach. Review it before
starting. Also note: Pipecat is Python-native; Rust requires an FFI boundary or full rewrite.

### TODO-4: Docker Containerization
**What:** Package Sureline as a Docker image for per-client deployment on client cloud (Azure VM, etc.)
**Why:** "Manual setup per client" doesn't scale past 3 clients. Container reduces deployment from
5 days to < 1 day.
**Effort:** S (CC: ~2 hours)
**Blocked by:** 3+ live client deployments — proves containerization is worth the setup cost.

### TODO-5: RAG for Document Queries (Phase 2)
**What:** Enable ChromaDB-backed document retrieval for clients with unstructured data (policies, FAQs).
**Why:** ChromaDB is already in the codebase but frozen. Some clients may have hybrid data needs
(structured SQL + unstructured docs). Currently the RAG runs on every call but results are
supplementary to SQL.
**Effort:** M (CC: ~4 hours to make per-client configurable)
**How to start:** Add `rag_enabled: true` field to per-client YAML. When false, skip RAG call entirely.

### TODO-6: Azure Monitor / Datadog Integration (Observability)
**What:** Ship structured JSON logs (call_id, latency breakdown, error rates) to a monitoring platform.
**Why:** Phase 1 logs to stdout — fine for dev, not for 24/7 enterprise operations.
**Effort:** S (CC: ~2 hours)
**Blocked by:** First paid client with SLA requirements.

### TODO-7: OTP Caller Verification (SMS Provider)
**What:** OTP sent via SMS, caller reads back. Requires SMS provider (Twilio SMS, MSG91).
**Why:** Highest-assurance caller verification for financial services clients.
**Effort:** M
**Blocked by:** First client that requires OTP-level verification.
