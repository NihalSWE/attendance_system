/* Salary by month (2026-10-31): a department row opens to its people; More
   closes when you click elsewhere. */
(function () {
    function toggle(row) {
        const group = row.closest(".salary__group");
        const open = group.classList.toggle("is-open");
        delete group.dataset.auto;
        row.setAttribute("aria-expanded", open ? "true" : "false");
        group.querySelectorAll(".salary__person").forEach(function (person) { person.hidden = !open; });
    }

    document.addEventListener("click", function (event) {
        const row = event.target.closest("[data-salary-toggle]");
        if (row) toggle(row);
        document.querySelectorAll(".salary__more[open]").forEach(function (menu) {
            if (!menu.contains(event.target)) menu.removeAttribute("open");
        });
    });

    // By department: search, Show N and pages, worked out in the page. A
    // department matches by its name or branch; one that matches only by a
    // person opens with just the people who match.
    const list = document.querySelector("[data-dept-list]");
    if (list) {
        const groups = Array.from(list.querySelectorAll(".salary__group"));
        const none = list.querySelector("[data-dept-none]");
        const info = list.querySelector("[data-dept-info]");
        const pages = list.querySelector("[data-dept-pages]");
        const search = list.querySelector("[data-dept-search]");
        const length = list.querySelector("[data-dept-length]");
        let page = 0;

        function setOpen(group, open, people) {
            group.classList.toggle("is-open", open);
            group.querySelector("[data-salary-toggle]").setAttribute("aria-expanded", open ? "true" : "false");
            group.querySelectorAll(".salary__person").forEach(function (person) {
                person.hidden = !open || (people !== undefined && !people.includes(person));
            });
        }

        function button(text, target, current, disabled, label) {
            const item = document.createElement("button");
            item.type = "button";
            item.className = "dt-paging-button" + (current ? " current" : "");
            item.textContent = text;
            item.disabled = disabled;
            if (label) item.setAttribute("aria-label", label);
            if (current) item.setAttribute("aria-current", "page");
            item.addEventListener("click", function () { page = target; draw(); });
            return item;
        }

        function draw() {
            const term = search.value.trim().toLowerCase();
            const size = Number(length.value) || 25;
            const shown = [];
            groups.forEach(function (group) {
                const people = Array.from(group.querySelectorAll(".salary__person"));
                const own = !term || group.dataset.search.includes(term);
                const hits = own ? [] : people.filter(person => person.dataset.search.includes(term));
                if (own || hits.length) shown.push({group: group, hits: own ? null : hits});
            });
            const count = Math.max(1, Math.ceil(shown.length / size));
            page = Math.min(page, count - 1);
            const first = page * size;
            groups.forEach(group => { group.hidden = true; });
            shown.slice(first, first + size).forEach(function ({group, hits}) {
                group.hidden = false;
                if (hits) {
                    group.dataset.auto = "1";
                    setOpen(group, true, hits);
                } else if (group.dataset.auto) {
                    delete group.dataset.auto;
                    setOpen(group, false);
                }
            });
            none.hidden = shown.length > 0;
            info.textContent = shown.length
                ? `Showing ${first + 1}–${Math.min(first + size, shown.length)} of ${shown.length} department${shown.length === 1 ? "" : "s"}`
                  + (shown.length < groups.length ? ` (filtered from ${groups.length})` : "")
                : "Showing 0–0 of 0";
            pages.replaceChildren(
                button("«", 0, false, page === 0, "First"),
                button("‹", page - 1, false, page === 0, "Previous"));
            const from = Math.max(0, Math.min(page - 2, count - 5));
            for (let index = from; index < Math.min(count, from + 5); index += 1) {
                pages.append(button(String(index + 1), index, index === page, false));
            }
            pages.append(button("›", page + 1, false, page >= count - 1, "Next"),
                         button("»", count - 1, false, page >= count - 1, "Last"));
        }

        let wait = null;
        search.addEventListener("input", function () {
            window.clearTimeout(wait);
            wait = window.setTimeout(function () { page = 0; draw(); }, 200);
        });
        length.addEventListener("change", function () { page = 0; draw(); });
        draw();
    }

    document.addEventListener("keydown", function (event) {
        const row = event.target.closest && event.target.closest("[data-salary-toggle]");
        if (row && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            toggle(row);
        }
        if (event.key === "Escape") {
            document.querySelectorAll(".salary__more[open]").forEach(menu => menu.removeAttribute("open"));
        }
    });

    // Generate runs in the background (2026-11-01): its progress, asked every
    // second; when it has finished the page reloads with the payslips and
    // what it did.
    const generateForm = document.querySelector("[data-generate-form]");
    if (generateForm) {
        generateForm.addEventListener("submit", function () {
            generateForm.querySelector("button").setAttribute("aria-busy", "true");
        });
    }
    const job = document.querySelector("[data-generate-job]");
    if (job) {
        const elapsed = job.querySelector("[data-job-elapsed]");
        const began = Date.now() - 1000 * Number(elapsed.dataset.seconds || 0);
        const ago = function () {
            const seconds = Math.round((Date.now() - began) / 1000);
            if (seconds < 5) return "just now";
            if (seconds < 60) return seconds + " s ago";
            return Math.floor(seconds / 60) + " min " + (seconds % 60) + " s ago";
        };
        const show = function (state) {
            job.querySelector("[data-job-percent]").textContent = state.percent;
            job.querySelector("[data-job-bar]").style.width = state.percent + "%";
            const steps = ["attendance", "salary"];
            steps.forEach(function (name, index) {
                const step = job.querySelector('[data-job-step="' + name + '"]');
                const now = steps.indexOf(state.stage);
                step.classList.toggle("is-done", index < now);
                step.classList.toggle("is-now", index === now);
                step.querySelector("[data-job-count]").textContent = index === now && state.total
                    ? state.done.toLocaleString() + " of " + state.total.toLocaleString() + " people" : "";
            });
        };
        const ask = function () {
            fetch(job.dataset.generateJob, { headers: { Accept: "application/json" }, credentials: "same-origin" })
                .then(function (reply) { return reply.ok ? reply.json() : Promise.reject(reply.status); })
                .then(function (state) {
                    if (!state.running) {
                        show({ percent: 100, stage: "done" });
                        window.location.reload();
                        return;
                    }
                    show(state);
                    window.setTimeout(ask, 1000);
                })
                .catch(function () { window.setTimeout(ask, 3000); });
        };
        elapsed.textContent = ago();
        window.setInterval(function () { elapsed.textContent = ago(); }, 1000);
        ask();
    }
})();
