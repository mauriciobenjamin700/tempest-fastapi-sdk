# Sound effects and music

Use to generate sound effects and music (MusicGen) and serve the result over HTTP at `/audio`.

Part of the [Self-hosted generative AI](../genai.md) recipe.

Generate a thunderclap, footsteps on dry leaves or a lo-fi track on your own
hardware, with no external API: `AudioGenerator` loads a HuggingFace audio
diffusion pipeline and follows the same conventions as `ImageGenerator`
(lazy load on a thread, idle unload, one render at a time, the seed returned
with every clip).

Requires the `[genai]` (torch + transformers) and `[genai-sound]`
(`diffusers`) extras:

```bash
uv add "tempest-fastapi-sdk[genai,genai-sound]"
```

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk.genai import AudioGenerationConfig, AudioGenerator

effects = AudioGenerator("cvssp/audioldm-s-full-v2")


async def main() -> None:
    """Generate 10 s of thunderstorm and save it."""
    clips = await effects.generate(
        "thunderstorm with heavy rain on a tin roof",
        config=AudioGenerationConfig(seconds=10.0, steps=50, seed=0),
    )
    clip = clips[0]
    Path("storm.wav").write_bytes(clip.data)
    print(clip.sample_rate, clip.seconds, clip.seed)


asyncio.run(main())
```

```text
16000 10.0 0
```

Each `GeneratedAudio` is a 16-bit PCM WAV with `sample_rate`, `channels`,
`seconds` and the `seed`. Without a `seed` in the config one is drawn and
returned, so every clip is reproducible on the same hardware and
precision: measured on the RTX 4070 Ti SUPER in bf16, the same seed produced
the same bytes, and `num_waveforms=2` produced two different clips in one
call (reproducing the second takes the same seed, the same `num_waveforms`
and its position).

## Which model to use

Measured on 2026-10-31 on an RTX 4070 Ti SUPER (bf16, 10 s, 50 steps,
model already loaded):

| Model | For | 10 s at 50 steps | No config (model default) | Licence |
| --- | --- | --- | --- | --- |
| `cvssp/audioldm-s-full-v2` | sound effects | ~1.1 s | 5.12 s of audio, 10 steps | `cc-by-nc-sa-4.0` |
| `ucsd-reach/musicldm` | music | ~1.1 s | 10.24 s of audio, 200 steps | `cc-by-nc-sa-4.0` |

Peak VRAM stayed under 1.1 GiB for both.

!!! warning "Both models are non-commercial"
    `cc-by-nc-sa-4.0` forbids commercial use. Check the model card before
    putting it in a product.

!!! danger "Where each model works"
    diffusers keeps `AudioLDMPipeline` and `MusicLDMPipeline` under
    `pipelines/deprecated` ("will not receive bug fixes or feature updates
    after Diffusers version 0.33.1"): the code is frozen. Measured on two
    resolutions:

    | Environment | AudioLDM | MusicLDM | AudioLDM2 |
    | --- | --- | --- | --- |
    | `transformers` 4.57.6 + `diffusers` 0.39.0 (with `transformers<5` pinned) | generates | generates | refused |
    | `transformers` 5.22.1 + `diffusers` 0.42.1 (`[genai,genai-sound]` alone) | generates | refused | refused |

    "Refused" means `load()` raises `ValueError` with the reason, instead of
    the first `generate` blowing up with an `AttributeError` deep inside:

    - **AudioLDM2** loads but fails on the first call with
      `'GPT2Model' object has no attribute '_update_model_kwargs_for_generation'`;
      the upstream fix
      ([diffusers#13111](https://github.com/huggingface/diffusers/pull/13111))
      was closed unmerged.
    - **MusicLDM** on `transformers` 5.22.1: `ClapModel.get_text_features`
      returns a `BaseModelOutputWithPooling` instead of a tensor, and the
      pipeline breaks with `'BaseModelOutputWithPooling' object has no
      attribute 'to'`. The load runs that call once to decide, without
      guessing a version, and frees the VRAM when it refuses: measured,
      8 MiB stay allocated while the exception is still alive.
    - **Stable Audio** takes `audio_end_in_s` instead of
      `audio_length_in_s` and is refused too.

## Music with MusicGen

`MusicGenerator` runs Meta's MusicGen through `transformers`, with no
diffusion and no `diffusers`: the `[genai]` extra is enough. Same
`AudioGenerationConfig`, same `GeneratedAudio`, same `/audio` route.

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk.genai import AudioGenerationConfig, MusicGenerator

music = MusicGenerator("facebook/musicgen-stereo-small")


async def main() -> None:
    """Generate 5 s of stereo rock and save it."""
    clips = await music.generate(
        "upbeat rock riff",
        config=AudioGenerationConfig(seconds=5.0, seed=0),
    )
    clip = clips[0]
    Path("rock.wav").write_bytes(clip.data)
    print(clip.channels, clip.seconds, clip.sample_rate)


asyncio.run(main())
```

