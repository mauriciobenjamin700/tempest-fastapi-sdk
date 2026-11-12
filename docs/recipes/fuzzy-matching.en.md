# Fuzzy matching and cities in text

The user writes "moro em sao jose dos campos - sp", "vou pra floripa" or
"sao paolo". You need to know **which** city it is, with its IBGE code and
coordinate, without an external service and without failing on a typo.

To match what the user typed against a **known list of names** (cities,
products, categories), lexical matching beats an embedding: no model to
download, no GPU, and a typo costs one edit. When the user describes it in
**other words**, this same page shows search by meaning (embeddings), the combined search, and the
agent tools; semantic search over long passages stays in [`genai`](genai.md)
(`Retriever`, `HybridRetriever`).

This recipe has two parts:

1. `tempest_fastapi_sdk.text`: normalization, edit distance and a fuzzy
   index, with no dependency.
2. `tempest_fastapi_sdk.geo`: the 5571 IBGE municipalities bundled, lookup by
   name and finding the city in free text.

## Normalize and compare

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

Output:

```text
sao jose dos campos sp
0.92
camiseta basica 101 0.93
```

- **`normalize_text`** strips accents, ignores case and turns punctuation
  into spaces. It is the form everything else compares.
- **`similarity`** goes from `0.0` to `1.0`: `1 - edits / longer length`.
  Swapping two neighbouring letters ("soa" for "sao") counts as one edit.
- **`FuzzyIndex`** holds name → value. `exact()` finds the normalized name;
  `search(query, cutoff=0.8, limit=5)` accepts typos down to `cutoff`.

!!! info "Why it is fast"
    The index stores the trigrams (three-letter slices) of each name. A query
    only computes the edit distance against the names that share enough
    trigrams with it. The filter **loses nothing**: a name `d` edits away
    shares at least `trigrams - 4d` with the query, and the index only drops
    below that. When that bound reaches zero (a short query with a low
    `cutoff`), the index compares against every name of a close length. A
    test compares the index with comparing against every name, over 300
    random queries for each `cutoff` from `0.5` to `0.9`, and the two
    agree.

## Find the city in a text

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

Output:

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

What each case shows:

- **A state right after confirms.** `"- sp"`, `"/SP"`, `", São Paulo"` or
  `"SP"` right after the name keep only that state's city and set
  `uf_confirmed=True` (score `1.0`). A state the name does not exist in
  (`"Campinas/RJ"`) drops the city.
- **The state is not a city.** In `"Campinas, São Paulo"`, "São Paulo" is
  Campinas' state and does not show up as the capital.
- **Nicknames** count: `bh`, `sampa`, `floripa`, `poa`, `bsb`, `jampa`,
  `sjc`, `rio` (`BR_CITY_NICKNAMES`). The Federal District's administrative
  regions (Ceilândia, Gama, ...) point at Brasília, and older spellings
  ("Açu" for "Assú") match too. A nickname is a one-word name like any other: it
  needs a sign of being a place (`"vou pra bh"`, `"Rio"`, `"sampa, sp"`, or
  the whole text being just `"bh"`).
