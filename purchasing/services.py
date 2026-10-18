"""Supplier writes, through the inventory master helper (same permission,
whitelist, validation and audit as items)."""

from inventory.masters import Master
from purchasing.models import Supplier

SUPPLIER = Master(Supplier, (
    "code", "name", "contact_person", "phone", "email", "address", "bin", "tin",
    "payment_terms_days", "items_supplied", "notes",
), "purchasing.supplier")
