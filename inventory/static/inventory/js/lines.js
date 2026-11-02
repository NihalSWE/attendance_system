/* The item lines of Stock in, Move stock and Sell (2026-10-28, plan §4.2;
   inventory/templates/inventory/hub/_line.html).

   Each line follows its item: the unit list holds only the item's own units
   (its base unit first), the cost or price says "per <unit>" (the product's price shown as a
   hint), batch and expiry show only for an item that keeps
   them, and on Move / Sell the batch list and "in the store" follow the item
   and the store chosen above. "Add a line" copies the last line, empty.
   Boxes marked data-source-only show only for that "Where it comes from". */
(function () {
    const form = document.querySelector("form[data-lines-form]");
    if (!form) return;
    const balances = JSON.parse((document.getElementById("stock-rows") || {}).textContent || "[]");
    const most = Number(form.dataset.most || 30);

    function chosen(select) {
        return select && select.options[select.selectedIndex];
    }

    function store() {
        const box = form.querySelector('[name="from_store"], [name="store"]');
        return box ? box.value : "";
    }

    function source() {
        const checked = form.querySelector('[name="source"]:checked');
        return checked ? checked.value : "";
    }

    function follow(line) {
        const item = chosen(line.querySelector("[data-line-item]"));
        const units = item && item.value ? JSON.parse(item.dataset.units || "[]") : [];
        const unitBox = line.querySelector("[data-line-unit]");
        const keep = unitBox.value;
        unitBox.innerHTML = "";
        units.forEach(function (unit) {
            const option = new Option(unit.code, unit.id);
            option.dataset.price = unit.price;
            option.dataset.factor = unit.factor;
            unitBox.appendChild(option);
        });
        if (units.some(unit => String(unit.id) === keep)) unitBox.value = keep;
        unitBox.closest(".ui-line__unit").hidden = units.length <= 1;
        const unit = chosen(unitBox);
        const code = unit ? unit.text : "—";
        line.querySelectorAll("[data-per-unit]").forEach(label => { label.textContent = "per " + code; });
        line.querySelectorAll("[data-qty-unit]").forEach(label => { label.textContent = units.length === 1 ? code : ""; });
        const keepsBatches = Boolean(item && item.dataset.batch);
        const now = source();
        line.querySelectorAll("[data-needs-batch]").forEach(function (field) {
            const wrongSource = field.dataset.sourceOnly && now && !field.dataset.sourceOnly.split(",").includes(now);
            field.hidden = !keepsBatches || wrongSource ||
                (field.dataset.needsBatch === "expiry" && !(item && item.dataset.expiry));
        });
        // The product's own price as a hint only: left empty, the customer's
        // price list decides (then the pack's price, then the product's).
        // Stock in's sale price box says what the price is now (2026-10-31).
        const sale = line.querySelector("[data-line-sale]");
        if (sale) sale.placeholder = unit && unit.dataset.price ? "now " + unit.dataset.price : "not set yet";
        const price = line.querySelector("[data-line-price]");
        if (price) price.placeholder = unit && unit.dataset.price ? unit.dataset.price + " (product price)" : "the customer's price";
        const batchBox = line.querySelector("[data-line-batch]");
        const rows = balances.filter(row => item && String(row.item) === item.value && String(row.store) === store());
        if (batchBox) {
            const was = batchBox.value;
            batchBox.innerHTML = "";
            batchBox.appendChild(new Option("The one that expires first", ""));
            rows.filter(row => row.batch).forEach(function (row) {
                batchBox.appendChild(new Option(row.label + " · " + row.qty, row.batch));
            });
            batchBox.value = Array.from(batchBox.options).some(o => o.value === was) ? was : "";
            batchBox.closest(".field").hidden = !keepsBatches;
        }
        const have = line.querySelector("[data-line-have]");
        if (have) {
            const total = rows.reduce((sum, row) => sum + Number(row.qty), 0);
            have.textContent = item && item.value ? "In the store: " + total.toLocaleString() + " " + (units[0] ? units[0].code : "") : "";
        }
    }

    // Stock in fills the line from the item (2026-11-01): the unit it is
    // bought in, its cost (the last price paid, else the expected buy price),
    // and above, its usual store and last supplier; the expiry it will get
    // from the shelf life is said under the box. Nothing typed is replaced.
    function setSelect(box, value) {
        if (!box || !value || box.value) return;
        if (!Array.from(box.options).some(option => option.value === String(value))) return;
        box.value = String(value);
        if (window.jQuery) window.jQuery(box).trigger("change");
        else box.dispatchEvent(new Event("change", {bubbles: true}));
    }

    function cost(line) {
        const box = line.querySelector("[data-line-cost]");
        if (!box) return;
        const item = chosen(line.querySelector("[data-line-item]"));
        const unit = chosen(line.querySelector("[data-line-unit]"));
        const help = line.querySelector("[data-cost-help]");
        if (!item || !item.value || !item.dataset.cost) {
            if (help) help.textContent = "";
            if (!box.dataset.typed) box.value = "";
            return;
        }
        const value = Number(item.dataset.cost) * Number((unit && unit.dataset.factor) || 1);
        if (!box.dataset.typed) box.value = String(Math.round(value * 100) / 100);
        if (help) help.textContent = "From the " + item.dataset.costFrom + "; change it if the bill says otherwise.";
    }

    function expiry(line) {
        const help = line.querySelector("[data-expiry-help]");
        if (!help) return;
        const item = chosen(line.querySelector("[data-line-item]"));
        const days = item ? Number(item.dataset.shelf) : 0;
        const made = line.querySelector('[name^="mfg_"]');
        if (!days) {
            help.textContent = "No shelf life on the item: give the expiry date.";
            return;
        }
        if (made && made.value) {
            const day = new Date(made.value + "T00:00:00");
            day.setDate(day.getDate() + days);
            help.textContent = "Empty: " + day.toLocaleDateString("en-GB", {day: "2-digit", month: "short", year: "numeric"})
                + " (manufacture date + " + days + " days).";
        } else {
            help.textContent = "Empty: the manufacture date + " + days + " days.";
        }
    }

    function fill(line) {
        follow(line);
        const item = chosen(line.querySelector("[data-line-item]"));
        if (item && item.value) {
            const unitBox = line.querySelector("[data-line-unit]");
            if (item.dataset.buyUnit && Array.from(unitBox.options).some(option => option.value === item.dataset.buyUnit)) {
                unitBox.value = item.dataset.buyUnit;
                follow(line);
            }
            setSelect(form.querySelector('[name="store"]'), item.dataset.store);
            if (source() === "purchase") setSelect(form.querySelector('[name="supplier"]'), item.dataset.supplier);
        }
        cost(line);
        expiry(line);
    }

    function showSource() {
        const now = source();
        form.querySelectorAll("[data-source-only]:not([data-needs-batch])").forEach(function (box) {
            box.hidden = now && !box.dataset.sourceOnly.split(",").includes(now);
        });
        form.querySelectorAll("[data-line]").forEach(follow);
    }

    function renumber(line, n) {
        line.dataset.line = n;
        line.querySelectorAll("[name]").forEach(function (box) {
            box.name = box.name.replace(/_\d+$/, "_" + n);
            if (box.id) box.id = box.id.replace(/_\d+$/, "_" + n);
            if (box.tagName === "SELECT") box.selectedIndex = 0; else box.value = "";
            delete box.dataset.typed;
        });
        line.querySelectorAll("label[for]").forEach(label => { label.htmlFor = label.htmlFor.replace(/_\d+$/, "_" + n); });
        line.querySelectorAll(".field__error").forEach(error => error.remove());
        line.querySelectorAll(".ui-field--error").forEach(field => field.classList.remove("ui-field--error"));
        const title = line.querySelector("[data-line-title]");
        if (title) title.textContent = "Line " + n;
    }

    form.addEventListener("change", function (event) {
        const line = event.target.closest("[data-line]");
        if (event.target.matches('[name="source"]')) showSource();
        if (event.target.matches('[name="from_store"], [name="store"]')) form.querySelectorAll("[data-line]").forEach(follow);
        if (line && event.target.matches("[data-line-item]")) fill(line);
        else if (line && event.target.matches("[data-line-unit]")) { follow(line); cost(line); }
        if (line && event.target.matches('[name^="mfg_"]')) expiry(line);
    });
    form.addEventListener("input", function (event) {
        const line = event.target.closest("[data-line]");
        if (event.isTrusted && event.target.matches("[data-line-cost]")) event.target.dataset.typed = "1";
        if (line && event.target.matches('[name^="mfg_"]')) expiry(line);
    });
    if (window.jQuery) {
        window.jQuery(form).on("select2:select", function (event) {
            const line = event.target.closest("[data-line]");
            if (event.target.matches('[name="from_store"], [name="store"]')) form.querySelectorAll("[data-line]").forEach(follow);
            if (line && event.target.matches("[data-line-item]")) fill(line);
            else if (line) { follow(line); cost(line); }
        });
    }
    const add = form.querySelector("[data-add-line]");
    if (add) {
        add.addEventListener("click", function () {
            const lines = form.querySelectorAll("[data-line]");
            if (lines.length >= most) return;
            const copy = lines[lines.length - 1].cloneNode(true);
            // A copied calendar has no life of its own: back to the plain
            // date box, then a calendar of its own (datepicker.js).
            copy.querySelectorAll(".dp").forEach(function (wrap) {
                const input = wrap.querySelector("input");
                input.classList.remove("dp__native");
                input.removeAttribute("tabindex");
                wrap.replaceWith(input);
            });
            renumber(copy, lines.length + 1);
            lines[lines.length - 1].after(copy);
            if (window.attachDatepicker) copy.querySelectorAll("input[data-datepicker]").forEach(window.attachDatepicker);
            fill(copy);
            if (lines.length + 1 >= most) add.hidden = true;
        });
    }
    showSource();
    form.querySelectorAll("[data-line]").forEach(function (line) {
        const item = chosen(line.querySelector("[data-line-item]"));
        if (item && item.value) fill(line); else follow(line);
    });
})();
