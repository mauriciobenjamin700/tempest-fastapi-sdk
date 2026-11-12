# Fuzzy matching and cities in text

The user writes "moro em sao jose dos campos - sp", "vou pra floripa" or
"sao paolo". You need to know **which** city it is, with its IBGE code and
coordinate, without an external service and without failing on a typo.

To match what the user typed against a **known list of names** (cities,
products, categories), lexical matching beats an embedding: no model to
download, no GPU, and a typo costs one edit. Semantic search over free-form
passages stays in [`genai`](genai.md) (`Embedder`, `Retriever`,
`HybridRetriever`).

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
    below that. A test compares, over 300 random queries, the index's result
    with comparing against every name, and the two agree.

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
  ("Açu" for "Assú") match too.
- **An everyday word is not a city.** "Feliz", "Natal" and "Alegre" are
  municipalities. Alone, lowercase, with no "em/de/moro" before and no state
  after, the word keeps half its score and falls below the default
  `min_score` (`0.6`). `"moro em Feliz/RS"` finds Feliz.
- **Homonyms without a state tie.** `"bom jesus"` returns every Bom Jesus
  with the same score; you decide, with other information (the CEP, the area
  code, the next question to the user).

`matched_text`, `start` and `end` point at the text **as the user wrote
it**, so you can highlight or replace it in the original.

!!! tip "Typos inside the sentence"
    Every run of up to four words with no exact match goes through the fuzzy
    lookup with `cutoff=0.85`. `"trabalho em sao paolo"` finds São Paulo with
    a lower score (`0.80`) than a correctly spelled name.

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
    generated module). Both sources list the same 5571 codes. To refresh:
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
