/* Custom select for STATIC choice lists.

   Form follows design_reference/ (.om-sel control/menu/option/tick); colour
   comes from our tokens via components.css. No library, no remote icons.

   Select2 is deliberately NOT used here — it is reserved for database-backed
   lists that may need search or paging. This control exists because a native
   <select> can style its closed box but never its open dropdown, which the
   operating system draws.

   Progressive enhancement: the real <select> keeps the value, stays in the form
   and posts normally. Without JavaScript it remains a working native select.

   Applied by StyledFormMixin as `.js-select`.
*/
(function () {
    "use strict";

    var ICON_CARET = '<svg class="cs__caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
    var ICON_TICK = '<svg class="cs__tick" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="m5 13 4 4L19 7"/></svg>';

    var open = null;

    function closeOpen() {
        if (!open) return;
        open.menu.classList.remove("cs__menu--open");
        open.control.classList.remove("cs__control--open");
        open.control.setAttribute("aria-expanded", "false");
        open = null;
    }

    function place(inst) {
        var r = inst.control.getBoundingClientRect();
        var menu = inst.menu;
        menu.style.visibility = "hidden";
        menu.style.display = "block";
        menu.style.minWidth = r.width + "px";
        var h = menu.offsetHeight;
        var w = menu.offsetWidth;
        menu.style.display = "";
        menu.style.visibility = "";

        var gap = 6;
        var top = r.bottom + gap;
        if (top + h > window.innerHeight - 8 && r.top - gap - h > 8) {
            top = r.top - gap - h;
        }
        top = Math.max(8, Math.min(top, window.innerHeight - h - 8));

        var left = r.left;
        if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
        left = Math.max(8, left);

        menu.style.top = Math.round(top) + "px";
        menu.style.left = Math.round(left) + "px";
    }

    document.addEventListener("click", function (e) {
        if (open && !open.wrap.contains(e.target) && !open.menu.contains(e.target)) {
            closeOpen();
        }
    });
    window.addEventListener("scroll", function () { if (open) place(open); }, true);
    window.addEventListener("resize", function () { if (open) place(open); });

    function build(select) {
        if (select.multiple) return;          // multi-select stays with Select2
        if (select.dataset.csReady) return;
        select.dataset.csReady = "1";

        var wrap = document.createElement("div");
        wrap.className = "cs";
        select.parentNode.insertBefore(wrap, select);
        wrap.appendChild(select);
        select.classList.add("cs__native");
        select.setAttribute("tabindex", "-1");

        var control = document.createElement("button");
        control.type = "button";
        control.className = "cs__control";
        control.setAttribute("aria-haspopup", "listbox");
        control.setAttribute("aria-expanded", "false");
        if (select.disabled) {
            control.disabled = true;
            control.classList.add("cs__control--disabled");
        }
        var value = document.createElement("span");
        value.className = "cs__value";
        control.appendChild(value);
        control.insertAdjacentHTML("beforeend", ICON_CARET);
        wrap.insertBefore(control, select);

        var menu = document.createElement("div");
        menu.className = "cs__menu";
        menu.setAttribute("role", "listbox");
        document.body.appendChild(menu);

        var inst = {wrap: wrap, control: control, menu: menu, select: select};
        var optionEls = [];

        function paint() {
            var opt = select.options[select.selectedIndex];
            var text = opt ? opt.text : "";
            var placeholder = select.dataset.placeholder || "Select…";
            value.textContent = text || placeholder;
            value.classList.toggle("cs__value--empty", !text || !opt.value);
            optionEls.forEach(function (el) {
                el.classList.toggle("cs__option--on", el.dataset.value === select.value);
                el.setAttribute("aria-selected", el.dataset.value === select.value);
            });
        }

        function choose(v) {
            select.value = v;
            select.dispatchEvent(new Event("change", {bubbles: true}));
            paint();
            closeOpen();
            control.focus();
        }

        function rebuild() {
            menu.textContent = "";
            optionEls = [];
            Array.prototype.forEach.call(select.options, function (opt) {
                var el = document.createElement("button");
                el.type = "button";
                el.className = "cs__option";
                el.dataset.value = opt.value;
                el.setAttribute("role", "option");
                el.disabled = opt.disabled;
                el.innerHTML = '<span class="cs__option-label"></span>' + ICON_TICK;
                el.querySelector(".cs__option-label").textContent = opt.text;
                el.addEventListener("click", function () { choose(opt.value); });
                menu.appendChild(el);
                optionEls.push(el);
            });
            paint();
        }

        control.addEventListener("click", function () {
            var wasOpen = open === inst;
            closeOpen();
            if (wasOpen || control.disabled) return;
            rebuild();
            place(inst);
            menu.classList.add("cs__menu--open");
            control.classList.add("cs__control--open");
            control.setAttribute("aria-expanded", "true");
            open = inst;
        });

        // Keyboard: arrows move through options, Enter/Space opens, Escape closes.
        control.addEventListener("keydown", function (e) {
            var i = select.selectedIndex;
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                var next = e.key === "ArrowDown" ? i + 1 : i - 1;
                if (next >= 0 && next < select.options.length) {
                    select.selectedIndex = next;
                    select.dispatchEvent(new Event("change", {bubbles: true}));
                    paint();
                }
            } else if (e.key === "Escape") {
                closeOpen();
            } else if ((e.key === "Enter" || e.key === " ") && open !== inst) {
                e.preventDefault();
                control.click();
            }
        });

        // Keep the control honest if application code changes the select.
        select.addEventListener("change", paint);

        rebuild();
    }

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && open) {
            var c = open.control;
            closeOpen();
            c.focus();
        }
    });

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("select.js-select").forEach(build);
    });

    window.initCustomSelects = function (root) {
        (root || document).querySelectorAll("select.js-select").forEach(build);
    };
})();
