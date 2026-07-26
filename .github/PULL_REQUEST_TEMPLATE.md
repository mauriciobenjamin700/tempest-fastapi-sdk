Tem Script? | Novas Env Vars
-- | --
Não/Sim | Não/Sim

> :warning: **NOTA**
> - <o que precisa ficar saliente para o revisor: migrations, env vars, scripts, integrações externas>

Issue relacionada: #

> Este repositório trabalha com **issue primeiro**: a superfície pública é versionada e a doc PT+EN entra no mesmo commit, então o escopo é combinado na issue antes do código. PR sem issue aceita costuma voltar com um pedido pra abrir uma — veja [Contribuindo](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/contributing/).

## Problema

<por que esse PR existe; o estado anterior, riscos, bugs latentes>

## Solução

<o que foi feito, em sub-seções (### Componente A / ### Componente B) quando faz sentido>

## Screenshots

**Descrição do Screenshot**:
<descrição da captura, ou "Não se aplica — alterações de backend/tooling/etc.">

## Outras mudanças

<bullets pequenos / "Nenhuma">

## Notas sobre deploy

<passos de deploy, rebuilds necessários, validação local executada>

**Validação local executada**:
- <comandos rodados (testes, build, lint) com resultado>

**Novas Variáveis de Ambiente**:
- Nenhuma | <nome>: <descrição>

**Novos Scripts e/ou Tarefas de Background**:
- Nenhum | <descrição>

**Novas Dependências**:
- Nenhuma | <pacote@versão>: <motivo>

**Documentação** (obrigatório quando muda superfície pública):
- [ ] `README.md`
- [ ] `CHANGELOG.md`
- [ ] receita / página em `docs/<página>.md` **e** `docs/<página>.en.md`
- [ ] entrada nos **dois** navs (`nav:` de topo + `nav:` do locale `en`), na posição alfabética de cada língua
- [ ] receita nova listada na tabela de `docs/recipes/index.md` **e** `.en.md`
- [ ] stub em `docs/reference.md` para cada símbolo público novo
- [ ] `uv run --group docs mkdocs build --strict` verde
- [ ] `uv run pytest tests/test_docs_organization.py` verde
