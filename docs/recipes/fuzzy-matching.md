# Busca aproximada (fuzzy) e cidades em texto

O usuário escreve "moro em sao jose dos campos - sp", "vou pra floripa" ou
"sao paolo". Você precisa saber **qual** cidade é, com o código do IBGE e a
coordenada, sem depender de serviço externo e sem errar quando ele digita
errado.

Para casar o que o usuário digitou com uma **lista conhecida de nomes**
(cidades, produtos, categorias), busca léxica resolve melhor que embedding:
não tem modelo para baixar, não precisa de GPU, e um erro de digitação custa
uma edição. A busca semântica sobre trechos livres continua no
[`genai`](genai.md) (`Embedder`, `Retriever`, `HybridRetriever`).

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

## Quanto custa

Medido num x86_64 com Python 3.12.7:

| O quê | Mediana |
| --- | --- |
| Primeira chamada no processo (monta o índice dos 5571 nomes) | 45,9 ms (N=5) |
| `"vou pra floripa semana que vem"` | 0,79 ms (N=200) |
| `"moro em sao jose dos campos - sp"` | 3,02 ms (N=200) |
| Frase com três cidades, uma com erro | 3,27 ms (N=200) |

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
- **`search_municipalities` / `municipality_by_ibge_code`**: busca por nome
  (com erro) e por código, com coordenada e capital.
- Homônimos sem UF voltam empatados. A decisão é sua.
