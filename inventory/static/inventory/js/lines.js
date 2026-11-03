/* The item lines of Stock in, Move stock and Sell (2026-10-28, plan §4.2;
   inventory/templates/inventory/hub/_line.html).

   Each line follows its item: the unit list holds only the item's own units
   (its base unit first), the cost or price says "per <unit>" (the product's price shown as a
   hint), batch and expiry show only for an item that keeps
   them, and on Move / Sell the batch list and "in the store" follow the item
   and the store chosen above. "Add a line" copies the last line, empty.
   Boxes marked data-source-only show only for that "Where it comes from".

   2026-11-02: once a store is chosen the item lists offer only what belongs
   there - on Sell and Move what is in stock in it; on Stock in the items kept
   in it (their usual store, else a store of their type, or stock already
   there). A purchase shows each line's quantity x cost, the subtotal, the VAT
   and the total. Stock waiting for a quality check is said apart: it cannot
   be sold or moved until it passes.

   2026-11-03: and the delivery cost. The delivery - and the VAT, unless the
   company claims it back - is shared over the lines by their value, and each
   line says what one really costs: that is what goes into stock
   (purchasing/landed.py works it out again when it is saved). */
(function () {
    const form = document.querySelector("form[data-lines-form]");
    if (!form) return;
    const balances = JSON.parse((document.getElementById("stock-rows") || {}).textContent || "[]");
    const most = Number(form.dataset.most || 30);
    const mode = form.dataset.linesMode || "";
    const storeKinds = JSON.parse((document.getElementById("store-kinds") || {}).textContent || "{}");
    const TYPED_STORES = ["raw", "packaging", "finished", "spare"];
    const firstItems = form.querySelector("[data-line-item]");
    const allItems = firstItems ? Array.from(firstItems.options).map(option => option.cloneNode(true)) : [];
    const $ = window.jQuery;

    function refresh(select) {
        if ($ && $.fn.select2 && $(select).data("select2")) $(select).trigger("change.select2");
    }

    function money(value) {
        return value.toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }

    function fits(option, storeId) {
        if (!option.value || !storeId) return true;
        const there = balances.some(row => String(row.item) === option.value && String(row.store) === storeId);
        if (mode !== "in") return there;
        if (there) return true;
        if (option.dataset.store) return option.dataset.store === storeId;
        const kind = storeKinds[storeId];
        return !TYPED_STORES.includes(kind) || kind === option.dataset.kind;
    }

    function onlyThisStore() {
        if (!allItems.length || !mode) return;
        const storeId = store();
        form.querySelectorAll("[data-line-item]").forEach(function (select) {
            const keep = select.value;
            select.innerHTML = "";
            allItems.forEach(function (option) {
                // A line already filled keeps its item.
                if (fits(option, storeId) || option.value === keep) select.appendChild(option.cloneNode(true));
            });
            select.value = keep;
            refresh(select);
        });
        const note = form.querySelector("[data-store-note]");
        if (note) {
            const shown = allItems.filter(option => option.value && fits(option, storeId)).length;
            note.textContent = storeId ? shown + " item" + (shown === 1 ? "" : "s") + " in the item lists for this store." : "";
        }
    }

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
        refresh(unitBox);
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
            const base = units[0] ? " " + units[0].code : "";
            const held = rows.filter(row => row.held).reduce((sum, row) => sum + Number(row.qty), 0);
            const ready = rows.filter(row => !row.held).reduce((sum, row) => sum + Number(row.qty), 0);
            have.textContent = "";
            if (item && item.value && store()) {
                have.append((mode === "sell" ? "Ready to sell: " : "In the store: ") + ready.toLocaleString() + base);
                if (held) {
                    // Made or bought stock not checked yet (2026-11-02): said here, not only when refused.
                    have.append(" · " + held.toLocaleString() + base + " waits for a quality check: ");
                    const qc = form.dataset.qcUrl;
                    if (qc) {
                        const link = document.createElement("a");
                        link.href = qc;
                        link.textContent = "pass it first";
                        have.append(link);
                    }
                }
            }
        }
    }

    // A purchase's bill (2026-11-02): each line, the subtotal, VAT, delivery
    // (2026-11-03) and the total paid; each line's real cost.
    let vatFrom = "";
    function bill() {
        const box = form.querySelector("[data-stock-total]");
        let subtotal = 0;
        const rows = [];
        form.querySelectorAll("[data-line]").forEach(function (line) {
            const out = line.querySelector("[data-line-total]");
            if (!out) return;
            const item = chosen(line.querySelector("[data-line-item]"));
            const unit = chosen(line.querySelector("[data-line-unit]"));
            const qty = Number((line.querySelector('[name^="qty_"]') || {}).value || 0);
            const cost = Number((line.querySelector("[data-line-cost]") || {}).value || 0);
            const amount = item && item.value ? qty * cost : 0;
            out.hidden = !(amount > 0) || source() !== "purchase";
            rows.push({ out: out, unit: unit ? unit.text : "", qty: qty, cost: cost, amount: amount });
            subtotal += amount;
        });
        const lineText = function (row) {
            return "Line total: " + row.qty.toLocaleString() + " " + row.unit + " × " + money(row.cost) + " = " +
                money(row.amount) + " Tk";
        };
        if (!box) {
            rows.forEach(function (row) { row.out.textContent = lineText(row); });
            return;
        }
        const rate = form.querySelector('[data-vat="vat_rate"]');
        const amount = form.querySelector('[data-vat="vat_amount"]');
        const by = form.querySelector("[data-vat-by]");
        if (by) by.value = vatFrom;
        let vat = 0;
        if (vatFrom === "amount" && amount.value !== "") {
            vat = Number(amount.value);
            rate.value = subtotal ? String(Math.round(vat / subtotal * 10000) / 100) : "";
        } else if (rate.value !== "") {
            vat = subtotal * Number(rate.value) / 100;
            amount.value = String(Math.round(vat * 100) / 100);
        } else if (amount.value !== "") {
            vat = Number(amount.value);
        }
        const deliveryBox = form.querySelector("[data-delivery]");
        const delivery = Number((deliveryBox || {}).value || 0);
        const extra = delivery + (box.dataset.vatInCost === "1" ? vat : 0);
        // Each line carries the extra costs in proportion to its value.
        rows.forEach(function (row) {
            let text = lineText(row);
            if (extra > 0 && subtotal > 0 && row.amount > 0) {
                text += " · real cost " + money(row.cost * (1 + extra / subtotal)) + " Tk each";
            }
            row.out.textContent = text;
        });
        box.querySelector("[data-bill-subtotal]").textContent = money(subtotal);
        box.querySelector("[data-bill-vat]").textContent = money(vat) + (rate.value ? " (" + rate.value + " %)" : "");
        box.querySelector("[data-bill-delivery]").textContent = money(delivery);
        box.querySelector("[data-bill-total]").textContent = money(subtotal + vat + delivery);
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
            onlyThisStore();
            if (source() === "purchase") setSelect(form.querySelector('[name="supplier"]'), item.dataset.supplier);
        }
        cost(line);
        expiry(line);
        bill();
    }

    function showSource() {
        const now = source();
        form.querySelectorAll("[data-source-only]:not([data-needs-batch])").forEach(function (box) {
            box.hidden = now && !box.dataset.sourceOnly.split(",").includes(now);
        });
        form.querySelectorAll("[data-line]").forEach(follow);
        bill();
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
        if (event.target.matches('[name="from_store"], [name="store"]')) {
            onlyThisStore();
            form.querySelectorAll("[data-line]").forEach(follow);
        }
        if (line && event.target.matches("[data-line-item]")) fill(line);
        else if (line && event.target.matches("[data-line-unit]")) { follow(line); cost(line); bill(); }
        if (line && event.target.matches('[name^="mfg_"]')) expiry(line);
    });
    form.addEventListener("input", function (event) {
        const line = event.target.closest("[data-line]");
        if (event.isTrusted && event.target.matches("[data-line-cost]")) event.target.dataset.typed = "1";
        if (line && event.target.matches('[name^="mfg_"]')) expiry(line);
        if (event.target.matches('[data-vat="vat_rate"]')) vatFrom = "rate";
        if (event.target.matches('[data-vat="vat_amount"]')) vatFrom = "amount";
        if (event.target.matches('[name^="qty_"], [data-line-cost], [data-vat], [data-delivery]')) bill();
    });
    if (window.jQuery) {
        window.jQuery(form).on("select2:select", function (event) {
            const line = event.target.closest("[data-line]");
            if (event.target.matches('[name="from_store"], [name="store"]')) {
                onlyThisStore();
                form.querySelectorAll("[data-line]").forEach(follow);
            }
            if (line && event.target.matches("[data-line-item]")) fill(line);
            else if (line) { follow(line); cost(line); bill(); }
        });
    }
    const add = form.querySelector("[data-add-line]");
    if (add) {
        add.addEventListener("click", function () {
            const lines = form.querySelectorAll("[data-line]");
            if (lines.length >= most) return;
            const copy = lines[lines.length - 1].cloneNode(true);
            // A copied search list has no life of its own: back to the plain
            // list, then a Select2 of its own (searchable.js).
            copy.querySelectorAll(".select2-container").forEach(container => container.remove());
            copy.querySelectorAll("select").forEach(function (select) {
                select.classList.remove("select2-hidden-accessible");
                ["data-select2-id", "aria-hidden", "tabindex"].forEach(name => select.removeAttribute(name));
                select.querySelectorAll("option").forEach(option => option.removeAttribute("data-select2-id"));
            });
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
            if (window.inventorySelect2) copy.querySelectorAll("select.js-select2").forEach(window.inventorySelect2);
            fill(copy);
            if (lines.length + 1 >= most) add.hidden = true;
        });
    }
    showSource();
    onlyThisStore();
    form.querySelectorAll("[data-line]").forEach(function (line) {
        const item = chosen(line.querySelector("[data-line-item]"));
        if (item && item.value) fill(line); else follow(line);
    });
})();
