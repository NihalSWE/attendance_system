"""Customer writes, through the inventory master helper (same permission,
whitelist, validation and audit as items)."""

from inventory.masters import Master
from sales.models import Customer

CUSTOMER = Master(Customer, (
    "code", "name", "customer_type", "contact_person", "phone", "whatsapp", "email",
    "address", "territory", "credit_limit", "payment_terms_days", "price_list_name", "notes",
), "sales.customer")
