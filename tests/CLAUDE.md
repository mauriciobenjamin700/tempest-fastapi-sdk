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

## Os guards são a autoridade das regras do repo

Todos rodam dentro do `make check`.

| Guard | Cobre | Ponto cego |
| --- | --- | --- |
| `test_docs_api_guard` | bloco `python` de doc parseia; nome de `__all__` resolve | prosa (roadmap/covers driftando) |
| `test_docs_signature_guard` | exemplo casa com assinatura real; import resolve; versão do snippet ≤ `pyproject.toml` | símbolo usado sem import; prosa |
| `test_docs_organization` | espelho `.en.md`, dois navs, ordem alfabética, índice de receitas | — |
| `test_docs_examples_compile` / `test_docs_examples_names` | exemplos completos compilam e usam nomes reais | — |
| `test_reference_coverage` | símbolo público tem stub em `docs/reference.md` | — |
| `test_kwargs_guard` | função lê chave do **próprio** `**kwargs` | splat em callable que absorve a chave |
| `test_reexport_guard` | `from x import Y as Y` + `__all__` em `__init__.py` | — |
| `test_vacuous_guard` | teste afirma cruzar processo/réplica e não cruza | — |
| `test_alias_guard` | `Field(alias=...)` voltando | — |
| `test_agent_docs_guard` | roster desta tabela bate com o disco; link e caminho citado em arquivo de agente existem | conteúdo da prosa |
| `test_version_agreement` | `pyproject.toml` e `__version__` concordam | — |
| `test_wheel_payload` | payload não-`.py` da wheel é exatamente a allowlist | — |

Marcadores de escape: `# docs-guard: skip` (fragmento não-parseável de
propósito), `# kwargs-guard: skip` (caso que genuinamente não é isso, com
docstring dizendo por quê).

## Ao adicionar guard novo

1. **Ele precisa provar que dispara** na forma que de fato shippou. Guard que
   não pode falhar é guard em que ninguém deveria confiar. O padrão da casa é
   um teste que alimenta o guard com o código exato do defeito histórico e
   assere a falha.
2. **Escopo estreito, medido.** Guard largo demais sinaliza teste correto e
   perde credibilidade — foi o que aconteceu na primeira versão do
   `test_vacuous_guard`:
   [`LESSONS.md`](../LESSONS.md#prosa-deduzida-shippa-errada-v02180).
3. **Entre nesta tabela.** `test_agent_docs_guard` falha se um
   `test_*_guard.py` novo não aparecer aqui, ou se uma linha daqui apontar um
   arquivo que não existe.

## Escrevendo o teste

- **Teste que afirma travessia precisa atravessar.** `test_vacuous_guard`
  falha quando o nome ou a docstring afirma ter cruzado processo, réplica ou
  restart ("across processes", "survives a restart") e o corpo não sai do
  lugar. Duas chamadas no mesmo processo não medem nada sobre o que sobrevive
  a ele. Afirmação sobre container ou pacote de sistema é testada
  construindo e rodando o ambiente.
- **Assere o modo de falha real**, não o que a biblioteca deveria fazer:
  status code e mensagem que o usuário vê. O FastAPI não converte
  `ValidationError` levantado dentro do corpo da rota
  ([`LESSONS.md`](../LESSONS.md#prosa-deduzida-shippa-errada-v02180)).
- **Fake não substitui o artefato real.** Suíte de fake esconde gap de
  design: dois defeitos do caminho de modelo só apareceram rodando peso de
  verdade, e quatro do caminho OO de fila só com broker real. Para superfície
  nova que fala com mundo externo, rode uma vez contra o real
  (`make test-model`, broker local) antes de confiar na suíte.
