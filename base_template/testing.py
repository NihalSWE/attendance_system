"""Test helpers shared across apps."""

from base_template.navigation import inventory_link_shown, inventory_menu_shown


def assert_inventory_menu(test, response, marker, menu="inventory", view=None):
    """The Store & Inventory menus follow ``INVENTORY_LINKS_SHOWN`` (2026-10-18):
    ``marker`` is on the page when that menu (and ``view``'s link, if given) is
    shown, and absent when it is hidden."""
    shown = inventory_menu_shown(menu) and (view is None or inventory_link_shown(menu, view))
    if shown:
        test.assertContains(response, marker)
    else:
        test.assertNotContains(response, marker)
