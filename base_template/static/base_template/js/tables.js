/* One server-side contract for company and self-service lists. */

/* A "Sort by" box for a table: <select data-table-sort="NAME"> (NAME is the
   table's data-server-table value, empty for an unnamed table), options valued
   "column:asc" / "column:desc", or "" for the page's default order. It sorts
   exactly as clicking that column's header does, and follows the headers: a
   header click shows the matching choice, or "Another column". */
function bindSortBox(table, api) {
    const name = table.dataset.serverTable || "";
    const box = Array.from(document.querySelectorAll("select[data-table-sort]"))
        .find(function (select) { return select.dataset.tableSort === name; });
    if (!box) return;
    let following = false;
    box.addEventListener("change", function () {
        if (following || box.value === "other") return;
        const [column, direction] = box.value.split(":");
        api.order(box.value ? [[Number(column), direction]] : []).draw();
    });
    api.on("order", function () {
        const order = api.order();
        const value = order.length ? order[0][0] + ":" + order[0][1] : "";
        const known = Array.from(box.options).some(function (option) {
            return option.value === value;
        });
        following = true;
        box.value = known ? value : "other";
        box.dispatchEvent(new Event("change", {bubbles: true}));  // repaint the custom select
        following = false;
    });
}

document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table[data-server-table]").forEach(function (table) {
        if (!window.DataTable) return; // The counted HTML pager still works.
        const allowed = (table.dataset.orderable || "").split(",");
        const headers = table.querySelectorAll("thead th");
        const card = table.closest(".card");
        const fallback = card && card.querySelector(".server-table-fallback");
        const notice = document.createElement("p");
        notice.className = "alert alert--warning";
        notice.hidden = true;
        notice.textContent = "The table could not load. Reload this page to try again.";
        table.parentElement.before(notice);
        table.querySelectorAll("tbody tr").forEach(function (row) {
            if (row.querySelector("td[colspan]")) row.remove();
        });
        // Kept on the element so a download link can read what the table is
        // showing right now - its search box and sort (export_links.js).
        table.serverTableApi = new DataTable(table, {
            serverSide: true,
            processing: true,
            searchDelay: 400,
            autoWidth: false,
            order: [],
            pageLength: Number(table.dataset.length) || 25,
            displayStart: Number(table.dataset.start) || 0,
            lengthMenu: [10, 25, 50, 100],
            pagingType: "full_numbers",
            search: {search: table.dataset.query || ""},
            columns: Array.from(headers, function (header, index) {
                // data-hidden: a column that exists only to sort by (the Daily
                // list's Employee ID, which the Employee cell already shows).
                return {data: String(index), orderable: allowed.includes(String(index)),
                    visible: !header.hasAttribute("data-hidden"),
                    className: header.classList.contains("numeric") ? "numeric" : ""};
            }),
            createdRow: function (row, data, dataIndex, cells) {
                // ``cells`` holds every column's cell, hidden ones too; row.cells
                // only the drawn ones, so a hidden column would shift the rest.
                (data.DT_RowData.cellAttrs || []).forEach(function (attrs, index) {
                    const cell = cells[index];
                    if (!cell) return;
                    Object.entries(attrs).forEach(function ([key, value]) {
                        cell.setAttribute(key, value || "");
                    });
                });
            },
            initComplete: function () {
                const api = this.api();
                bindSortBox(table, api);
                const jump = document.createElement("form");
                jump.className = "dt-length";
                const label = document.createElement("label");
                label.textContent = "Go to page ";
                const input = document.createElement("input");
                input.type = "number";
                input.className = "dt-input";
                input.min = "1";
                input.required = true;
                input.setAttribute("aria-label", "Go to page");
                label.append(input);
                const go = document.createElement("button");
                go.type = "submit";
                go.className = "btn btn--ghost btn--sm";
                go.textContent = "Go";
                jump.append(label, go);
                api.table().container().querySelector(".dt-layout-row:last-child").append(jump);
                function sync() {
                    const info = api.page.info();
                    input.max = String(Math.max(1, info.pages));
                    input.value = String(info.page + 1);
                    go.disabled = info.pages < 2;
                }
                jump.addEventListener("submit", function (event) {
                    event.preventDefault();
                    const page = Number(input.value) - 1;
                    if (Number.isInteger(page) && page >= 0 && page < api.page.info().pages) api.page(page).draw("page");
                });
                api.on("draw", sync);
                sync();
            },
            ajax: function (data, callback) {
                const url = new URL(window.location.href);
                url.hash = "";
                // A named list (data-server-table="shifts") draws only itself.
                url.searchParams.set("table", table.dataset.serverTable || "1");
                url.searchParams.set("draw", data.draw);
                url.searchParams.set("start", data.start);
                url.searchParams.set("length", data.length);
                url.searchParams.set("search[value]", data.search.value);
                data.order.forEach(function (order, index) {
                    url.searchParams.set(`order[${index}][column]`, order.column);
                    url.searchParams.set(`order[${index}][dir]`, order.dir);
                });
                fetch(url, {headers: {Accept: "application/json"}})
                    .then(function (response) {
                        if (!response.ok) throw new Error("Table unavailable");
                        return response.json();
                    }).then(function (result) {
                        notice.hidden = true;
                        if (fallback) fallback.hidden = true;
                        if (card) card.querySelectorAll(".server-table-empty").forEach(element => element.hidden = true);
                        callback(result);
                        table.dispatchEvent(new CustomEvent("server-table:draw", {bubbles: true}));
                    }).catch(function () {
                        notice.hidden = false;
                        if (fallback) fallback.hidden = false;
                        callback({draw: data.draw, recordsTotal: 0, recordsFiltered: 0, data: []});
                    });
            },
            language: {search: "Search all results:", lengthMenu: "Show _MENU_",
                info: "Showing _START_–_END_ of _TOTAL_", infoEmpty: "Showing 0–0 of 0",
                emptyTable: "No results for these filters.", zeroRecords: "No matching results."}
        });
    });
});
