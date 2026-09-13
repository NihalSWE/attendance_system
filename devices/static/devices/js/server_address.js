/* Live status for a server address change.

   A change is not something the software can make happen: it waits for the
   device to poll, apply the setting and come back at the new address. That can
   take a minute or two, and the page has to say which of those is happening —
   a generic spinner would hide the one thing the reader needs to know, which
   is whether the terminal has been touched yet.

   So this polls a small JSON endpoint and rewrites the message, the detail
   line and the recovery instructions in place. The panel is already correct
   when the page is served; without this file it is simply not live, and a
   refresh shows the same thing. Polling stops as soon as the change reaches a
   final state, so a finished page is not asking the server anything. */
document.addEventListener("DOMContentLoaded", function () {
    const panel = document.querySelector("[data-address-panel]");
    if (!panel) return;

    const url = panel.dataset.statusUrl;
    if (!url) return;

    const live = panel.querySelector("[data-address-live]");
    const alertBox = panel.querySelector("[data-address-alert]");
    const message = panel.querySelector("[data-address-message]");
    const detail = panel.querySelector("[data-address-detail]");
    const saved = panel.querySelector("[data-address-saved]");
    if (!live || !alertBox || !message || !detail) return;

    const POLL_MS = 4000;
    let timer = null;
    /* Set only by a poll that came back still running. A finished state can
       therefore never trigger a reload on its own — which is what turned a
       failed change into an endless refresh loop: the page rendered a finished
       "unreachable" state, polled once, saw `finished`, reloaded, and did the
       whole thing again forever. */
    let sawRunning = false;
    let reloading = false;

    /* Which alert colour each state deserves. Named states rather than a
       success/failure pair, because "nothing was sent" and "the device is
       lost" are both failures and need very different reactions. */
    const TONE = {
        checking: "alert--info",
        queued: "alert--info",
        delivered: "alert--info",
        acknowledged: "alert--info",
        confirmed: "alert--success",
        unreachable: "alert--warning",
        not_applied: "alert--warning",
        cancelled: "alert--info",
        lost: "alert--danger"
    };

    function setTone(status) {
        Object.values(TONE).forEach(function (cls) {
            alertBox.classList.remove(cls);
        });
        alertBox.classList.add(TONE[status] || "alert--info");
    }

    function describe(data) {
        if (data.reason) return data.reason;
        let text = "Changing to " + data.new_address;
        if (data.previous_address) text += ", from " + data.previous_address;
        return text + ".";
    }

    function renderRecovery(data) {
        const existing = panel.querySelector("[data-address-recovery]");
        if (!data.recovery) {
            if (existing) existing.remove();
            return;
        }
        if (existing) return;

        /* Built as elements rather than innerHTML: the values are an address
           and a port, but they came off a form, and a template string here
           would be an injection point for no benefit. */
        const rows = [
            ["On the device, go to", data.recovery.menu_path],
            ["Set the server address back to", data.recovery.address],
            ["Set the server port back to", data.recovery.port],
            ["Enable domain name", data.recovery.domain_name_setting]
        ];
        const wrap = document.createElement("div");
        wrap.className = "table-wrap";
        wrap.setAttribute("data-address-recovery", "");
        const table = document.createElement("table");
        table.className = "table";
        const body = document.createElement("tbody");
        rows.forEach(function (pair) {
            const tr = document.createElement("tr");
            const label = document.createElement("td");
            label.textContent = pair[0];
            const value = document.createElement("td");
            const strong = document.createElement("strong");
            strong.textContent = pair[1];
            value.appendChild(strong);
            tr.appendChild(label);
            tr.appendChild(value);
            body.appendChild(tr);
        });
        table.appendChild(body);
        wrap.appendChild(table);
        detail.insertAdjacentElement("afterend", wrap);
    }

    function render(data) {
        if (!data.status) {
            live.hidden = true;
            return;
        }
        live.hidden = false;
        setTone(data.status);
        message.textContent = data.message;
        detail.textContent = describe(data);
        renderRecovery(data);

        if (saved && data.saved_address) {
            const strong = document.createElement("strong");
            strong.textContent = data.saved_address;
            saved.replaceChildren(strong);
        }

        const next = panel.querySelector("[data-address-next]");
        if (next) next.hidden = !data.active;

        if (!data.finished) {
            sawRunning = true;
            return;
        }
        /* Finished. Stop asking, and reload only if we watched it finish, so
           the history table and the edit form catch up. Landing on a page that
           was already finished reloads nothing. */
        if (timer !== null) {
            window.clearInterval(timer);
            timer = null;
        }
        if (sawRunning && !reloading) {
            reloading = true;
            window.location.reload();
        }
    }

    function poll() {
        fetch(url, {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(function (response) {
                return response.ok ? response.json() : null;
            })
            .then(function (data) {
                if (data) render(data);
            })
            /* A failed poll is not worth shouting about: the server-rendered
               panel is still on screen and the next tick may succeed. */
            .catch(function () {});
    }

    /* Straight from the server, not guessed from a CSS class. Only a change
       that is still moving is worth watching; every finished state — reachable
       or not, applied or not — is already fully rendered. */
    if (panel.dataset.addressActive === "1") {
        timer = window.setInterval(poll, POLL_MS);
        poll();
    }
});
