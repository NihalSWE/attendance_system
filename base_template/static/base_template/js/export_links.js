/* Downloads carry what the table is showing right now.

   A download link is rendered with the page's own filters already in it
   (search, status, dates...). The table's search box and column sort live in
   DataTables, not in the address bar, so at the moment of the click this adds
   them in the same form the table's own requests use - search[value] and
   order[i][column|dir] - and the server applies them with the same code.

     <a href="..." data-export>         the page's one server table
     <a href="..." data-export="shifts"> the table named data-server-table="shifts"

   Without JavaScript the link still works: it carries the page filters and
   the no-script table search, just not a clicked sort. */
document.addEventListener("click", function (event) {
    const link = event.target.closest("a[data-export]");
    if (!link) return;
    const name = link.dataset.export;
    const table = document.querySelector(
        name ? `table[data-server-table="${name}"]` : "table[data-server-table]");
    const api = table && table.serverTableApi;
    if (!api) return;
    const url = new URL(link.getAttribute("href"), window.location.href);
    [...url.searchParams.keys()]
        .filter(key => key === "search[value]" || key.startsWith("order["))
        .forEach(key => url.searchParams.delete(key));
    url.searchParams.set("search[value]", api.search() || "");
    api.order().forEach(function (entry, index) {
        const column = Array.isArray(entry) ? entry[0] : entry.column;
        const dir = Array.isArray(entry) ? entry[1] : entry.dir;
        url.searchParams.set(`order[${index}][column]`, column);
        url.searchParams.set(`order[${index}][dir]`, dir);
    });
    link.setAttribute("href", url.toString());
});
