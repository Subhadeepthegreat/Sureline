Here is the public-docs, repo-example, and release-notes map of how Sarvam works with Pipecat for STT and TTS. Pipecat exposes Sarvam as first-class services on both sides of the voice pipeline: `SarvamSTTService` for streaming speech recognition and `SarvamTTSService` / `SarvamHttpTTSService` for synthesis. The Sarvam extra installs with `pip install "pipecat-ai[sarvam]"`, and Pipecat’s supported-services page lists Sarvam under both STT and TTS. ([docs.pipecat.ai][1])

At the pipeline level, the shipped Sarvam example wires the flow as `transport.input() -> stt -> user_aggregator -> llm -> tts -> transport.output() -> assistant_aggregator`, and enables interruptions plus metrics in `PipelineTask`. In the current example, Pipecat uses `SarvamSTTService(model="saaras:v3")` and `SarvamTTSService(model="bulbul:v3", voice="shubh")`, with a local Silero VAD in the user aggregator and an initial `LLMRunFrame()` when the client connects. ([GitHub][2])

## STT: what Pipecat is doing with Sarvam

`SarvamSTTService` is a WebSocket-based streaming STT service. Pipecat’s STT docs say the service supports real-time recognition with VAD and multiple audio formats, and the current docs mark the older constructor-style parameters as deprecated in favor of `settings=SarvamSTTService.Settings(...)`. The settings API exposes `model`, `language`, `prompt`, `vad_signals`, and `high_vad_sensitivity`, and Pipecat validates which combinations are legal for the chosen Sarvam model. ([docs.pipecat.ai][3])

The model behavior matters. Pipecat documents three relevant STT model families: `saarika:v2.5` for same-language transcription, `saaras:v2.5` for translation with auto-detected language, and `saaras:v3` for the newer mode-based flow. For `saaras:v3`, Pipecat documents support for `mode` values like `transcribe`, `translate`, `verbatim`, `translit`, and `codemix`, plus `prompt`. It also documents that `prompt` is not valid on `saarika:v2.5`, and that `language` is ignored by `saaras:v2.5` because that model auto-detects language. ([docs.pipecat.ai][3])

The other important STT knob is VAD routing. Pipecat says that with `vad_signals=False` it relies on Pipecat’s local VAD and flushes the server buffer on `VADUserStoppedSpeakingFrame`, while `vad_signals=True` switches to Sarvam’s server-side VAD and lets Sarvam emit `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame`. That means the practical “who decides turn boundaries?” choice is part of the STT config, not just the transport. ([docs.pipecat.ai][3])

Pipecat also added keepalive support to the base STT layer, not just websocket-specific STT classes. The Sarvam STT docs expose `keepalive_timeout` and `keepalive_interval`; the docs say `keepalive_timeout` is the seconds of no audio before silence is sent to keep the connection alive, and the release notes say this keepalive mechanism was moved into `STTService` so any STT service can use it. Pipecat’s release notes also state that `SarvamSTTService` received keepalive support to prevent idle connection timeouts. ([docs.pipecat.ai][3])

Sarvam’s own STT docs line up with that model. Sarvam says its STT API offers REST, Batch, and Streaming APIs, and that the Streaming API is for real-time WebSocket transcription. The docs say the streaming STT API supports only WAV and raw PCM for real-time streaming, and that raw PCM must be 16 kHz. Sarvam also says Saaras v3 is the recommended model, with 22 Indian languages plus English and code-mixing support. ([Sarvam AI Developer Documentation][4])

## TTS: what Pipecat is doing with Sarvam

`SarvamTTSService` is Pipecat’s real-time WebSocket TTS path, and `SarvamHttpTTSService` is the simpler HTTP fallback. Pipecat’s TTS docs say Sarvam’s service is specialized for Indian languages and voices, with runtime settings for `model`, `voice`, `language`, `enable_preprocessing`, `pace`, `pitch`, `loudness`, `temperature`, `min_buffer_size`, and `max_chunk_length`. Pipecat also marks the old constructor-style parameters as deprecated in favor of `SarvamTTSService.Settings(...)` and `SarvamHttpTTSService.Settings(...)`. ([docs.pipecat.ai][5])

