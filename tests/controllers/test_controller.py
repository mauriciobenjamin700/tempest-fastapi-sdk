"""Tests for tempest_fastapi_sdk.controllers.Controller."""

from tempest_fastapi_sdk import BaseController, Controller


class InvoiceService:
    """Stand-in for a service the controller orchestrates."""

    async def issue(self, amount: int) -> int:
        """Return the issued amount.

        Args:
            amount (int): The amount.

        Returns:
            int: The amount.
        """
        return amount


class LedgerService:
    """Second stand-in service, recording what was posted."""

    def __init__(self) -> None:
        """Start with an empty ledger."""
        self.posted: list[int] = []

    async def post(self, amount: int) -> None:
        """Record one posting.

        Args:
            amount (int): The amount.
        """
        self.posted.append(amount)


class BillingController(Controller):
    """Orchestrates two services with no single-resource CRUD."""

    def __init__(self, invoices: InvoiceService, ledger: LedgerService) -> None:
        """Receive the services it coordinates.

        Args:
            invoices (InvoiceService): The invoice service.
            ledger (LedgerService): The ledger service.
        """
        self.invoices: InvoiceService = invoices
        self.ledger: LedgerService = ledger

    async def charge(self, amount: int) -> int:
        """Issue an invoice and post it to the ledger.

        Args:
            amount (int): The amount.

        Returns:
            int: The issued amount.
        """
        issued = await self.invoices.issue(amount)
        await self.ledger.post(issued)
        return issued


class TestController:
    def test_has_no_crud_surface(self) -> None:
        for name in ("get_by_id", "list", "paginate", "count", "update", "delete"):
            assert not hasattr(Controller, name)

    def test_base_controller_specializes_controller(self) -> None:
        assert issubclass(BaseController, Controller)

    async def test_orchestrates_injected_services(self) -> None:
        ledger = LedgerService()
        controller = BillingController(InvoiceService(), ledger)
        assert isinstance(controller, Controller)
        assert await controller.charge(42) == 42
        assert ledger.posted == [42]
