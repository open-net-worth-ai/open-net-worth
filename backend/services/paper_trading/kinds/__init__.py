"""The asset-kind registry: one adapter per kind behind one protocol. ``kind_for`` picks the
adapter that owns an asset; adding a kind is adding an adapter here, never a branch elsewhere."""

from __future__ import annotations

from backend.services.paper_trading.kinds.base import BaseKind
from backend.services.paper_trading.kinds.crypto import CryptoKind
from backend.services.paper_trading.kinds.fund_nav import FundNavKind
from backend.services.paper_trading.kinds.fx import FxKind
from backend.services.paper_trading.kinds.index import IndexKind
from backend.services.paper_trading.kinds.listed import ListedKind
from backend.services.paper_trading.kinds.manual import ManualKind
from backend.services.paper_trading.kinds.metal import MetalKind
from backend.services.paper_trading.model import PaperAsset

ADAPTERS: tuple[BaseKind, ...] = (
    ListedKind(),
    FundNavKind(),
    CryptoKind(),
    MetalKind(),
    FxKind(),
    IndexKind(),
    ManualKind(),
)
_BY_KEY = {a.key: a for a in ADAPTERS}


def kind_for(asset: PaperAsset) -> BaseKind:
    for adapter in ADAPTERS:
        if adapter.owns(asset):
            return adapter
    raise KeyError(f"No paper adapter for kind {asset.kind!r}")


def adapter(key: str) -> BaseKind:
    return _BY_KEY[key]
