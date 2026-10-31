/* Attendance -> Summary (2026-10-31): Change opens the side panel with that
   person's days; the steppers and the preview follow what is typed. Saving is
   base_template/js/fetch_forms.js (the page reloads with the row marked). */
(function () {
    const drawer = document.getElementById("summary-drawer");
    if (!drawer) return;
    const body = drawer.querySelector("[data-summary-body]");

    function close() {
        if (!drawer.open) return;
        drawer.classList.add("is-closing");
        window.setTimeout(function () {
            drawer.classList.remove("is-closing");
            drawer.close();
        }, 190);
    }

    function preview(form) {
        const box = form.querySelector('[name="present_days"]');
        if (!box) return;
        const working = Number(form.dataset.working);
        const present = Math.max(0, Math.min(working, Math.round(Number(box.value) || 0)));
        [["present", present], ["absent", working - present]].forEach(function ([name, value]) {
            const cell = form.querySelector('[data-preview="' + name + '"]');
            if (!cell || cell.textContent === String(value)) return;
            cell.textContent = value;
            cell.classList.remove("is-bump");
            void cell.offsetWidth;
            cell.classList.add("is-bump");
        });
    }

    document.addEventListener("click", async function (event) {
        const opener = event.target.closest("[data-summary-open]");
        if (opener) {
            const address = new URL(opener.dataset.summaryOpen, window.location.href);
            address.searchParams.set("back", window.location.pathname + window.location.search);
            body.innerHTML = '<p class="ui-muted" style="padding:24px">Loading…</p>';
            drawer.showModal();
            try {
                const reply = await fetch(address, {headers: {"X-Requested-With": "fetch"}});
                body.innerHTML = reply.ok ? await reply.text()
                    : '<p class="ui-muted" style="padding:24px">Could not open: ' + reply.status + "</p>";
            } catch (error) {
                body.innerHTML = '<p class="ui-muted" style="padding:24px">No connection. Try again.</p>';
            }
            const form = body.querySelector("[data-summary-form]");
            if (form) {
                preview(form);
                const first = form.querySelector(".summary__number");
                if (first) first.focus();
            }
            return;
        }
        if (event.target.closest("#summary-drawer [data-close-dialog]")) {
            event.preventDefault();
            close();
            return;
        }
        const step = event.target.closest("#summary-drawer [data-step]");
        if (step) {
            const form = step.closest("form");
            const box = form.querySelector('[name="present_days"]');
            const working = Number(form.dataset.working);
            box.value = Math.max(0, Math.min(working, (Math.round(Number(box.value)) || 0) + Number(step.dataset.step)));
            box.dispatchEvent(new Event("input", {bubbles: true}));
        }
    });

    document.addEventListener("input", function (event) {
        const form = event.target.closest && event.target.closest("#summary-drawer [data-summary-form]");
        if (form) preview(form);
    });

    // A click on the dimmed page closes the panel; so does Escape (animated).
    drawer.addEventListener("click", function (event) {
        if (event.target === drawer) close();
    });
    drawer.addEventListener("cancel", function (event) {
        event.preventDefault();
        close();
    });

    // The changed row scrolls into view after the page reloads.
    const changed = document.querySelector(".summary__row.is-changed");
    if (changed) changed.scrollIntoView({block: "center", behavior: "smooth"});
})();
