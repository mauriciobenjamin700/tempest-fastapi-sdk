"""``tempest pr-prompt`` — build the prompt that makes an AI fill a PR template.

Writing a pull-request description by hand is the step everyone skips
when the branch is finally green, and the result is a PR body that says
"fix stuff" over 40 changed files. Any assistant can write a good one —
what it lacks is the two things that live in the repository: the
**template** the team agreed on, and the **diff** the branch actually
produced.

This module assembles both into a single prompt:

1. the pull-request template — the repository's own
   (``.github/pull_request_template.md`` and friends) when it has one,
   otherwise the bundled PT-BR / EN-US default;
2. the rules that stop the model from returning the template with the
   placeholders still in it (no ``Sim/Não`` left undecided, no
   ``_italic hint_``, no section dropped);
3. the branch context — commit subjects, the ``--name-status`` file
   list, and a bounded excerpt of each file's patch.

The result goes to stdout, so it pipes straight into whichever assistant
the user runs::

    tempest pr-prompt | claude -p
    tempest pr-prompt --out pr_prompt.txt

Only the excerpts are bounded. The commit subjects and the changed-file
list always go in whole, so the model always knows *what* changed and
only *how* it changed is sampled — and everything the sampling drops is
reported: a file left without a patch by ``--max-files`` and a patch cut
by ``--max-chars`` are both stated inside the prompt, so a partial
context reads as partial rather than as the whole change. Passing
``None`` for either bound (``--full`` on the command line) lifts it.

Diffs use the three-dot range ``base...head`` — the merge-base diff,
which is what the forge shows on the pull request — while commits use
``base..head``. Reading the two-dot diff instead would attribute every
commit that landed on ``base`` since the branch started to this PR.
"""

from __future__ import annotations

import importlib.resources
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tempest_fastapi_sdk.core.enums import BaseStrEnum

DEFAULT_BASE: str = "main"
"""Branch a pull request is opened against when ``--base`` is omitted."""

DEFAULT_MAX_FILES: int = 10
"""How many files contribute a patch excerpt before the rest is summarized."""

DEFAULT_MAX_CHARS: int = 1500
"""How many characters of each file's patch are kept in the prompt."""

TEMPLATE_CANDIDATES: tuple[str, ...] = (
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/PULL_REQUEST_TEMPLATE/pull_request_template.md",
    ".gitlab/merge_request_templates/default.md",
    "docs/pull_request_template.md",
    ".pull_request_template.md",
    "pull_request_template.md",
)
"""Repository template locations, in the order they are looked up.

The forges accept several spellings and a project only ever has one, so
the first hit wins. A repository that keeps its template somewhere else
passes ``--template``.
"""


class PromptLanguage(BaseStrEnum):
    """Language of the bundled template and of the prompt's instructions.

    Only the *bundled* template is translated: a repository template is
    used verbatim in whatever language it was written in, since it is
    that repository's contract.
    """

    PT_BR = "pt"
    EN_US = "en"


class GitError(RuntimeError):
    """A ``git`` invocation failed, or the repository lacks what was asked.

    Carries the command's own ``stderr`` so the caller can print the
    reason git gave instead of a generic failure.
    """


@dataclass(frozen=True, slots=True)
class DiffExcerpt:
    """A single file's patch, possibly cut to the character budget.

    Attributes:
        path (str): Repository-relative path of the file.
        patch (str): The unified diff, truncated to ``--max-chars``.
        truncated (bool): Whether the patch was cut. Rendered into the
            prompt so the model does not read a partial hunk as the
            complete change.
    """

    path: str
    patch: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class ResolvedTemplate:
    """The pull-request template the prompt will carry.

    Attributes:
        text (str): The template's markdown.
        source (str): Where it came from — a repository-relative path, or
            the bundled file's name. Reported on stderr so the user knows
            which template the model was handed.
        bundled (bool): True when the SDK's own template was used because
            the repository has none.
    """

    text: str
    source: str
    bundled: bool


