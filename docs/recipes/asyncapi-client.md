# Cliente de WebSocket (AsyncAPI)

A receita de [cliente de integração (OpenAPI)](openapi-client.md) gera um
cliente HTTP de uma especificação. Esta faz o mesmo para o que o OpenAPI
**não** descreve: uma conexão que fica aberta, carrega mensagem nos dois
sentidos, e onde o servidor fala sem ninguém pedir.

Não é uma limitação de ferramenta — é de formato. O OpenAPI modela uma
requisição e a resposta dela; não existe onde encaixar um socket. Por isso
rota de WebSocket costuma virar um parágrafo de prosa na documentação, e
prosa não gera cliente nenhum.

O formato que descreve isso é o **AsyncAPI 3.0**, e o SDK lê.

## Gerando

```bash
uv run tempest asyncapi-client http://127.0.0.1:3000/asyncapi.json \
    --name zap \
    --out src/integrations/zap_ws
```

Saem três arquivos, como no gerador de OpenAPI:

| Arquivo | O que tem |
| --- | --- |
| `schemas.py` | Uma classe Pydantic por payload de frame |
| `stream.py` | O cliente, mais as duas uniões e o erro de frame desconhecido |
| `__init__.py` | Re-export dos dois, na forma dupla (`as` + `__all__`) |

Requer o extra `[websocket]`, que o SDK já usa no lado servidor — nenhuma
dependência nova entra por causa disto.

## Usando

```python
import asyncio

from src.integrations.zap_ws import (
    AckFrame,
    ErrorFrame,
    ServerMessageFrame,
    SubscribeFrame,
    ZapStream,
)


async def main() -> None:
    """Assinar tudo e imprimir o que chegar."""
    async with ZapStream(x_api_key="<sua chave>") as stream:
        await stream.send(SubscribeFrame(action="subscribe", room="*"))

        async for frame in stream:
            match frame:
                case ServerMessageFrame():
                    print(frame.payload.remote_jid, frame.payload.text)
                case AckFrame():
                    print("ack:", frame.event)
                case ErrorFrame():
                    print("erro:", frame.message)


asyncio.run(main())
```

O `url` tem default quando o documento declara um `servers`, e o header do
handshake vira **argumento obrigatório** quando o documento o marca como
requerido — `x-api-key` virou `x_api_key`.

## Duas uniões, não uma

O que você envia e o que você recebe são tipos diferentes:

```python
from src.integrations.zap_ws import (
    AckFrame,
    ErrorFrame,
    SendFrame,
    ServerMessageFrame,
    SubscribeFrame,
    UnsubscribeFrame,
)

ZapStreamClientFrame = SubscribeFrame | UnsubscribeFrame | SendFrame
ZapStreamServerFrame = AckFrame | ErrorFrame | ServerMessageFrame
```

O `send` aceita só a primeira; o `receive` e a iteração devolvem só a
segunda. Um `match` sobre a união de entrada é exaustivo para o
type-checker, então frame novo no documento vira erro de tipo no seu
serviço em vez de um `else` silencioso.

!!! tip "Cada variante é reconhecida pelo discriminante"
    O gerador procura, em cada payload, uma propriedade cujo `enum` tem
    exatamente um valor — que é como um literal é renderizado em JSON
    Schema. Esse par vira a chave de uma tabela de despacho.

    Um frame que chega com um discriminante que o documento não declara
    levanta `ZapStreamFrameError`, carregando a tag e o corpo. A causa comum
    é servidor à frente do documento commitado, e o conserto é regenerar.

## A parte que mais dá errado: a direção

O `action` do AsyncAPI é relativo a **quem publicou o documento**. Como quem
publica é o servidor, um frame que o cliente envia aparece no documento como
`action: "receive"` — o servidor é quem recebe.

Um gerador de cliente precisa **inverter** todos.

!!! danger "Inversão errada não quebra nada visível"
    O cliente compila, passa no type-check, e manda o que deveria escutar.
    Nenhum sinal aparece até a primeira mensagem sumir.

    Por isso o gerador **recusa** um documento que não declare
    `x-tempest-perspective: "server"` na raiz:

    ```console
    $ uv run tempest asyncapi-client ./doc.json --name x --out /tmp/x
    error: ./doc.json does not declare `x-tempest-perspective`. AsyncAPI's
    `action` is relative to whoever published the document, and a client has
    to invert it — so a document that does not say which end wrote it cannot
    be generated from.
    ```

    Documento gerado pelo `tempest-express-sdk` já carrega o campo. Num
    escrito à mão, é uma linha.

A inversão acontece **uma vez**, no parser. O IR fala só a perspectiva do
cliente (`outbound`/`inbound`), então nem o emissor nem quem lê o IR precisa
lembrar de quem é o `action` que está segurando.

## Um canal, sempre

A especificação é explícita: em WebSocket *"the channel represents the
connection [...] there's only one channel"*. Diferente de Kafka ou MQTT,
onde canal é tópico. Documento com mais de um canal é recusado — está
descrevendo outro transporte, e este gerador emite cliente de WebSocket.

Salas, quando existem, são conceito dos **frames**, não do canal.

## Validação vem junto

Constraint do documento vira validação Pydantic no modelo gerado:

```python
from src.integrations.zap_ws import SubscribeFrame

# docs-guard: skip — a chamada recusada abaixo é o assunto da seção
SubscribeFrame(action="subscribe", room="nao@vale@nada")
# pydantic_core.ValidationError: String should match pattern
# '^(\*|\d{10,15}|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+)$'
```

O frame inválido nem chega à rede. É o mesmo regex que o servidor aplica,
porque os dois saem do mesmo schema.

## Mantendo em dia

Como no gerador de OpenAPI, o documento é vendorizado e os arquivos gerados
são commitados, para que o consumidor não rode codegen:

```bash
make zap-ws-fetch    # relê o documento do gateway (precisa dele no ar)
make zap-ws-regen    # regenera a partir do vendorizado, offline
```

O `SPEC_SHA256` registra de quais bytes este checkout gerou, e
`tests/integrations/messaging/zap_ws/test_generated_drift.py` falha se
alguém editar o gerado à mão ou esquecer de atualizar o digest.

!!! note "Duas superfícies, dois documentos"
    Um serviço que fala HTTP **e** WebSocket publica os dois: o
    `/openapi.json` para as rotas e o `/asyncapi.json` para o socket. Eles
    convivem porque nenhum formato sozinho descreve os dois.

## Recapitulando

- O OpenAPI não descreve socket; o AsyncAPI descreve, e o SDK lê 3.x.
- `tempest asyncapi-client` escreve `schemas.py`, `stream.py` e o barrel.
- Uma união para o que você envia, outra para o que recebe; `match` exaustivo.
- Direção é invertida uma vez, no parser — e o documento tem que dizer de
  quem é a perspectiva, ou é recusado.
- Um canal por conexão, porque WebSocket não tem canal virtual.
- Constraint do documento vira validação, então frame inválido não sai.
- Documento vendorizado, gerado commitado, drift test guardando os dois.
