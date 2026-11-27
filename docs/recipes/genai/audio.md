# Áudio: STT e TTS

Use para transcrever áudio (STT) e gerar voz (TTS), escolher idioma e modelo de voz pela licença e por onde roda.

Parte da receita [IA generativa self-hosted](../genai.md).

Interpretar e gerar voz no seu hardware — sem API externa. Requer o extra
`[genai-audio]` (faster-whisper) para transcrever e, para o TTS do Coqui
(XTTS v2), o `[genai-tts-coqui]` — separado do `[genai-audio]` desde a
0.349.0 ([por quê](../../installation.md#genai-audio-coqui-split)); as engines
importam preguiçosamente.

!!! warning "Na v0.252.0 o extra passou a trazer o runtime do Coqui"
    O `coqui-tts` deixa torch, torchaudio e torchcodec atrás dos extras dele
    (`[cpu]`, `[cuda]`, `[codec]`), então até a v0.251.0 instalar
    `[genai-audio]` dava um TTS que **não importava**. Medido com coqui-tts
    0.27.5 e torch 2.13.0:

    ```text
    ModuleNotFoundError: No module named 'torchaudio'
    ```

    E com o torchaudio no lugar, um resolve novo trazia transformers 5.x,
    que removeu o símbolo que `TTS.api` importava de forma eager:

    ```text
    ImportError: cannot import name 'isin_mps_friendly' from 'transformers.pytorch_utils'
    ```

    Por isso, da v0.252.0 até a 0.341.0, o extra fixou `transformers<5`. O
    `coqui-tts` 0.28.0 não importa mais esse símbolo, e desde a 0.342.0
    o extra do Coqui (hoje `[genai-tts-coqui]`) exige essa versão e não tem
    mais o teto. Medido em
    2026-11-01 com coqui-tts 0.28.0, transformers 5.22.1 e torch 2.14: XTTS v2
    com voz pronta e com clonagem, e Bark, sintetizaram uma frase em
    português que o Whisper transcreveu de volta com as mesmas palavras.
    `[genai-tts-coqui]` declara `torch>=2.14`, `torchaudio>=2.11`,
    `torchcodec>=0.16` e `six` (o coqui-tts 0.28.0 o importa sem declarar).
    Quem fixava esses pins à mão pode removê-los.

!!! info "No coqui-tts 0.28.0 o XTTS v2 vem do Hugging Face Hub"
    O modelo é baixado do repositório `tts-hub/XTTS-v2` para o cache do Hub
    (`~/.cache/huggingface/hub/models--tts-hub--XTTS-v2`), não mais só para
    `~/.local/share/tts`. O primeiro uso precisa de rede; com o cache cheio,
    `HF_HUB_OFFLINE=1` funciona (medido: a síntese concluiu offline). Em
    container, monte esse diretório num volume para não baixar de novo a cada
    deploy.

    Na carga, a transformers 5 registra no log
    `bos_token_id must be None or an integer within the vocabulary ... got 50256`
    (e o mesmo para `eos_token_id`). A síntese conclui normalmente.

!!! danger "XTTS v2 pede aceite de licença, e num servidor não há quem responda"
    O model default do `TextToSpeech` é licence-gated. Lido no coqui-tts
    0.28.0, `ModelManager.tos_agreed` aceita só um `tos_agreed.txt` ao lado dos
    pesos **ou** `COQUI_TOS_AGREED` igual à string `"1"`; sem isso, `ask_tos`
    chama `input()`. A síntese roda em `asyncio.to_thread`, onde não existe
    tty para responder — então exporte antes do primeiro `synthesize`:

    ```bash
    export COQUI_TOS_AGREED=1
    ```

    `COQUI_TOS_AGREED=true` não vale: a comparação é com `"1"`.

## Interpretar áudio (STT)

`SpeechToText` transcreve com **faster-whisper** (Whisper via CTranslate2,
rápido em CPU/GPU). Carrega uma vez, roda em worker thread, serializa
chamadas por um semáforo.

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText("base", device="auto")     # tiny…large-v3


async def main() -> None:
    """Run this example."""
    result = await stt.transcribe("reuniao.wav")
    print(result.text, result.language, result.duration)
    for seg in result.segments:                    # timestamps por trecho
        print(seg.start, seg.end, seg.text)


asyncio.run(main())
```

Aceita caminho de arquivo local ou `bytes` — string que não é arquivo existente (URL, protocolo do FFmpeg como `concat:`) levanta `FileNotFoundError`, sem ir à rede. `device`/`compute_type` resolvem sozinhos
(`float16` na GPU, `int8` na CPU).

### Transcrever mais rápido em CPU

Os quatro parâmetros abaixo mexem em **como** o decode é agendado, não em
quais pesos rodam — mesmo modelo, mesma precisão:

```python
from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText(
    "large-v3-turbo",
    device="cpu",
    compute_type="int8",
    batch_size=8,                      # decodifica 8 trechos de fala em paralelo
    cpu_threads=0,                     # 0 = CTranslate2 decide (intra_threads)
    num_workers=1,                     # traduções paralelas no mesmo modelo
    condition_on_previous_text=False,  # desligue junto com batch_size
)
```

- **`batch_size`** troca o `WhisperModel.transcribe()` sequencial pelo
  `BatchedInferencePipeline` do faster-whisper. Exige `vad_filter=True` (é
  o VAD que corta o áudio nos trechos que viram um batch) — passar um sem
  o outro levanta `ValueError` na construção, não na primeira
  transcrição. Custo: pico de memória proporcional ao valor.
- **`cpu_threads`** / **`num_workers`** são o `intra_threads` /
  `inter_threads` do CTranslate2. `num_workers` só importa quando várias
  chamadas concorrentes dividem a mesma instância.
- **`condition_on_previous_text`** entrega a cada janela o texto da
  anterior como contexto. O default é `True`, que é o do próprio
  faster-whisper — mantido para uma atualização do SDK não mudar
  transcrição de ninguém em silêncio. Desligue ao usar `batch_size`: ele
  serializa trechos que decodificariam em paralelo, e é o caminho pelo
  qual um trecho ruim contamina os seguintes (o loop de repetição).

!!! tip "Áudio longo não precisa parecer travado"
    O faster-whisper devolve um gerador, então uma reunião de uma hora
    passa minutos sem sinal nenhum. `on_progress` recebe
    `(segundos_prontos, segundos_totais)` conforme o decode anda:

    ```python
    import asyncio
    import logging

    from tempest_fastapi_sdk.genai.audio import SpeechToText

    logger = logging.getLogger(__name__)
    stt = SpeechToText("base", device="cpu")


    async def main() -> None:
        """Run this example."""

        def progresso(pronto: float, total: float) -> None:
            """Log how far the decode got."""
            logger.info("transcrito %.1fs de %.1fs", pronto, total)

        await stt.transcribe("reuniao.wav", on_progress=progresso)


    asyncio.run(main())
    ```

    O callback roda **na worker thread**, não no event loop: não toque em
    corotina lá dentro sem `loop.call_soon_threadsafe`.

### Dar vocabulário ao Whisper: `prompt` e `hotwords`

Nome próprio é onde o Whisper mais erra: numa busca de passagens por voz,
"de Picos para Parnaíba amanhã" pode sair com o destino irreconhecível. O
serviço já sabe quais nomes são prováveis — as cidades das próprias rotas,
os produtos do catálogo — e o `prompt` é como entregá-los ao modelo:

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import SpeechToText, Transcription

stt: SpeechToText = SpeechToText("base", device="cpu")

CIDADES: list[str] = ["Picos", "Teresina", "Parnaíba", "Floriano", "Oeiras"]


def prompt_de_dominio(nomes: list[str], limite: int = 60) -> str:
    """Monta o prompt com os nomes mais frequentes primeiro.

    Args:
        nomes (list[str]): Nomes do domínio, do mais ao menos frequente.
        limite (int): Quantos nomes entram no prompt.

    Returns:
        str: Uma frase curta citando os nomes.
    """
    return "Viagens entre " + ", ".join(nomes[:limite]) + "."


async def main() -> None:
    """Run this example."""
    result: Transcription = await stt.transcribe(
        "pedido.wav",
        language="pt",
        prompt=prompt_de_dominio(CIDADES),
    )
    print(result.text)


asyncio.run(main())
```

- **`prompt`** vira o `initial_prompt` do faster-whisper: texto que o
  modelo lê como se viesse **antes** do áudio. Escreva como frase do
  próprio domínio, com a grafia que você quer de volta (acento, maiúscula);
  o Whisper não segue instruções ("transcreva os nomes de cidade") como um
  modelo de chat.
- **O limite é de tokens, e o começo é o que se perde.** No faster-whisper
  1.2.1 o contexto anterior guarda os **últimos** 223 tokens
  (`max_length // 2 - 1`, com `max_length = 448`) — medido chamando o
  `get_prompt` dele com 400 tokens de contexto: entram 223, a partir do
  178º. A documentação da OpenAI fala em 224 tokens para o `whisper-1`
  ([guia de speech-to-text](https://developers.openai.com/api/docs/guides/speech-to-text)).
  Por isso a receita põe os nomes **mais frequentes** num prompt curto,
  em vez de despejar a lista inteira: o que passa do limite cai em
  silêncio. Quantos nomes cabem depende do tokenizer do modelo; com lista
  longa, conte os tokens antes em vez de estimar.
- **`hotwords`** (só no `SpeechToText`) entra no prompt de **toda** janela,
  com ou sem `condition_on_previous_text`; no faster-whisper 1.2.1 ficam os
  **primeiros** 223 tokens dele. Já o `prompt` condiciona a primeira janela
  do modelo sequencial e só segue adiante enquanto
  `condition_on_previous_text=True`; no `batch_size` ele vai na frente de
  cada lote. Para áudio curto (um pedido de voz) os dois chegam ao mesmo
  lugar; para uma reunião longa, `hotwords` é o que continua valendo.
- **`temperature`** fixa uma temperatura para o decode. `None` mantém o
  default do faster-whisper, a escada `0.0, 0.2, …, 1.0` que ele sobe quando
  uma janela reprova nos checks de compressão/log-probabilidade; um `float`
  troca a escada por esse valor só.
- O piso do extra (`faster-whisper>=1.2.1`) já tem os três parâmetros, no
  `WhisperModel` e no `BatchedInferencePipeline` — um teste da suíte confere
  a assinatura instalada.

!!! note "Quanto o prompt melhora é medida sua, não nossa"
    O SDK garante que o texto chega ao decoder; o ganho de acerto depende do
    modelo, do áudio e da lista. Meça com gravações do seu domínio antes de
    contar com ele.

!!! info "Uma instância compartilhada carrega o modelo uma vez só"
    `load()` é protegido por um `threading.Lock`, e não pelo semáforo de
    concorrência — o semáforo admite `max_concurrent` chamadas por
    definição, então duas delas chegando numa instância fria construíam
    duas cópias do modelo (corrigido na v0.235.0). Continue
    compartilhando uma instância entre requisições: é o que economiza o
    carregamento.

## Gerar voz (TTS)

`TextToSpeech` sintetiza com **Coqui TTS** (WAV). Mesma disciplina
(lazy + thread + semáforo).

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import TextToSpeech

tts = TextToSpeech("tts_models/multilingual/multi-dataset/xtts_v2")


async def main() -> None:
    """Run this example."""
    wav = await tts.synthesize("Olá, mundo.", language="pt")   # -> bytes WAV
    # clonagem de voz (XTTS): passe um clipe de referência
    wav = await tts.synthesize("Oi!", language="pt", speaker_wav="ref.wav")


asyncio.run(main())
```

`synthesize` devolve os `bytes` do WAV; passe `out_path=` pra também
gravar em disco.

## Idioma (PT-BR / EN-US)

Não precisa saber o código do Whisper nem escolher modelo TTS: use o enum
`LanguageEnum`. Ele resolve o código (`pt`/`en`) pro STT e um modelo TTS bom
por idioma:

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import LanguageEnum, SpeechToText, TextToSpeech


async def main() -> None:
    """Run this example."""
    # STT: força o idioma sem decorar o código
    await SpeechToText("base").transcribe("audio.wav", language=LanguageEnum.PT_BR)

    # TTS: pega o modelo padrão do idioma automaticamente
    tts = TextToSpeech.for_language(LanguageEnum.PT_BR)     # modelo pt-BR
    wav = await tts.synthesize("Olá, mundo.")


asyncio.run(main())
```

`preset_for(LanguageEnum.PT_BR)` expõe o preset (`whisper_language`,
`tts_model`, `tts_language`) se quiser inspecionar/override. `language=`
no `transcribe`/`synthesize` também aceita o código cru (`"pt"`) ou `None`
(auto-detect no STT).

!!! tip "Loop de voz completo"
    Encadeie com o LLM: **STT** transcreve a fala → `TextGenerator`/RAG
    responde → **TTS** fala a resposta. Tudo local, nada sai da máquina.

## Voz multilíngue com Bark (licença MIT)

`BarkSpeech` roda o Bark da Suno pelo `transformers`. É a opção de TTS local
com pesos de **uso comercial liberado** (MIT) e vozes em português. Basta o
extra `[genai]`, e ele serve o `/tts` do `make_genai_router` como o
`TextToSpeech` e o `OllamaSpeech`.

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import BarkSpeech, LanguageEnum

voz = BarkSpeech("suno/bark-small")


async def main() -> None:
    """Lê um aviso longo em português e grava o WAV."""
    aviso = (
        "O sistema de transporte da cidade passa por mudanças nesta semana. "
        "As linhas que atendem o centro terão novos horários a partir de "
        "segunda-feira. Quem usa o ônibus das oito deve chegar ao ponto dez "
        "minutos antes."
    )
    await voz.synthesize(aviso, language=LanguageEnum.PT_BR, seed=0, out_path="aviso.wav")


asyncio.run(main())
```

- **`language`** escolhe a primeira voz da língua (`v2/pt_speaker_0`);
  **`speaker`** escolhe uma voz específica (`v2/pt_speaker_1`). `voices()`
  lista os 261 presets do checkpoint: 130 com `v2/` e 131 nomes legados sem
  o prefixo.
- **Texto longo é dividido por frase** (`.`, `!`, `?`, `…`, `;` ou `:`
  seguidos de espaço; `Dr.`, `Sra.`, `Av.` e afins não quebram a frase, e
  números como `R$ 3,50` ou `1.000` nunca são partidos). Uma chamada do Bark para em 13,95 s
  de áudio: medido, 180 caracteres de português saíram inteiros e 240 foram
  cortados. `synthesize` parte o texto em pedaços de até `max_chunk_chars`
  (160), fala cada um e junta com `pause_seconds` (0,25 s) de silêncio. Um
  texto de 442 caracteres virou 35,75 s de áudio completo; sem a divisão,
  parava em 13,95 s.
- **É lento.** Na RTX 4070 Ti SUPER, em bf16 (o default), os 35,75 s levaram
  88,6 s; em fp16, 12,7 s levaram de 24 a 27 s. Para resposta em tempo real,
  prefira o `OllamaSpeech`. O `suno/bark` (o checkpoint grande) é mais lento
  ainda: no mesmo texto de PT, 12,68 s de áudio levaram 35,4 s com pico de
  2,3 GiB, contra 11,37 s em 19 s e 0,92 GiB do `bark-small`.
- **Erros respondem `422`**, com `details={"field": ...}`. Antes de carregar
  o modelo: texto sem nada para falar (`text`), seed fora de `0..2**64 - 1`
  (`seed`) e `speaker` que não é voz da `language` pedida (`speaker`).
  Depois: preset que o checkpoint não traz (`speaker`, ou `language` quando a
  língua não tem voz).
- **A seed é global**, aplicada com `torch.manual_seed`. Em bf16, a mesma
  seed repetiu os mesmos bytes.

!!! note "Os pesos são um pickle"
    O `suno/bark-small` publica `pytorch_model.bin`, não safetensors. O
    `torch` 2.14 carrega com `weights_only=True`, que não executa código do
    arquivo.

## Escolher modelo de voz e áudio: licença e onde roda

Antes de escolher um modelo de voz, confira duas coisas: **a licença dos
pesos** (que pode ser diferente da licença do código) e **se ele roda pelo
que o SDK já depende**. Guia genérico de "melhores modelos" costuma errar
nas duas.

A tabela abaixo foi lida da API de modelos do Hugging Face
(`https://huggingface.co/api/models/<id>`, campo `tags`) em 2026-10-31.
Licença de peso muda; confira o model card antes de shippar.

| Modelo | Tarefa | Licença dos pesos | Roda por |
| --- | --- | --- | --- |
| `hexgrad/Kokoro-82M` | TTS | `apache-2.0` | pacote `kokoro` (exige Python `<3.13`); o `kokoro-onnx` 0.6.1 exige `<3.14` e espeak nativo, por isso o SDK não embrulha |
| `nari-labs/Dia-1.6B-0626` | TTS | `apache-2.0` | `transformers` (`DiaForConditionalGeneration`, fora do pipeline) |
| `sesame/csm-1b` | TTS | `apache-2.0` (gated) | `transformers` |
| `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | TTS | `apache-2.0` | fora do `transformers` 4.57.6 |
| `ResembleAI/chatterbox` | TTS | `mit` | pacote `chatterbox-tts` |
| `suno/bark-small` | TTS | `mit` | `BarkSpeech` ([Bark](#voz-multilingue-com-bark-licenca-mit)) |
| `openai/whisper-large-v3-turbo` | STT | `mit` | `SpeechToText` (faster-whisper) |
| `coqui/XTTS-v2` | TTS | `other` (CPML) | `TextToSpeech` (Coqui) |
| `SWivid/F5-TTS` | TTS | **`cc-by-nc-4.0`** | pacote `f5-tts` |
| `facebook/musicgen-large` | música | **`cc-by-nc-4.0`** | `transformers` |
| `cvssp/audioldm-s-full-v2` | efeitos | **`cc-by-nc-sa-4.0`** | `AudioGenerator` ([efeitos e música](sound.md)) |
| `ucsd-reach/musicldm` | música | **`cc-by-nc-sa-4.0`** | `AudioGenerator`; medido: gera no `transformers` 4.57.6, recusado no 5.22.1 |
| `cvssp/audioldm2-large` | efeitos | **`cc-by-nc-sa-4.0`** | `diffusers` (`AudioLDM2Pipeline`), quebrado no `transformers` atual |
| `stabilityai/stable-audio-open-1.0` | música/efeitos | `other` (gated) | `diffusers` (`StableAudioPipeline`) |

!!! warning "Código MIT não quer dizer peso MIT"
    O repositório do F5-TTS é MIT, mas os pesos publicados são
    `cc-by-nc-4.0`; o AudioLDM 2 é `cc-by-nc-sa-4.0`. Os dois aparecem como
    "MIT, uso comercial liberado" em listas de recomendação. Uso comercial
    de pesos NC exige outro acordo com o autor.

O que já está no ambiente do extra, sem dependência nova: no venv em que
isto foi medido (`diffusers` 0.39.0, `transformers` 4.57.6), o `diffusers`
exporta `AudioLDM2Pipeline`, `StableAudioPipeline`, `MusicLDMPipeline` e
`AutoPipelineForText2Audio`, e o mapeamento text-to-waveform do
`transformers` cobre `bark`, `csm`, `musicgen`, `musicgen_melody`, `vits`,
`seamless_m4t`, `qwen2_5_omni`, `qwen3_omni_moe` e `fastspeech2_conformer`.
Kokoro não está nesse mapeamento.

!!! info "Pacotes cujas restrições o SDK não aceita"
    Lido do `requires-dist` no PyPI em 2026-10-31: `chatterbox-tts` 0.1.7
    fixa `torch==2.6.0`, `transformers==5.2.0` e `diffusers==0.29.0`;
    `stable-audio-tools` 0.0.20 fixa `torch==2.7.1` e exige Python `<3.11`;
    `f5-tts` 1.1.22 puxa `gradio`; `kokoro` 0.9.4 exige Python `<3.13`.
    Trazer qualquer um para um extra prenderia o lock de quem instala o SDK.

### Voz pelo Ollama

O Ollama passou a gerar fala na v0.40.11 (2026-10-24), pelo endpoint
compatível com a OpenAI `POST /v1/audio/speech`, com o modelo `phonon`
(checkpoint inglês de 31M). A v0.40.12 trouxe o `lfm3-audio` (2B, licença LFM
Open License v1.0, 18 línguas incluindo português). As notas da v0.40.11
falam em macOS, e a página do `lfm3-audio` mostra só chat em texto.

Medido em 2026-10-31 com a imagem `ollama/ollama:0.40.13` em Docker, Linux
(WSL2), RTX 4070 Ti SUPER 16 GB, driver 591.86:

- **Os dois rodam na GPU no Linux.** `ollama ps` mostra `phonon` com runner
  `mlx` e `lfm3-audio` com `llamacpp`, ambos `100% GPU`; com os dois
  carregados, o uso de VRAM subiu de 1515 MiB para 4565 MiB.
- **`/api/show` declara a capacidade.** `phonon` responde
  `capabilities: ["speech"]` e 38 vozes em `voices`; `lfm3-audio` responde
  `["completion", "audio", "speech", "tools"]`, com vozes como
  `female-portuguese` e `male-portuguese`.
- **TTS.** Saída `audio/wav` 16-bit mono 24 kHz, com o tamanho do cabeçalho
  RIFF em `0xFFFFFFFF` (o stream não sabe o tamanho de antemão); `pcm`
  devolve `audio/pcm`. Com o modelo quente, uma frase de 6,3 s de áudio saiu
  em 0,14 s (`phonon`) e uma de 7,0 s em 0,65 s (`lfm3-audio`). A
  **primeira** requisição depois do pull levou 116 s (`phonon`) e 55 s
  (`lfm3-audio`); depois de descarregar com `keep_alive: 0`, recarregar levou
  1,4 s.
- **Erros são `400` com `invalid_request_error`:** `response_format` `mp3`
  (`use wav or pcm`), `speed` diferente de 1 (`only speed 1 is supported`),
  voz desconhecida (a mensagem lista as vozes), `input` acima de 4096
  caracteres, e `/api/chat` num modelo só de fala
  (`"phonon" does not support chat`).
- **STT funciona, por três caminhos não documentados:**
  `POST /v1/audio/transcriptions` (multipart `file` + `model`, como na
  OpenAI; aceita WAV e MP3, e `response_format=verbose_json` devolve
  `language`, `duration` e `segments`), `POST /v1/chat/completions` com uma
  parte `input_audio` em base64, e `POST /api/chat` com o áudio em base64 no
  campo `images`. Com o modelo quente, transcrever 7 s levou 0,13 s.
  Mandar áudio para o `phonon` responde `400`
  (`"phonon" does not support audio input`).

!!! note "Qualidade de transcrição: uma amostra não é taxa"
    Num único clipe de 7 s em português, o `lfm3-audio` transcreveu "Lá, este
    áudio..." (perdeu o "O" de "Olá") e o `SpeechToText("large-v3-turbo")`
    acertou "Olá! Este áudio...". Os dois escreveram "Olama". É uma amostra,
    não uma comparação de taxa de erro.

### `OllamaSpeech`: voz e transcrição pelo SDK

`OllamaSpeech` embrulha os dois endpoints e a lista de vozes, com o mesmo
cliente HTTP (retry, circuit-breaker, `X-Request-ID`) e o mesmo
`OllamaError` do `OllamaGenerator`. Só precisa do extra `[genai-ollama]`.

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaSpeech

speech = OllamaSpeech("lfm3-audio", timeout=180.0)


async def main() -> None:
    """Fala em português e transcreve de volta."""
    vozes = await speech.voices()
    print("female-portuguese" in vozes)

    wav = await speech.synthesize(
        "Bom dia! O SDK agora fala português pelo Ollama.",
        voice="female-portuguese",
        out_path="bom-dia.wav",
    )

    resultado = await speech.transcribe(wav)
    print(resultado.text, resultado.duration)
    await speech.aclose()


asyncio.run(main())
```

Uma execução contra o Ollama 0.40.13 na RTX 4070 Ti SUPER:

```text
True
Bom dia. O Sicoa agora fala português pelo Olama. 3.84
```

A síntese não é determinística: o mesmo programa, rodado quatro vezes, deu
áudios de 3,68 s a 4,32 s, e a sigla "SDK" voltou como "Raskiká", "Sicoa",
"STEC" e sumiu na quarta ("Bom dia, esta agora, fala português..."). Falada
pelo `lfm3-audio` e ouvida de volta pelo mesmo modelo, ela não sobreviveu à
volta nenhuma vez. Escreva siglas por extenso no texto que vai virar voz.

O que o `OllamaSpeech` faz por você:

- **Corrige o cabeçalho do WAV.** O stream do daemon deixa os tamanhos RIFF
  e `data` em `0xFFFFFFFF`, e o `wave` do Python lê `2147483647` frames.
  Como `synthesize` junta a resposta inteira, ele reescreve os tamanhos: um
  clipe de 4,32 s abre com `103680` frames a 24 kHz.
  `response_format="pcm"` devolve as amostras cruas, sem cabeçalho.
- **Separa erro do pedido de erro do servidor.** Quando o daemon recusa o
  pedido com `400` (voz desconhecida, modelo sem fala ou sem entrada de
  áudio, texto vazio ou acima de 4096 caracteres, áudio que não decodifica),
  levanta `ValidationException` (`422`) com a mensagem dele e
  `details={"backend": "ollama", "model": ...}`. Modelo não baixado ou falha
  do daemon continuam `OllamaError`.
- **Recusa o que o daemon ignoraria.** O Ollama só devolve o rótulo de
  `language` que recebeu (áudio em inglês enviado com `language=pt` voltou em
  inglês, marcado `"portuguese"`), e na fala a língua vem da voz. Por isso
  `transcribe` e `synthesize` aceitam `language` só como `None`: qualquer
  valor levanta `ValidationException` com `details={"field": "language"}`,
  antes de ir à rede. `Transcription.language` fica vazio. `prompt` e
  `temperature` também só entram como `None` (`details={"field": "prompt"}`
  ou `"temperature"`): se o daemon os respeita não foi medido, então não são
  enviados nem descartados em silêncio. Também não há
  `keep_alive`, que esses endpoints ignoram.
- **Caminho é caminho.** `transcribe("https://...")` levanta
  `FileNotFoundError` sem ir à rede, como o `SpeechToText`.

### Voz em stream: `stream_speech` e `speech_response`

`synthesize` espera o clipe inteiro. `stream_speech` entrega cada pedaço
assim que o daemon descarrega — PCM 16 bits mono 24 kHz por padrão, sem
cabeçalho, o formato que um player em stream quer —, com os mesmos erros.
Para servir por HTTP, use `speech_response`, que já devolve o
`StreamingResponse`:

```python
from fastapi import FastAPI, Response

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import OllamaSpeech

speech = OllamaSpeech("lfm3-audio", timeout=180.0)
app = FastAPI()
register_exception_handlers(app)


@app.get("/fala")
async def fala(texto: str) -> Response:
    """Devolve a fala em PCM conforme é gerada.

    Args:
        texto (str): O texto a falar.

    Returns:
        Response: Áudio PCM 16 bits mono 24 kHz, em stream.
    """
    return await speech.speech_response(texto, voice="female-portuguese")
```

!!! warning "Por que não montar o `StreamingResponse` direto"
    Um `StreamingResponse` manda status e cabeçalhos **antes** de ler o
    primeiro chunk. Medido: com `StreamingResponse(speech.stream_speech(...))`
    e uma voz desconhecida, o cliente recebeu **200 com corpo vazio**, e o
    Starlette logou `Caught handled exception, but response already started`.
    O `speech_response` lê o primeiro chunk antes de montar a resposta: a
    recusa sobe ali e vira o `422` do SDK, e o resto do áudio segue em stream.

Medido com o `lfm3-audio` quente: num texto de ~18 s de áudio, o primeiro
chunk chegou em 24 a 30 ms, contra 1,76 s do `synthesize` inteiro. `language`
é recusado antes da rede.

### Servindo por HTTP: `/tts` e `/transcribe`

O `make_genai_router` aceita qualquer objeto que satisfaça
`SpeechTranscriber` (para `/transcribe`) e `SpeechSynthesizer` (para
`/tts`), de `tempest_fastapi_sdk.genai.audio`. `SpeechToText`,
`TextToSpeech` e `OllamaSpeech` satisfazem; o `speaker` do corpo do `/tts`
vira a `voice` do Ollama.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import OllamaSpeech, make_genai_router

speech = OllamaSpeech("lfm3-audio", timeout=180.0)

app = FastAPI()
register_exception_handlers(app)
app.include_router(make_genai_router(speech_to_text=speech, text_to_speech=speech))
```

Medido contra o Ollama 0.40.13, com o `httpx.ASGITransport` na frente do
app:

| Pedido | Resposta |
| --- | --- |
| `POST /api/genai/tts` `{"text": "Olá pelo roteador.", "speaker": "male-portuguese"}` | `200`, `audio/wav` de 34560 frames |
| `POST /api/genai/transcribe` com esse WAV | `200`, `"text": "Olá pelo roteador."` |
| `POST /api/genai/tts` `{"text": "x", "speaker": "nope"}` | `422`, `VALIDATION_ERROR`, a mensagem do daemon em `detail` |
| `POST /api/genai/tts` `{"text": "x", "language": "pt"}` | `422`, `details: {"field": "language"}` |
| `POST /api/genai/transcribe` com 3000 bytes zerados | `422`, `"detail": "Failed to load image or audio file"` |

!!! tip "O primeiro pedido depois do pull é lento"
    Ele carrega o modelo: 116 s no `phonon` e 55 s no `lfm3-audio`, contra
    1,4 s numa recarga depois. Com o `timeout` padrão de 120 s, o primeiro
    `synthesize` do `phonon` passa raspando; aqueça o modelo no deploy ou
    suba o `timeout`.

### Fontes

- [Ollama: speech](https://docs.ollama.com/capabilities/speech)
- [Release notes do Ollama](https://github.com/ollama/ollama/releases) (v0.40.11 a v0.40.13)
- [`lfm3-audio` na biblioteca do Ollama](https://ollama.com/library/lfm3-audio)
- [Transformers: text-to-speech](https://huggingface.co/docs/transformers/en/tasks/text-to-speech)
- [transformers PR #39796: padronização do pipeline text-to-audio](https://github.com/huggingface/transformers/pull/39796)
- [Diffusers: pipelines de áudio](https://huggingface.co/docs/diffusers/en/api/pipelines/overview)
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
