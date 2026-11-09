---
name: release
description: Corta uma release do tempest-fastapi-sdk — CHANGELOG, docs, gate completo, bump, tag, push, publicação no PyPI pela máquina local (make publish) e deploy do site (make docs-deploy). Dispara quando o usuário pedir "cortar release", "publicar versão", "bump para X.Y.Z", "tagueia", "sobe pro PyPI", "/release". Sabe distinguir mudança docs-only (que não bumpa, não tagueia) do fluxo normal.
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
humano e agente chamam. **Nada passa pelo GitHub Actions** (desde
2026-11-09, trava de cobrança — `LESSONS.md`): não há workflow, e não se cria,
reativa nem dispara um (`gh workflow run`, `gh run`). Esta skill decide
*quando* e *em que ordem* chamá-los, e o que olhar entre um e outro.

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
make docs-deploy
```

`make docs-deploy` builda o site (strict, dois locales) e empurra para a
`gh-pages`. Pare aqui.

Mudança só de tooling (Makefile, `scripts/`, `tests/`, `.github/`) sem delta
em `tempest_fastapi_sdk/` também não bumpa: commit `chore:`/`tests:`, sem
release.

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
`make check` (lint + fmt-check + mypy strict + a suíte em paralelo, com os
guards — a CI não testa, este é o gate) → `make audit` →
`make docs-build` (mkdocs `--strict`) → `make smoke` (instala a wheel numa venv
limpa e importa a superfície de topo, único passo que pega defeito de
empacotamento) → commita `feat: vX.Y.Z — <assunto>` → cria a tag local.

Sem `SUBJECT` o commit sai como `chore: release vX.Y.Z`, que **não** é a
convenção deste repo — passe o assunto.

O push fica manual de propósito.

Release que mexe em dependência ou em código sensível à versão do Python roda
também `make test-matrix`.

## 5. Push, com confirmação

```bash
git push origin main && git push origin vX.Y.Z
```

**Confirme com o usuário antes.** O push não publica nada sozinho — mas a tag
no remoto é a promessa de uma versão, e o `make publish` exige que ela esteja
lá.

## 6. Publicar, da máquina local

```bash
make publish VERSION=X.Y.Z DRY_RUN=1   # tudo menos upload e GitHub Release
make publish VERSION=X.Y.Z             # precisa de UV_PUBLISH_TOKEN no ambiente
```

Rode num checkout limpo **da tag** (o `HEAD` do `make release` já é ela). O
`scripts/publish.py`, em ordem: recusa sem `UV_PUBLISH_TOKEN` (e sem `gh`
logado) → `HEAD` = tag, árvore limpa, tag no `origin` e commit na
`origin/main` → tag × `pyproject` × `__version__` × `uv.lock` → seção do
CHANGELOG → `uv lock --check` → `make audit` → `uv build` em `dist/` limpo →
`twine check --strict` → wheel com os `.py` e o `env.py.template` → smoke da
instalação base numa venv 3.12 limpa → `uv publish` → `gh release create`
com a seção do CHANGELOG (API, não Actions). A primeira recusa para tudo.

**Confirme com o usuário antes do run sem `DRY_RUN`** — versão publicada no
PyPI não volta. O `uv publish` usa `--check-url`, então rodar de novo depois
de uma falha no meio só sobe o que faltou.

## 7. Site

```bash
make docs-deploy
```

Num checkout limpo da `origin/main`: builda uma vez com
`mkdocs build --strict`, confere `index.html` e `en/index.html` e empurra
aquele diretório para a `gh-pages` (`ghp-import`). `DRY_RUN=1` builda sem
empurrar.

## 8. Verificação pós-publicação

O JSON da PyPI mente por cache; use o índice simples sem cache.

```bash
curl -s https://pypi.org/simple/tempest-fastapi-sdk/ | grep -o "tempest_fastapi_sdk-$VER[^\"#]*"
uv pip install --no-cache --index-url https://pypi.org/simple "tempest-fastapi-sdk==$VER" --dry-run
gh release view "v$VER"
```

## Armadilha conhecida

Em worktree paralelo o bump conflita: se outro branch já bumpou para a mesma
versão, **renumere o seu** em vez de forçar. Worktree alheio é intocável, e
força bruta ali já custou um force-push no ref de um PR aberto.
