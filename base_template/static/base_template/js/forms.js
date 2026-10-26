/* Shared controls follow field semantics, not the number of options. */
document.addEventListener("DOMContentLoaded", function () {
    const $ = window.jQuery;
    // Search by what is shown or by the option's data-code: an employee is
    // found by their Employee ID even where only the name shows (2026-10-26).
    function matches(params, data) {
        const term = $.trim(params.term || "").toLowerCase();
        if (!term) return data;
        if (data.children && data.children.length) {
            const kids = data.children.map(child => matches(params, child)).filter(Boolean);
            if (!kids.length) return null;
            const copy = $.extend({}, data, true);
            copy.children = kids;
            return copy;
        }
        const text = (data.text || "").toLowerCase();
        const code = (data.element && data.element.getAttribute("data-code") || "").toLowerCase();
        return text.indexOf(term) > -1 || (code && code.indexOf(term) > -1) ? data : null;
    }
    if ($ && $.fn.select2) {
        $(".js-select2").each(function () {
            var $el = $(this);
            var count = $el.find("option").length;
            // In a modal <dialog> the list opens on the dialog, not behind it.
            var $dialog = $el.closest("dialog");
            $el.select2({
                dropdownParent: $dialog.length ? $dialog : $(document.body),
                theme: "paper",
                width: "100%",
                // A short fixed list needs no search box; a long or
                // database-backed one does.
                // data-search="always": a search box however short the list.
                minimumResultsForSearch: ($el.data("search") === "always" || count > 8) ? 0 : Infinity,
                matcher: matches,
                closeOnSelect: !$el.prop("multiple"),
                placeholder: $el.data("placeholder") || null,
                allowClear: !$el.prop("required") && !$el.prop("multiple")
            });
        });
    }
    // Lists that follow another field (2026-10-26; inventory/forms.py
    // CompanyForm.link_choices). A Unit list offers only the chosen item's
    // units (its own unit chosen, the list hidden when that is the only one);
    // a Batch or Bin list only the chosen item's batches or store's bins.
    function follow(child, parentName, allowed, preferred) {
        const form = child.form || child.closest("form");
        const parent = form && form.querySelector('[name="' + parentName + '"]');
        if (!parent) return;
        const all = Array.from(child.options).map(option => option.cloneNode(true));
        const field = child.closest(".field");
        const update = function (keepValue) {
            const chosen = parent.options[parent.selectedIndex];
            const keep = allowed(chosen);
            const current = child.value;
            child.innerHTML = "";
            all.forEach(function (option) {
                if (!option.value || keep === null || keep(option)) child.appendChild(option.cloneNode(true));
            });
            const values = Array.from(child.options).map(option => option.value).filter(Boolean);
            const wanted = keepValue && values.includes(current) ? current : preferred(chosen, values);
            child.value = wanted || "";
            if (field && child.dataset.unitsOf) field.hidden = Boolean(chosen && chosen.value) && values.length <= 1;
            if ($ && $.fn.select2 && $(child).data("select2")) $(child).trigger("change.select2");
            child.dispatchEvent(new Event("change", {bubbles: true}));
        };
        parent.addEventListener("change", () => update(false));
        if ($) $(parent).on("select2:select select2:clear", () => update(false));
        update(true);
    }
    document.querySelectorAll("select[data-units-of]").forEach(function (unit) {
        follow(unit, unit.dataset.unitsOf,
               chosen => chosen && chosen.dataset.units
                   ? (allowed => option => allowed.includes(option.value))(chosen.dataset.units.split(","))
                   : null,
               (chosen, values) => chosen && chosen.dataset.baseUnit && values.includes(chosen.dataset.baseUnit)
                   ? chosen.dataset.baseUnit : (values.length === 1 ? values[0] : ""));
    });
    document.querySelectorAll("select[data-follows]").forEach(function (child) {
        const by = child.dataset.followBy;
        follow(child, child.dataset.follows,
               chosen => chosen && chosen.value ? option => option.dataset[by] === chosen.value : null,
               () => "");
    });
    // Batch boxes only for an item that keeps batches or expires (2026-10-26;
    // inventory/forms.py BATCH_ENTRY): hidden and emptied for one that does not.
    document.querySelectorAll("[data-item-needs]").forEach(function (input) {
        const form = input.form || input.closest("form");
        const item = form && form.querySelector('select[name="item"]');
        const field = input.closest(".field");
        if (!item || !field) return;
        const update = function () {
            const chosen = item.options[item.selectedIndex];
            const needed = !chosen || !chosen.value || chosen.dataset[input.dataset.itemNeeds] === "1";
            field.hidden = !needed;
            if (!needed && input.value) {
                input.value = "";
                input.dispatchEvent(new Event("change", {bubbles: true}));
            }
        };
        item.addEventListener("change", update);
        if ($) $(item).on("select2:select select2:clear", update);
        update();
    });
    // A tick that needs another tick (2026-10-26): "Has an expiry date" ticks
    // "Keeps batches", and that one cannot be cleared while the first stays.
    document.querySelectorAll("input[type=checkbox][data-ticks]").forEach(function (box) {
        const other = box.form && box.form.querySelector('[name="' + box.dataset.ticks + '"]');
        if (!other) return;
        const sync = function () {
            if (box.checked && !other.checked) {
                other.checked = true;
                other.dispatchEvent(new Event("change", {bubbles: true}));
            }
        };
        box.addEventListener("change", sync);
        other.addEventListener("change", function () {
            if (!other.checked && box.checked) {
                box.checked = false;
                box.dispatchEvent(new Event("change", {bubbles: true}));
            }
        });
        sync();
    });
    document.querySelectorAll("input[data-phone-prefix]").forEach(function (input) {
        function normalize() {
            let digits = input.value.replace(/[^0-9]/g, "");
            if (digits.startsWith("0088")) digits = digits.slice(2);
            if (!digits.startsWith("88")) digits = "88" + digits;
            input.value = "+" + digits.slice(0, 15);
        }
        input.addEventListener("input", normalize);
        input.addEventListener("blur", normalize);
        normalize();
    });
    document.addEventListener("keydown", function (event) {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || input.type !== "number") return;
        if (event.ctrlKey || event.metaKey || event.altKey || event.key.length !== 1) return;
        const decimal = input.step === "any" || (input.step && Number(input.step) % 1 !== 0);
        const signed = input.min === "" || Number(input.min) < 0;
        if (/^[0-9]$/.test(event.key)) return;
        if (decimal && event.key === "." && !input.value.includes(".")) return;
        if (signed && event.key === "-" && !input.value.includes("-")) return;
        event.preventDefault();
    });
    document.addEventListener("paste", function (event) {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || input.type !== "number") return;
        const text = event.clipboardData.getData("text").trim();
        const decimal = input.step === "any" || (input.step && Number(input.step) % 1 !== 0);
        const valid = decimal ? /^-?[0-9]+(\.[0-9]+)?$/.test(text) : /^-?[0-9]+$/.test(text);
        if (!valid || (text.startsWith("-") && input.min !== "" && Number(input.min) >= 0)) event.preventDefault();
    });
});