The Sarvam TTS model split is also important. Pipecat documents `bulbul:v2`, `bulbul:v3-beta`, and `bulbul:v3`, and says `bulbul:v2` supports pitch and loudness control, while the v3 models add temperature but do not support pitch or loudness. Default speaker and sample rate also differ by model: v2 defaults to `anushka` and 22050 Hz, while v3 defaults to `shubh` and 24000 Hz. ([docs.pipecat.ai][5])

Sarvam’s own streaming TTS WebSocket is a persistent connection: connect once, send a config message first, then stream text, flush when needed, and send ping messages to keep the connection alive. Sarvam says the streaming API sends audio chunks progressively as text is processed, and its examples show the `config -> text -> flush` pattern with optional completion events. The docs list output audio formats including `mp3`, `wav`, `aac`, `opus`, `flac`, `pcm`, `mulaw`, and `alaw`. ([Sarvam AI Developer Documentation][6])

Pipecat’s current Sarvam TTS docs say the WebSocket service exposes standard connection events: `on_connected`, `on_disconnected`, and `on_connection_error`. The Sarvam release notes also matter here: Pipecat fixed `SarvamTTSService` so audio and error frames route through `append_to_audio_context()` instead of `push_frame()`, which is the change that makes interruptions and audio context ordering behave correctly. ([docs.pipecat.ai][5])

## The practical code shape

A minimal modern Pipecat Sarvam setup looks like this in structure, even when the exact service settings vary by language and model:

```python
stt = SarvamSTTService(
    api_key=os.getenv("SARVAM_API_KEY"),
    settings=SarvamSTTService.Settings(
        model="saaras:v3",
        language=Language.HI_IN,
        mode="transcribe",
        prompt="Transcribe Hindi conversation about technology.",
    ),
)

tts = SarvamTTSService(
    api_key=os.getenv("SARVAM_API_KEY"),
    settings=SarvamTTSService.Settings(
        model="bulbul:v3",
        voice="shubh",
        language=Language.HI,
    ),
)

pipeline = Pipeline([
    transport.input(),
    stt,
    user_aggregator,
    llm,
    tts,
    transport.output(),
    assistant_aggregator,
])
```

That shape matches the official Pipecat example and the current docs pattern: settings objects instead of deprecated constructor args, local VAD for user turn detection unless you enable Sarvam server-side VAD, and `allow_interruptions=True` at the task level for clean barge-in behavior. ([GitHub][2])

## Important caveat on versions

A recent Pipecat issue reports that the `pipecat-ai[sarvam]` extra was still pinning `sarvamai==0.1.21`, which did not include newer Saaras v3 features like `set_prompt` and the newer mode parameter; the reporter had to override dependencies to `sarvamai>=0.1.25`. That is an issue report, not an official docs statement, but it is worth checking your installed Sarvam SDK version before assuming the latest model features are available through the Pipecat extra. ([GitHub][7])

## What this means in practice

For STT, the live path is Sarvam WebSocket streaming with Pipecat controlling turn flow, buffering, and optional keepalive silence packets. For TTS, the live path is Sarvam WebSocket streaming with config/text/flush/ping semantics, and Pipecat’s audio-context layer is what makes interruption handling and frame ordering reliable. The current docs strongly suggest that the “best” setup for a modern voice agent is `saaras:v3` on STT and `bulbul:v3` on TTS, both configured through Pipecat `Settings` objects. ([docs.pipecat.ai][3])

[1]: https://docs.pipecat.ai/server/services/supported-services?utm_source=chatgpt.com "Supported Services"
[2]: https://github.com/pipecat-ai/pipecat/blob/main/examples/foundational/07z-interruptible-sarvam.py "pipecat/examples/foundational/07z-interruptible-sarvam.py at main · pipecat-ai/pipecat · GitHub"
[3]: https://docs.pipecat.ai/server/services/stt/sarvam "Sarvam - Pipecat"
[4]: https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/speech-to-text/overview "Speech-to-Text APIs | Sarvam API Docs"
[5]: https://docs.pipecat.ai/api-reference/server/services/tts/sarvam "Sarvam AI - Pipecat"
[6]: https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/text-to-speech/streaming-api/web-socket "Streaming Text-to-Speech API | Sarvam API Docs"
[7]: https://github.com/pipecat-ai/pipecat/issues/3783?utm_source=chatgpt.com "Sarvam integration is broken · Issue #3783 · pipecat-ai ..."
