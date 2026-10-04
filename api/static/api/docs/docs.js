/* The API documentation site: open nested fields with +, expand / collapse
   all, language tabs (remembered), copy buttons, the menu on phones. */
document.addEventListener("DOMContentLoaded", function () {
    function setOpen(node, open) {
        const button = node.querySelector(":scope > .field-node__head > [data-field-toggle]");
        const children = node.querySelector(":scope > .field-node__children");
        if (!button || !children) return;
        button.setAttribute("aria-expanded", open ? "true" : "false");
        children.hidden = !open;
    }

    document.addEventListener("click", function (event) {
        const toggle = event.target.closest("[data-field-toggle]");
        if (toggle) {
            const node = toggle.closest(".field-node");
            setOpen(node, toggle.getAttribute("aria-expanded") !== "true");
            return;
        }
        const all = event.target.closest("[data-expand-all], [data-collapse-all]");
        if (all) {
            const open = all.hasAttribute("data-expand-all");
            all.closest("[data-tree]").querySelectorAll(".field-node.has-children")
                .forEach(function (node) { setOpen(node, open); });
            return;
        }
        const copy = event.target.closest("[data-copy-next], [data-copy-prev]");
        if (copy) {
            const source = copy.hasAttribute("data-copy-next")
                ? copy.nextElementSibling : copy.previousElementSibling;
            if (source) copyText(copy, source.textContent.trim());
            return;
        }
        const tab = event.target.closest("[data-tab]");
        if (tab) {
            chooseLanguage(tab.dataset.tab);
            try { localStorage.setItem("apidocs-language", tab.dataset.tab); } catch (e) { /* private mode */ }
        }
    });

    function chooseLanguage(key) {
        document.querySelectorAll("[data-tabs]").forEach(function (tabs) {
            if (!tabs.querySelector('[data-tab="' + key + '"]')) return;
            tabs.querySelectorAll("[data-tab]").forEach(function (b) {
                b.setAttribute("aria-selected", b.dataset.tab === key ? "true" : "false");
            });
            tabs.querySelectorAll("[data-panel]").forEach(function (p) {
                p.hidden = p.dataset.panel !== key;
            });
        });
    }
    try {
        const saved = localStorage.getItem("apidocs-language");
        if (saved) chooseLanguage(saved);
    } catch (e) { /* storage blocked */ }

    function copyText(button, text) {
        const done = function () {
            const label = button.textContent;
            button.textContent = "Copied";
            setTimeout(function () { button.textContent = label; }, 1500);
        };
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done);
            return;
        }
        const area = document.createElement("textarea");
        area.value = text;
        document.body.appendChild(area);
        area.select();
        try { document.execCommand("copy"); done(); } catch (e) { button.textContent = "Press Ctrl+C"; }
        area.remove();
    }

    const menu = document.getElementById("apidocs-menu");
    const menuToggle = document.querySelector("[data-menu-toggle]");
    if (menu && menuToggle) {
        menuToggle.addEventListener("click", function () {
            const open = !menu.classList.contains("is-open");
            menu.classList.toggle("is-open", open);
            menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
        });
    }
});
