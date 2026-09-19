/* Tick rows in a list, then act on them from a bar that slides up.

   Markup: row checkboxes ``input[data-pick]`` (their name and value are what
   is sent, via form="<bar id>"), an optional header ``input[data-pick-all]``,
   and ``form[data-selection-bar]`` holding the count, the buttons and, when
   the list is paged, a "Select all N" button (``data-pick-everyone``,
   ``data-total``) that sends all=1 instead of numbers.

   Server-side tables replace their rows on paging, search and sorting, so the
   ticked values are kept here, re-ticked when a page comes back, and the ones
   not on screen are added to the form when it is sent. */
document.addEventListener("DOMContentLoaded", function () {
    var bar = document.querySelector("[data-selection-bar]");
    if (!bar) return;
    var chosen = new Set();
    var everyone = false;
    var fieldName = "pin";
    var countEl = bar.querySelector("[data-pick-count]");
    var everyoneField = bar.querySelector("[data-pick-everyone-field]");
    var everyoneBtn = bar.querySelector("[data-pick-everyone]");
    var total = everyoneBtn ? parseInt(everyoneBtn.dataset.total || "0", 10) : 0;

    function boxes() { return document.querySelectorAll("input[data-pick]"); }
    function headBox() { return document.querySelector("input[data-pick-all]"); }

    function refresh() {
        var n = everyone ? total : chosen.size;
        countEl.textContent = n;
        if (everyoneField) everyoneField.value = everyone ? "1" : "";
        if (everyoneBtn) everyoneBtn.hidden = everyone || n === 0 || n >= total;
        if (n > 0 && bar.hidden) {
            bar.hidden = false;
            requestAnimationFrame(function () { bar.classList.add("selection-bar--open"); });
        } else if (n === 0) {
            bar.classList.remove("selection-bar--open");
            bar.hidden = true;
        }
        var visible = Array.prototype.slice.call(boxes());
        var head = headBox();
        if (head) {
            var on = visible.filter(function (b) { return b.checked; }).length;
            head.checked = visible.length > 0 && on === visible.length;
            head.indeterminate = on > 0 && on < visible.length;
        }
        visible.forEach(function (b) {
            var row = b.closest("tr");
            if (row) row.classList.toggle("is-picked", b.checked);
        });
    }

    function restore() {
        boxes().forEach(function (b) {
            fieldName = b.name || fieldName;
            b.checked = everyone || chosen.has(b.value);
        });
        refresh();
    }

    document.addEventListener("change", function (e) {
        var t = e.target;
        if (t.matches("input[data-pick]")) {
            fieldName = t.name || fieldName;
            if (t.checked) chosen.add(t.value);
            else { chosen.delete(t.value); everyone = false; }
            refresh();
        } else if (t.matches("input[data-pick-all]")) {
            boxes().forEach(function (b) {
                fieldName = b.name || fieldName;
                b.checked = t.checked;
                if (t.checked) chosen.add(b.value); else chosen.delete(b.value);
            });
            if (!t.checked) everyone = false;
            refresh();
        }
    });

    if (everyoneBtn) {
        everyoneBtn.addEventListener("click", function () {
            everyone = true;
            restore();
        });
    }
    var clearBtn = bar.querySelector("[data-pick-clear]");
    if (clearBtn) {
        clearBtn.addEventListener("click", function () {
            everyone = false;
            chosen.clear();
            restore();
        });
    }

    // Rows on other pages are not in the page: send their values too.
    bar.addEventListener("submit", function () {
        bar.querySelectorAll("input[data-carried]").forEach(function (i) { i.remove(); });
        if (everyone) return;
        var onScreen = new Set(Array.prototype.map.call(boxes(), function (b) { return b.value; }));
        chosen.forEach(function (value) {
            if (onScreen.has(value)) return;
            var i = document.createElement("input");
            i.type = "hidden";
            i.name = fieldName;
            i.value = value;
            i.setAttribute("data-carried", "");
            bar.appendChild(i);
        });
    });

    var tbody = document.querySelector("table[data-server-table] tbody");
    if (tbody && window.MutationObserver) {
        new MutationObserver(restore).observe(tbody, {childList: true});
    }
});