- **An everyday word is not a city.** "Feliz", "Natal" and "Alegre" are
  municipalities. Lowercase, among other words, with no "em/de/moro" before
  and no state after, the word keeps half its score and falls below the
  default `min_score` (`0.6`). `"moro em Feliz/RS"` finds Feliz, and so does a
  text that is **only** the name (`"feliz"`, a form's city field).
- **Homonyms without a state tie.** `"bom jesus"` returns every Bom Jesus
  with the same score; you decide, with other information (the CEP, the area
  code, the next question to the user).

`matched_text`, `start` and `end` point at the text **as the user wrote
it**, so you can highlight or replace it in the original.

!!! tip "Typos inside the sentence"
    Every run of up to four words, at least five letters long and with no
    exact match, goes through the fuzzy lookup with `cutoff=0.85`.
    `"trabalho em sao paolo"` finds São Paulo at `0.80`: the name similarity
    (`0.89`) times `0.9`, since no state confirmed it.

## By name and by code

```python
from tempest_fastapi_sdk.geo import municipality_by_ibge_code, search_municipalities

for match in search_municipalities("sao paolo", limit=2):
    print(match.municipality.name, match.municipality.uf.value, round(match.score, 2))

city = municipality_by_ibge_code(3550308)
assert city is not None
print(city.name, city.coordinate.latitude, city.coordinate.longitude, city.capital)
```

Output:

```text
São Paulo SP 0.89
São Paulo -23.5329 -46.6395 True
```

`search_municipalities(name, uf=...)` fits a form's "city" field: with `uf`,
that state only. `br_municipalities()` returns the whole list, ordered by
code.

## By meaning: AI search

`FuzzyIndex` finds the name **spelled** alike; it does not find the name that
**means** the same. "tênis de corrida" shares no letters with "calçado
esportivo". That is what an embedding is for, and `genai.rag` brings two
indexes over any SDK embedder (`Embedder`, `OllamaEmbedder`,
`OpenAICompatEmbedder`, `OnnxEmbedder`):

- **`SemanticNameIndex`**: ranks the names by cosine similarity to the query.
  The names are embedded once, in a batch, on the first search.
- **`HybridNameIndex`**: runs both sides, spelling (word by word, typos
  included) and meaning, and fuses the rankings with
  `reciprocal_rank_fusion`. Each result carries `lexical_score` and
  `semantic_score`, so you can see why it came back.

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

Output, with `nomic-embed-text` on a local Ollama:

```text
'camizeta basica' -> Camiseta básica de algodão (spelling 0.94, meaning 0.84)
'tênis de corrida' -> Calçado esportivo para corrida (spelling 0.50, meaning 0.64)
'headphone sem fio' -> Fone de ouvido bluetooth (spelling -, meaning 0.57)
```

- `"camizeta basica"` came through **both** sides: spelling matches both
  misspelled words, and so does meaning.
- `"tênis de corrida"` came through **meaning**. The word "corrida" matches,
  but alone it covers only half the query.
- `"headphone sem fio"` came **only** through meaning: no word matches.

!!! info "Short words do not count"
    The spelling side ignores words shorter than three letters
    (`MIN_LEXICAL_WORD_LENGTH`). Without that, the "de" in "tênis de corrida"
    matched every name holding a "de".

!!! tip "When to use which"
    A list of proper names (cities, people, brands): `FuzzyIndex`, no model.
    A catalogue where users describe what they want in other words:
    `HybridNameIndex`, which keeps spelling as a signal of its own instead
    of relying on the model having seen the misspelling.

## In a conversation: agent tool

An agent talking to a user needs to find the city without you writing the
step. `municipality_search_tool()` gives it the municipality lookup (sentence
or name, optional `uf`), and `name_search_tool(index)` exposes any of the
three indexes.

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

Output of one of the runs with `qwen2.5:3b` on a local Ollama:

```text
['find_brazilian_city']
A cidade é Florianópolis, que está localizada no estado de Santa Catarina (UF SC). Seu código IBGE é 4205407 e as coordenadas geográficas são lat -27.5945, lon -48.5477.
```

Over 20 runs in a row (N=20) the model called the tool in every one and
answered Florianópolis/SC with code 4205407 in every one. In 3 of them it
passed `uf="SC"` along with the sentence: the state filters what the
sentence found, instead of replacing the sentence search with a lookup of
the whole text as a name. The answer's wording changes from run to run; what
the tool returns does not.

## What it costs

Measured on x86_64 with Python 3.12.7:

| What | Median |
| --- | --- |
| First call in the process (builds the index of 5571 names) | 45.9 ms (N=5) |
| `"vou pra floripa semana que vem"` | 0.79 ms (N=200) |
| `"moro em sao jose dos campos - sp"` | 3.02 ms (N=200) |
| A sentence with three cities, one misspelled | 3.27 ms (N=200) |

The index is built once per process and stays in memory.

!!! note "Where the data comes from"
    Name and state come from the IBGE localities API (public data);
    coordinate, capital and the older spellings come from
    `kelvins/municipios-brasileiros` (MIT, with the license notice in the
    generated module). Both sources list the same 5571 codes. The
    `list_states()` table ([BR helpers](br-helpers.md) recipe) counts 5606
    because it includes the Federal District's 35 administrative regions;
    here they are nicknames of Brasília. To refresh:
    `uv run python scripts/regen_br_municipalities.py`. A test pins the data
    hash, so a regeneration shows up in the PR diff.

## Recap

- **`tempest_fastapi_sdk.text`**: `normalize_text`, `similarity` and
  `FuzzyIndex` to match what the user types against any list of names, with
  no dependency.
- **`find_municipalities_in_text`**: finds the city in a sentence, with the
  state confirming and breaking ties, nicknames, and everyday words filtered.
- **`search_municipalities` / `municipality_by_ibge_code`**: lookup by name
  (typos included) and by code, with coordinate and capital.
- Homonyms without a state come back tied. The decision is yours.
- **`SemanticNameIndex` / `HybridNameIndex`**: search by meaning, and by
  meaning plus spelling, over any SDK embedder.
- **`municipality_search_tool` / `name_search_tool`**: the same searches as
  an agent tool.
