/* Real DataTables over the server-rendered, paginated fallback. */
document.addEventListener("DOMContentLoaded", function () {
    const toggle = document.querySelector(".mobile-menu");
    const side = document.getElementById("platform-sidebar");
    if (toggle && side) {
        const close = function () {
            side.classList.remove("is-open");
            toggle.setAttribute("aria-expanded", "false");
        };
        toggle.addEventListener("click", function () {
            const open = side.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", String(open));
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                close();
                toggle.focus();
            }
        });
        document.addEventListener("click", function (event) {
            if (!side.contains(event.target) && !toggle.contains(event.target)) close();
        });
    }

    const table = document.getElementById("companies-table");
    if (!table) return;
    if (!window.DataTable) {
        const note = document.querySelector(".enhancement-note");
        if (note) note.hidden = false;
        return;
    }
    table.querySelectorAll(".fallback-empty").forEach(element => element.remove());
    const escaped = DataTable.render.text();
    new DataTable(table, {
        serverSide: true,
        processing: true,
        ajax: table.dataset.source,
        pageLength: 25,
        lengthMenu: [10, 25, 50, 100],
        search: {search: table.dataset.query || ""},
        order: [[0, "asc"]],
        autoWidth: false,
        columns: [
            {data: "name", render: escaped},
            {data: "code", render: escaped},
            {
                data: "status",
                render: function (status, type) {
                    if (type !== "display") return status;
                    const badge = document.createElement("span");
                    const colors = {Active: "badge--success", Trial: "badge--warning", Suspended: "badge--danger"};
                    badge.className = "badge " + (colors[status] || "badge--neutral");
                    badge.textContent = status;
                    return badge.outerHTML;
                }
            },
            {data: "employee_count", className: "numeric"},
            {
                data: "url",
                orderable: false,
                searchable: false,
                render: function (url, type, row) {
                    if (type !== "display") return url;
                    const link = document.createElement("a");
                    link.href = url;
                    link.className = "text-link";
                    link.textContent = "Manage";
                    link.setAttribute("aria-label", "Manage " + row.name);
                    return link.outerHTML;
                }
            }
        ],
        language: {
            emptyTable: "No companies yet. Create your first company to begin onboarding.",
            search: "Search companies:",
            lengthMenu: "Show _MENU_",
            info: "Showing _START_–_END_ of _TOTAL_",
            infoEmpty: "Showing 0–0 of 0",
            paginate: {first: "First", last: "Last", next: "Next", previous: "Previous"}
        },
        initComplete: function () {
            document.querySelectorAll(".table-fallback").forEach(element => element.hidden = true);
        }
    });
});
