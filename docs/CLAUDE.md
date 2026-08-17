# CLAUDE.md — docs/

Regras da documentação. O `CLAUDE.md` da raiz vale também; aqui fica o que só
importa ao escrever página.

Este arquivo **não é página do site**: `mkdocs.yml` o exclui via
`exclude_docs`, e `tests/test_docs_organization.py` o ignora ao varrer páginas.
Por isso ele não tem espelho `.en.md` nem entrada no `nav:`.

## Ao adicionar (ou renomear) uma página

1. **Duas línguas.** `docs/<página>.md` (PT-BR, default) **e**
   `docs/<página>.en.md` (EN-US). Espelho faltando cai em fallback silencioso
   no site: a página aparece, em português, sem aviso.
2. **Dois navs.** A entrada vai no `nav:` de topo **e** no `nav:` do locale
   `en` (dentro do plugin `i18n` no `mkdocs.yml`). O `mkdocs-static-i18n`
   traduz rótulo mas **não reordena** nav compartilhado — é por isso que
   existem dois, e mexer em um exige mexer no outro.
3. **Na posição alfabética**, em cada língua, pelo rótulo visível (comparação
   case- e acento-insensível). Vale para a seção `Receitas`/`Recipes` e a
   subseção `Exemplos completos`/`Complete examples`.
4. **Índice da landing.** Receita nova entra na tabela de
   `docs/recipes/index.md` **e** `.en.md`, também em ordem alfabética.
5. **Referência.** Símbolo público novo ganha stub em `docs/reference.md` (a
   `reference.en.md` é página-ponteiro, não duplica).
6. **Build limpo.** `make docs-build` (mkdocs `--strict`) com zero warning.
   Âncora quebrada sai só como `INFO`, então ao adicionar link cross-page
   confira a âncora no HTML buildado.

## O que fica fora da ordem alfabética, de propósito

Abas de topo (`Início → Instalação → Arquitetura → Tutorial → …`), páginas de
`learning/`, a trilha de `getting-started/` (uv → versões do Python → primeiro
projeto → documentação oficial) e o tour na landing de receitas seguem **ordem
didática**. Ordenar essas seria a regressão.

Fora do nav, a mesma disciplina vale para listas que o leitor usa como índice:
tabela de módulos do README, tabela de extras da instalação (com `[all]` por
último, por ser catch-all) e os grupos temáticos de `docs/reference.md` (com
`## Superfície de topo` fixa no início). Dentro da referência, os blocos `###`
de módulo e suas entradas `:::` mantêm o agrupamento por submódulo — ali o
agrupamento é a informação.

## Estilo de escrita: padrão FastAPI (tiangolo)

Tutorial progressivo (um conceito por página curta), exemplo **completo e
executável** (com imports, sem `...`), explicação pedaço por pedaço, `Recap`
no fim, admonition do Material (`!!! tip`, `!!! warning`, `???` colapsável)
para camadas opcionais, voz em segunda pessoa. Página que é dump de API plano
ou usa fragmento não atende o padrão.

## O que os guards cobrem daqui

`test_docs_organization` (espelho, dois navs, ordem, landing),
`test_docs_api_guard` (todo bloco `python` parseia; nome de `__all__`
resolve), `test_docs_signature_guard` (exemplo casa com assinatura real,
import resolve, versão de snippet ≤ `pyproject.toml`),
`test_docs_examples_compile`/`_names`, `test_reference_coverage`.

**Prosa fica fora de todos eles.** Frase que promete parâmetro inexistente só
falha se um exemplo passar o argumento; roadmap dizendo "backlog" para algo já
entregue não falha nunca. Releia a prosa que escreveu, ou chame o agente
`docs-prose-auditor`.
