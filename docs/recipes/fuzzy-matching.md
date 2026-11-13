# Busca aproximada (fuzzy) e cidades em texto

O usuário escreve "moro em sao jose dos campos - sp", "vou pra floripa" ou
"sao paolo". Você precisa saber **qual** cidade é, com o código do IBGE e a
coordenada, sem depender de serviço externo e sem errar quando ele digita
errado.

Para casar o que o usuário digitou com uma **lista conhecida de nomes**
(cidades, produtos, categorias), busca léxica resolve melhor que embedding:
não tem modelo para baixar, não precisa de GPU, e um erro de digitação custa
uma edição. Quando o usuário descreve com **outras palavras**, a mesma
página mostra a busca por significado (embeddings) e a combinada, e as tools
de agente; a busca semântica sobre trechos longos continua no
[`genai`](genai.md) (`Retriever`, `HybridRetriever`).

Esta receita tem duas partes:

1. `tempest_fastapi_sdk.text`: normalização, distância de edição e um índice
   de busca aproximada, sem nenhuma dependência.
2. `tempest_fastapi_sdk.geo`: os 5571 municípios do IBGE embutidos, a busca
   por nome e a extração da cidade de um texto livre.

## Normalizar e comparar

```python
from tempest_fastapi_sdk.text import FuzzyIndex, normalize_text, similarity

print(normalize_text("  São José-dos Campos/SP "))
print(round(similarity("florianopols", "florianopolis"), 2))

index: FuzzyIndex[int] = FuzzyIndex(
    [("Camiseta básica", 101), ("Calça jeans", 102), ("Camisa social", 103)]
)
for match in index.search("camizeta basica"):
    print(match.key, match.value, round(match.score, 2))
```

Saída:

```text
sao jose dos campos sp
0.92
camiseta basica 101 0.93
```

- **`normalize_text`** tira acento, ignora caixa e troca pontuação por
  espaço. É a forma que todo o resto compara.
- **`similarity`** vai de `0.0` a `1.0`: `1 - edições / tamanho do maior`.
  Trocar duas letras vizinhas ("soa" por "sao") conta como uma edição só.
- **`FuzzyIndex`** guarda nome → valor. `exact()` acha o nome normalizado;
  `search(query, cutoff=0.8, limit=5)` aceita erros até o `cutoff`.

!!! info "Por que é rápido"
    O índice guarda os trigramas (pedaços de três letras) de cada nome. Uma
    consulta só calcula a distância de edição contra os nomes que dividem
    trigramas suficientes com ela. O filtro **não perde** nome nenhum: um nome
    a `d` edições divide pelo menos `trigramas - 4d` com a consulta, e o
    índice só descarta abaixo disso. Quando esse limite chega a zero (consulta
    curta com `cutoff` baixo), o índice compara com todos os nomes de tamanho
    próximo. Um teste compara o índice com a comparação contra todos os
    nomes, em 300 consultas aleatórias para cada `cutoff` de `0.5` a `0.9`,
    e os dois batem.

## Achar a cidade num texto

```python
from tempest_fastapi_sdk.geo import find_municipalities_in_text

for text in [
    "moro em sao jose dos campos - sp",
    "Sou de Campinas, São Paulo",
    "vou pra floripa semana que vem",
    "estou feliz hoje",
    "nasci em bom jesus",
]:
    matches = find_municipalities_in_text(text, limit=3)
    print(text)
    for match in matches:
        city = match.municipality
        print(
            f"  {city.name}/{city.uf.value} ibge={city.ibge_code}"
            f" score={match.score:.2f} confirmed={match.uf_confirmed}"
            f" text={match.matched_text!r}"
        )
```

Saída:

```text
moro em sao jose dos campos - sp
  São José dos Campos/SP ibge=3549904 score=1.00 confirmed=True text='sao jose dos campos'
Sou de Campinas, São Paulo
  Campinas/SP ibge=3509502 score=1.00 confirmed=True text='Campinas'
vou pra floripa semana que vem
  Florianópolis/SC ibge=4205407 score=0.90 confirmed=False text='floripa'
estou feliz hoje
nasci em bom jesus
  Bom Jesus/PI ibge=2201903 score=0.90 confirmed=False text='bom jesus'
  Bom Jesus/RN ibge=2401701 score=0.90 confirmed=False text='bom jesus'
  Bom Jesus/PB ibge=2502201 score=0.90 confirmed=False text='bom jesus'
```

