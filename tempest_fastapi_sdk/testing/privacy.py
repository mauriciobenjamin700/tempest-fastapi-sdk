"""Ready-made CI guard for :class:`~tempest_fastapi_sdk.privacy.SubjectGraph`."""

from __future__ import annotations

from tempest_fastapi_sdk.privacy.graph import SubjectGraph


def assert_subject_graph_valid(graph: SubjectGraph) -> None:
    """Fail when the schema would break erasure of a data subject.

    Call it from a test so a foreign key added without ``ON DELETE
    CASCADE`` toward a table of the subject's closure (or an unjustified
    ``SET NULL``) fails CI instead of failing the first erasure request in
    production.

    Example:

        >>> def test_subject_graph() -> None:
        ...     assert_subject_graph_valid(SubjectGraph(Base.metadata, root="users"))

    Args:
        graph (SubjectGraph): The graph to check.

    Raises:
        AssertionError: Listing every line of
            :meth:`SubjectGraph.violations` when there is at least one.
    """
    problems = graph.violations()
    if problems:
        lines = "\n".join(f"  - {problem}" for problem in problems)
        raise AssertionError(
            f"subject graph rooted at {graph.root.name!r} has "
            f"{len(problems)} erasure violation(s):\n{lines}"
        )


__all__: list[str] = ["assert_subject_graph_valid"]
