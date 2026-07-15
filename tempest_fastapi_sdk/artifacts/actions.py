"""Admin action factory for activating an artifact version.

Builds an :class:`~tempest_fastapi_sdk.AdminAction` that mirrors the
downstream ``activate_model_version`` handler generically: it flips
``is_current`` on the single selected row and clears it on the siblings
of the same ``name`` in one transaction. The concrete model is read from
``ctx.repository.model``, so the same action works for any
:class:`~tempest_fastapi_sdk.artifacts.ArtifactVersionMixin` table.
"""

from __future__ import annotations

from sqlalchemy import select, update

from tempest_fastapi_sdk.admin.actions import (
    AdminAction,
    AdminActionContext,
    AdminActionResult,
    admin_action,
    resolve_admin_action,
)


def make_activate_artifact_action(
    *,
    label: str = "Activate version",
    name: str = "activate_artifact",
    dangerous: bool = False,
) -> AdminAction:
    """Build an admin action that activates the selected artifact version.

    Register the returned action's handler on an ``AdminModel``::

        action = make_activate_artifact_action(label="Ativar versão")
        site.register(AdminModel(model=ModelVersion, actions=[action.handler]))

    Args:
        label (str): Dropdown label shown to the operator.
        name (str): Stable identifier (the submitted form value); must
            be unique among the model's actions.
        dangerous (bool): Flag the action as destructive for a stronger
            UI confirm.

    Returns:
        AdminAction: The action metadata + handler. Its ``handler``
        carries the ``@admin_action`` marker, so pass ``action.handler``
        to ``AdminModel(actions=[...])``.
    """

    @admin_action(label=label, name=name, dangerous=dangerous)
    async def _activate(ctx: AdminActionContext) -> AdminActionResult:
        """Activate the selected version, clearing same-name siblings.

        Args:
            ctx (AdminActionContext): Selected row ids + request DB
                session + repository for the admin's model.

        Returns:
            AdminActionResult: Flash message shown on the list view.
        """
        session = ctx.db_session
        model = ctx.repository.model
        rows = (
            (await session.execute(select(model).where(model.id.in_(ctx.ids))))
            .scalars()
            .all()
        )
        if len(rows) != 1:
            return AdminActionResult(
                "Select exactly one version to activate.",
                category="warning",
            )
        target = rows[0]
        await session.execute(
            update(model).where(model.name == target.name).values(is_current=False),
        )
        target.is_current = True
        await session.commit()
        return AdminActionResult(
            f"Activated version {target.version!r} of {target.name!r}.",
        )

    return resolve_admin_action(_activate)


__all__: list[str] = [
    "make_activate_artifact_action",
]
