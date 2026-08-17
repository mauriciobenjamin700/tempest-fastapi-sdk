---
name: release
description: Corta uma release do tempest-fastapi-sdk — bump nos dois arquivos de versão, entrada no CHANGELOG, gate completo, commit, tag e push. Dispara quando o usuário pedir "cortar release", "publicar versão", "bump para X.Y.Z", "tagueia", "sobe pro PyPI", "/release". Sabe distinguir mudança docs-only (que não bumpa, não tagueia) do fluxo normal.
allowed-tools:
  - Bash
  - Read
  - Edit
  - Grep
  - Glob
---

# release

Fluxo de release deste repo. Prosa ao usuário em PT-BR.

## 0. Decida primeiro: é docs-only?

```bash
git diff --stat origin/main..HEAD -- tempest_fastapi_sdk/
```

Vazio **e** o diff toca só `docs/`, `README.md`, `CLAUDE.md`, `LESSONS.md`,
`SHIPPED.md`? Então é **docs-only**: sem bump, sem CHANGELOG, sem tag.

```bash
uv run --group docs mkdocs build --strict
uv run pytest tests/test_docs_api_guard.py tests/test_docs_organization.py -q
git commit -m "docs: <assunto>" && git push origin main
```

`docs.yml` redeploya o Pages no push da `main`. Pare aqui.

Docstring que muda assinatura ou comportamento **não** é docs-only — segue o
fluxo abaixo.

## 1. Bump

O script cuida dos dois arquivos que nunca podem discordar
(`pyproject.toml` e `tempest_fastapi_sdk/__init__.py`):

```bash
python3 .claude/skills/release/scripts/bump_version.py <X.Y.Z>
```

Comportamento medido (7 casos, contra cópias dos arquivos reais):

| Situação | Saída |
| --- | --- |
| bump válido, CHANGELOG já tem a entrada | reescreve os dois, exit `0` |
| bump válido, CHANGELOG sem a entrada | reescreve os dois, avisa, exit `2` |
| versão fora de `X.Y.Z` | recusa, exit `1` |
| já está naquela versão | recusa, exit `1` |
| os dois arquivos discordam antes do bump | recusa e nomeia os dois valores, exit `1` |

Aceita `--root <path>` (útil em worktree) e `--dry-run`. **Exit `2` não é
sucesso**: significa que falta a entrada do changelog.

## 2. CHANGELOG

Entrada `## [X.Y.Z] — YYYY-MM-DD` no formato Keep a Changelog, cobrindo toda
mudança pública. Data real — o repo não tem acesso a `Date.now()` em script,
use `date +%F` no shell.

## 3. Docs na mesma mudança

Superfície pública nova exige: receita em `docs/<page>.md` **e**
`docs/<page>.en.md`, entrada nos **dois** `nav:` do `mkdocs.yml`, linha na
tabela de `docs/recipes/index.md` (+ `.en.md`), stub em `docs/reference.md`,
snippet de install do README com a versão nova. As regras completas estão no
`CLAUDE.md` da raiz; o guard é `tests/test_docs_organization.py`.

## 4. Gate — os três, nesta ordem

```bash
UV_PYTHON=3.11 make check
UV_PYTHON=3.11 uv run --group docs mkdocs build --strict
UV_PYTHON=3.11 make smoke
```

`make check` = lint + fmt-check + mypy strict + suíte inteira (inclui os 10
guards). `make smoke` instala a wheel recém-buildada numa venv limpa e
importa a superfície de topo — é o único passo que pega defeito de
empacotamento.

Nenhum dos três pode estar amarelo. `mkdocs build --strict` reporta âncora
quebrada só como `INFO`: ao adicionar link cross-page, confira a âncora no
HTML buildado.

## 5. Auditoria de prosa

Os guards não leem prosa. Antes de taggear, dispare o agente
`docs-prose-auditor` (`.claude/agents/`) para conferir afirmação
não-medida, roadmap driftado e símbolo inexistente. Corrija o que ele achar
**antes** da tag — depois dela, a correção custa uma release nova.

## 6. Commit, tag, push

```bash
git add -A && git commit -m "feat: vX.Y.Z — <assunto>"
git tag vX.Y.Z
git push origin main && git push origin vX.Y.Z
```

CI na tag roda `release-pypi.yml` (trusted publishing, sem token); `docs.yml`
redeploya o Pages depois. **Não empurre tag com docs vermelhas** — tag
publicada é irreversível no PyPI e vira release re-cortada.

## 7. Verificação pós-publicação

O JSON da PyPI mente por cache. Confira pelo índice simples e force refresh:

```bash
uv pip install --refresh "tempest-fastapi-sdk==X.Y.Z" --dry-run
gh run list --limit 5
```

## Regras de segurança deste fluxo

- Confirme com o usuário antes de `git push` da tag — é o passo irreversível.
- Nunca `git add -A` sem antes olhar `git status --short`: `dist/`, `site/` e
  `logs/` são ignorados, mas artefato novo pode não estar.
- Em worktree paralelo, bump conflita: se outro branch já bumpou, renumere o
  seu em vez de forçar (ver memória "Parallel release conflicts").
