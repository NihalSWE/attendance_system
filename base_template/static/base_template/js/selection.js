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
    var refreshPage = document.querySelector("[data-refresh-verification-page]");
    if (refreshPage) {
        refreshPage.addEventListener("click", function () {
            var url = new URL(window.location.href);
            ["q", "table_q", "search[value]", "page", "start"].forEach(function (key) {
                url.searchParams.delete(key);
            });
            if (url.href === window.location.href) window.location.reload();
            else window.location.replace(url.href);
        });
    }
    var bar = document.querySelector("[data-selection-bar]");
    if (!bar) return;
    var chosen = new Set();
    var everyone = false;
    var fieldName = "pin";
    var countEl = bar.querySelector("[data-pick-count]");
    var everyoneField = bar.querySelector("[data-pick-everyone-field]");
    var everyoneBtn = bar.querySelector("[data-pick-everyone]");
    var total = everyoneBtn ? parseInt(everyoneBtn.dataset.total || "0", 10) : 0;
    var bulkCount = document.querySelector("[data-bulk-pick-count]");
    var table = document.querySelector("table[data-server-table]");
    var everyoneSearch = null;
    var verificationDialog = document.getElementById("bulk-verification-dialog");

    function verificationURL(button) {
        var url = new URL(button.formAction);
        if (table && table.serverTableApi) {
            url.searchParams.set("search[value]", (everyone ? everyoneSearch : table.serverTableApi.search()) || "");
        }
        return url;
    }

    function boxes() { return document.querySelectorAll("input[data-pick]"); }
    function headBox() { return document.querySelector("input[data-pick-all]"); }

    function refresh() {
        var n = everyone ? total : chosen.size;
        countEl.textContent = n;
        if (bulkCount) bulkCount.textContent = n;
        if (everyoneField) everyoneField.value = everyone ? "1" : "";
        if (everyoneBtn) everyoneBtn.hidden = everyone || n === 0 || total === 0 || (!bulkCount && n >= total);
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
            if (bulkCount && table && table.serverTableApi) everyoneSearch = table.serverTableApi.search();
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

    if (verificationDialog) {
        var verificationButton = verificationDialog.querySelector("[data-bulk-verification]");
        var methods = Array.from(verificationDialog.querySelectorAll('input[name="blocked_methods"]'));
        var help = verificationDialog.querySelector("[data-verification-help]");
        var title = verificationDialog.querySelector("#bulk-verification-title");
        var previewVersion = 0;

        function updateVerificationButton() {
            verificationButton.disabled = !methods.some(function (method) { return method.checked && !method.disabled; });
        }

        function showVerification(mode) {
            var version = ++previewVersion;
            var unblock = mode === "unblock";
            title.textContent = unblock ? "Unblock verification methods" : "Block verification methods";
            verificationButton.value = mode;
            verificationButton.textContent = unblock ? "Unblock methods" : "Block methods";
            methods.forEach(function (method) {
                method.checked = false;
                method.disabled = unblock;
                method.closest("label").querySelector("[data-blocked-count]").textContent = "";
            });
            updateVerificationButton();
            help.textContent = unblock ? "Checking blocked methods…" : "Choose which methods to block.";
            if (!unblock) return;

            var data = new FormData();
            data.set("csrfmiddlewaretoken", bar.elements.csrfmiddlewaretoken.value);
            data.set("preview", "1");
            if (everyone) data.set("all", "1");
            else chosen.forEach(function (id) { data.append("employee", id); });
            fetch(verificationURL(verificationButton), {method: "POST", body: data}).then(function (response) {
                if (!response.ok) throw new Error("Preview unavailable");
                return response.json();
            }).then(function (preview) {
                if (version !== previewVersion) return;
                if (bulkCount) bulkCount.textContent = preview.selected_count;
                var candidates = methods.filter(function (method) { return preview.counts[method.value] > 0; });
                var shared = candidates.filter(function (method) { return preview.counts[method.value] === preview.selected_count; });
                var preselected = candidates.find(function (method) { return method.value === verificationDialog.dataset.preferredMethod; });
                if (!preselected && candidates.length === 1) preselected = candidates[0];
                if (!preselected && shared.length === 1) preselected = shared[0];
                methods.forEach(function (method) {
                    var count = preview.counts[method.value];
                    method.disabled = count === 0;
                    method.checked = method === preselected;
                    method.closest("label").querySelector("[data-blocked-count]").textContent = "· " + count + " blocked";
                });
                help.textContent = candidates.length ? "Choose which blocked methods to restore."
                    : "The selected employees have no blocked methods.";
                updateVerificationButton();
            }).catch(function () {
                if (version === previewVersion) help.textContent = "Could not check blocked methods. Reopen this dialog to retry.";
            });
        }

        bar.querySelectorAll("[data-verification-mode]").forEach(function (button) {
            button.addEventListener("click", function () {
                showVerification(button.dataset.verificationMode);
                if (!verificationDialog.open) verificationDialog.showModal();
            });
        });
        verificationDialog.querySelectorAll("[data-close-dialog]").forEach(function (button) {
            button.addEventListener("click", function () { verificationDialog.close(); });
        });
        verificationDialog.addEventListener("click", function (event) {
            if (event.target === verificationDialog) verificationDialog.close();
        });
        methods.forEach(function (method) { method.addEventListener("change", updateVerificationButton); });
    }

    // Rows on other pages are not in the page: send their values too.
    bar.addEventListener("submit", function (e) {
        bar.querySelectorAll("input[data-carried]").forEach(function (i) { i.remove(); });
        var button = e.submitter;
        if (button && button.hasAttribute("data-bulk-verification")) {
            button.formAction = verificationURL(button).toString();
        }
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

    if (bulkCount && table && everyoneBtn) {
        table.addEventListener("server-table:draw", function () {
            if (!table.serverTableApi) return;
            if (everyone && everyoneSearch !== table.serverTableApi.search()) {
                everyone = false;
                chosen.clear();
            }
            total = table.serverTableApi.page.info().recordsDisplay;
            everyoneBtn.textContent = "Select all " + total;
            restore();
        });
    }
    var tbody = table && table.querySelector("tbody");
    if (tbody && window.MutationObserver) {
        new MutationObserver(restore).observe(tbody, {childList: true});
    }
});
