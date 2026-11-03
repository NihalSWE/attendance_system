/* Inventory pages: the modals.

   [data-open-dialog="id"] opens <dialog id="id"> - delegated, so buttons in
   rows a server-side table redraws keep working. [data-close-dialog] and a
   click on the backdrop close it. A form the server refused comes back with
   data-open-on-load naming its modal, which opens again with the reasons.
   Confirmations are confirm.js's modal (data-confirm), never the browser's. */
document.addEventListener("DOMContentLoaded", function () {
    function open(dialog) {
        if (dialog && typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    }
    document.addEventListener("click", function (event) {
        var opener = event.target.closest("[data-open-dialog]");
        if (opener) {
            event.preventDefault();
            var dialog = document.getElementById(opener.dataset.openDialog);
            // One dialog shared by every row (Print, Void on the label register):
            // the button says where its form posts and what it is about.
            if (dialog && opener.dataset.dialogAction) {
                var form = dialog.querySelector("form");
                if (form) form.action = opener.dataset.dialogAction;
                var subject = dialog.querySelector("[data-dialog-subject]");
                if (subject) subject.textContent = opener.dataset.dialogSubject || "";
                dialog.querySelectorAll("[data-reprint-only]").forEach(function (part) {
                    part.hidden = opener.dataset.dialogReprint !== "1";
                });
            }
            open(dialog);
            return;
        }
        var closer = event.target.closest("[data-close-dialog]");
        if (closer) {
            var dialog = closer.closest("dialog");
            if (dialog) dialog.close();
        }
    });
    document.querySelectorAll("dialog.modal").forEach(function (dialog) {
        dialog.addEventListener("click", function (event) {
            if (event.target === dialog) dialog.close();
        });
    });
    var again = document.querySelector("[data-open-on-load]");
    if (again && again.dataset.openOnLoad) open(document.getElementById(again.dataset.openOnLoad));
});

/* Recipe: several materials at once (2026-11-02, hub/_recipe.html). Each
   material chosen in the search list gets a row with its own "one batch uses"
   (in its unit) and wastage; a removed one loses its row, typed ones stay. */
document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select[data-pick-materials]").forEach(function (select) {
        const form = select.closest("form");
        const box = form && form.querySelector("[data-pick-rows]");
        if (!box) return;
        function row(option) {
            const pk = option.value;
            const unit = option.dataset.unit || "";
            const div = document.createElement("div");
            div.className = "ui-pick-row";
            div.dataset.pick = pk;
            div.innerHTML =
                '<div class="ui-pick-row__name"></div>' +
                '<div class="field"><label class="field__label" for="qty_' + pk + '">The recipe uses</label>' +
                '<div class="ui-unit"><input class="input" id="qty_' + pk + '" name="qty_' + pk + '" type="number" step="any" min="0" inputmode="decimal">' +
                '<span class="ui-unit__label"></span></div></div>' +
                '<div class="field"><label class="field__label" for="scrap_' + pk + '">Normal wastage (optional)</label>' +
                '<div class="ui-unit"><input class="input" id="scrap_' + pk + '" name="scrap_' + pk + '" type="number" step="any" min="0" max="100" inputmode="decimal" placeholder="0">' +
                '<span class="ui-unit__label">%</span></div></div>';
            div.querySelector(".ui-pick-row__name").textContent = option.textContent;
            const small = document.createElement("small");
            small.textContent = "counted in " + unit;
            div.querySelector(".ui-pick-row__name").appendChild(small);
            div.querySelector(".ui-unit__label").textContent = unit;
            return div;
        }
        function sync() {
            const wanted = Array.from(select.selectedOptions);
            box.querySelectorAll("[data-pick]").forEach(function (old) {
                if (!wanted.some(option => option.value === old.dataset.pick)) old.remove();
            });
            wanted.forEach(function (option) {
                if (!box.querySelector('[data-pick="' + option.value + '"]')) box.appendChild(row(option));
            });
        }
        select.addEventListener("change", sync);
        if (window.jQuery) window.jQuery(select).on("select2:select select2:unselect select2:clear", sync);
        sync();
    });
});
