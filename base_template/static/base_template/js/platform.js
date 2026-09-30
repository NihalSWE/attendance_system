/* Real DataTables over the server-rendered, paginated fallback. */
document.addEventListener("DOMContentLoaded", function () {
    /* Other platform lists use the shared server-side tables (tables.js). */
    const table = document.getElementById("companies-table");
    if (!table) return;
    if (!window.DataTable) {
        const note = document.querySelector(".enhancement-note");
        if (note) note.hidden = false;
        return;
    }
    table.querySelectorAll(".fallback-empty").forEach(element => element.remove());
    // SL first, as on every list (2026-09-30); the server counts columns without it.
    const sl = document.createElement("th");
    sl.className = "numeric table-sl";
    sl.textContent = "SL";
    table.querySelector("thead th").before(sl);
    table.querySelectorAll("tbody tr").forEach(row => row.prepend(document.createElement("td")));
    const escaped = DataTable.render.text();
    new DataTable(table, {
        serverSide: true,
        processing: true,
        ajax: {
            url: table.dataset.source,
            data: function (request) {
                request.order = request.order.map(entry => ({...entry, column: entry.column - 1}));
            },
        },
        pageLength: 25,
        lengthMenu: [10, 25, 50, 100],
        search: {search: table.dataset.query || ""},
        order: [[1, "asc"]],
        autoWidth: false,
        columns: [
            {
                data: null, orderable: false, searchable: false, className: "numeric table-sl",
                render: (_data, _type, _row, meta) => meta.settings._iDisplayStart + meta.row + 1,
            },
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
