"""Admin actions that simulate Mercado Pago moves on a service's charges.

:func:`mercado_pago_simulation_actions` turns a
:class:`~tempest_fastapi_sdk.integrations.payment.adapters.MercadoPagoSimulator`
into bulk actions for the panel of the service's charge table: a developer
or QA marks pending rows and clicks "Simular pagamento", the order moves on
the simulator, and the signed webhook reaches the service's own webhook
route, which confirms the purchase the way production does.

The simulator refuses to be built outside ``ENV=development`` and
``ENV=test``, so the actions cannot exist in production either.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

import httpx

from tempest_fastapi_sdk.admin.actions import (
    ActionHandler,
    AdminActionContext,
    AdminActionResult,
    admin_action,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.integrations.payment.adapters import (
        MercadoPagoSimulator,
        MercadoPagoTransition,
    )

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_SIMULATION_TRANSITIONS: Final[tuple[MercadoPagoTransition, ...]] = (
    "pay",
    "expire",
    "refund",
)
"""The moves offered when the caller names none."""

SIMULATION_ACTION_LABELS: Final[Mapping[str, str]] = {
    "pay": "Simular pagamento",
    "expire": "Simular expiração",
    "fail": "Simular falha",
    "cancel": "Simular cancelamento",
    "capture": "Simular captura",
    "refund": "Simular estorno",
}
"""Label of each move in the panel's action menu."""

DANGEROUS_TRANSITIONS: Final[frozenset[str]] = frozenset({"refund"})
"""Moves the panel confirms with the destructive prompt."""

SIMULATION_ACTION_PREFIX: Final[str] = "simulate_mercado_pago_"
"""Prefix of each action's name; the move follows (``simulate_mercado_pago_pay``)."""


def _make_action(
    simulator: MercadoPagoSimulator,
    transition: MercadoPagoTransition,
    *,
    reference_field: str,
    label: str,
) -> ActionHandler:
    """Build the action applying one move to every selected row.

    Args:
        simulator (MercadoPagoSimulator): Moves, signs and delivers.
        transition (MercadoPagoTransition): The move.
        reference_field (str): The row attribute holding the order's
            ``external_reference`` (or its id).
        label (str): The menu label.

    Returns:
        ActionHandler: The decorated action.
    """

    @admin_action(
        label=label,
        name=f"{SIMULATION_ACTION_PREFIX}{transition}",
        dangerous=transition in DANGEROUS_TRANSITIONS,
    )
    async def simulate(ctx: AdminActionContext) -> AdminActionResult:
        """Apply the move to each selected row and report each delivery.

        Rows are applied one at a time, in the order they were selected; a
        failure on one row does not stop the others.

        Args:
            ctx (AdminActionContext): The selection and the session.

        Returns:
            AdminActionResult: ``success`` when every delivery was answered
            ``2xx``; ``error`` naming the references that were not found,
            whose move the order's state refused, whose webhook answered
            otherwise, or that could not be simulated.
        """
        rows = await ctx.repository.list(filters={"id": ctx.ids})
        by_id = {str(row.id): str(getattr(row, reference_field)) for row in rows}
        references = [by_id[str(row_id)] for row_id in ctx.ids if str(row_id) in by_id]
        delivered: list[str] = []
        problems: list[str] = []
        for reference in references:
            try:
                result = await simulator.apply(reference, transition)
            except KeyError:
                problems.append(f"{reference}: pedido não encontrado no simulador")
            except ValueError as error:
                problems.append(f"{reference}: {error}")
            except httpx.HTTPError as error:
                logger.warning("simulation of %s failed: %r", reference, error)
                problems.append(f"{reference}: simulador indisponível ({error!r})")
            else:
                status = result.delivery.status_code
                if 200 <= status < 300:
                    delivered.append(f"{reference} ({result.order.get('status')})")
                else:
                    problems.append(f"{reference}: webhook respondeu {status}")
        message = f"{label}: {len(delivered)} entregue(s)"
        if delivered:
            message += " — " + ", ".join(delivered)
        if problems:
            return AdminActionResult(
                f"{message}; falharam — " + "; ".join(problems), category="error"
            )
        return AdminActionResult(f"{message}.")

    return simulate


def mercado_pago_simulation_actions(
    simulator: MercadoPagoSimulator,
    *,
    reference_field: str = "correlation_id",
    transitions: Sequence[MercadoPagoTransition] = DEFAULT_SIMULATION_TRANSITIONS,
    labels: Mapping[str, str] | None = None,
) -> list[ActionHandler]:
    """Build one admin action per Mercado Pago move.

    Hang the result on the ``AdminModel`` of the service's charge table
    (``AdminModel(ChargeModel, actions=[*mercado_pago_simulation_actions(sim)])``).
    Each action reads ``reference_field`` from every selected row and calls
    :meth:`~tempest_fastapi_sdk.integrations.payment.adapters.MercadoPagoSimulator.apply`
    with it, so the simulator must deliver on its own: build it with
    ``app=`` and ``webhook_url=`` (in process) or an absolute
    ``webhook_url``. "Simular estorno" is marked ``dangerous``.

    Args:
        simulator (MercadoPagoSimulator): Moves, signs and delivers. Its
            construction already refused anything but development and test.
        reference_field (str): The row attribute holding the order's
            ``external_reference`` — the reference the service sent on
            create — or the order id.
        transitions (Sequence[MercadoPagoTransition]): The moves to offer,
            in menu order.
        labels (Mapping[str, str] | None): Menu labels by move, over
            :data:`SIMULATION_ACTION_LABELS`.

    Returns:
        list[ActionHandler]: The actions, ready for ``AdminModel(actions=)``.

    Raises:
        ValueError: When the simulator has no ``webhook_url``, a move is
            repeated, or a move is unknown.
    """
    if simulator.webhook_url is None:
        raise ValueError(
            "the admin actions need a simulator that delivers on its own: "
            "build it with app= and webhook_url=, or an absolute webhook_url"
        )
    if len(set(transitions)) != len(transitions):
        raise ValueError(f"repeated move in {list(transitions)!r}")
    resolved = {**SIMULATION_ACTION_LABELS, **(labels or {})}
    unknown = [move for move in transitions if move not in SIMULATION_ACTION_LABELS]
    if unknown:
        raise ValueError(f"unknown move(s) {unknown!r}")
    return [
        _make_action(
            simulator, move, reference_field=reference_field, label=resolved[move]
        )
        for move in transitions
    ]


__all__: list[str] = [
    "DANGEROUS_TRANSITIONS",
    "DEFAULT_SIMULATION_TRANSITIONS",
    "SIMULATION_ACTION_LABELS",
    "SIMULATION_ACTION_PREFIX",
    "mercado_pago_simulation_actions",
]
