# Quem falou o quê: diarização

Use para separar um áudio por falante (sherpa-onnx), reconhecer quem é por perfil de voz e servir isso por HTTP ou CLI.

Parte da receita [IA generativa self-hosted](../genai.md).

Transcrever responde *o que foi dito*. Diarizar responde *quem disse* —
recorta a gravação em turnos e agrupa as vozes. Junto, dá a ata de uma
reunião ou o registro de uma ligação:

```python
# scripts/ata.py

import asyncio

from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeakerDiarizer,
    SpeechToText,
)


async def main() -> None:
    """Transcribe a two-party call, attributing each line."""
    transcriber = ConversationTranscriber(
        stt=SpeechToText(model_size="small"),
        diarizer=SpeakerDiarizer(num_speakers=2),
    )
    conversa = await transcriber.transcribe("ligacao.wav", language="pt")
    print(conversa.transcript())
    print(conversa.by_speaker())


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Falante 0: Bom dia, em que posso ajudar?
Falante 1: Queria saber sobre a segunda via do boleto.
```

!!! info "Extra necessário"
    ```bash
    uv add "tempest-fastapi-sdk[genai-audio,genai-diarization]"
    ```
    `[genai-audio]` traz o Whisper (transcrição), `[genai-diarization]`
    traz o `sherpa-onnx` (quem falou). Nenhum dos dois usa PyTorch.

## Por que sherpa-onnx e não pyannote

Medido, não suposto. O `pyannote.audio` 4.0.7 declara **21 dependências**
de runtime — `torch>=2.8`, `lightning`, `matplotlib`, três pacotes de
OpenTelemetry e um cliente da API paga do fornecedor — e o pipeline
pré-treinado dele é **gated** no HuggingFace: o build do container precisa
de token e de licença aceita na mão.

O `sherpa-onnx` declara **uma** dependência, roda em ONNX Runtime sem
PyTorch, e os modelos são abertos. Numa gravação de 57 s com 4 falantes
separou os quatro com **RTF 0,125** em CPU — oito vezes mais rápido que
tempo real.

## Os modelos não vêm no wheel

São 46 MB. Baixe uma vez, de preferência no build:

```python
from tempest_fastapi_sdk.genai.audio import ensure_models

ensure_models()  # honra TEMPEST_VOICE_MODEL_DIR
```

Deixar para a primeira requisição faz um usuário pagar o download dentro
do timeout dele.

Os dois modelos têm o SHA-256 fixado no SDK (`SEGMENTATION_MODEL.sha256`,
`EMBEDDING_MODEL.sha256`), e o `ensure_models` confere o arquivo toda vez
que o resolve — download novo ou cache —, levantando `OSError` se não bater.
Um modelo trocado no release upstream, ou um cache corrompido, falha alto
em vez de mudar o comportamento do serviço sem nada no diff. O download
usa timeout de socket de 60 s: uma conexão que fica muda esse tempo levanta
em vez de travar o primeiro `load()` para sempre (um download lento, mas
andando, termina normalmente).

## Quantos falantes? Ele descobre sozinho

Diarizar precisa responder duas perguntas, e só uma é fácil. *Onde cada turno
começa e termina* sai do modelo de segmentação. *Quantas vozes distintas
existem* não sai de modelo nenhum — precisa ser inferido de como os turnos se
agrupam, e é a parte que erra o transcrito de um jeito que ninguém percebe:
oito participantes numa ligação de duas pontas, ou quatro pessoas coladas numa
só.

O padrão é `num_speakers="auto"`. Medido num banco de doze gravações cuja
contagem está correta **por construção** — turnos recortados de gravações
distintas, logo pessoas distintas, em vez de saírem do próprio diarizador:

| método | exato | erro médio |
| --- | --- | --- |
| limiar 0,5 | 4/10 | 1,90 |
| limiar 0,7 | 8/10 | 0,40 |
| limiar 0,9 | 8/10 | 0,20 |
| **automático** | **12/12** | **0,00** |