@dataclass(frozen=True, slots=True)
class PullRequestContext:
    """Everything read out of the repository for one branch comparison.

    Attributes:
        repository (str): The repository directory's name.
        base (str): The resolved base ref (may be ``origin/main`` when
            the local ``main`` does not exist).
        head (str): The branch being described, or a short sha when HEAD
            is detached.
        commits (list[str]): Commit subjects, newest first.
        files (list[str]): ``git diff --name-status`` lines.
        excerpts (list[DiffExcerpt]): Per-file patches, bounded.
        omitted_files (int): Changed files with no excerpt because of
            ``--max-files``.
    """

    repository: str
    base: str
    head: str
    commits: list[str]
    files: list[str]
    excerpts: list[DiffExcerpt]
    omitted_files: int


_PROMPT_HEADERS: dict[PromptLanguage, dict[str, str]] = {
    PromptLanguage.PT_BR: {
        "role": (
            "Você é um engenheiro experiente escrevendo a descrição de um Pull Request."
        ),
        "rules": "REGRAS OBRIGATÓRIAS — LEIA COM ATENÇÃO",
        "template": "TEMPLATE A SER PREENCHIDO (NÃO ALTERAR)",
        "context": "CONTEXTO DO PR (USE PARA PREENCHER)",
        "repository": "Repositório",
        "branch": "Branch",
        "commits": "Commits",
        "files": "Arquivos alterados",
        "patches": "Trechos do diff",
        "no_commits": "(nenhum commit entre as duas refs)",
        "no_files": "(nenhum arquivo alterado)",
        "no_patches": "(nenhum trecho de diff incluído)",
        "truncated": "trecho cortado — o patch deste arquivo continua",
        "omitted": (
            "Mais {count} arquivo(s) alterado(s) sem trecho de diff aqui: "
            "leia a lista acima e trate o diff como parcial."
        ),
        "closing": "Qualquer violação das regras acima torna a resposta inválida.",
    },
    PromptLanguage.EN_US: {
        "role": (
            "You are an experienced engineer writing the description of a Pull Request."
        ),
        "rules": "MANDATORY RULES — READ CAREFULLY",
        "template": "TEMPLATE TO FILL IN (DO NOT ALTER)",
        "context": "PR CONTEXT (USE IT TO FILL THE TEMPLATE)",
        "repository": "Repository",
        "branch": "Branch",
        "commits": "Commits",
        "files": "Changed files",
        "patches": "Diff excerpts",
        "no_commits": "(no commits between the two refs)",
        "no_files": "(no changed files)",
        "no_patches": "(no diff excerpt included)",
        "truncated": "excerpt cut — this file's patch continues",
        "omitted": (
            "{count} more changed file(s) carry no excerpt here: read the "
            "list above and treat the diff as partial."
        ),
        "closing": "Breaking any rule above makes the answer invalid.",
    },
}

_PROMPT_RULES: dict[PromptLanguage, tuple[str, ...]] = {
    PromptLanguage.PT_BR: (
        "TODOS os campos do template DEVEM ser preenchidos.",
        'NÃO deixe "Sim/Não". Escolha explicitamente Sim ou Não.',
        "NÃO deixe placeholders: nem texto em itálico de instrução, nem "
        "[insira o screenshot aqui], nem colchetes vazios.",
        'Se algo NÃO se aplicar, escreva explicitamente "Nenhuma", '
        '"Nenhum" ou "Não se aplica" — nunca apague a seção.',
        "TODA seção do template aparece na resposta, na mesma ordem e com "
        "o mesmo título.",
        "Descreva o que o diff mostra. NÃO invente migrations, variáveis "
        "de ambiente, scripts ou dependências que não aparecem no "
        "contexto.",
        "NÃO adicione comentários, saudações ou explicações fora do template.",
        "A resposta DEVE conter APENAS o markdown válido do template preenchido.",
    ),
    PromptLanguage.EN_US: (
        "EVERY field in the template MUST be filled in.",
        'Do NOT leave "Yes/No". Pick Yes or No explicitly.',
        "Do NOT leave placeholders: no italic instruction text, no "
        "[insert screenshot here], no empty brackets.",
        'If something does NOT apply, write "None" or "Not applicable" '
        "explicitly — never drop the section.",
        "EVERY section of the template appears in the answer, in the same "
        "order and under the same heading.",
        "Describe what the diff shows. Do NOT invent migrations, "
        "environment variables, scripts or dependencies that are absent "
        "from the context.",
        "Do NOT add comments, greetings or explanations outside the template.",
        "The answer MUST contain ONLY the valid markdown of the filled template.",
    ),
}


