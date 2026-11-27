# Audio: STT and TTS

Use to transcribe audio (STT) and generate speech (TTS), and to pick the language and voice model by licence and where it runs.

Part of the [Self-hosted generative AI](../genai.md) recipe.

Interpret and generate voice on your hardware — no external API. Needs the
`[genai-audio]` extra (faster-whisper) to transcribe and, for Coqui TTS
(XTTS v2), `[genai-tts-coqui]` — split from `[genai-audio]` since 0.349.0
([why](../../installation.md#genai-audio-coqui-split)); the engines import
lazily.

!!! warning "In v0.252.0 the extra started shipping the Coqui runtime"
    `coqui-tts` keeps torch, torchaudio and torchcodec behind its own extras
    (`[cpu]`, `[cuda]`, `[codec]`), so up to v0.251.0 installing
    `[genai-audio]` gave you a TTS that **would not import**. Measured with
    coqui-tts 0.27.5 and torch 2.13.0:

    ```text
    ModuleNotFoundError: No module named 'torchaudio'
    ```

    And with torchaudio in place, a fresh resolve brought transformers 5.x,
    which removed the symbol `TTS.api` imported eagerly:

    ```text
    ImportError: cannot import name 'isin_mps_friendly' from 'transformers.pytorch_utils'
    ```

    That is why, from v0.252.0 to 0.341.0, the extra pinned
    `transformers<5`. `coqui-tts` 0.28.0 no longer imports that symbol, and
    since 0.342.0 the Coqui extra (today `[genai-tts-coqui]`) requires that
    version and drops the cap.
    Measured on 2026-11-01 with coqui-tts 0.28.0, transformers 5.22.1 and
    torch 2.14: XTTS v2 with a built-in voice and with cloning, and Bark,
    synthesized a Portuguese sentence that Whisper transcribed back
    word for word. `[genai-tts-coqui]` declares `torch>=2.14`,
    `torchaudio>=2.11`, `torchcodec>=0.16` and `six` (coqui-tts 0.28.0 imports
    it without declaring it). If you pinned those by hand, you can drop them.

!!! info "With coqui-tts 0.28.0, XTTS v2 comes from the Hugging Face Hub"
    The model is downloaded from the `tts-hub/XTTS-v2` repository into the
    Hub cache (`~/.cache/huggingface/hub/models--tts-hub--XTTS-v2`), no longer
    only into `~/.local/share/tts`. The first use needs network; with the
    cache filled, `HF_HUB_OFFLINE=1` works (measured: synthesis completed
    offline). In a container, mount that directory on a volume so a deploy
    does not download it again.

    On load, transformers 5 logs
    `bos_token_id must be None or an integer within the vocabulary ... got 50256`
    (and the same for `eos_token_id`). Synthesis completes normally.

!!! danger "XTTS v2 asks you to accept a licence, and a server has nobody to answer"
    `TextToSpeech`'s default model is licence-gated. Read in coqui-tts 0.28.0,
    `ModelManager.tos_agreed` accepts only a `tos_agreed.txt` beside the
    weights **or** `COQUI_TOS_AGREED` equal to the **string** `"1"`; without
    it, `ask_tos` calls `input()`. Synthesis runs in `asyncio.to_thread`,
    where there is no tty to answer — so export it before the first
    `synthesize`:

    ```bash
    export COQUI_TOS_AGREED=1
    ```

    `COQUI_TOS_AGREED=true` does not count: the comparison is against `"1"`.

## Interpret audio (STT)

`SpeechToText` transcribes with **faster-whisper** (Whisper via
CTranslate2, fast on CPU/GPU). Loads once, runs in a worker thread,
serializes calls through a semaphore.

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText("base", device="auto")     # tiny…large-v3


async def main() -> None:
    """Run this example."""
    result = await stt.transcribe("meeting.wav")
    print(result.text, result.language, result.duration)
    for seg in result.segments:                    # per-span timestamps
        print(seg.start, seg.end, seg.text)


asyncio.run(main())
```

Accepts a local file path or `bytes` — a string that is not an existing file (a URL, an FFmpeg protocol such as `concat:`) raises `FileNotFoundError` without touching the network. `device`/`compute_type` resolve automatically
(`float16` on GPU, `int8` on CPU).

### Transcribing faster on CPU

The four knobs below change **how** the decode is scheduled, not which
weights run — same model, same precision:

```python
from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText(
    "large-v3-turbo",
    device="cpu",
    compute_type="int8",
    batch_size=8,                      # decode 8 speech spans in parallel
    cpu_threads=0,                     # 0 = CTranslate2 decides (intra_threads)
    num_workers=1,                     # parallel translations in one model
    condition_on_previous_text=False,  # turn off alongside batch_size
)
```

- **`batch_size`** swaps sequential `WhisperModel.transcribe()` for
  faster-whisper's `BatchedInferencePipeline`. It requires
  `vad_filter=True` — the VAD is what cuts the audio into the spans a
  batch is made of — and passing one without the other raises
  `ValueError` at construction, not on the first transcription. The cost
  is peak memory proportional to the value.
- **`cpu_threads`** / **`num_workers`** are CTranslate2's `intra_threads`
  / `inter_threads`. `num_workers` only matters when several concurrent
  calls share the instance.
- **`condition_on_previous_text`** hands each window the previous
  window's text as context. The default is `True`, which is
  faster-whisper's own — kept so an SDK upgrade does not silently change
  anybody's transcripts. Turn it off when batching: it serializes spans
  that would otherwise decode in parallel, and it is the path by which
  one bad span contaminates the ones after it (the repetition loop).

!!! tip "A long file should not look like a hang"
    faster-whisper hands back a generator, so an hour-long meeting spends
    minutes with no signal at all. `on_progress` receives
    `(seconds_done, total_seconds)` as the decode advances:

    ```python
    import asyncio
    import logging

    from tempest_fastapi_sdk.genai.audio import SpeechToText

    logger = logging.getLogger(__name__)
    stt = SpeechToText("base", device="cpu")


    async def main() -> None:
        """Run this example."""

        def progress(done: float, total: float) -> None:
            """Log how far the decode got."""
            logger.info("transcribed %.1fs of %.1fs", done, total)

        await stt.transcribe("meeting.wav", on_progress=progress)


    asyncio.run(main())
    ```

    The callback runs **on the worker thread**, not the event loop: do not
    touch a coroutine in there without `loop.call_soon_threadsafe`.

### Give Whisper the vocabulary: `prompt` and `hotwords`

Proper nouns are where Whisper misses most: in a voice search for bus
tickets, "from Picos to Parnaíba tomorrow" can come back with the
destination unrecognisable. The service already knows which names are
likely — the cities on its own routes, the products in its catalogue — and
`prompt` is how you hand them to the model:

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import SpeechToText, Transcription

stt: SpeechToText = SpeechToText("base", device="cpu")

CITIES: list[str] = ["Picos", "Teresina", "Parnaíba", "Floriano", "Oeiras"]


def domain_prompt(names: list[str], limit: int = 60) -> str:
    """Build the prompt with the most frequent names first.

    Args:
        names (list[str]): Domain names, most frequent first.
        limit (int): How many names go into the prompt.

    Returns:
        str: A short sentence naming them.
    """
    return "Viagens entre " + ", ".join(names[:limit]) + "."


async def main() -> None:
    """Run this example."""
    result: Transcription = await stt.transcribe(
        "request.wav",
        language="pt",
        prompt=domain_prompt(CITIES),
    )
    print(result.text)


asyncio.run(main())
```

- **`prompt`** becomes faster-whisper's `initial_prompt`: text the model
  reads as if it came **before** the audio. Write it as a sentence from the
  domain itself, in the language of the audio and with the spelling you want
  back (accents, capitals); Whisper does not follow instructions ("transcribe
  the city names") the way a chat model does.
- **The limit is in tokens, and the start is what gets dropped.** In
  faster-whisper 1.2.1 the previous context keeps the **last** 223 tokens
  (`max_length // 2 - 1`, with `max_length = 448`) — measured by calling its
  `get_prompt` with 400 tokens of context: 223 go in, from the 178th on.
  OpenAI's documentation states 224 tokens for `whisper-1`
  ([speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text)).
  That is why the recipe puts the **most frequent** names in a short prompt
  instead of dumping the whole list: whatever passes the limit is dropped
  silently. How many names fit depends on the model's tokenizer; with a long
  list, count the tokens up front instead of estimating.
- **`hotwords`** (`SpeechToText` only) goes into the prompt of **every**
  window, whatever `condition_on_previous_text` says; faster-whisper 1.2.1
  keeps its **first** 223 tokens. `prompt`, by contrast, conditions the
  first window of the sequential model and only carries forward while
  `condition_on_previous_text=True`; with `batch_size` it goes in front of
  every batch. For short audio (one voice request) both land in the same
  place; for a long meeting, `hotwords` is the one that keeps applying.
- **`temperature`** pins one temperature for the decode. `None` keeps
  faster-whisper's default, the `0.0, 0.2, …, 1.0` ladder it climbs when a
  window fails its compression/log-probability checks; a `float` replaces
  the ladder with that single value.
- The extra's floor (`faster-whisper>=1.2.1`) already has all three
  parameters, on `WhisperModel` and on `BatchedInferencePipeline` — a test in
  the suite checks the installed signature.

!!! note "How much the prompt helps is your measurement, not ours"
    The SDK guarantees the text reaches the decoder; the accuracy gain
    depends on the model, the audio and the list. Measure it on recordings
    from your domain before relying on it.

!!! info "One shared instance loads the model exactly once"
    `load()` is guarded by a `threading.Lock`, not by the concurrency
    semaphore — the semaphore admits `max_concurrent` callers by
    definition, so two of them arriving on a cold instance used to build
    two copies of the model (fixed in v0.235.0). Keep sharing one
    instance across requests: that is what saves the load.

## Generate voice (TTS)

`TextToSpeech` synthesizes with **Coqui TTS** (WAV). Same discipline
(lazy + thread + semaphore).

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import TextToSpeech

tts = TextToSpeech("tts_models/multilingual/multi-dataset/xtts_v2")


async def main() -> None:
    """Run this example."""
    wav = await tts.synthesize("Hello, world.", language="en")   # -> WAV bytes
    # voice cloning (XTTS): pass a reference clip
    wav = await tts.synthesize("Hi!", language="en", speaker_wav="ref.wav")


asyncio.run(main())
```

`synthesize` returns the WAV `bytes`; pass `out_path=` to also write it to
disk.

## Language (PT-BR / EN-US)

No need to know the Whisper code or pick a TTS model: use the `LanguageEnum`
enum. It resolves the code (`pt`/`en`) for STT and a good TTS model per
language:

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import LanguageEnum, SpeechToText, TextToSpeech


async def main() -> None:
    """Run this example."""
    # STT: force the language without memorizing the code
    await SpeechToText("base").transcribe("audio.wav", language=LanguageEnum.PT_BR)

    # TTS: picks the language's default model automatically
    tts = TextToSpeech.for_language(LanguageEnum.EN_US)     # en-US model
    wav = await tts.synthesize("Hello, world.")


asyncio.run(main())
```

`preset_for(LanguageEnum.PT_BR)` exposes the preset (`whisper_language`,
`tts_model`, `tts_language`) to inspect/override. `language=` on
`transcribe`/`synthesize` also accepts a raw code (``"pt"``) or ``None``
(auto-detect for STT).

!!! tip "Full voice loop"
    Chain with the LLM: **STT** transcribes speech → `TextGenerator`/RAG
    answers → **TTS** speaks the reply. All local, nothing leaves the box.

## Multilingual voice with Bark (MIT licence)

`BarkSpeech` runs Suno's Bark through `transformers`. It is the local TTS
option with **commercially usable** weights (MIT) and Portuguese voices. The
`[genai]` extra is enough, and it backs `/tts` on `make_genai_router` like
`TextToSpeech` and `OllamaSpeech`.

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import BarkSpeech, LanguageEnum

voice = BarkSpeech("suno/bark-small")


async def main() -> None:
    """Read a long Portuguese notice aloud and save the WAV."""
    notice = (
        "O sistema de transporte da cidade passa por mudanças nesta semana. "
        "As linhas que atendem o centro terão novos horários a partir de "
        "segunda-feira. Quem usa o ônibus das oito deve chegar ao ponto dez "
        "minutos antes."
    )
    await voice.synthesize(notice, language=LanguageEnum.PT_BR, seed=0, out_path="notice.wav")


asyncio.run(main())
```

- **`language`** picks the language's first voice (`v2/pt_speaker_0`);
  **`speaker`** picks a specific one (`v2/pt_speaker_1`). `voices()` lists
  the checkpoint's 261 presets: 130 with `v2/` and 131 legacy names without
  the prefix.
- **Long text is split by sentence** (`.`, `!`, `?`, `…`, `;` or `:`
  followed by whitespace; `Dr.`, `Sra.`, `Av.` and the like do not end a
  sentence, and numbers like `R$ 3,50` or `1.000` are never split). One Bark call stops at 13.95 s of
  audio: measured, 180 Portuguese characters came out whole and 240 were cut.
  `synthesize` splits the text into pieces of up to `max_chunk_chars` (160),
  speaks each, and joins them with `pause_seconds` (0.25 s) of silence. A
  442-character text became 35.75 s of complete audio; without the split it
  stopped at 13.95 s.
- **It is slow.** On the RTX 4070 Ti SUPER, in bf16 (the default), those
  35.75 s took 88.6 s; in fp16, 12.7 s took 24 to 27 s. For real-time
  replies, prefer `OllamaSpeech`. `suno/bark` (the large checkpoint) is
  slower still: on the same Portuguese text, 12.68 s of audio took 35.4 s at
  a 2.3 GiB peak, against 11.37 s in 19 s and 0.92 GiB for `bark-small`.
- **Errors answer `422`**, with `details={"field": ...}`. Before the model
  loads: text with nothing to speak (`text`), a seed outside `0..2**64 - 1`
  (`seed`) and a `speaker` that is not a voice of the requested `language`
  (`speaker`). After: a preset the checkpoint does not ship (`speaker`, or
  `language` when the language has no voice).
- **The seed is global**, applied with `torch.manual_seed`. In bf16, the
  same seed repeated the same bytes.

!!! note "The weights are a pickle"
    `suno/bark-small` publishes `pytorch_model.bin`, not safetensors. torch
    2.14 loads it with `weights_only=True`, which does not execute code from
    the file.

## Picking a voice or audio model: licence and where it runs

Before picking a voice model, check two things: **the licence of the
weights** (which can differ from the code's licence) and **whether it runs
on what the SDK already depends on**. Generic "best models" guides often get
both wrong.

The table below was read from the Hugging Face models API
(`https://huggingface.co/api/models/<id>`, `tags` field) on 2026-10-31.
Weight licences change; check the model card before shipping.

| Model | Task | Weight licence | Runs on |
| --- | --- | --- | --- |
| `hexgrad/Kokoro-82M` | TTS | `apache-2.0` | `kokoro` package (requires Python `<3.13`); `kokoro-onnx` 0.6.1 requires `<3.14` and a native espeak, so the SDK does not wrap it |
| `nari-labs/Dia-1.6B-0626` | TTS | `apache-2.0` | `transformers` (`DiaForConditionalGeneration`, outside the pipeline) |
| `sesame/csm-1b` | TTS | `apache-2.0` (gated) | `transformers` |
| `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | TTS | `apache-2.0` | not in `transformers` 4.57.6 |
| `ResembleAI/chatterbox` | TTS | `mit` | `chatterbox-tts` package |
| `suno/bark-small` | TTS | `mit` | `BarkSpeech` ([Bark](#multilingual-voice-with-bark-mit-licence)) |
| `openai/whisper-large-v3-turbo` | STT | `mit` | `SpeechToText` (faster-whisper) |
| `coqui/XTTS-v2` | TTS | `other` (CPML) | `TextToSpeech` (Coqui) |
| `SWivid/F5-TTS` | TTS | **`cc-by-nc-4.0`** | `f5-tts` package |
| `facebook/musicgen-large` | music | **`cc-by-nc-4.0`** | `transformers` |
| `cvssp/audioldm-s-full-v2` | sound effects | **`cc-by-nc-sa-4.0`** | `AudioGenerator` ([sound effects and music](sound.md)) |
| `ucsd-reach/musicldm` | music | **`cc-by-nc-sa-4.0`** | `AudioGenerator`; measured: generates on `transformers` 4.57.6, refused on 5.22.1 |
| `cvssp/audioldm2-large` | sound effects | **`cc-by-nc-sa-4.0`** | `diffusers` (`AudioLDM2Pipeline`), broken on current `transformers` |
| `stabilityai/stable-audio-open-1.0` | music/effects | `other` (gated) | `diffusers` (`StableAudioPipeline`) |

!!! warning "MIT code does not mean MIT weights"
    The F5-TTS repository is MIT, but its published weights are
    `cc-by-nc-4.0`; AudioLDM 2 is `cc-by-nc-sa-4.0`. Both show up as "MIT,
    commercial use allowed" in recommendation lists. Commercial use of NC
    weights needs a separate agreement with the author.

What the extra's environment already has, with no new dependency: in the
venv this was measured in (`diffusers` 0.39.0, `transformers` 4.57.6),
`diffusers` exports `AudioLDM2Pipeline`, `StableAudioPipeline`,
`MusicLDMPipeline` and `AutoPipelineForText2Audio`, and the `transformers`
text-to-waveform mapping covers `bark`, `csm`, `musicgen`,
`musicgen_melody`, `vits`, `seamless_m4t`, `qwen2_5_omni`, `qwen3_omni_moe`
and `fastspeech2_conformer`. Kokoro is not in that mapping.

!!! info "Packages whose constraints the SDK does not accept"
    Read from `requires-dist` on PyPI on 2026-10-31: `chatterbox-tts` 0.1.7
    pins `torch==2.6.0`, `transformers==5.2.0` and `diffusers==0.29.0`;
    `stable-audio-tools` 0.0.20 pins `torch==2.7.1` and requires Python
    `<3.11`; `f5-tts` 1.1.22 pulls in `gradio`; `kokoro` 0.9.4 requires
    Python `<3.13`. Bringing any of them into an extra would pin the lock of
    everyone who installs the SDK.

### Voice through Ollama

Ollama started generating speech in v0.40.11 (2026-10-24), through the
OpenAI-compatible `POST /v1/audio/speech` endpoint, with the `phonon` model
(31M English checkpoint). v0.40.12 brought `lfm3-audio` (2B, LFM Open
License v1.0, 18 languages including Portuguese). The v0.40.11 notes mention
macOS, and the `lfm3-audio` page only shows a text chat.

Measured on 2026-10-31 with the `ollama/ollama:0.40.13` image in Docker,
Linux (WSL2), RTX 4070 Ti SUPER 16 GB, driver 591.86:

- **Both run on the GPU on Linux.** `ollama ps` shows `phonon` on the `mlx`
  runner and `lfm3-audio` on `llamacpp`, both `100% GPU`; with both loaded,
  VRAM use went from 1515 MiB to 4565 MiB.
- **`/api/show` declares the capability.** `phonon` answers
  `capabilities: ["speech"]` and 38 voices in `voices`; `lfm3-audio` answers
  `["completion", "audio", "speech", "tools"]`, with voices such as
  `female-portuguese` and `male-portuguese`.
- **TTS.** Output is `audio/wav` 16-bit mono 24 kHz, with the RIFF size
  field set to `0xFFFFFFFF` (the stream does not know the length up front);
  `pcm` returns `audio/pcm`. With the model warm, a sentence of 6.3 s of
  audio came back in 0.14 s (`phonon`) and one of 7.0 s in 0.65 s
  (`lfm3-audio`). The **first** request after the pull took 116 s
  (`phonon`) and 55 s (`lfm3-audio`); after unloading with `keep_alive: 0`,
  reloading took 1.4 s.
- **Errors are `400` with `invalid_request_error`:** `response_format`
  `mp3` (`use wav or pcm`), `speed` other than 1
  (`only speed 1 is supported`), an unknown voice (the message lists the
  voices), `input` over 4096 characters, and `/api/chat` on a speech-only
  model (`"phonon" does not support chat`).
- **STT works, through three undocumented paths:**
  `POST /v1/audio/transcriptions` (multipart `file` + `model`, as in
  OpenAI; accepts WAV and MP3, and `response_format=verbose_json` returns
  `language`, `duration` and `segments`), `POST /v1/chat/completions` with a
  base64 `input_audio` part, and `POST /api/chat` with base64 audio in the
  `images` field. With the model warm, transcribing 7 s took 0.13 s. Sending
  audio to `phonon` answers `400` (`"phonon" does not support audio input`).

!!! note "Transcription quality: one sample is not a rate"
    On a single 7 s Portuguese clip, `lfm3-audio` transcribed "Lá, este
    áudio..." (dropping the "O" of "Olá") and
    `SpeechToText("large-v3-turbo")` got "Olá! Este áudio...". Both wrote
    "Olama". It is one sample, not an error-rate comparison.

### `OllamaSpeech`: voice and transcription through the SDK

`OllamaSpeech` wraps both endpoints and the voice list, with the same HTTP
client (retry, circuit-breaker, `X-Request-ID`) and the same `OllamaError`
as `OllamaGenerator`. It only needs the `[genai-ollama]` extra.

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaSpeech

speech = OllamaSpeech("lfm3-audio", timeout=180.0)


async def main() -> None:
    """Speak Portuguese and transcribe it back."""
    voices = await speech.voices()
    print("female-portuguese" in voices)

    wav = await speech.synthesize(
        "Bom dia! O SDK agora fala português pelo Ollama.",
        voice="female-portuguese",
        out_path="bom-dia.wav",
    )

    result = await speech.transcribe(wav)
    print(result.text, result.duration)
    await speech.aclose()


asyncio.run(main())
```

One run against Ollama 0.40.13 on the RTX 4070 Ti SUPER:

```text
True
Bom dia. O Sicoa agora fala português pelo Olama. 3.84
```

Synthesis is not deterministic: the same program, run four times, produced
clips from 3.68 s to 4.32 s, and the acronym "SDK" came back as "Raskiká",
"Sicoa", "STEC" and vanished on the fourth ("Bom dia, esta agora, fala
português..."). Spoken by `lfm3-audio` and heard back by the same model, it
did not survive the round trip once. Spell acronyms out in text that will
become speech.

What `OllamaSpeech` does for you:

- **Fixes the WAV header.** The daemon's stream leaves the RIFF and `data`
  sizes at `0xFFFFFFFF`, and Python's `wave` reads `2147483647` frames.
  Since `synthesize` buffers the whole response, it rewrites the sizes: a
  4.32 s clip opens with `103680` frames at 24 kHz.
  `response_format="pcm"` returns the raw samples, with no header.
- **Separates a bad request from a server failure.** When the daemon
  refuses the request with `400` (unknown voice, model without speech or
  without audio input, empty input or input over 4096 characters, audio that
  does not decode), it raises `ValidationException` (`422`) with the
  daemon's message and `details={"backend": "ollama", "model": ...}`. A model
  that is not pulled, or a daemon failure, stays an `OllamaError`.
- **Refuses what the daemon would ignore.** Ollama only echoes back the
  `language` label it received (English audio sent with `language=pt` came
  back in English, tagged `"portuguese"`), and for speech the voice decides
  the language. So `transcribe` and `synthesize` accept `language` only as
  `None`: any value raises `ValidationException` with
  `details={"field": "language"}`, before any request. `Transcription.language`
  stays empty. `prompt` and `temperature` are likewise accepted only as
  `None` (`details={"field": "prompt"}` or `"temperature"`): whether the
  daemon honours them has not been measured, so they are neither sent nor
  silently dropped. There is no `keep_alive` either, which these endpoints
  ignore.
- **A path is a path.** `transcribe("https://...")` raises
  `FileNotFoundError` without touching the network, like `SpeechToText`.

### Streamed voice: `stream_speech` and `speech_response`

`synthesize` waits for the whole clip. `stream_speech` yields each piece as
the daemon flushes it — 16-bit mono 24 kHz PCM by default, no header, the
format a streaming player wants — with the same errors. To serve it over HTTP,
use `speech_response`, which returns the `StreamingResponse` already:

```python
from fastapi import FastAPI, Response

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import OllamaSpeech

speech = OllamaSpeech("lfm3-audio", timeout=180.0)
app = FastAPI()
register_exception_handlers(app)


@app.get("/speak")
async def speak(text: str) -> Response:
    """Return the speech as PCM while it is generated.

    Args:
        text (str): The text to speak.

    Returns:
        Response: 16-bit mono 24 kHz PCM, streamed.
    """
    return await speech.speech_response(text, voice="female-portuguese")
```

!!! warning "Why not build the `StreamingResponse` yourself"
    A `StreamingResponse` sends its status and headers **before** reading the
    first chunk. Measured: with `StreamingResponse(speech.stream_speech(...))`
    and an unknown voice, the client got **200 with an empty body**, and
    Starlette logged `Caught handled exception, but response already started`.
    `speech_response` reads the first chunk before building the response: the
    refusal raises there and becomes the SDK's `422`, and the rest of the audio
    still streams.

Measured with a warm `lfm3-audio`: on a text of ~18 s of audio, the first
chunk arrived in 24 to 30 ms, against 1.76 s for the whole `synthesize`.
`language` is refused before any request.

### Serving over HTTP: `/tts` and `/transcribe`

`make_genai_router` takes any object that satisfies `SpeechTranscriber`
(for `/transcribe`) and `SpeechSynthesizer` (for `/tts`), from
`tempest_fastapi_sdk.genai.audio`. `SpeechToText`, `TextToSpeech` and
`OllamaSpeech` all do; the `/tts` body's `speaker` becomes Ollama's `voice`.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import OllamaSpeech, make_genai_router

speech = OllamaSpeech("lfm3-audio", timeout=180.0)

app = FastAPI()
register_exception_handlers(app)
app.include_router(make_genai_router(speech_to_text=speech, text_to_speech=speech))
```

Measured against Ollama 0.40.13, with `httpx.ASGITransport` in front of the
app:

| Request | Response |
| --- | --- |
| `POST /api/genai/tts` `{"text": "Olá pelo roteador.", "speaker": "male-portuguese"}` | `200`, `audio/wav` with 34560 frames |
| `POST /api/genai/transcribe` with that WAV | `200`, `"text": "Olá pelo roteador."` |
| `POST /api/genai/tts` `{"text": "x", "speaker": "nope"}` | `422`, `VALIDATION_ERROR`, the daemon's message in `detail` |
| `POST /api/genai/tts` `{"text": "x", "language": "pt"}` | `422`, `details: {"field": "language"}` |
| `POST /api/genai/transcribe` with 3000 zero bytes | `422`, `"detail": "Failed to load image or audio file"` |

!!! tip "The first request after the pull is slow"
    It loads the model: 116 s for `phonon` and 55 s for `lfm3-audio`, against
    1.4 s for a later reload. With the default 120 s `timeout`, the first
    `phonon` `synthesize` barely makes it; warm the model up at deploy time or
    raise the `timeout`.

### Sources

- [Ollama: speech](https://docs.ollama.com/capabilities/speech)
- [Ollama release notes](https://github.com/ollama/ollama/releases) (v0.40.11 to v0.40.13)
- [`lfm3-audio` in the Ollama library](https://ollama.com/library/lfm3-audio)
- [Transformers: text-to-speech](https://huggingface.co/docs/transformers/en/tasks/text-to-speech)
- [transformers PR #39796: text-to-audio pipeline standardization](https://github.com/huggingface/transformers/pull/39796)
- [Diffusers: audio pipelines](https://huggingface.co/docs/diffusers/en/api/pipelines/overview)
- Model cards: [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
  [Dia-1.6B](https://huggingface.co/nari-labs/Dia-1.6B-0626),
  [CSM-1B](https://huggingface.co/sesame/csm-1b),
  [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base),
  [Chatterbox](https://huggingface.co/ResembleAI/chatterbox),
  [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo),
  [XTTS-v2](https://huggingface.co/coqui/XTTS-v2),
  [F5-TTS](https://huggingface.co/SWivid/F5-TTS),
  [MusicGen large](https://huggingface.co/facebook/musicgen-large),
  [AudioLDM 2 large](https://huggingface.co/cvssp/audioldm2-large),
  [Stable Audio Open](https://huggingface.co/stabilityai/stable-audio-open-1.0)
