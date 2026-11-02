/* Every dropdown on the inventory pages has a search box (2026-11-02).

   Lists from the database are already Select2 (forms.js, .js-select2); the
   short fixed lists (.js-select, customselect.js) and the plain lists of
   Stock in / Move stock / Sell lines become Select2 too, with the search box
   shown however short the list. Runs before forms.js sets Select2 up on
   DOMContentLoaded, so it only changes the classes.

   Select2 announces a choice as a jQuery event, which code listening for the
   browser's own "change" never hears: a list changed here (data-bridge) also
   fires a real "change", so the boxes that follow it keep working.

   window.inventorySelect2(select) sets up one list added later (a copied line).
   A list inside [data-plain-select] is left as it is. */
(function () {
    document.querySelectorAll("select.js-select, select[data-searchable]").forEach(function (box) {
        if (box.multiple || box.closest("[data-plain-select]")) return;
        box.classList.remove("js-select");
        box.classList.add("js-select2");
        box.dataset.bridge = "1";
    });
    document.querySelectorAll("select.js-select2").forEach(function (box) {
        box.dataset.search = "always";
    });

    window.inventorySelect2 = function (box) {
        const $ = window.jQuery;
        if (!$ || !$.fn.select2 || $(box).data("select2")) return;
        const $dialog = $(box).closest("dialog");
        $(box).select2({
            dropdownParent: $dialog.length ? $dialog : $(document.body),
            theme: "paper",
            width: "100%",
            minimumResultsForSearch: 0,
            placeholder: box.dataset.placeholder || null
        });
    };

    document.addEventListener("DOMContentLoaded", function () {
        const $ = window.jQuery;
        if (!$) return;
        $(document).on("select2:select select2:clear", "select[data-bridge]", function (event) {
            event.target.dispatchEvent(new Event("change", {bubbles: true}));
        });
        // The search box takes the typing at once when a list opens.
        $(document).on("select2:open", function () {
            const search = document.querySelector(".select2-container--open .select2-search__field");
            if (search) search.focus();
        });
    });
})();
