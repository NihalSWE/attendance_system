/* Device connection, kept current (plan step N8).

   Two things on the device pages move without a reload:

   - every "Connected / Last seen … ago / Not connected" badge, asked for once
     every 20 seconds — a device calls in every 10–20 seconds, so asking more
     often cannot learn anything new;
   - a connection test, asked every 3 seconds while it waits, because that is
     the moment somebody is standing at the terminal watching the screen.

   Everything is already correct when the page is served. Without this file the
   page is a snapshot; a reload shows the same answer. Every value painted here
   comes from our own JSON, but it is still set as text, never as HTML. */
document.addEventListener("DOMContentLoaded", function () {
    const BADGE_MS = 20000;
    const TEST_MS = 3000;
    const TONES = ["success", "warning", "danger", "neutral", "info"];

    /* ---------------------------------------------------------------- badges */
    document.querySelectorAll("[data-connection-board]").forEach(function (board) {
        const url = board.dataset.connectionsUrl;
        const ids = board.dataset.connectionDevices || "";
        if (!url || !ids) return;

        function paint(id, state) {
            board.querySelectorAll('[data-connection-for="' + id + '"]').forEach(function (badge) {
                TONES.forEach(function (tone) { badge.classList.remove("badge--" + tone); });
                badge.classList.add("badge--" + state.tone);
                badge.textContent = state.label;
            });
            board.querySelectorAll('[data-connection-detail-of="' + id + '"]').forEach(function (el) {
                el.textContent = state.detail || "";
            });
        }

        function poll() {
            fetch(url + "?devices=" + encodeURIComponent(ids), {
                headers: {"X-Requested-With": "XMLHttpRequest"}
            })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data || !data.devices) return;
                    Object.keys(data.devices).forEach(function (id) {
                        paint(id, data.devices[id]);
                    });
                })
                /* A failed poll leaves the last good answer on screen. */
                .catch(function () {});
        }

        window.setInterval(poll, BADGE_MS);
    });

    /* ------------------------------------------------------------------ test */
    const test = document.querySelector("[data-connection-test]");
    if (!test || !test.dataset.statusUrl) return;

    const statusEl = test.querySelector("[data-test-status]");
    const commandEl = test.querySelector("[data-test-command]");
    const adviceEl = test.querySelector("[data-test-advice]");
    const zone = test.dataset.timezone || undefined;
    let timer = null;

    function clock(iso) {
        try {
            return new Date(iso).toLocaleTimeString([], {
                hour: "2-digit", minute: "2-digit", second: "2-digit",
                hour12: false, timeZone: zone
            });
        } catch (e) {
            return new Date(iso).toLocaleTimeString();
        }
    }

    function status(strong, rest) {
        const b = document.createElement("strong");
        b.textContent = strong;
        statusEl.replaceChildren(b, document.createTextNode(" " + rest));
    }

    function renderAdvice(items) {
        if (!items || !items.length) {
            adviceEl.hidden = true;
            return;
        }
        if (!adviceEl.hidden && adviceEl.childElementCount) return;
        adviceEl.replaceChildren();
        items.forEach(function (item) {
            const box = document.createElement("div");
            box.className = "conn-advice__item";
            const title = document.createElement("strong");
            title.textContent = item.title;
            const text = document.createElement("p");
            text.className = "t-muted";
            text.textContent = item.text;
            box.append(title, text);
            if (item.values && item.values.length) {
                const list = document.createElement("dl");
                list.className = "conn-advice__values";
                item.values.forEach(function (pair) {
                    const dt = document.createElement("dt");
                    dt.textContent = pair[0];
                    const dd = document.createElement("dd");
                    dd.textContent = pair[1];
                    list.append(dt, dd);
                });
                box.append(list);
            }
            adviceEl.append(box);
        });
        adviceEl.hidden = false;
    }

    function finished(data) {
        const commandDone = !data.command || !data.command.stage ||
            data.command.stage === "answered" || data.command.stage === "unknown";
        return data.checked_in && commandDone;
    }

    function tick() {
        fetch(test.dataset.statusUrl, {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) return;
                test.classList.remove("conn-test--ok", "conn-test--failed");
                if (data.checked_in) {
                    test.classList.add("conn-test--ok");
                    status("Connected at " + clock(data.checked_in_at) + ".",
                           "The device checked in after the test started.");
                } else if (data.gave_up) {
                    test.classList.add("conn-test--failed");
                    status("No check-in yet.",
                           "Nothing has arrived for " + data.waited_seconds +
                           " seconds. Check the points below; the test keeps listening.");
                } else {
                    status("Waiting for the device to check in…",
                           "Started " + clock(data.started_at) + " — " +
                           data.waited_seconds + " s so far.");
                }
                if (data.command && data.command.text) {
                    commandEl.textContent = data.command.text;
                    commandEl.hidden = false;
                }
                renderAdvice(data.checked_in ? [] : data.advice);

                if (finished(data)) {
                    window.clearInterval(timer);
                    timer = null;
                }
            })
            .catch(function () {});
    }

    tick();
    timer = window.setInterval(tick, TEST_MS);
});