O automático ganha por motivo estrutural, não por constante feliz: um limiar
pergunta *quão perto é perto o bastante*, resposta que muda com o microfone, o
idioma e a sala; o método espectral pergunta *onde essa matriz de afinidade se
divide naturalmente*, que é propriedade da própria gravação.

Custa uma segunda passada — um embedding por turno mais uma decomposição —,
desprezível perto da segmentação que produziu os turnos.

!!! tip "Sabe quantos são? Diga."
    ```python
    from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer

    diarizer = SpeakerDiarizer(num_speakers=2)
    ```
    Numa ligação de duas pontas você sabe. É exato e pula a segunda passada.
    `num_speakers=None` volta ao agrupamento só por limiar, que é a opção mais
    fraca e existe para quem quer o comportamento antigo.

    Quando a contagem varia por requisição, passe **por chamada** em vez de
    ajustar o objeto:

    ```python
    turns = await diarizer.diarize(audio, num_speakers=2)
    ```

    Um diarizador é construído uma vez e compartilhado por todas as
    requisições, então escrever nele afeta as que estão em voo: duas chamadas
    simultâneas pedindo 2 e 5 falantes viam ambas a última escrita, e quem
    pediu 2 recebia 5 sem erro nenhum. O argumento por chamada não tem esse
    problema; `ConversationTranscriber.transcribe(num_speakers=...)` repassa
    por esse caminho.

!!! warning "Monólogo era reportado como conversa"
    A busca pelo maior salto espectral **sempre** acha uma divisão, inclusive
    onde não há: um ditado real de seis turnos voltava como dois falantes.

    Uma voz só é *uniformemente* parecida consigo mesma — até o par de turnos
    mais distante dela está perto —, enquanto duas vozes produzem pares
    genuinamente longe. Medido nas doze gravações, o percentil 10 da
    similaridade ficou em 0,490–0,667 para um falante e −0,080–0,166 para mais
    de um; o veto fica no meio dessa folga.

    Esse número é propriedade da **escala de similaridade do modelo embarcado**,
    não constante universal: trocar de modelo exige remedi-lo.

## Como a atribuição funciona, e onde ela erra

A gravação é transcrita **uma vez**, não uma vez por turno: entregar
trechos de dois segundos ao Whisper joga fora o contexto que ele usa para
decidir pontuação e grafia, e custa uma inferência por turno. Depois cada
trecho transcrito vai para o turno com quem ele mais se sobrepõe no tempo.

O preço dessa escolha: um trecho do Whisper pode **atravessar** uma troca
de falante, e aí ele inteiro cai em quem detém a maior parte. Um turno que
o diarizador descartou por ser curto demais ainda transcreve — e esse
texto sai com `speaker = -1`, nunca some. Perder palavra caladamente é
pior do que admitir que não se sabe quem falou.

## Reconhecer quem é: perfis de voz

Diarização separa falante 0 de falante 1. Identificação diz que o falante 0
**é a Ana**, casando a voz com um perfil cadastrado.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import VoiceProfileService

from src.db.models import UserModel

VoiceProfileModel = make_voice_profile_model(user_table="users")
perfis = VoiceProfileService(profile_model=VoiceProfileModel)
```

Cadastro exige consentimento — não é opcional e não é configurável:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeechToText,
    VoiceProfileService,
)

from src.db.models import UserModel

perfis = VoiceProfileService(
    profile_model=make_voice_profile_model(user_table="users"),
)
transcriber = ConversationTranscriber(stt=SpeechToText())


async def cadastrar_voz(session: AsyncSession, usuario: UserModel) -> None:
    """Enrol a user's voice after they consented to it."""
    await perfis.enroll(
        session,
        user_id=usuario.id,
        audio="cadastro.wav",
        consent_reference="politica-biometria-v3",
        label="cadastro no onboarding",
    )
```

Depois, a transcrição já sai com nome:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeechToText,
    VoiceProfileService,
)

from src.db.models import UserModel

perfis = VoiceProfileService(
    profile_model=make_voice_profile_model(user_table="users"),
)
transcriber = ConversationTranscriber(stt=SpeechToText())