O que cada caso mostra:

- **A UF ao lado confirma.** `"- sp"`, `"/SP"`, `", São Paulo"` ou `"SP"`
  logo depois do nome deixam só a cidade daquele estado e marcam
  `uf_confirmed=True` (nota `1.0`). Uma UF em que o nome não existe
  (`"Campinas/RJ"`) descarta a cidade.
- **O estado não vira cidade.** Em `"Campinas, São Paulo"`, o "São Paulo" é o
  estado de Campinas e não aparece como a capital.
- **Apelidos** comuns entram: `bh`, `sampa`, `floripa`, `poa`, `bsb`,
  `jampa`, `sjc`, `rio` (`BR_CITY_NICKNAMES`). As regiões administrativas do
  DF (Ceilândia, Gama, ...) apontam para Brasília, e grafias antigas ("Açu"
  para "Assú") também casam. Apelido é nome de uma palavra como outro qualquer:
  precisa de sinal de lugar (`"vou pra bh"`, `"Rio"`, `"sampa, sp"`, ou o
  texto inteiro ser só `"bh"`).
- **Palavra comum não vira cidade.** "Feliz", "Natal" e "Alegre" são
  municípios. Minúscula, no meio de outras palavras, sem "em/de/moro" antes
  e sem UF depois, a palavra fica com metade da nota e cai abaixo do
  `min_score` padrão (`0.6`). `"moro em Feliz/RS"` acha Feliz, e um texto que
  é **só** o nome (`"feliz"`, o campo "cidade" de um formulário) também.
- **Homônimos sem UF empatam.** `"bom jesus"` devolve todas as Bom Jesus com
  a mesma nota; quem decide é você, com outra informação (o CEP, o DDD, a
  pergunta seguinte ao usuário).

`matched_text`, `start` e `end` apontam para o trecho **como o usuário
escreveu**, então dá para destacar ou substituir no texto original.

!!! tip "Erro de digitação dentro da frase"
    Cada sequência de até quatro palavras, com pelo menos cinco letras e sem
    correspondência exata, passa pela busca aproximada com `cutoff=0.85`.
    `"trabalho em sao paolo"` acha São Paulo com `0.80`: a semelhança do nome
    (`0.89`) vezes `0.9`, porque nenhuma UF confirmou.

## Pedido de viagem: de onde, pra onde, quando

Achar a cidade é metade do trabalho de uma busca de viagem. A outra metade é
saber o **papel** dela: em "quero ir de Teresina pra Parnaíba sexta de
manhã", Teresina é a origem, Parnaíba o destino, sexta a data e manhã o
período. `parse_trip_intent` lê tudo isso numa frase digitada ou transcrita:

```python
from datetime import date

from tempest_fastapi_sdk.geo import parse_trip_intent

today = date(2026, 10, 14)
for text in [
    "quero ir de teresinha pra parnaiba sexta de manhã",
    "de Pico pra Sobrau amanhã",
    "de floripa pra sampa dia 20",
    "de pedro segundo pra piripiri à noite",
]:
    intent = parse_trip_intent(text, today)
    origin = intent.origin.name if intent.origin else None
    destination = intent.destination.name if intent.destination else None
    print(text, "->", origin, destination, intent.departure_date, intent.period)
```

```text
quero ir de teresinha pra parnaiba sexta de manhã -> Teresina Parnaíba 2026-10-16 manha
de Pico pra Sobrau amanhã -> Picos Sobral 2026-10-15 None
de floripa pra sampa dia 20 -> Florianópolis São Paulo 2026-10-20 None
de pedro segundo pra piripiri à noite -> Pedro II Piripiri None noite
```

Cada linha mostra um erro que a fala transcrita produz e que a busca
absorve:

- **"teresinha"**, **"Pico"**: letra trocada ou que caiu. O `FuzzyIndex`
  junta os candidatos por distância de edição, e a razão do `difflib`
  decide entre eles (Teresina ganha de Terezinha/PE).
