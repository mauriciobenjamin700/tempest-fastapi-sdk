"""Wallet module — balance, ledger, holds and Pix withdrawals in integer cents.

Reusable building blocks over the SDK primitives: a balance column mixin
and an abstract ledger table with a ``make_*`` factory, a
:class:`WalletService` whose every balance change is one SQL ``UPDATE``
plus its ledger line in the same transaction, a withdrawal that debits
before it pays and gives the money back only when the provider refused,
:func:`claim_once` for settlements that must run once, fee and split
arithmetic in basis points, and an opt-in :func:`make_wallet_router`.

The decisions that go wrong the same way in every product, and are made
here once:

* a balance read in Python and written back loses credits under
  concurrency — the service never does that;
* a hold checked before the debit can be drained by two withdrawals at
  once — the hold is part of the debit's ``WHERE``;
* a payout that failed ambiguously must keep its debit — returning it
  could pay twice;
* money is never a ``float``.
"""

from tempest_fastapi_sdk.wallet.models import (
    BaseWalletEntryModel as BaseWalletEntryModel,
)
from tempest_fastapi_sdk.wallet.models import (
    WalletBalanceMixin as WalletBalanceMixin,
)
from tempest_fastapi_sdk.wallet.models import (
    make_wallet_entry_model as make_wallet_entry_model,
)
from tempest_fastapi_sdk.wallet.money import (
    BASIS_POINTS_PER_UNIT as BASIS_POINTS_PER_UNIT,
)
from tempest_fastapi_sdk.wallet.money import (
    OPENPIX_FEE_TIERS as OPENPIX_FEE_TIERS,
)
from tempest_fastapi_sdk.wallet.money import NetSplit as NetSplit
from tempest_fastapi_sdk.wallet.money import OpenPixFeeTiers as OpenPixFeeTiers
from tempest_fastapi_sdk.wallet.money import (
    openpix_fee_cents as openpix_fee_cents,
)
from tempest_fastapi_sdk.wallet.money import split_net as split_net
from tempest_fastapi_sdk.wallet.router import (
    make_wallet_router as make_wallet_router,
)
from tempest_fastapi_sdk.wallet.schemas import (
    PixDestinationSchema as PixDestinationSchema,
)
from tempest_fastapi_sdk.wallet.schemas import (
    WalletBalanceSchema as WalletBalanceSchema,
)
from tempest_fastapi_sdk.wallet.schemas import WalletEntryKind as WalletEntryKind
from tempest_fastapi_sdk.wallet.schemas import (
    WalletEntrySchema as WalletEntrySchema,
)
from tempest_fastapi_sdk.wallet.schemas import (
    WithdrawalSchema as WithdrawalSchema,
)
from tempest_fastapi_sdk.wallet.schemas import (
    WithdrawRequestSchema as WithdrawRequestSchema,
)
from tempest_fastapi_sdk.wallet.service import (
    PAYOUT_REFERENCE_TYPE as PAYOUT_REFERENCE_TYPE,
)
from tempest_fastapi_sdk.wallet.service import WalletService as WalletService
from tempest_fastapi_sdk.wallet.service import claim_once as claim_once

__all__: list[str] = [
    "BASIS_POINTS_PER_UNIT",
    "OPENPIX_FEE_TIERS",
    "PAYOUT_REFERENCE_TYPE",
    "BaseWalletEntryModel",
    "NetSplit",
    "OpenPixFeeTiers",
    "PixDestinationSchema",
    "WalletBalanceMixin",
    "WalletBalanceSchema",
    "WalletEntryKind",
    "WalletEntrySchema",
    "WalletService",
    "WithdrawRequestSchema",
    "WithdrawalSchema",
    "claim_once",
    "make_wallet_entry_model",
    "make_wallet_router",
    "openpix_fee_cents",
    "split_net",
]
