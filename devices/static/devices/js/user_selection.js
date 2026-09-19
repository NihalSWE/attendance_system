/* Device users: tick users, then copy them to another device of the same model.

   The table is paged on the server and its rows are replaced on every page
   change, so the ticked user numbers are kept here and re-ticked when a page
   comes back; the ones not on screen are added to the form when it is sent.
   "Select all on this device" sends all=1 and lets the server take them all. */
document.addEventListener("DOMContentLoaded", function () {
    var bar = document.querySelector("[data-selection-bar]");
    if (!bar) return;
    var chosen = new Set();
    var everyone = false;
    var countEl = bar.querySelector("[data-pick-count]");
    var everyoneField = bar.querySelector("[data-pick-everyone-field]");
    var everyoneBtn = bar.querySelector("[data-pick-everyone]");
    var total = parseInt((everyoneBtn.textContent.match(/\d+/) || ["0"])[0], 10);

    function boxes() { return document.querySelectorAll("input[data-pick]"); }
    function headBox() { return document.querySelector("input[data-pick-all]"); }

    function refresh() {
        var n = everyone ? total : chosen.size;
        countEl.textContent = n;
        everyoneField.value = everyone ? "1" : "";
        everyoneBtn.hidden = everyone || n === 0;
        var show = n > 0;
        if (show && bar.hidden) {
            bar.hidden = false;
            requestAnimationFrame(function () { bar.classList.add("selection-bar--open"); });
        } else if (!show) {
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
        visible.forEach(function (b) { b.closest("tr").classList.toggle("is-picked", b.checked); });
    }

    function restore() {
        boxes().forEach(function (b) { b.checked = everyone || chosen.has(b.value); });
        refresh();
    }

    document.addEventListener("change", function (e) {
        var t = e.target;
        if (t.matches("input[data-pick]")) {
            if (t.checked) chosen.add(t.value);
            else { chosen.delete(t.value); everyone = false; }
            refresh();
        } else if (t.matches("input[data-pick-all]")) {
            boxes().forEach(function (b) {
                b.checked = t.checked;
                if (t.checked) chosen.add(b.value); else chosen.delete(b.value);
            });
            if (!t.checked) everyone = false;
            refresh();
        }
    });

    everyoneBtn.addEventListener("click", function () {
        everyone = true;
        restore();
    });
    bar.querySelector("[data-pick-clear]").addEventListener("click", function () {
        everyone = false;
        chosen.clear();
        restore();
    });

    // Rows on other pages are not in the DOM: send their numbers too.
    bar.addEventListener("submit", function () {
        bar.querySelectorAll("input[data-carried]").forEach(function (i) { i.remove(); });
        if (everyone) return;
        var onScreen = new Set(Array.prototype.map.call(boxes(), function (b) { return b.value; }));
        chosen.forEach(function (pin) {
            if (onScreen.has(pin)) return;
            var i = document.createElement("input");
            i.type = "hidden";
            i.name = "pin";
            i.value = pin;
            i.setAttribute("data-carried", "");
            bar.appendChild(i);
        });
    });

    // The server-side table swaps its rows on paging, search and sorting.
    var tbody = document.querySelector("table[data-server-table] tbody");
    if (tbody && window.MutationObserver) {
        new MutationObserver(restore).observe(tbody, {childList: true});
    }
});