def _run_git(args: Sequence[str], *, cwd: Path) -> str:
    """Run a ``git`` command and return its stdout.

    Args:
        args (Sequence[str]): Arguments after the ``git`` executable.
        cwd (Path): Directory the command runs in.

    Returns:
        str: The command's stdout, stripped of the trailing newline.

    Raises:
        GitError: When git is missing from PATH or exits non-zero. The
            message carries git's own stderr, which names the actual
            problem (unknown ref, not a repository, ...).
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git was not found on PATH.") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.rstrip("\n")


def repository_root(cwd: Path) -> Path:
    """Return the root of the git repository containing ``cwd``.

    Args:
        cwd (Path): Any directory inside the repository.

    Returns:
        Path: The absolute repository root.

    Raises:
        GitError: When ``cwd`` is not inside a git repository.
    """
    return Path(_run_git(["rev-parse", "--show-toplevel"], cwd=cwd))


def repository_name(cwd: Path) -> str:
    """Return the repository's name as the forge knows it.

    The directory name is the obvious answer and the wrong one inside a
    ``git worktree``, where each checkout lives in its own directory
    named after the task. The ``origin`` remote carries the real name, so
    it is read first and the directory name is only the fallback for a
    repository with no remote.

    Args:
        cwd (Path): A directory inside the repository.

    Returns:
        str: The repository name.

    Raises:
        GitError: When git fails resolving the repository root.
    """
    root = repository_root(cwd)
    try:
        url = _run_git(["remote", "get-url", "origin"], cwd=root)
    except GitError:
        return root.name
    cleaned = url.rstrip("/").removesuffix(".git").replace(":", "/")
    return cleaned.rpartition("/")[2] or root.name


def current_branch(cwd: Path) -> str:
    """Return the checked-out branch name.

    A detached HEAD has no branch name — git answers the literal
    ``HEAD`` — so the short commit sha is returned instead, which is what
    identifies the work in that state.

    Args:
        cwd (Path): A directory inside the repository.

    Returns:
        str: The branch name, or the short sha when HEAD is detached.

    Raises:
        GitError: When git fails.
    """
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    if branch != "HEAD":
        return branch
    return _run_git(["rev-parse", "--short", "HEAD"], cwd=cwd)


def resolve_base(base: str, cwd: Path) -> str:
    """Resolve the base ref, falling back to its remote-tracking form.

    A fresh clone often has no local ``main`` — only ``origin/main`` —
    and asking for a diff against a ref that does not exist is the most
    common way this command fails. When ``base`` does not resolve,
    ``origin/<base>`` is tried before giving up.

    Args:
        base (str): The base ref as the user typed it.
        cwd (Path): A directory inside the repository.

    Returns:
        str: A ref that resolves to a commit.

    Raises:
        GitError: When neither ``base`` nor ``origin/<base>`` exists.
    """
    for candidate in (base, f"origin/{base}"):
        try:
            _run_git(
                ["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"], cwd=cwd
            )
        except GitError:
            continue
        return candidate
    raise GitError(
        f"base ref {base!r} does not exist (nor does origin/{base}). "
        "Pass an existing branch, tag or commit."
    )


def commit_subjects(base: str, head: str, cwd: Path) -> list[str]:
    """Return the subjects of the commits ``head`` has and ``base`` lacks.

    Args:
        base (str): The base ref.
        head (str): The branch being described.
        cwd (Path): A directory inside the repository.

    Returns:
        list[str]: Commit subjects, newest first. Empty when the branch
        adds no commits.

    Raises:
        GitError: When git fails.
    """
    output = _run_git(
        ["log", "--no-merges", "--pretty=format:%s", f"{base}..{head}"],
        cwd=cwd,
    )
    return [line for line in output.splitlines() if line.strip()]


def changed_files(base: str, head: str, cwd: Path) -> list[str]:
    """Return the ``--name-status`` lines of the merge-base diff.

    Args:
        base (str): The base ref.
        head (str): The branch being described.
        cwd (Path): A directory inside the repository.

    Returns:
        list[str]: Lines such as ``"M\\tsrc/api/app.py"``.

    Raises:
        GitError: When git fails.
    """
    output = _run_git(["diff", "--name-status", f"{base}...{head}"], cwd=cwd)
    return [line for line in output.splitlines() if line.strip()]


def files_by_churn(base: str, head: str, cwd: Path) -> list[str]:
    """Return the changed text files, most changed lines first.

    Ranking matters because only the first ``max_files`` files get a
    patch: taking them in git's alphabetical order spends the budget on
    ``.github/`` and ``CHANGELOG.md`` while the file the pull request is
    actually about never reaches the model. Binary files are dropped —
    their patch says ``Binary files differ`` and nothing else.

    Args:
        base (str): The base ref.
        head (str): The branch being described.
        cwd (Path): A directory inside the repository.

    Returns:
        list[str]: Paths ordered by added+deleted lines, descending.

    Raises:
        GitError: When git fails.
    """
    ranked: list[tuple[int, int, str]] = []
    output = _run_git(
        ["diff", "--numstat", "--no-renames", f"{base}...{head}"], cwd=cwd
    )
    for position, line in enumerate(output.splitlines()):
        added, _, rest = line.partition("\t")
        deleted, _, path = rest.partition("\t")
        if not path.strip() or added == "-" or deleted == "-":
            continue
        ranked.append((int(added) + int(deleted), position, path))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return [path for _, _, path in ranked]


def diff_excerpts(
    base: str,
    head: str,
    cwd: Path,
    *,
    max_files: int | None = DEFAULT_MAX_FILES,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> tuple[list[DiffExcerpt], int]:
    """Collect a bounded patch excerpt per changed file.

    The whole diff of a large branch is bigger than most context windows
    and mostly noise, so by default only the ``max_files`` most-changed
    files contribute a patch and each is cut at ``max_chars``. Both
    bounds are reported back to the caller rather than applied silently,
    and ``None`` lifts either one.

    Args:
        base (str): The base ref.
        head (str): The branch being described.
        cwd (Path): A directory inside the repository.
        max_files (int | None): How many files get an excerpt. ``0``
            disables excerpts entirely, ``None`` excerpts every file.
        max_chars (int | None): Characters kept per patch, or ``None``
            for the whole patch.

    Returns:
        tuple[list[DiffExcerpt], int]: The excerpts and how many changed
        files were left without one.

    Raises:
        GitError: When git fails.
    """
    names = files_by_churn(base, head, cwd)
    if max_files is not None and max_files <= 0:
        return [], len(names)

    selected = names if max_files is None else names[:max_files]
    excerpts: list[DiffExcerpt] = []
    for name in selected:
        patch = _run_git(["diff", f"{base}...{head}", "--", name], cwd=cwd)
        if not patch.strip():
            continue
        if max_chars is not None and max_chars < len(patch):
            excerpts.append(
                DiffExcerpt(path=name, patch=_cut(patch, max_chars), truncated=True)
            )
        else:
            excerpts.append(DiffExcerpt(path=name, patch=patch, truncated=False))
    return excerpts, len(names) - len(selected)


def _cut(patch: str, max_chars: int) -> str:
    """Trim a patch to ``max_chars`` without leaving half a diff line.

    A hard slice ends mid-token, and a diff line that starts with ``-``
    or ``+`` but stops in the middle of an expression reads as code that
    does not exist. Cutting back to the last newline costs a few
    characters and keeps every line in the excerpt a real one.

    Args:
        patch (str): The full patch.
        max_chars (int): The character budget.

    Returns:
        str: The trimmed patch.
    """
    head = patch[:max_chars]
    boundary = head.rfind("\n")
    return head[:boundary] if boundary > 0 else head


def bundled_template(language: PromptLanguage) -> str:
    """Return the SDK's own pull-request template for a language.

    Args:
        language (PromptLanguage): Which translation to load.

    Returns:
        str: The template's markdown.
    """
    filename = {
        PromptLanguage.PT_BR: "pull_request_template.pt-BR.md",
        PromptLanguage.EN_US: "pull_request_template.en-US.md",
    }[language]
    resource = (
        importlib.resources.files("tempest_fastapi_sdk.cli") / "_templates" / filename
    )
    return resource.read_text(encoding="utf-8")


def resolve_template(
    root: Path,
    *,
    template: Path | None = None,
    language: PromptLanguage = PromptLanguage.PT_BR,
) -> ResolvedTemplate:
    """Pick the pull-request template to hand the model.

    The repository's own template always wins: it is the contract that
    repository's reviewers read, and a generated description that ignores
    it is a description someone has to rewrite. The bundled default only
    covers the repository that has none.

    Args:
        root (Path): The repository root, where the candidates are looked
            up.
        template (Path | None): An explicit template path, which wins
            over both the repository's and the bundled one.
        language (PromptLanguage): Language of the bundled fallback.

    Returns:
        ResolvedTemplate: The template text and where it came from.

    Raises:
        GitError: When an explicit ``--template`` path does not exist.
    """
    if template is not None:
        path = template.expanduser()
        if not path.is_file():
            raise GitError(f"template file not found: {path}")
        return ResolvedTemplate(
            text=path.read_text(encoding="utf-8"),
            source=str(path),
            bundled=False,
        )

    for candidate in TEMPLATE_CANDIDATES:
        path = root / candidate
        if path.is_file():
            return ResolvedTemplate(
                text=path.read_text(encoding="utf-8"),
                source=candidate,
                bundled=False,
            )

    return ResolvedTemplate(
        text=bundled_template(language),
        source=f"bundled ({language.value})",
        bundled=True,
    )


def collect_context(
    *,
    base: str = DEFAULT_BASE,
    head: str | None = None,
    cwd: Path | None = None,
    max_files: int | None = DEFAULT_MAX_FILES,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> PullRequestContext:
    """Read one branch comparison out of the repository.

    Commits and the changed-file list are always complete; only the patch
    excerpts are bounded.

    Args:
        base (str): The base ref the pull request targets.
        head (str | None): The branch being described. Defaults to the
            checked-out one.
        cwd (Path | None): Any directory inside the repository. Defaults
            to the current working directory.
        max_files (int | None): How many files contribute a patch
            excerpt. ``0`` drops every patch, ``None`` excerpts them all.
        max_chars (int | None): Characters kept per patch, or ``None``
            for the whole patch.

    Returns:
        PullRequestContext: Commits, changed files and bounded excerpts.

    Raises:
        GitError: When the directory is not a repository, the base ref
            does not resolve, or git fails.
    """
    working_dir = (cwd or Path.cwd()).expanduser().resolve()
    root = repository_root(working_dir)
    resolved_base = resolve_base(base, root)
    resolved_head = head or current_branch(root)
    excerpts, omitted = diff_excerpts(
        resolved_base,
        resolved_head,
        root,
        max_files=max_files,
        max_chars=max_chars,
    )
    return PullRequestContext(
        repository=repository_name(root),
        base=resolved_base,
        head=resolved_head,
        commits=commit_subjects(resolved_base, resolved_head, root),
        files=changed_files(resolved_base, resolved_head, root),
        excerpts=excerpts,
        omitted_files=omitted,
    )


def build_prompt(
    context: PullRequestContext,
    template: ResolvedTemplate,
    *,
    language: PromptLanguage = PromptLanguage.PT_BR,
) -> str:
    """Render the final prompt from a context and a template.

    Args:
        context (PullRequestContext): What the branch changed.
        template (ResolvedTemplate): The template to be filled in.
        language (PromptLanguage): Language of the instructions around
            the template.

    Returns:
        str: The complete prompt, ready to be piped into an assistant.
    """
    labels = _PROMPT_HEADERS[language]
    rules = "\n".join(
        f"{index}. {rule}" for index, rule in enumerate(_PROMPT_RULES[language], 1)
    )
    commits = (
        "\n".join(f"- {subject}" for subject in context.commits)
        or (labels["no_commits"])
    )
    files = "\n".join(context.files) or labels["no_files"]

    patches: list[str] = []
    for excerpt in context.excerpts:
        note = f"\n{labels['truncated']}" if excerpt.truncated else ""
        patches.append(
            f"#### {excerpt.path}\n```diff\n{excerpt.patch}\n```{note}",
        )
    if context.omitted_files:
        patches.append(labels["omitted"].format(count=context.omitted_files))
    patch_block = "\n\n".join(patches) or labels["no_patches"]

    return "\n".join(
        (
            labels["role"],
            "",
            f"## {labels['rules']}",
            "",
            rules,
            "",
            "---",
            "",
            f"## {labels['template']}",
            "",
            template.text.strip(),
            "",
            "---",
            "",
            f"## {labels['context']}",
            "",
            f"{labels['repository']}: {context.repository}",
            f"{labels['branch']}: `{context.head}` <- `{context.base}`",
            "",
            f"### {labels['commits']}",
            "",
            commits,
            "",
            f"### {labels['files']}",
            "",
            files,
            "",
            f"### {labels['patches']}",
            "",
            patch_block,
            "",
            "---",
            "",
            labels["closing"],
            "",
        )
    )


def generate_pr_prompt(
    *,
    base: str = DEFAULT_BASE,
    head: str | None = None,
    cwd: Path | None = None,
    template: Path | None = None,
    language: PromptLanguage = PromptLanguage.PT_BR,
    max_files: int | None = DEFAULT_MAX_FILES,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> tuple[str, PullRequestContext, ResolvedTemplate]:
    """Read the repository and render the prompt in one call.

    Args:
        base (str): The base ref the pull request targets.
        head (str | None): The branch being described. Defaults to the
            checked-out one.
        cwd (Path | None): Any directory inside the repository.
        template (Path | None): An explicit template path.
        language (PromptLanguage): Language of the instructions and of
            the bundled fallback template.
        max_files (int | None): How many files contribute a patch
            excerpt. ``0`` drops every patch, ``None`` excerpts them all.
        max_chars (int | None): Characters kept per patch, or ``None``
            for the whole patch.

    Returns:
        tuple[str, PullRequestContext, ResolvedTemplate]: The prompt plus
        the context and template it was built from, so a caller can
        report what was read and what was dropped.

    Raises:
        GitError: When the repository, the base ref or the template path
            cannot be resolved.
    """
    context = collect_context(
        base=base,
        head=head,
        cwd=cwd,
        max_files=max_files,
        max_chars=max_chars,
    )
    root = repository_root((cwd or Path.cwd()).expanduser().resolve())
    resolved_template = resolve_template(root, template=template, language=language)
    prompt = build_prompt(context, resolved_template, language=language)
    return prompt, context, resolved_template


__all__: list[str] = [
    "DEFAULT_BASE",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_FILES",
    "TEMPLATE_CANDIDATES",
    "DiffExcerpt",
    "GitError",
    "PromptLanguage",
    "PullRequestContext",
    "ResolvedTemplate",
    "build_prompt",
    "bundled_template",
    "changed_files",
    "collect_context",
    "commit_subjects",
    "current_branch",
    "diff_excerpts",
    "generate_pr_prompt",
    "repository_name",
    "repository_root",
    "resolve_base",
    "resolve_template",
]
