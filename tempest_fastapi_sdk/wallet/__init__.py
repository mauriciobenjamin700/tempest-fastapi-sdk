"""Wallet module — balance in integer cents, statement and replay-safe moves.

Reusable building blocks over the SDK primitives: a balance column for
the application's user model (:class:`WalletBalanceMixin`, or
:class:`OverdraftWalletBalanceMixin` when a reversal may leave a debt),
an append-only statement table (:class:`BaseWalletEntryModel` +
:func:`make_wallet_entry_model`), a :class:`WalletRepository` whose
every write is one conditional ``UPDATE ... RETURNING``, and a
:class:`WalletService` that pairs each move with its statement line in
one transaction.

Needs nothing beyond the base install: SQLAlchemy is already there.
"""

from tempest_fastapi_sdk.wallet.exceptions import (
    WalletEntryNotFoundException as WalletEntryNotFoundException,
)
from tempest_fastapi_sdk.wallet.exceptions import (
    WalletInsufficientFundsException as WalletInsufficientFundsException,
)
from tempest_fastapi_sdk.wallet.exceptions import (
    WalletNotFoundException as WalletNotFoundException,
)
from tempest_fastapi_sdk.wallet.exceptions import (
    WalletReferenceConflictException as WalletReferenceConflictException,
)
from tempest_fastapi_sdk.wallet.models import (
    WALLET_BALANCE_CHECK_NAME as WALLET_BALANCE_CHECK_NAME,
)
from tempest_fastapi_sdk.wallet.models import (
    BaseWalletEntryModel as BaseWalletEntryModel,
)
from tempest_fastapi_sdk.wallet.models import (
    OverdraftWalletBalanceMixin as OverdraftWalletBalanceMixin,
)
from tempest_fastapi_sdk.wallet.models import (
    WalletBalanceMixin as WalletBalanceMixin,
)
from tempest_fastapi_sdk.wallet.models import (
    make_wallet_entry_model as make_wallet_entry_model,
)
from tempest_fastapi_sdk.wallet.repository import (
    WalletRepository as WalletRepository,
)
from tempest_fastapi_sdk.wallet.schemas import (
    WalletBalanceSchema as WalletBalanceSchema,
)
from tempest_fastapi_sdk.wallet.schemas import WalletEntryKind as WalletEntryKind
from tempest_fastapi_sdk.wallet.schemas import (
    WalletEntrySchema as WalletEntrySchema,
)
from tempest_fastapi_sdk.wallet.service import (
    REVERSAL_REFERENCE_TYPE as REVERSAL_REFERENCE_TYPE,
)
from tempest_fastapi_sdk.wallet.service import WalletService as WalletService

__all__: list[str] = [
    "REVERSAL_REFERENCE_TYPE",
    "WALLET_BALANCE_CHECK_NAME",
    "BaseWalletEntryModel",
    "OverdraftWalletBalanceMixin",
    "WalletBalanceMixin",
    "WalletBalanceSchema",
    "WalletEntryKind",
    "WalletEntryNotFoundException",
    "WalletEntrySchema",
    "WalletInsufficientFundsException",
    "WalletNotFoundException",
    "WalletReferenceConflictException",
    "WalletRepository",
    "WalletService",
    "make_wallet_entry_model",
]
