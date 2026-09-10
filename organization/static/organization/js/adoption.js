/* Dependent job-title multiselect on the department adoption form.

   Changing the department refetches the titles filed under it, because a title
   from another department is rejected by the server anyway — offering it would
   only let someone pick something that cannot be saved.

   Progressive enhancement: the field is already populated server-side for the
   selected department, so without this file the form still works, it just does
   not refilter until the page reloads. */
document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("form[data-titles-url]");
    if (!form) return;

    const department = form.querySelector("#id_department");
    const titles = form.querySelector("#id_designations");
    if (!department || !titles) return;

    /* An adopted department is fixed, so its titles never need refetching. */
    if (department.disabled) return;

    const jq = window.jQuery;

    /* Select2 announces a pick with jQuery's .trigger("change"), which does
       NOT reach a listener bound through addEventListener. Bind through
       jQuery when it is present, native otherwise. */
    function onChange(element, handler) {
        if (jq) {
            jq(element).on("change", handler);
        } else {
            element.addEventListener("change", handler);
        }
    }

    function repopulate(options) {
        const previously = new Set(
            Array.from(titles.selectedOptions).map(option => option.value)
        );
        titles.innerHTML = "";
        options.forEach(function (item) {
            const option = document.createElement("option");
            option.value = item.id;
            option.textContent = item.text;
            /* Keep a tick if the same title exists under the new department. */
            option.selected = previously.has(String(item.id));
            titles.appendChild(option);
        });
        /* Select2 renders from the underlying element, so it needs telling. */
        if (jq && jq(titles).data("select2")) {
            jq(titles).trigger("change.select2");
        }
    }

    onChange(department, function () {
        const value = department.value;
        if (!value) {
            repopulate([]);
            return;
        }
        const url = form.dataset.titlesUrl + "?department=" + encodeURIComponent(value);
        fetch(url, {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(response => (response.ok ? response.json() : {results: []}))
            .then(data => repopulate(data.results || []))
            /* A failed lookup must not leave stale titles from the previous
               department on screen, since those would fail validation. */
            .catch(() => repopulate([]));
    });
});
