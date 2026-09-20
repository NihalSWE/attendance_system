/* Date picker and date-range picker.

   Form follows design_reference/; colour comes from our tokens via
   datepicker.css. No library, no remote icons.

   Progressive enhancement: the real <input type="date"> keeps the value and is
   what the form posts. We hide it and drive it from the custom control, so with
   JavaScript disabled the native field still works.

   Usage:
     <input type="date" name="opened_on" data-datepicker>
     <input type="date" name="start" data-daterange="trip">
     <input type="date" name="end"   data-daterange="trip">
*/
(function () {
    "use strict";

    var MONTHS = ["January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December"];
    var WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];

    var ICON_CAL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/></svg>';
    var ICON_CHEV = '<svg class="dp__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
    var ICON_PREV = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>';
    var ICON_NEXT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>';

    function iso(d) {
        if (!d) return "";
        var m = String(d.getMonth() + 1).padStart(2, "0");
        var day = String(d.getDate()).padStart(2, "0");
        return d.getFullYear() + "-" + m + "-" + day;
    }
    function parseIso(value) {
        if (!value) return null;
        var parts = value.split("-");
        if (parts.length !== 3) return null;
        var d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
        return isNaN(d.getTime()) ? null : d;
    }
    function pretty(d) {
        if (!d) return "";
        return d.getDate() + " " + MONTHS[d.getMonth()].slice(0, 3) + " " + d.getFullYear();
    }
    function sameDay(a, b) {
        return a && b && a.getFullYear() === b.getFullYear()
            && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    }
    function startOfDay(d) {
        return new Date(d.getFullYear(), d.getMonth(), d.getDate());
    }
    function addDays(d, n) {
        var c = new Date(d.getTime());
        c.setDate(c.getDate() + n);
        return c;
    }

    /* One calendar surface, reused by both the single and range pickers. */
    function Calendar(onPick) {
        var cal = document.createElement("div");
        cal.className = "dp__cal";
        var head = document.createElement("div");
        head.className = "dp__calhead";
        var prev = document.createElement("button");
        prev.type = "button";
        prev.className = "dp__navbtn";
        prev.innerHTML = ICON_PREV;
        prev.setAttribute("aria-label", "Previous month");
        var label = document.createElement("div");
        label.className = "dp__selectors";
        // Real month/year dropdowns, as the reference has. A disabled label
        // would force dozens of clicks to reach a distant year.
        var monthSel = document.createElement("select");
        monthSel.className = "js-select";
        MONTHS.forEach(function (m, i) {
            var o = document.createElement("option");
            o.value = i;
            o.textContent = m.slice(0, 3);
            monthSel.appendChild(o);
        });
        var yearSel = document.createElement("select");
        yearSel.className = "js-select";
        var thisYear = new Date().getFullYear();
        for (var y = thisYear - 80; y <= thisYear + 10; y++) {
            var o = document.createElement("option");
            o.value = y;
            o.textContent = y;
            yearSel.appendChild(o);
        }
        var next = document.createElement("button");
        next.type = "button";
        next.className = "dp__navbtn";
        next.innerHTML = ICON_NEXT;
        next.setAttribute("aria-label", "Next month");
        label.append(monthSel, yearSel);
        head.append(prev, label, next);

        var week = document.createElement("div");
        week.className = "dp__week";
        WEEKDAYS.forEach(function (w) {
            var c = document.createElement("div");
            c.className = "dp__weekday";
            c.textContent = w;
            week.appendChild(c);
        });

        var days = document.createElement("div");
        days.className = "dp__days";
        cal.append(head, week, days);

        var view = startOfDay(new Date());
        var state = {start: null, end: null, single: null};
        var syncing = false;   // true while render() writes the dropdowns

        function render() {
            syncing = true;
            monthSel.value = String(view.getMonth());
            yearSel.value = String(view.getFullYear());
            if (window.initCustomSelects) window.initCustomSelects(head);
            // Repaint the custom triggers without re-entering render().
            monthSel.dispatchEvent(new Event("change", {bubbles: false}));
            yearSel.dispatchEvent(new Event("change", {bubbles: false}));
            syncing = false;
            days.textContent = "";
            var first = new Date(view.getFullYear(), view.getMonth(), 1);
            // Monday-first grid.
            var lead = (first.getDay() + 6) % 7;
            var cursor = addDays(first, -lead);
            var today = startOfDay(new Date());

            for (var i = 0; i < 42; i++) {
                var d = new Date(cursor.getTime());
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className = "dp__day";
                btn.textContent = d.getDate();
                if (d.getMonth() !== view.getMonth()) btn.classList.add("dp__day--muted");
                if (sameDay(d, today)) btn.classList.add("dp__day--today");

                if (state.single && sameDay(d, state.single)) btn.classList.add("dp__day--on");
                if (state.start && sameDay(d, state.start)) {
                    btn.classList.add("dp__day--on", "dp__day--range-start");
                }
                if (state.end && sameDay(d, state.end)) {
                    btn.classList.add("dp__day--on", "dp__day--range-end");
                }
                if (state.start && state.end && d > state.start && d < state.end) {
                    btn.classList.add("dp__day--in-range");
                }
                (function (picked) {
                    btn.addEventListener("click", function () { onPick(picked); });
                })(d);
                days.appendChild(btn);
                cursor = addDays(cursor, 1);
            }
        }

        monthSel.addEventListener("change", function () {
            if (syncing) return;
            view = new Date(view.getFullYear(), Number(monthSel.value), 1);
            render();
        });
        yearSel.addEventListener("change", function () {
            if (syncing) return;
            view = new Date(Number(yearSel.value), view.getMonth(), 1);
            render();
        });

        prev.addEventListener("click", function () {
            view = new Date(view.getFullYear(), view.getMonth() - 1, 1);
            render();
        });
        next.addEventListener("click", function () {
            view = new Date(view.getFullYear(), view.getMonth() + 1, 1);
            render();
        });

        return {
            el: cal,
            render: render,
            setState: function (s) { Object.assign(state, s); },
            focusOn: function (d) { if (d) view = new Date(d.getFullYear(), d.getMonth(), 1); }
        };
    }

    function buildShell(input) {
        var wrap = document.createElement("div");
        wrap.className = "dp";
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "dp__btn";
        btn.innerHTML = ICON_CAL + '<span class="dp__value dp__value--empty"></span>' + ICON_CHEV;
        var panel = document.createElement("div");
        panel.className = "dp__panel";
        // Lives on <body> so no ancestor overflow can clip it; inside a modal
        // <dialog> it lives on the dialog, as the page behind is unreachable.
        (input.closest("dialog") || document.body).appendChild(panel);
        wrap.append(btn);
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        input.classList.add("dp__native");
        input.setAttribute("tabindex", "-1");
        return {wrap: wrap, btn: btn, panel: panel, value: btn.querySelector(".dp__value")};
    }

    var openPanel = null;

    function placePanel(shell) {
        var btn = shell.btn.getBoundingClientRect();
        var panel = shell.panel;
        panel.style.visibility = "hidden";
        panel.style.display = "flex";
        var w = panel.offsetWidth;
        var h = panel.offsetHeight;
        panel.style.display = "";
        panel.style.visibility = "";

        var gap = 6;
        // Prefer below; flip above only when below would not fit but above does.
        var top = btn.bottom + gap;
        if (top + h > window.innerHeight - 8 && btn.top - gap - h > 8) {
            top = btn.top - gap - h;
        }
        top = Math.max(8, Math.min(top, window.innerHeight - h - 8));

        var left = btn.left;
        if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
        left = Math.max(8, left);

        panel.style.top = Math.round(top) + "px";
        panel.style.left = Math.round(left) + "px";
    }

    // Keep an open panel anchored while the page moves underneath it.
    function trackWhileOpen() {
        if (openPanel) placePanel(openPanel);
    }
    window.addEventListener("scroll", trackWhileOpen, true);
    window.addEventListener("resize", trackWhileOpen);

    function closeOpen() {
        if (openPanel) {
            openPanel.panel.classList.remove("dp__panel--open");
            openPanel.btn.classList.remove("dp__btn--open");
            openPanel.btn.setAttribute("aria-expanded", "false");
            openPanel = null;
        }
    }
    // The month and year dropdowns are custom selects, and a custom select
    // moves its option list to <body> so an overflow:hidden ancestor cannot
    // clip it. That list is therefore outside the calendar panel in the DOM,
    // but it belongs to the calendar: picking a month must not count as a
    // click outside and close the calendar.
    function inOwnDropdown(target) {
        return !!(target && target.closest && target.closest(".cs__menu"));
    }

    // Where the click travelled, recorded when it was dispatched. Clicking a
    // day redraws the grid and removes the very button that was clicked, so
    // by the time this handler runs the target is no longer inside the panel.
    // Asking the panel whether it contains the target would call that an
    // outside click and close the range picker after its first date.
    function clickedInside(e, node) {
        var path = e.composedPath ? e.composedPath() : [];
        return path.indexOf(node) !== -1 || node.contains(e.target);
    }

    document.addEventListener("click", function (e) {
        if (openPanel && !clickedInside(e, openPanel.wrap)
            && !clickedInside(e, openPanel.panel)
            && !inOwnDropdown(e.target)) closeOpen();
    });
    // Capture phase, so this check runs before the custom select's own
    // Escape handler closes the list, whichever script loaded first.
    // Escape inside an open month/year list closes only that list; a second
    // Escape closes the calendar.
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && openPanel
            && !document.querySelector(".cs__menu--open")) {
            var b = openPanel.btn;
            closeOpen();
            b.focus();
        }
    }, true);

    function initSingle(input) {
        var shell = buildShell(input);
        var selected = parseIso(input.value);

        function paint() {
            shell.value.textContent = selected ? pretty(selected) : (input.dataset.placeholder || "Select a date");
            shell.value.classList.toggle("dp__value--empty", !selected);
        }

        var calendar = Calendar(function (d) {
            selected = d;
            input.value = iso(d);
            input.dispatchEvent(new Event("change", {bubbles: true}));
            paint();
            calendar.setState({single: selected});
            calendar.render();
            closeOpen();
        });
        shell.panel.appendChild(calendar.el);

        // Follow a value set by a script (e.g. a dialog pre-filling today).
        input.addEventListener("change", function () {
            var now = parseIso(input.value);
            if ((now && now.getTime()) === (selected && selected.getTime())) return;
            selected = now;
            calendar.setState({single: selected});
            calendar.focusOn(selected);
            paint();
        });

        var foot = document.createElement("div");
        foot.className = "dp__foot";
        var clear = document.createElement("button");
        clear.type = "button";
        clear.className = "btn btn--quiet btn--sm";
        clear.textContent = "Clear";
        clear.addEventListener("click", function () {
            selected = null;
            input.value = "";
            input.dispatchEvent(new Event("change", {bubbles: true}));
            calendar.setState({single: null});
            calendar.render();
            paint();
            closeOpen();
        });
        foot.appendChild(clear);
        calendar.el.appendChild(foot);

        shell.btn.setAttribute("aria-haspopup", "dialog");
        shell.btn.setAttribute("aria-expanded", "false");
        shell.btn.addEventListener("click", function () {
            var isOpen = openPanel === shell;
            closeOpen();
            if (isOpen) return;
            calendar.setState({single: selected});
            calendar.focusOn(selected || new Date());
            calendar.render();
            placePanel(shell);
            shell.panel.classList.add("dp__panel--open");
            shell.btn.classList.add("dp__btn--open");
            shell.btn.setAttribute("aria-expanded", "true");
            openPanel = shell;
        });

        paint();
    }

    var RANGE_PRESETS = [
        ["Today", 0, 0],
        ["Last 7 days", -6, 0],
        ["Last 30 days", -29, 0],
        ["This month", "month", 0],
        ["Last month", "lastmonth", 0]
    ];

    function initRange(startInput, endInput) {
        var shell = buildShell(startInput);
        shell.wrap.appendChild(endInput);
        endInput.classList.add("dp__native");

        var start = parseIso(startInput.value);
        var end = parseIso(endInput.value);
        var picking = "start";

        function paint() {
            var text = (start || end)
                ? (pretty(start) || "…") + " – " + (pretty(end) || "…")
                : (startInput.dataset.placeholder || "Select a date range");
            shell.value.textContent = text;
            shell.value.classList.toggle("dp__value--empty", !start && !end);
        }
        function commit() {
            if (typeof summary !== "undefined" && summary) {
                summary.textContent = (start || end)
                    ? pretty(start) + " → " + (pretty(end) || "…") : "";
            }
            startInput.value = iso(start);
            endInput.value = iso(end);
            startInput.dispatchEvent(new Event("change", {bubbles: true}));
            endInput.dispatchEvent(new Event("change", {bubbles: true}));
            paint();
        }

        var calendar = Calendar(function (d) {
            if (picking === "start" || (start && d < start)) {
                start = d;
                end = null;
                picking = "end";
            } else {
                end = d;
                picking = "start";
            }
            calendar.setState({start: start, end: end});
            calendar.render();
            commit();
            if (start && end) closeOpen();
        });

        var presets = document.createElement("div");
        presets.className = "dp__presets";
        var plabel = document.createElement("div");
        plabel.className = "dp__presets-label";
        plabel.textContent = "Quick ranges";
        presets.appendChild(plabel);
        RANGE_PRESETS.forEach(function (p) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "dp__preset";
            b.textContent = p[0];
            b.addEventListener("click", function () {
                var today = startOfDay(new Date());
                if (p[1] === "month") {
                    start = new Date(today.getFullYear(), today.getMonth(), 1);
                    end = today;
                } else if (p[1] === "lastmonth") {
                    start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                    end = new Date(today.getFullYear(), today.getMonth(), 0);
                } else {
                    start = addDays(today, p[1]);
                    end = addDays(today, p[2]);
                }
                picking = "start";
                calendar.setState({start: start, end: end});
                calendar.focusOn(start);
                calendar.render();
                commit();
            });
            presets.appendChild(b);
        });

        // "Last 30 days" suits a report filter, not a form that records
        // something for the future such as leave. A field opts out with
        // data-presets="none" on its first input.
        if (startInput.dataset.presets === "none") {
            shell.panel.append(calendar.el);
        } else {
            shell.panel.append(presets, calendar.el);
        }

        var foot = document.createElement("div");
        foot.className = "dp__foot";
        var clear = document.createElement("button");
        clear.type = "button";
        clear.className = "btn btn--quiet btn--sm";
        clear.textContent = "Clear";
        clear.addEventListener("click", function () {
            start = end = null;
            picking = "start";
            calendar.setState({start: null, end: null});
            calendar.render();
            commit();
        });
        var summary = document.createElement("span");
        summary.className = "dp__summary";
        var spacer = document.createElement("span");
        spacer.className = "spacer";
        var apply = document.createElement("button");
        apply.type = "button";
        apply.className = "btn btn--primary btn--sm";
        apply.textContent = "Apply";
        apply.addEventListener("click", closeOpen);
        foot.append(summary, spacer, clear, apply);
        calendar.el.appendChild(foot);

        shell.btn.setAttribute("aria-haspopup", "dialog");
        shell.btn.setAttribute("aria-expanded", "false");
        shell.btn.addEventListener("click", function () {
            var isOpen = openPanel === shell;
            closeOpen();
            if (isOpen) return;
            calendar.setState({start: start, end: end});
            calendar.focusOn(start || new Date());
            calendar.render();
            placePanel(shell);
            shell.panel.classList.add("dp__panel--open");
            shell.btn.classList.add("dp__btn--open");
            shell.btn.setAttribute("aria-expanded", "true");
            openPanel = shell;
        });

        paint();
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("input[data-datepicker]").forEach(initSingle);

        var groups = {};
        document.querySelectorAll("input[data-daterange]").forEach(function (input) {
            var key = input.dataset.daterange;
            (groups[key] = groups[key] || []).push(input);
        });
        Object.keys(groups).forEach(function (key) {
            if (groups[key].length === 2) initRange(groups[key][0], groups[key][1]);
            else groups[key].forEach(initSingle);
        });
    });
})();
