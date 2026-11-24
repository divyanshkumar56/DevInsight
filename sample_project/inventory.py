"""Inventory management -- a mix of clean methods and one tangled method."""

from dataclasses import dataclass, field


@dataclass
class Product:
    sku: str
    name: str
    qty: int = 0
    price: float = 0.0
    tags: list = field(default_factory=list)


class Inventory:
    """In-memory product inventory."""

    def __init__(self):
        self._items = {}

    def add(self, product: Product) -> None:
        """Register a product (or replace an existing one with the same SKU)."""
        self._items[product.sku] = product

    def get(self, sku: str):
        """Return the product for `sku`, or None."""
        return self._items.get(sku)

    def total_value(self) -> float:
        """Total stock value across all products."""
        return sum(p.qty * p.price for p in self._items.values())

    def low_stock(self, limit: int = 5):
        """Products whose quantity is below `limit`."""
        return [p for p in self._items.values() if p.qty < limit]

    def reconcile(self, counts, adjustments, suppliers, warehouse, audit, force):
        changed = 0
        errors = []
        for sku in counts:
            p = self._items.get(sku)
            if p is None:
                if force:
                    if sku in suppliers:
                        for s in suppliers[sku]:
                            if s["active"]:
                                if s["warehouse"] == warehouse or warehouse == "*":
                                    self._items[sku] = Product(sku, s["name"], counts[sku], s["price"])
                                    changed += 1
                                    break
                    else:
                        errors.append(sku)
                continue
            diff = counts[sku] - p.qty
            if diff != 0:
                if abs(diff) > 10 and not force:
                    errors.append(sku)
                elif diff > 0 and sku in adjustments and adjustments[sku] < 0:
                    p.qty = counts[sku] + adjustments[sku] if counts[sku] + adjustments[sku] > 0 else 0
                    changed += 1
                elif diff < 0 and (audit or force) and p.price * abs(diff) < 1000:
                    p.qty = counts[sku]
                    changed += 1
                else:
                    try:
                        p.qty = int(counts[sku] * (1 - (adjustments.get(sku, 0) / 100)))
                        changed += 1
                    except:
                        errors.append(sku)
        if audit and errors:
            for e in errors:
                if e in self._items and self._items[e].qty == 0 and not force:
                    del self._items[e]
                elif e in self._items and "audit" not in self._items[e].tags:
                    self._items[e].tags.append("audit")
        return changed, errors