```text
2 4.94 32000
```

What differs from diffusion, all measured on an RTX 4070 Ti SUPER with
`transformers` 4.57.6 and 5.22.1 (both behaved the same):

- **Length is a token budget.** It runs at 50 frames per second, and the
  clip comes out 60 ms shorter than asked (5 s becomes 4.94 s).
  `GeneratedAudio.seconds` carries the real length. Without `seconds`, the
  checkpoint default applies: 29.92 s for `musicgen-small` and
  `musicgen-stereo-small`.
- **There is a ceiling: 40.96 s.** The decoder has 2048 positions; 2048
  tokens rendered 40.9 s and 2049 fired a CUDA device-side assert that
  **poisons the process**: every later CUDA call failed. So `generate`
  refuses with `422` (`details={"field": "seconds", "limit": 40.96}`) before
  calling the model, and `max_seconds` exposes the ceiling.
- **`steps` and `negative_prompt` do not exist here** and answer `422`
  instead of being ignored. `guidance_scale` and `num_waveforms` work.
- **Stereo comes from the checkpoint**: `musicgen-stereo-small` returns 2
  channels.
- **Time**: 10 s of audio took ~9.2 s in bf16, ~9.3 s in fp16 and ~10 s in
  fp32 (`musicgen-small`, one prompt); 30 s, ~31 to 33 s. Peak VRAM from
  1.3 GiB (5 s) to 2.5 GiB (35 s), for two clips per call.
- **The seed is global.** transformers' `generate` takes no generator, so
  the seed goes through `torch.manual_seed`, which seeds the whole process.
  The same seed repeated the same bytes.

!!! warning "Non-commercial weights"
    MusicGen checkpoints are `cc-by-nc-4.0`.

### Following a melody: `musicgen-melody`

With a `musicgen-melody` checkpoint, `generate(melody=...)` takes a reference
clip (path or bytes) and the music follows its melody. `MusicGenerator`
detects the checkpoint type at load (`accepts_melody`), decodes the clip with
`load_audio` (a 16-bit WAV needs nothing else; other formats need PyAV, from
`[genai-audio]`) and casts the features to the model's precision — in bf16,
without that, `transformers` breaks with
`mat1 and mat2 must have the same dtype`.

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk.genai import AudioGenerationConfig, MusicGenerator

melody_model = MusicGenerator("facebook/musicgen-melody")


async def main() -> None:
    """Redo rock.wav as an acoustic guitar."""
    clips = await melody_model.generate(
        "calm acoustic guitar version",
        config=AudioGenerationConfig(seconds=10.0, seed=0),
        melody=Path("rock.wav"),
    )
    Path("guitar.wav").write_bytes(clips[0].data)


asyncio.run(main())
```

Measured on the RTX 4070 Ti SUPER, in bf16: 10 s took 10.8 s with the model
warm (3.3 GiB peak; 30 s, 4.3 GiB) and the same seed repeated the result. A
melody on a checkpoint without support (`musicgen-small`) answers `422` with
`details={"field": "melody"}`. `musicgen-melody` weights are `cc-by-nc-4.0`
too.

## Serving over HTTP: `/audio`

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import AudioGenerator, make_genai_router

app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_genai_router(audio_generator=AudioGenerator("cvssp/audioldm-s-full-v2")),
)
```

`audio_generator` takes any `SupportsAudioGeneration`: an `AudioGenerator` or a `MusicGenerator`.
With `MusicGenerator("facebook/musicgen-small")`, `{"seconds": 4.0, "seed": 7}` answered `200` with 126080 frames at 32 kHz, and `{"steps": 5}`, `422`.

`POST /api/genai/audio` with
`{"prompt": "dog barking in the distance", "config": {"seconds": 4.0, "seed": 42}}`
answered `200`, `audio/wav` with 64000 frames at 16 kHz, and
`X-Audio-Seed: 42`. The route generates one clip (`num_waveforms` above 1 is
`422`) and applies the `max_audio_seconds` (30) and `max_audio_steps` (200,
MusicLDM's default) limits from `GenAIRequestLimits`, answering `422` with
`details={"field": ..., "limit": ...}`.
