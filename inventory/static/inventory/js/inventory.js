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