async def ata(session: AsyncSession, participantes: list[UserModel]) -> str:
    """Transcribe a meeting with each line attributed to a person."""
    conversa = await transcriber.transcribe(
        "reuniao.wav",
        identify_with=perfis,
        session=session,
        user_ids=[p.id for p in participantes],
    )
    return conversa.transcript()
```

!!! danger "Isso é dado biométrico"
    Impressão vocal identifica uma pessoa como um template de digital. Pela
    LGPD é **dado pessoal sensível** (Art. 5º, II), e o tratamento exige
    consentimento **específico e destacado** para essa finalidade (Art. 11, I)
    — termo de uso genérico não cobre.

    Por isso `consent_reference` é obrigatório e um valor em branco levanta
    `ConsentRequired`. O SDK guarda o vetor e o consentimento na mesma linha, e
    **nunca grava o áudio**: o vetor não se reproduz de volta em som, o que faz
    um vazamento dessa tabela custar muito menos que um vazamento das
    gravações.

    `forget_user()` existe como método, e não como exemplo na doc, porque
    "apague meus dados biométricos" é direito incondicional (Art. 18, VI) e não
    pode depender de cada projeto escrever o `WHERE` certo.

!!! tip "Restrinja aos participantes"
    `user_ids=[...]` transforma "quem no banco inteiro é essa voz" em "qual
    destas cinco pessoas é". Mais rápido, e muito menos sujeito a colocar o
    nome de um estranho numa linha.

Medido com vozes reais: cadastrando a partir de um turno e identificando
**outro** turno da mesma pessoa, a similaridade deu 0,687 e 0,734; um falante
não cadastrado voltou `None`. O limiar padrão é 0,5 — suba para qualquer coisa
que conceda acesso, porque aí o erro caro deixa de ser "não reconheceu" e passa
a ser "reconheceu errado".

Trocar o modelo de embedding invalida todo perfil já cadastrado: os vetores
deixam de ser comparáveis e as pessoas silenciosamente param de ser
reconhecidas. `stale_profiles()` encontra quem precisa recadastrar.

## Servindo por HTTP e pela linha de comando

```python
# src/api/app.py

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.genai.audio import make_voice_router

from src.api.dependencies.auth import current_user_id
from src.core.resources import db, perfis, transcriber


def create_app() -> FastAPI:
    """Mount the voice routes behind the service's own auth."""
    app = FastAPI()
    app.include_router(
        make_voice_router(
            session_factory=db.session_dependency,
            transcriber=transcriber,
            profiles=perfis,
            current_user_id=current_user_id,
            dependencies=[Depends(current_user_id)],
        ),
    )
    return app
```

Quatro rotas: `POST /voice/transcribe`, e `POST` / `GET` / `DELETE`
`/voice/profiles`.

!!! warning "`current_user_id` é obrigatório junto de `profiles`"
    Cadastrar ou apagar usando um id vindo do **corpo** da requisição deixaria
    qualquer um gravar biometria na conta alheia. O router recusa essa
    combinação na hora da montagem — falha em produção seria tarde demais.

As rotas de listar e apagar não são cortesia: são o direito da pessoa sobre o
próprio dado. E a listagem **não devolve o embedding** — ela precisa saber que
o perfil existe, não receber uma cópia do próprio template biométrico por HTTP.

!!! info "O comando `tempest` pede o extra `[cli]` (ou `[cli-ops]` no servidor)"
    Sem um dos dois, `tempest` sai com código 2 e imprime como instalar. Em
    desenvolvimento: `uv add --dev "tempest-fastapi-sdk[cli]"`. Detalhes em
    [Instalação → CLI](../../installation.md#cli).

Da linha de comando:

```bash
tempest voice models                        # baixa os modelos (faça no build)
tempest voice diarize reuniao.wav -n 2      # quem falou quando
tempest voice transcribe reuniao.wav -n 2   # quem disse o quê
```

`diarize` não carrega o Whisper — é o jeito rápido de conferir se a contagem de
falantes e o limiar estão certos antes de pagar pela transcrição.
