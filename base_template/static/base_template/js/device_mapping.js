/* Employees list: the Map and Bulk map dialogs (native <dialog>).

   The branch -> devices list comes from the page (json_script "map-branches"):
   [{id, name, devices: [{id, name}]}], only branches this viewer may map in.
   Select2 inside a modal dialog must draw its dropdown inside the dialog, or
   it opens behind it, so the dialog's selects are set up here with
   dropdownParent rather than by forms.js. */
document.addEventListener("DOMContentLoaded", function () {
    var data = document.getElementById("map-branches");
    if (!data) return;
    var branches = JSON.parse(data.textContent);
    var byId = {};
    branches.forEach(function (b) { byId[String(b.id)] = b; });
    var $ = window.jQuery;

    function today() {
        var d = new Date();
        var pad = function (n) { return (n < 10 ? "0" : "") + n; };
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    function fill(select, options) {
        select.innerHTML = "";
        options.forEach(function (o) {
            var opt = document.createElement("option");
            opt.value = o.value;
            opt.textContent = o.label;
            select.appendChild(opt);
        });
        if ($ && $.fn.select2 && $(select).data("select2")) $(select).trigger("change");
    }

    function select2In(dialog) {
        if (!($ && $.fn.select2)) return;
        dialog.querySelectorAll("select.js-select2").forEach(function (el) {
            var $el = $(el);
            if ($el.data("select2")) $el.select2("destroy");
            $el.select2({theme: "paper", width: "100%", dropdownParent: $(dialog),
                         minimumResultsForSearch: el.options.length > 8 ? 0 : Infinity});
        });
    }

    function setDate(input, value) {
        input.value = value;
        input.dispatchEvent(new Event("change", {bubbles: true}));
    }

    function open(dialog) {
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
        select2In(dialog);
    }

    document.querySelectorAll("[data-close-dialog]").forEach(function (btn) {
        btn.addEventListener("click", function () { btn.closest("dialog").close(); });
    });
    // A click on the backdrop (outside the card) closes the dialog.
    document.querySelectorAll("dialog.modal").forEach(function (dialog) {
        dialog.addEventListener("click", function (e) {
            if (e.target === dialog) dialog.close();
        });
    });

    // --- Map one employee ---
    var mapDialog = document.getElementById("map-dialog");
    function field(name) { return mapDialog.querySelector('[data-map-field="' + name + '"]'); }
    document.querySelectorAll("[data-map-employee]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var branch = byId[btn.dataset.mapBranch] || {name: "", devices: []};
            field("employee").value = btn.dataset.mapEmployee;
            field("name").textContent = btn.dataset.mapName;
            field("code").textContent = btn.dataset.mapCode || "—";
            field("branch-name").textContent = branch.name;
            fill(field("device"), branch.devices.map(function (d) {
                return {value: d.id, label: d.name};
            }));
            setDate(field("start"), today());
            open(mapDialog);
        });
    });

    // --- Bulk map a branch ---
    var bulkDialog = document.getElementById("bulk-map-dialog");
    var bulkBranch = bulkDialog.querySelector('[data-bulk-field="branch"]');
    var bulkDevice = bulkDialog.querySelector('[data-bulk-field="device"]');
    function fillBulkDevices() {
        var branch = byId[bulkBranch.value] || {devices: []};
        var n = branch.devices.length;
        fill(bulkDevice, [{value: "", label: "All devices of the branch (" + n + ")"}].concat(
            branch.devices.map(function (d) { return {value: d.id, label: d.name}; })));
    }
    if ($ && bulkBranch.tagName === "SELECT") $(bulkBranch).on("change", fillBulkDevices);
    else bulkBranch.addEventListener("change", fillBulkDevices);
    document.querySelectorAll('[data-open-dialog="bulk-map-dialog"]').forEach(function (btn) {
        btn.addEventListener("click", function () {
            fillBulkDevices();
            open(bulkDialog);
        });
    });
});
