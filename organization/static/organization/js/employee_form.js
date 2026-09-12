/* Dependent branch -> department -> designation selects on the create
   employee form.

   Each choice narrows the next, because a department belongs to one branch and
   this company decides which designations that department uses. The server
   re-checks the whole chain, so this is convenience rather than enforcement — but offering a combination that
   cannot be saved is a worse experience than not listing it.

   Progressive enhancement: the fields are already narrowed server-side from
   whatever is selected, so without this file the form still validates, it just
   needs a submit to refilter. */
document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("form[data-departments-url]");
    if (!form) return;

    const branch = form.querySelector("#id_branch");
    const department = form.querySelector("#id_department");
    const designation = form.querySelector("#id_designation");
    if (!branch || !department || !designation) return;

    const jq = window.jQuery;

    /* Select2 replaces the visible control and announces a pick with jQuery's
       .trigger("change"), which does NOT reach a listener bound through
       addEventListener. Binding through jQuery when it is present is therefore
       the only way to hear a real user's selection; the native path stays as
       the fallback for a page where Select2 did not load. */
    function onChange(element, handler) {
        if (jq) {
            jq(element).on("change", handler);
        } else {
            element.addEventListener("change", handler);
        }
    }

    function fill(select, options, placeholder) {
        const previous = select.value;
        select.innerHTML = "";

        const blank = document.createElement("option");
        blank.value = "";
        blank.textContent = placeholder;
        select.appendChild(blank);

        options.forEach(function (item) {
            const option = document.createElement("option");
            option.value = item.id;
            option.textContent = item.text;
            /* Keep the choice if it still exists in the narrowed list. */
            option.selected = String(item.id) === previous;
            select.appendChild(option);
        });

        if (jq && jq(select).data("select2")) {
            jq(select).trigger("change.select2");
        }
    }

    function load(url, select, placeholder, then) {
        fetch(url, {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(response => (response.ok ? response.json() : {results: []}))
            .then(function (data) {
                fill(select, data.results || [], placeholder);
                if (then) then();
            })
            /* On failure clear rather than leave stale options from the
               previous parent, which would fail server validation. */
            .catch(function () {
                fill(select, [], placeholder);
                if (then) then();
            });
    }

    onChange(branch, function () {
        if (!branch.value) {
            fill(department, [], "Select a department");
            fill(designation, [], "Select a designation");
            return;
        }
        load(
            form.dataset.departmentsUrl + "?branch=" + encodeURIComponent(branch.value),
            department,
            "Select a department",
            function () {
                /* The department list changed, so the old designation list
                   no longer applies until a department is picked again. */
                fill(designation, [], "Select a designation");
            }
        );
    });

    onChange(department, function () {
        if (!department.value) {
            fill(designation, [], "Select a designation");
            return;
        }
        load(
            form.dataset.designationsUrl + "?department=" + encodeURIComponent(department.value),
            designation,
            "Select a designation"
        );
    });
});
