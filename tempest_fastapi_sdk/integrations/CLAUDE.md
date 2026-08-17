# CLAUDE.md — integrations/

Regras deste subpacote. O `CLAUDE.md` da raiz vale também; aqui fica o que só
importa quando você mexe em integração com API de terceiro.

## Forma do namespace

`integrations/<tipo>/<provedor>` — agrupado pelo **que o terceiro faz**
(`payment`), não pelo nome do vendor. Serviço que troca de provedor muda um
segmento de import. Nada aqui é importado por `import tempest_fastapi_sdk`;
cada integração é alcançada pelo caminho do provedor.

## Metade gerada: versionada, nunca editada à mão

O gerado vem de spec pinada em `vendor/<provedor>-openapi.yaml` via
`scripts/regen_<provedor>.py` (`make openpix-regen`), e é **commitado**. Um
teste de drift (`tests/integrations/payment/openpix/test_generated_drift.py`)
falha se o disco divergir do que o script produz — editar o arquivo gerado é
como código gerado versionado apodrece.

Ao mexer no gerador:

- **O codegen espelha o formatter.** Teste com `run_format=False`: o `ruff`
  normaliza aspas por escape, nunca quebra string longa e junta literal
  solto, então gerador validado só com formatação ligada esconde o que ele de
  fato emite.
- **A spec fica fora da wheel.** `vendor/` é insumo de build, não payload de
  runtime.
- Regenerou? Rode o drift test **e** o `make check` — o gerado passa pelos
  mesmos guards do resto.

## `__all__` é obrigatório, e wildcard não é re-export

O pacote OpenPix resolve 373 nomes gerados de forma lazy (PEP 562
`__getattr__`) e os expõe ao type-checker com `from ...schemas import *` sob
`TYPE_CHECKING`. **Isso não exporta nada.** Medido com basedpyright contra a
wheel 0.232.0: `from ...openpix import ChargePayload` no consumidor recebeu
*"ChargePayload" is not exported from module*. mypy aceitou, e foi por isso
que shippou.

Para superfície gerada, `__all__` é a única forma disponível de re-export — e
é suficiente. Ele é **gerado** por `scripts/regen_openpix.py` e pinado pelo
drift test; símbolo novo entra pelo gerador, não editando `__init__.py`.

Fora da metade gerada, vale a regra da raiz: `from x import Y as Y` **e**
`__all__`, as duas formas.

## `Field(alias=...)` é defeito

Runtime não distingue `alias` de `validation_alias`+`serialization_alias` com
`populate_by_name=True`; o pyright distingue — `alias` renomeia o parâmetro do
`__init__` sintetizado e passa a exigir `correlationID` em vez de
`correlation_id`. Escreva o nome do fio duas vezes. `tests/test_alias_guard.py`
falha se `alias=` voltar. Detalhe da medição:
[`../../LESSONS.md`](../../LESSONS.md).

## Armadilhas de API de terceiro já pagas

Levantadas na integração OpenPix, e o tipo de coisa a conferir em provedor
novo antes de confiar na spec:

- chave de assinatura de webhook em **RSA-1024** (não 2048);
- valor monetário em **centavo dentro de float** — daí existir `to_cents`;
- prefixo de rota irregular entre grupos de endpoint;
- **zero `operationId`** na spec, então o nome de método é derivado pelo
  gerador e é decisão nossa, não do provedor.

Cada uma dessas é uma coisa que a spec não diz e que só apareceu batendo na
API. Provedor novo: mede antes de documentar.
