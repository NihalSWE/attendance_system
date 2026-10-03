/* ERP webhook page (2026-10-01): Copy puts the new secret key on the clipboard. */
document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const source = document.querySelector(button.dataset.copy);
    if (!source) return;
    const text = source.textContent.trim();
    const done = function () {
        const label = button.textContent;
        button.textContent = "Copied";
        setTimeout(function () { button.textContent = label; }, 1800);
    };
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done);
        return;
    }
    // Plain http (a local server): select it, so Ctrl+C copies.
    const range = document.createRange();
    range.selectNodeContents(source);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    try {
        document.execCommand("copy");
        done();
    } catch (error) {
        button.textContent = "Press Ctrl+C";
    }
});

/* Debug messages (2026-10-03): shown as they come, as words or as JSON, for
   15 minutes; then the card says so and the messages are gone. */
document.addEventListener("DOMContentLoaded", function () {
    const card = document.querySelector("#webhook-debug[data-debug-active='1']");
    if (!card) return;
    const list = card.querySelector("[data-debug-list]");
    const pre = card.querySelector("[data-debug-json]");
    const empty = card.querySelector("[data-debug-empty]");
    const left = card.querySelector("[data-debug-left]");
    const copy = card.querySelector("[data-debug-copy]");
    const views = card.querySelectorAll("[data-debug-view]");
    let view = "messages";
    let entries = [];
    let seconds = Number(left ? left.dataset.seconds : 0);
    const openIds = new Set();

    function clock() {
        if (!left) return;
        const m = Math.floor(seconds / 60), s = seconds % 60;
        left.textContent = "On - " + m + ":" + String(s).padStart(2, "0") + " left";
    }

    function badge(ok) {
        const span = document.createElement("span");
        span.className = "badge " + (ok === true ? "badge--success" : ok === false ? "badge--danger" : "badge--neutral");
        span.textContent = ok === true ? "Success" : ok === false ? "Failed" : "Info";
        return span;
    }

    function entryNode(entry) {
        const node = document.createElement("article");
        node.className = "debug-entry" + (entry.ok === true ? " debug-entry--ok" : entry.ok === false ? " debug-entry--fail" : "");
        const head = document.createElement("div");
        head.className = "debug-entry__head";
        const what = document.createElement("strong");
        what.textContent = entry.what;
        const at = document.createElement("time");
        at.textContent = entry.at;
        head.append(badge(entry.ok), what, at);
        const message = document.createElement("p");
        message.className = "debug-entry__message";
        message.textContent = entry.message;
        node.append(head, message);
        if (entry.detail && Object.keys(entry.detail).length) {
            const details = document.createElement("details");
            details.open = openIds.has(entry.id);
            details.addEventListener("toggle", function () {
                if (details.open) openIds.add(entry.id); else openIds.delete(entry.id);
            });
            const summary = document.createElement("summary");
            summary.textContent = "Details (JSON)";
            const code = document.createElement("pre");
            code.textContent = JSON.stringify(entry.detail, null, 2);
            details.append(summary, code);
            node.append(details);
        }
        return node;
    }

    function paint() {
        empty.hidden = entries.length > 0;
        list.hidden = view !== "messages";
        pre.hidden = view !== "json" || !entries.length;
        copy.hidden = view !== "json" || !entries.length;
        if (view === "messages") {
            list.replaceChildren.apply(list, entries.map(entryNode));
        } else {
            pre.textContent = JSON.stringify(entries, null, 2);
        }
        views.forEach(function (button) {
            const on = button.dataset.debugView === view;
            button.setAttribute("aria-pressed", on ? "true" : "false");
            button.classList.toggle("btn--primary", on);
            button.classList.toggle("btn--ghost", !on);
        });
    }

    function finish() {
        // Time is up: the messages are gone; reload shows the card switched off.
        window.location.reload();
    }

    function load(state) {
        if (!state.active) { finish(); return; }
        seconds = state.seconds_left;
        clock();
        const changed = state.entries.length !== entries.length
            || (state.entries[0] || {}).id !== (entries[0] || {}).id;
        entries = state.entries;
        if (changed) paint();
    }

    views.forEach(function (button) {
        button.addEventListener("click", function () { view = button.dataset.debugView; paint(); });
    });
    copy.addEventListener("click", function () {
        const text = pre.textContent;
        const done = function () {
            copy.textContent = "Copied";
            setTimeout(function () { copy.textContent = "Copy JSON"; }, 1800);
        };
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done);
        } else {
            const range = document.createRange();
            range.selectNodeContents(pre);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            copy.textContent = "Press Ctrl+C";
        }
    });

    const first = document.getElementById("webhook-debug-data");
    load(first ? JSON.parse(first.textContent) : {active: true, seconds_left: seconds, entries: []});
    paint();
    setInterval(function () {
        // The countdown only shows; the server says when it is over (next poll).
        if (seconds > 0) { seconds -= 1; clock(); }
    }, 1000);
    setInterval(function () {
        fetch(card.dataset.debugUrl, {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (state) { if (state) load(state); })
            .catch(function () { /* the next tick tries again */ });
    }, 3000);
});
