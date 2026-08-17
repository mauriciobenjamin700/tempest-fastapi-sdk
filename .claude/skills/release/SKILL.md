---
name: release
description: Corta uma release do tempest-fastapi-sdk — CHANGELOG, docs, gate completo, bump, tag e push. Dispara quando o usuário pedir "cortar release", "publicar versão", "bump para X.Y.Z", "tagueia", "sobe pro PyPI", "/release". Sabe distinguir mudança docs-only (que não bumpa, não tagueia) do fluxo normal.
allowed-tools:
  - Bash
  - Read
  - Edit
  - Grep
  - Glob
---

# release

Fluxo de release deste repo. Prosa ao usuário em PT-BR.

Os passos executáveis são **targets do Makefile** — ele é a autoridade que
humano, CI e agente já chamam. Esta skill decide *quando* e *em que ordem*
chamá-los, e o que olhar entre um e outro.

## 0. Decida primeiro: é docs-only?

```bash
git diff --stat origin/main..HEAD -- tempest_fastapi_sdk/
```

Vazio **e** o diff toca só `docs/`, `README.md`, `CLAUDE.md`, `LESSONS.md` ou
`SHIPPED.md`? Então é **docs-only**: sem bump, sem CHANGELOG, sem tag.

```bash
make docs-build
uv run pytest tests/test_docs_api_guard.py tests/test_docs_organization.py -q
git commit -m "docs: <assunto>" && git push origin main
```

`docs.yml` redeploya o Pages no push da `main`. Pare aqui.

Docstring que muda assinatura ou comportamento **não** é docs-only — segue o
fluxo abaixo.

## 1. CHANGELOG antes de tudo

Entrada `## [X.Y.Z] — YYYY-MM-DD` (Keep a Changelog) cobrindo toda mudança
pública. Data real: `date +%F`. O `make release` **recusa** sem essa entrada,
então escrevê-la primeiro é o caminho curto, não uma formalidade.

## 2. Docs na mesma mudança

Superfície pública nova exige página nas duas línguas, entrada nos dois
`nav:`, linha na landing de receitas, stub na referência e snippet de install
com a versão nova. As regras vivem em [`docs/CLAUDE.md`](../../../docs/CLAUDE.md);
o guard é `tests/test_docs_organization.py`.

## 3. Auditoria de prosa

Os guards não leem prosa. Dispare o agente `docs-prose-auditor`
(`.claude/agents/`) para conferir afirmação não-medida, roadmap driftado e
símbolo inexistente. Corrija **antes** da tag — depois dela, a correção custa
uma release nova.

## 4. Bump + gate + tag, num comando

```bash
make release VERSION=X.Y.Z SUBJECT="<assunto>"
```

O target, em ordem: recusa árvore suja → recusa sem a entrada do CHANGELOG →
faz o bump em `pyproject.toml` e `tempest_fastapi_sdk/__init__.py` → roda
`make check` (lint + fmt-check + mypy strict + suíte, com os guards) →
`make docs-build` (mkdocs `--strict`) → `make smoke` (instala a wheel numa venv
limpa e importa a superfície de topo, único passo que pega defeito de
empacotamento) → commita `feat: vX.Y.Z — <assunto>` → cria a tag local.

Sem `SUBJECT` o commit sai como `chore: release vX.Y.Z`, que **não** é a
convenção deste repo — passe o assunto.

O push fica manual de propósito.

## 5. Push, com confirmação

```bash
git push origin main && git push origin vX.Y.Z
```

**Confirme com o usuário antes** — é o passo irreversível: a tag dispara
`release-pypi.yml` (trusted publishing, sem token) e versão publicada no PyPI
não volta. Depois `docs.yml` redeploya o Pages.

## 6. Verificação pós-publicação

O JSON da PyPI mente por cache; use o índice simples e force refresh.

```bash
uv pip install --refresh "tempest-fastapi-sdk==$VER" --dry-run
gh run list --limit 5
```

## Armadilha conhecida

Em worktree paralelo o bump conflita: se outro branch já bumpou para a mesma
versão, **renumere o seu** em vez de forçar. Worktree alheio é intocável, e
força bruta ali já custou um force-push no ref de um PR aberto.
