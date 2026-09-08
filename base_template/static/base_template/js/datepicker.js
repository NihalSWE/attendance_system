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
    var WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

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
        var monthBtn = document.createElement("button");
        monthBtn.type = "button";
        monthBtn.className = "dp__seltrigger";
        monthBtn.disabled = true;
        var next = document.createElement("button");
        next.type = "button";
        next.className = "dp__navbtn";
        next.innerHTML = ICON_NEXT;
        next.setAttribute("aria-label", "Next month");
        label.appendChild(monthBtn);
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

        function render() {
            monthBtn.textContent = MONTHS[view.getMonth()] + " " + view.getFullYear();
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
        wrap.append(btn, panel);
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        input.classList.add("dp__native");
        input.setAttribute("tabindex", "-1");
        return {wrap: wrap, btn: btn, panel: panel, value: btn.querySelector(".dp__value")};
    }

    function placePanel(shell) {
        var rect = shell.btn.getBoundingClientRect();
        shell.panel.classList.toggle("dp__panel--up",
            rect.bottom + 380 > window.innerHeight && rect.top > 380);
        shell.panel.classList.toggle("dp__panel--right",
            rect.left + 460 > window.innerWidth);
    }

    var openPanel = null;
    function closeOpen() {
        if (openPanel) {
            openPanel.panel.classList.remove("dp__panel--open");
            openPanel.btn.classList.remove("dp__btn--open");
            openPanel.btn.setAttribute("aria-expanded", "false");
            openPanel = null;
        }
    }
    document.addEventListener("click", function (e) {
        if (openPanel && !openPanel.wrap.contains(e.target)) closeOpen();
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && openPanel) {
            var b = openPanel.btn;
            closeOpen();
            b.focus();
        }
    });

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

        shell.panel.append(presets, calendar.el);

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
        var spacer = document.createElement("span");
        spacer.className = "spacer";
        var apply = document.createElement("button");
        apply.type = "button";
        apply.className = "btn btn--primary btn--sm";
        apply.textContent = "Apply";
        apply.addEventListener("click", closeOpen);
        foot.append(clear, spacer, apply);
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
