/* Salary by month (2026-10-31): a department row opens to its people; More
   closes when you click elsewhere. */
(function () {
    function toggle(row) {
        const group = row.closest(".salary__group");
        const open = group.classList.toggle("is-open");
        row.setAttribute("aria-expanded", open ? "true" : "false");
    }

    document.addEventListener("click", function (event) {
        const row = event.target.closest("[data-salary-toggle]");
        if (row) toggle(row);
        document.querySelectorAll(".salary__more[open]").forEach(function (menu) {
            if (!menu.contains(event.target)) menu.removeAttribute("open");
        });
    });

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
})();
