"""Leave policies, versions, the ledger and balances (Phase E, 2026-09-27;
docs/LEAVE_FULL_DESIGN.md §2-3). Filled in by E2/E3."""


def policy_check(employee, leave_type, days, shape, fitted):
    """Refuse leave the employee's leave policy does not allow. No policy: nothing."""
    return None
