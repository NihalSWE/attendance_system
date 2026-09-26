/* The employee profile: tabs, and the modals its actions open.

   Tabs: each <a data-tab="x" href="#x"> shows <section data-panel="x"> and
   hides the rest; the address keeps the tab (#personal), so Back, a reload and
   a link land on the same one. Without JavaScript every section shows in turn.

   Modals: [data-open-dialog="id"] opens <dialog id="id">; [data-close-dialog]
   and a click on the backdrop close it. A form refused by the server comes back
   with data-open-on-load naming its modal, which opens again with the reasons.
   Never the browser's own alert or confirm. */
document.addEventListener("DOMContentLoaded", function () {
    const tabs = [...document.querySelectorAll("[data-profile-tabs] [data-tab]")];
    const panels = [...document.querySelectorAll("[data-panel]")];
    if (tabs.length) {
        document.documentElement.classList.add("profile-tabs-ready");
        const show = function (name, focus) {
            const known = tabs.some(tab => tab.dataset.tab === name);
            const chosen = known ? name : tabs[0].dataset.tab;
            tabs.forEach(function (tab) {
                const on = tab.dataset.tab === chosen;
                tab.classList.toggle("is-active", on);
                tab.setAttribute("aria-selected", on ? "true" : "false");
                if (on && focus) tab.focus();
            });
            panels.forEach(panel => { panel.hidden = panel.dataset.panel !== chosen; });
        };
        tabs.forEach(function (tab) {
            tab.addEventListener("click", function (event) {
                event.preventDefault();
                history.replaceState(null, "", "#" + tab.dataset.tab);
                show(tab.dataset.tab);
            });
        });
        window.addEventListener("hashchange", () => show(location.hash.slice(1)));
        show(location.hash.slice(1));
    }

    const open = function (dialog) {
        if (dialog && typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    };
    document.querySelectorAll("[data-open-dialog]").forEach(function (button) {
        button.addEventListener("click", function () {
            open(document.getElementById(button.dataset.openDialog));
        });
    });
    document.querySelectorAll("dialog.modal").forEach(function (dialog) {
        dialog.querySelectorAll("[data-close-dialog]").forEach(function (button) {
            button.addEventListener("click", () => dialog.close());
        });
        dialog.addEventListener("click", function (event) {
            if (event.target === dialog) dialog.close();
        });
    });
    const again = document.querySelector("[data-open-on-load]");
    if (again) open(document.getElementById(again.dataset.openOnLoad));
});
