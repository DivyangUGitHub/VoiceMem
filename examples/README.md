# Examples

Six runnable examples, from basic memory usage to real-time voice models.

    pip install voicemem
    export OPENAI_API_KEY=sk-...

| Example | What it does | Extra requirement |
|---|---|---|
| 01_memory.py | Store and retrieve — minimal usage | Download audio models |
| 02_streaming.py | Feed audio chunks and inspect each turn | Same |
| 03_simple_agent_with_voicemem_memory.py | Full voice agent with memory and interruption | Microphone |
| 04_all_local_l40s.py | Fully local pipeline on one L40S | Local vLLM + VoxCPM |
| 05_realtime_gpt_qwen.py | Realtime GPT/Qwen integration with memory | API key |
| 06_mic_memory.py | Listen, transcribe, retrieve and store without LLM/TTS | Microphone |

## 01 — Store and retrieve

    python examples/01_memory.py

VoiceMem(mode="normal") uses the audio perception and dual-brain pipeline.
VoiceMem(mode="leftbrain_only") stores factual text without emotional attribution.

## 02 — Streaming

    python examples/02_streaming.py speech.wav

Audio is processed chunk by chunk. When VAD reports turn_over, the memory context has already been retrieved in the background.

## 03 — Full voice agent

    python examples/03_simple_agent_with_voicemem_memory.py

Microphone → VoiceMem memory prefetch → OpenAI response → TTS.
For the web demo, run python web/run.py.

## 04 — Fully local

    vllm serve Qwen/Qwen3-8B --port 8000 --gpu-memory-utilization 0.5
    python examples/04_all_local_l40s.py

The pipeline keeps ASR, VAD, embeddings, fact extraction, replies, and TTS on the local machine.

## 05 — Realtime GPT / Qwen

    OPENAI_API_KEY=sk-... python examples/05_realtime_gpt_qwen.py gpt
    DASHSCOPE_API_KEY=sk-... python examples/05_realtime_gpt_qwen.py qwen

Realtime audio and VoiceMem memory retrieval run in parallel. Memory is injected per response.

## 06 — Listen only

    python examples/06_mic_memory.py

This example has no generation model. It shows listening, transcription, retrieval, and memory storage.

## Echo cancellation

The shared audio layer uses AEC so the assistant's playback is not mistaken for user speech. Playback and recording share the same audio stream so the reference signal stays aligned.

## Local embeddings

The examples use local E5 for embeddings and slot classification to keep speculative retrieval low-latency and offline.

Do not mix memory stores created with different embedding dimensions. Local E5 and OpenAI embeddings use different dimensions.
