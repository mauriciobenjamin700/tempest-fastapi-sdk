# CLAUDE.md — tests/

Regras da suíte. O `CLAUDE.md` da raiz vale também; aqui fica o que só
importa quando você está escrevendo ou consertando teste.

## Ferramentas e fixtures

- `pytest` + `pytest-asyncio`. Banco de teste é SQLite in-memory; serviço
  externo (Redis, RabbitMQ, HTTP) é mockado em teste unitário.
- Teste de logging **passa `file_output=False`**. O default escreve em disco
  desde a v0.22.0 e deixa uma pasta `logs/` no cwd de quem rodou.
- Espelhe a árvore do pacote: `tests/<subpacote>/test_<modulo>.py`. Guard
  vive na raiz de `tests/` com o nome `test_<regra>_guard.py`.

## Os 10 guards são a autoridade das regras do repo

`test_docs_api_guard`, `test_docs_signature_guard`,
`test_docs_organization`, `test_docs_examples_compile`,
`test_docs_examples_names`, `test_reference_coverage`, `test_kwargs_guard`,
`test_reexport_guard`, `test_vacuous_guard`, `test_alias_guard` — todos dentro
do `make check`.

Ao adicionar guard novo:

1. **Ele precisa provar que dispara** na forma que de fato shippou. Guard que
   não pode falhar é guard em que ninguém deveria confiar. O padrão da casa é
   um teste que alimenta o guard com o código exato do defeito histórico e
   assere a falha.
2. **Escopo estreito, medido.** A primeira versão do `vacuous_guard` policiava
   as palavras "determinístico"/"idempotente", sinalizou 22 testes, uns 20
   corretos — e um deles afirmava o **oposto**. Reduzir o escopo foi a
   correção.
3. Marcadores de escape existem e são intencionais: `# docs-guard: skip`,
   `# kwargs-guard: skip`. Usar só com docstring dizendo por quê.

Racional de cada guard: [`../LESSONS.md`](../LESSONS.md).

## Teste que afirma travessia precisa atravessar

`test_vacuous_guard.py` falha quando o nome ou a docstring de um teste
**afirma** ter cruzado processo, réplica ou restart ("across processes",
"survives a restart") e o corpo não sai do lugar. Duas chamadas no mesmo
processo não medem nada sobre o que sobrevive a ele — foi assim que a
afirmação de determinismo entre processos da v0.218.0 passou com teste verde.

Afirmação sobre container, imagem slim ou pacote de sistema é testada
**construindo e rodando** o ambiente, não lendo o Dockerfile.

## Modo de falha é reproduzido, não deduzido

Ao testar erro, assere o que o usuário vê: status code real, mensagem real. O
FastAPI **não** converte um `ValidationError` levantado dentro do corpo da
rota em 422 — isso saiu como 500 em produção porque foi deduzido em vez de
medido.

## Fake não substitui o artefato real

Suíte de fake esconde gap de design: dois defeitos do caminho de modelo só
apareceram rodando peso de verdade, e quatro do caminho OO de fila só com
broker real. Para superfície nova que fala com mundo externo, rode uma vez
contra o real (`make test-model`, broker local) antes de confiar na suíte.
