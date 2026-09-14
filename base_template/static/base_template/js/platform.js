/* Real DataTables over the server-rendered, paginated fallback. */
document.addEventListener("DOMContentLoaded", function () {
    /* Server-rendered platform tables that only want client-side sorting and
       filtering over the current page. Same `data-enhance` contract the device
       screens use, so there is one convention rather than two. The server's own
       count and pager stay authoritative. */
    if (window.DataTable) {
        document.querySelectorAll("table[data-enhance]").forEach(function (enhanced) {
            new DataTable(enhanced, {
                paging: false,
                info: false,
                autoWidth: false,
                order: [],
                columnDefs: [{targets: "no-sort", orderable: false}],
                language: {
                    search: "Filter this page:",
                    zeroRecords: "No rows match that filter."
                }
            });
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