- **"Sobrau"**: o "l" de fim de sílaba que o português falado diz "u". Uma
  chave fonética (`vocalized_key`) casa exato, sem baixar o corte.
- **"floripa"**, **"sampa"**: os apelidos de `BR_CITY_NICKNAMES`.
- **"pedro segundo"**: o numeral romano dito por extenso.

!!! tip "Só as cidades que você atende"
    Passe `municipalities=` com as cidades que têm rota, e só elas são
    reconhecidas. Cidade real fora da lista volta em `unrecognized`, com o
    que foi dito, para o app perguntar em vez de buscar sem destino:

    ```python
    from datetime import date

    from tempest_fastapi_sdk.geo import (
        build_trip_city_index,
        municipality_by_name,
        parse_trip_intent,
    )

    served = [
        municipality_by_name(name, uf)
        for name, uf in [("Teresina", "PI"), ("Picos", "PI"), ("Sobral", "CE")]
    ]
    index = build_trip_city_index([city for city in served if city is not None])

    intent = parse_trip_intent(
        "de Teresina pra Natal amanhã", date(2026, 10, 14), municipalities=index
    )
    print(intent.origin.name, intent.destination, dict(intent.unrecognized))
    # Teresina None {<TripRoleEnum.DESTINATION: 'destination'>: 'natal'}
    ```

    `municipality_by_name` liga o nome que o seu banco guarda ("SAO LUIS",
    "MA") ao registro do IBGE, sem caixa nem acento. Monte o índice uma vez
    e reaproveite.

!!! note "Homônimos e ambiguidade"
    "Batalha" existe no PI e em AL. Sem UF, os dois voltam em
    `intent.origin.candidates` (`resolved` é `None`, `name` é "Batalha"),
    para a decisão ser sua. "Batalha no Piauí" ou "Batalha PI" resolve.

!!! warning "Palavra comum no país inteiro"
    Sem `municipalities=`, uma palavra solta minúscula só vira cidade quando a
    frase é só cidades ("picos sobral"): "estou feliz de ir pra fortaleza" não
    é a cidade de Feliz (RS). Com a lista restrita, essa trava não existe.

### Sair agora

Na busca por voz, "quero ir agora" é tão comum quanto "hoje". `agora`,
`agora mesmo`, `já`, `imediatamente`, `o quanto antes`, `o mais rápido
possível`, `neste momento` e `nesse momento` viram `departure_date=today` e
ligam `departure_now`:

```python
from datetime import date

from tempest_fastapi_sdk.geo import parse_trip_intent

today = date(2026, 11, 13)
for text in [
    "quero ir de picos para teresina agora",
    "picos teresina agora mesmo",
    "de picos pra teresina o quanto antes",
    "agora quero ver de picos pra teresina amanhã",
]:
    intent = parse_trip_intent(text, today)
    print(text, "->", intent.departure_date, intent.departure_now)
```

```text
quero ir de picos para teresina agora -> 2026-11-13 True
picos teresina agora mesmo -> 2026-11-13 True
de picos pra teresina o quanto antes -> 2026-11-13 True
agora quero ver de picos pra teresina amanhã -> 2026-11-14 False
```

- **Palavra inteira**: "agorafobia" e "Jaqueira" (a cidade de PE) não contam,
  sem caixa nem acento ("JÁ!" conta).
