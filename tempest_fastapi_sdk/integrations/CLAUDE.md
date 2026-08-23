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
`scripts/regen_<provedor>.py` (`make openpix-regen`, `make mercadopago-regen`),
e é **commitado**. Um teste de drift
(`tests/integrations/payment/<provedor>/test_generated_drift.py`) falha se o
disco divergir do que o script produz — editar o arquivo gerado é como código
gerado versionado apodrece.

Ao mexer no gerador:

- **O codegen espelha o formatter.** O gerador mora fora deste diretório
  (`tempest_fastapi_sdk/openapi/generate.py` + `scripts/regen_openpix.py`), mas
  a regra vale ao mexer nele: teste com `run_format=False`, porque o `ruff`
  normaliza aspas por escape, nunca quebra string longa e junta literal
  solto, então gerador validado só com formatação ligada esconde o que ele de
  fato emite.
- **O formatter também desfaz quebra — e sem reconferir a régua.** Docstring
  cujo conteúdo cabe em **uma** linha tem o `"""` de fecho puxado de volta
  para ela, mesmo que o resultado passe de 88 colunas
  ([medição](../../LESSONS.md#o-formatter-desfaz-quebra-de-docstring-v02490)).
  Emissor que mira exatamente a régua entrega `E501`; o alvo da última linha
  de conteúdo é `MAX_LINE - 3`, ou duas linhas de conteúdo.
- **A spec fica fora da wheel.** `vendor/` é insumo de build, não payload de
  runtime.
- Regenerou? Rode o drift test **e** o `make check` — o gerado passa pelos
  mesmos guards do resto.

## `__all__` é obrigatório, e wildcard não é re-export

Superfície gerada é resolvida de forma lazy (PEP 562 `__getattr__`) e exposta
ao type-checker com `from ...schemas import *` sob `TYPE_CHECKING` — **isso não
exporta nada** para o consumidor
([medição](../../LESSONS.md#wildcard-não-é-re-export-v02320)).

O que fazer aqui: `__all__` é a única forma disponível, e é suficiente. Ele é
**gerado** por `scripts/regen_openpix.py` e pinado pelo drift test — símbolo
novo entra pelo gerador, nunca editando `__init__.py` na mão.

Fora da metade gerada, vale a regra da raiz: `from x import Y as Y` **e**
`__all__`, as duas formas.

## `Field(alias=...)` é defeito

Escreva o nome do fio duas vezes: `validation_alias` para ler,
`serialization_alias` para escrever. `tests/test_alias_guard.py` falha se
`alias=` voltar; a medição que motivou o guard está em
[`LESSONS.md`](../../LESSONS.md#fieldalias-quebra-o-consumidor-não-o-runtime-v02340).

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
