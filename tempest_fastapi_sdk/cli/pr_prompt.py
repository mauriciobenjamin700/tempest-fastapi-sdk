"""Compatibility re-export of the PR-description prompt generator.

``tempest pr-prompt`` moved to
[`tempest-cli`](https://pypi.org/project/tempest-cli/) in v0.226.0 along
with the rest of the framework-agnostic tooling — it reads git and a
markdown template, and never touched anything FastAPI.

The command keeps working under ``tempest``, and these symbols keep
importing from here. New code should import from ``tempest_cli``.
"""

from tempest_cli.pr_prompt import DEFAULT_BASE as DEFAULT_BASE
from tempest_cli.pr_prompt import DEFAULT_MAX_CHARS as DEFAULT_MAX_CHARS
from tempest_cli.pr_prompt import DEFAULT_MAX_FILES as DEFAULT_MAX_FILES
from tempest_cli.pr_prompt import TEMPLATE_CANDIDATES as TEMPLATE_CANDIDATES
from tempest_cli.pr_prompt import DiffExcerpt as DiffExcerpt
from tempest_cli.pr_prompt import GitError as GitError
from tempest_cli.pr_prompt import PromptLanguage as PromptLanguage
from tempest_cli.pr_prompt import PullRequestContext as PullRequestContext
from tempest_cli.pr_prompt import ResolvedTemplate as ResolvedTemplate
from tempest_cli.pr_prompt import build_prompt as build_prompt
from tempest_cli.pr_prompt import bundled_template as bundled_template
from tempest_cli.pr_prompt import changed_files as changed_files
from tempest_cli.pr_prompt import collect_context as collect_context
from tempest_cli.pr_prompt import commit_subjects as commit_subjects
from tempest_cli.pr_prompt import current_branch as current_branch
from tempest_cli.pr_prompt import diff_excerpts as diff_excerpts
from tempest_cli.pr_prompt import files_by_churn as files_by_churn
from tempest_cli.pr_prompt import generate_pr_prompt as generate_pr_prompt
from tempest_cli.pr_prompt import repository_name as repository_name
from tempest_cli.pr_prompt import repository_root as repository_root
from tempest_cli.pr_prompt import resolve_base as resolve_base
from tempest_cli.pr_prompt import resolve_template as resolve_template

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
    "files_by_churn",
    "generate_pr_prompt",
    "repository_name",
    "repository_root",
    "resolve_base",
    "resolve_template",
]
