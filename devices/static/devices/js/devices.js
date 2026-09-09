/* Progressive enhancement for the device tables.

   Every table on these pages is already rendered and paginated by the server,
   so the page is complete without this file. DataTables adds client-side
   sorting and filtering on top of the current page of rows where it loads. */
document.addEventListener("DOMContentLoaded", function () {
    if (!window.DataTable) return;

    document.querySelectorAll("table[data-enhance]").forEach(function (table) {
        new DataTable(table, {
            paging: false,
            info: false,
            autoWidth: false,
            order: [],
            columnDefs: [
                {targets: "no-sort", orderable: false}
            ],
            language: {
                search: "Filter this page:",
                zeroRecords: "No rows match that filter."
            }
        });
        /* The server's own count and pager stay authoritative: DataTables is
           only filtering the rows already on screen. */
        const note = table.closest(".card").querySelector("[data-filter-note]");
        if (note) note.hidden = false;
    });
});
