/* Keep the "Now" column current without reloading the page.

   The column is already correct when the page is served — the view derives it
   from today's scans — so without this file it is simply a snapshot of the
   moment you opened the page. What this adds is that it keeps up: somebody
   scanning out for lunch changes their badge within the minute.

   Once a minute, not once a second. The underlying data only moves when
   somebody walks past a terminal, and a page left open on a wall display
   should not be asking a question sixty times more often than the answer can
   change. */
document.addEventListener("DOMContentLoaded", function () {
    const board = document.querySelector("[data-now-board]");
    if (!board) return;

    const url = board.dataset.nowUrl;
    const employees = board.dataset.nowEmployees || "";
    if (!url || !employees) return;

    const EVERY_MS = 60000;

    function paint(cell, status) {
        /* Built as elements rather than innerHTML: the label is ours, but the
           cell is rewritten sixty times an hour and a template string here
           would be the one place an injection could hide. */
        const badge = document.createElement("span");
        badge.className = "badge badge--" + status.tone;
        badge.textContent = status.label;
        cell.replaceChildren(badge);

        if (status.since) {
            const since = document.createElement("div");
            since.className = "t-faint";
            since.textContent = "since " + status.since;
            cell.appendChild(since);
        }
    }

    function poll() {
        fetch(url + "?employees=" + encodeURIComponent(employees), {
            headers: {"X-Requested-With": "XMLHttpRequest"}
        })
            .then(function (response) {
                return response.ok ? response.json() : null;
            })
            .then(function (data) {
                if (!data || !data.employees) return;
                Object.keys(data.employees).forEach(function (id) {
                    const cell = board.querySelector('[data-now-for="' + id + '"]');
                    if (cell) paint(cell, data.employees[id]);
                });
            })
            /* A failed poll is not worth shouting about: the column on screen
               is still the last good answer, and the next tick may succeed. */
            .catch(function () {});
    }

    window.setInterval(poll, EVERY_MS);
});
