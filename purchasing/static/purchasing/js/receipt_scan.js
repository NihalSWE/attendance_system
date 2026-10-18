/* Goods receipt: a scan fills the "Add what arrived" modal.

   The scan box (inventory/js/scanbox.js) announces "scan:result". When it is
   an item - its barcode, a pack barcode or a supplier's GS1-128 with batch,
   expiry and quantity - the modal opens with the item (or its order line),
   the unit, the quantity, the batch and the expiry filled in. The person
   checks it, adds the cost if needed, and presses Add line. */
document.addEventListener("scan:result", function (event) {
    "use strict";
    var answer = event.detail;
    if (!answer || !answer.found || !answer.item) return;
    var dialog = document.getElementById("line-dialog");
    if (!dialog) return;
    var form = dialog.querySelector("form");
    var $ = window.jQuery;

    function choose(name, test) {
        var select = form.querySelector('select[name="' + name + '"]');
        if (!select) return false;
        for (var i = 0; i < select.options.length; i++) {
            if (test(select.options[i])) {
                select.value = select.options[i].value;
                if ($ && $(select).data("select2")) $(select).trigger("change");
                else select.dispatchEvent(new Event("change", {bubbles: true}));
                return true;
            }
        }
        return false;
    }

    function set(name, value) {
        var input = form.querySelector('[name="' + name + '"]');
        if (!input || value === undefined || value === null || value === "") return;
        input.value = value;
        input.dispatchEvent(new Event("change", {bubbles: true}));
    }

    var code = answer.item.code;
    var onOrder = choose("order_line", function (option) {
        return option.text.indexOf(code + " ") === 0;
    });
    if (!onOrder) choose("item", function (option) { return option.value === String(answer.item.id); });
    if (answer.quantity) {
        choose("uom", function (option) { return option.text.trim() === answer.uom; });
        set("qty_received", answer.quantity);
    }
    if (answer.batch) {
        set("batch_no", answer.batch.batch_no);
        set("expiry_date", answer.batch.expiry_date);
    } else if (answer.gs1) {
        // A supplier's batch that is not in the software yet: keep their number
        // and expiry; our own batch number is made when posting.
        set("supplier_batch_no", answer.gs1.batch);
        set("expiry_date", answer.gs1.expiry);
    }
    if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
});