- **Data dita vence**: com "amanhã", "sábado" ou "20/10" na frase, a data é a
  dita e `departure_now` é `False`. Só a data dita que cai em hoje ("hoje
  agora", ou "sexta" numa sexta) mantém `departure_now=True`. Data que não existe ("dia 31 de
  fevereiro") também vence: a data fica `None` para o app perguntar.
- **Sem preposição**: "picos teresina agora mesmo" cai na regra das cidades
  em ordem (primeira origem, segunda destino), porque o "agora mesmo" sai da
  frase antes e só sobram cidades.

!!! info "O parser não tem relógio"
    `parse_trip_intent` recebe `today: date`, não a hora. `departure_now`
    não vem com `period_start`: quem chama aplica o "a partir de agora" com o
    próprio relógio, mostrando só as viagens de hoje que ainda vão sair.

A qualidade é fixada por uma tabela de 65 frases reais
(`tests/geo/test_trip_intent.py`), vindas da busca de viagem em produção do
BusCar. Mudança que quebre uma linha é regressão da busca.

## Por nome e por código

```python
from tempest_fastapi_sdk.geo import municipality_by_ibge_code, search_municipalities

for match in search_municipalities("sao paolo", limit=2):
    print(match.municipality.name, match.municipality.uf.value, round(match.score, 2))

city = municipality_by_ibge_code(3550308)
assert city is not None
print(city.name, city.coordinate.latitude, city.coordinate.longitude, city.capital)
```

Saída:

```text
São Paulo SP 0.89
São Paulo -23.5329 -46.6395 True
```

`search_municipalities(nome, uf=...)` serve para um campo "cidade" de
formulário: com `uf`, só aquele estado. `br_municipalities()` devolve a lista
inteira, ordenada por código.

## Por significado: busca com IA

`FuzzyIndex` acha o nome **escrito** parecido; não acha o nome que **quer
dizer** a mesma coisa. "tênis de corrida" não tem letra em comum com
"calçado esportivo". Para isso existe o embedding, e o `genai.rag` traz dois
índices sobre qualquer embedder do SDK (`Embedder`, `OllamaEmbedder`,
`OpenAICompatEmbedder`, `OnnxEmbedder`):

- **`SemanticNameIndex`**: ranqueia os nomes por similaridade de cosseno com a
  consulta. Os nomes são embedados uma vez, em lote, na primeira busca.
- **`HybridNameIndex`**: roda os dois lados, escrita (palavra por palavra,
  com erro de digitação) e significado, e funde os rankings com
  `reciprocal_rank_fusion`. Cada resultado traz `lexical_score` e
  `semantic_score`, então você vê por que ele veio.

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaEmbedder
from tempest_fastapi_sdk.genai.rag import HybridNameIndex

PRODUCTS: list[tuple[str, int]] = [
    ("Calçado esportivo para corrida", 1),
    ("Camiseta básica de algodão", 2),
    ("Calça jeans masculina", 3),
    ("Garrafa térmica 1 litro", 4),
    ("Mochila para notebook", 5),
    ("Fone de ouvido bluetooth", 6),
]


async def main() -> None:
    embedder = OllamaEmbedder(model="nomic-embed-text")
    index: HybridNameIndex[int] = HybridNameIndex(embedder, PRODUCTS)
    for query in ["camizeta basica", "tênis de corrida", "headphone sem fio"]:
        best = (await index.search(query, limit=1))[0]
        spelling = "-" if best.lexical_score is None else f"{best.lexical_score:.2f}"
        meaning = "-" if best.semantic_score is None else f"{best.semantic_score:.2f}"
        print(f"{query!r} -> {best.key} (spelling {spelling}, meaning {meaning})")


asyncio.run(main())
```

Saída, com `nomic-embed-text` num Ollama local:

```text
'camizeta basica' -> Camiseta básica de algodão (spelling 0.94, meaning 0.84)
'tênis de corrida' -> Calçado esportivo para corrida (spelling 0.50, meaning 0.64)
'headphone sem fio' -> Fone de ouvido bluetooth (spelling -, meaning 0.57)
```

- `"camizeta basica"` veio pelos **dois** lados: a escrita casa as duas
  palavras com erro, e o sentido também.
- `"tênis de corrida"` veio pelo **significado**. A palavra "corrida" casa,
  mas sozinha cobre só metade da consulta.
- `"headphone sem fio"` veio **só** pelo significado: nenhuma palavra casa.

!!! info "Palavra curta não conta"
    O lado da escrita ignora palavras com menos de três letras
    (`MIN_LEXICAL_WORD_LENGTH`). Sem isso, o "de" de "tênis de corrida"
    casava com todo nome que tivesse um "de".

!!! tip "Quando usar cada um"
    Lista de nomes próprios (cidades, pessoas, marcas): `FuzzyIndex`, sem
    modelo. Catálogo em que o usuário descreve o que quer com outras
    palavras: `HybridNameIndex`, que mantém a escrita como sinal próprio em
    vez de depender de o modelo ter visto a grafia errada.

## Numa conversa: tool de agente

Um agente que conversa com o usuário precisa achar a cidade sem que você
escreva o passo. `municipality_search_tool()` dá a ele a busca de município
(frase ou nome, com `uf` opcional), e `name_search_tool(index)` expõe
qualquer um dos três índices.

```python
import asyncio

from tempest_fastapi_sdk.agents import Agent, municipality_search_tool
from tempest_fastapi_sdk.genai import OllamaGenerator


async def main() -> None:
    agent = Agent(
        OllamaGenerator(model="qwen2.5:3b"),
        tools=[municipality_search_tool()],
    )
    run = await agent.run(
        "O usuário escreveu: 'vou pra floripa semana que vem'. "
        "Qual é a cidade, a UF e o código IBGE?"
    )
    print([step.name for step in run.steps if step.kind == "tool"])
    print(run.output)


asyncio.run(main())
```

Saída de uma das execuções com `qwen2.5:3b` no Ollama local:

```text
['find_brazilian_city']
A cidade é Florianópolis, que está localizada no estado de Santa Catarina (UF SC). Seu código IBGE é 4205407 e as coordenadas geográficas são lat -27.5945, lon -48.5477.
```

Em 20 execuções seguidas (N=20) o modelo chamou a tool em todas e respondeu
Florianópolis/SC com o código 4205407 em todas. Em 3 delas ele passou
`uf="SC"` junto da frase: a UF filtra o que a frase achou, em vez de trocar a
busca na frase pela busca do texto inteiro como nome. A redação da resposta
muda a cada execução; o que a tool devolve, não.

## Quanto custa

Medido num x86_64 com Python 3.12.7:

| O quê | Mediana |
| --- | --- |
| Primeira chamada no processo (monta o índice dos 5571 nomes) | 45,9 ms (N=5) |
| `"vou pra floripa semana que vem"` | 0,79 ms (N=200) |
| `"moro em sao jose dos campos - sp"` | 3,02 ms (N=200) |
| Frase com três cidades, uma com erro | 3,27 ms (N=200) |
| `parse_trip_intent`, frase sem erro (país inteiro) | 0,03 ms (N=200) |
| `parse_trip_intent`, "de Pico pra Sobrau amanhã" (país inteiro, cache frio) | 8,4 ms (N=200) |
| `parse_trip_intent`, "teresinha" + "floripa" (país inteiro, cache frio) | 28,3 ms (N=200) |
| Índice restrito a 4 cidades (`build_trip_city_index`) | 0,05 ms |

O índice é montado uma vez por processo e fica em memória.

!!! note "De onde vêm os dados"
    Nome e UF vêm da API de localidades do IBGE (dado público); coordenada,
    capital e as grafias antigas vêm de `kelvins/municipios-brasileiros`
    (MIT, com o aviso de licença no módulo gerado). As duas fontes listam os
    mesmos 5571 códigos. A lista de `list_states()` (receita
    [Helpers BR](br-helpers.md)) conta 5606 porque inclui as 35 regiões
    administrativas do DF; aqui elas são apelidos de Brasília. Para atualizar:
    `uv run python scripts/regen_br_municipalities.py`. Um teste fixa o hash
    dos dados, então uma regeneração aparece no diff do PR.

## Recap

- **`tempest_fastapi_sdk.text`**: `normalize_text`, `similarity` e
  `FuzzyIndex` para casar o que o usuário digita com qualquer lista de nomes,
  sem dependência.
- **`find_municipalities_in_text`**: acha a cidade numa frase, com a UF
  confirmando e desempatando, apelidos e palavra comum filtrada.
- **`parse_trip_intent`**: origem, destino, data e período de uma frase de
  viagem, com fala transcrita (erro, "l" vocalizado, numeral por extenso) e
  restrita às cidades que você atende (`municipalities=`). "Agora", "já" e
  "o quanto antes" são hoje com `departure_now`; o filtro por hora é seu.
- **`search_municipalities` / `municipality_by_ibge_code`**: busca por nome
  (com erro) e por código, com coordenada e capital.
- Homônimos sem UF voltam empatados. A decisão é sua.
- **`SemanticNameIndex` / `HybridNameIndex`**: busca por significado, e por
  significado mais escrita, sobre qualquer embedder do SDK.
- **`municipality_search_tool` / `name_search_tool`**: as mesmas buscas como
  tool de agente.
