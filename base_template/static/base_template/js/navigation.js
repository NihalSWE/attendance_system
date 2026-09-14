/* Shared responsive navigation; native details keep menus usable without JS. */
document.addEventListener("DOMContentLoaded", function () {
    const sidebar = document.querySelector(".sidebar");
    const toggle = document.querySelector(".navigation-toggle");
    if (!sidebar || !toggle) return;
    document.documentElement.classList.add("navigation-ready");

    const links = [...sidebar.querySelectorAll(".sidebar__child")];
    function selectSection() {
        const samePage = links.filter(link => new URL(link.href).pathname === location.pathname);
        if (!samePage.length) return; // Server selected an edit/detail alias.
        const selected = samePage.find(link => new URL(link.href).hash === location.hash)
            || samePage.find(link => !new URL(link.href).hash);
        samePage.forEach(function (link) {
            link.classList.toggle("is-active", link === selected);
            if (link === selected) {
                link.setAttribute("aria-current", location.hash ? "location" : "page");
                link.closest("details").open = true;
            } else {
                link.removeAttribute("aria-current");
            }
        });
    }
    selectSection();
    window.addEventListener("hashchange", selectSection);

    const mobile = window.matchMedia("(max-width: 1024px)");
    const backdrop = document.createElement("div");
    backdrop.className = "navigation-backdrop";
    backdrop.hidden = true;
    backdrop.setAttribute("aria-hidden", "true");
    document.body.append(backdrop);
    const page = document.querySelector("main.page");
    let open = false;
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "btn btn--quiet btn--sm navigation-close";
    closeButton.textContent = "Close";
    closeButton.setAttribute("aria-label", "Close navigation");
    sidebar.querySelector(".sidebar__brand").append(closeButton);
    closeButton.addEventListener("click", () => close(true));
    function focusable() {
        return [...sidebar.querySelectorAll("a[href], button, summary")]
            .filter(element => element.getClientRects().length);
    }
    function close(restoreFocus = false) {
        open = false;
        sidebar.classList.remove("is-open");
        backdrop.hidden = true;
        document.body.classList.remove("navigation-open");
        if (page) page.inert = false;
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-label", "Open navigation");
        if (restoreFocus) toggle.focus();
    }
    toggle.addEventListener("click", function () {
        if (open) return close(true);
        open = true;
        sidebar.classList.add("is-open");
        backdrop.hidden = false;
        document.body.classList.add("navigation-open");
        if (page) page.inert = true;
        toggle.setAttribute("aria-expanded", "true");
        toggle.setAttribute("aria-label", "Close navigation");
        (sidebar.querySelector('[aria-current]') || focusable()[0])?.focus();
    });
    backdrop.addEventListener("click", () => close(true));
    document.addEventListener("keydown", function (event) {
        if (!open) return;
        if (event.key === "Escape") {
            event.preventDefault();
            close(true);
        } else if (event.key === "Tab") {
            const targets = focusable();
            const first = targets[0];
            const last = targets[targets.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
            }
        }
    });
    sidebar.addEventListener("click", function (event) {
        const link = event.target.closest("a[href]");
        if (!link || !open) return;
        close();
        const destination = new URL(link.href);
        if (destination.pathname === location.pathname && destination.hash) {
            const section = document.getElementById(destination.hash.slice(1));
            if (section) {
                section.setAttribute("tabindex", "-1");
                section.focus({preventScroll: true});
            }
        }
    });
    mobile.addEventListener("change", () => close());
});
