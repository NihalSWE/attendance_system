/* Time picker for HH:MM boxes.

   The text box stays: typing "0930" or "9:30" is the fastest way to enter a
   time, and it is what the form posts. data-timepicker adds a clock button
   and a panel of hour and minute buttons in the date picker's form
   (timepicker.css). With JavaScript off, the plain box still works.

   Usage:  <input type="text" name="start_time" data-timepicker>

   Typing is tidied on blur: "9" -> 09:00, "930" -> 09:30, "9.30" -> 09:30,
   "21:5" -> 21:05 (as the server reads it). Anything else is left for the
   server to reject with its message, rather than silently changed.
*/
(function () {
    "use strict";

    var ICON_CLOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
    var MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];

    function pad(n) {
        return String(n).padStart(2, "0");
    }

    /* "0930", "9:30", "9.3", "9" -> [9, 30]; null if it is not a time. */
    function parse(text) {
        var t = (text || "").trim();
        if (!t) return null;
        var m = t.match(/^(\d{1,2})(?:[:.](\d{1,2}))?$/) || t.match(/^(\d{1,2})(\d{2})$/);
        if (!m) return null;
        var h = Number(m[1]);
        // As the server reads it (%H:%M, %H.%M): "21:5" is 21:05.
        var min = m[2] === undefined ? 0 : Number(m[2]);
        if (h > 23 || min > 59) return null;
        return [h, min];
    }

    var open = null;   // {wrap, button, panel, input}

    function place(item) {
        var box = item.wrap.getBoundingClientRect();
        var panel = item.panel;
        panel.style.visibility = "hidden";
        panel.style.display = "block";
        var w = panel.offsetWidth, h = panel.offsetHeight;
        panel.style.display = "";
        panel.style.visibility = "";
        var gap = 6;
        var top = box.bottom + gap;
        if (top + h > window.innerHeight - 8 && box.top - gap - h > 8) top = box.top - gap - h;
        top = Math.max(8, Math.min(top, window.innerHeight - h - 8));
        var left = box.left;
        if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
        panel.style.top = Math.round(top) + "px";
        panel.style.left = Math.round(Math.max(8, left)) + "px";
    }

    function close() {
        if (!open) return;
        open.panel.classList.remove("tp__panel--open");
        open.button.setAttribute("aria-expanded", "false");
        open = null;
    }

    function clickedInside(e, node) {
        var path = e.composedPath ? e.composedPath() : [];
        return path.indexOf(node) !== -1 || node.contains(e.target);
    }

    document.addEventListener("click", function (e) {
        if (open && !clickedInside(e, open.wrap) && !clickedInside(e, open.panel)) close();
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && open) {
            var input = open.input;
            close();
            input.focus();
        }
    }, true);
    window.addEventListener("scroll", function () { if (open) place(open); }, true);
    window.addEventListener("resize", function () { if (open) place(open); });

    function init(input) {
        var wrap = document.createElement("div");
        wrap.className = "tp";
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        input.classList.add("tp__input");

        var button = document.createElement("button");
        button.type = "button";
        button.className = "tp__btn";
        button.innerHTML = ICON_CLOCK;
        button.setAttribute("aria-label", "Choose a time");
        button.setAttribute("aria-haspopup", "dialog");
        button.setAttribute("aria-expanded", "false");
        wrap.appendChild(button);

        var panel = document.createElement("div");
        panel.className = "tp__panel";
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-label", "Choose a time");
        document.body.appendChild(panel);   // no ancestor overflow can clip it

        var item = {wrap: wrap, button: button, panel: panel, input: input};
        var chosenHour = null;

        function current() {
            return parse(input.value);
        }

        function write(h, m) {
            input.value = pad(h) + ":" + pad(m);
            input.dispatchEvent(new Event("input", {bubbles: true}));
            input.dispatchEvent(new Event("change", {bubbles: true}));
        }

        function section(title, values, selected, onPick, label) {
            var box = document.createElement("div");
            box.className = "tp__section";
            var head = document.createElement("div");
            head.className = "tp__label";
            head.textContent = title;
            var grid = document.createElement("div");
            grid.className = "tp__grid tp__grid--" + (values.length > 12 ? "hours" : "minutes");
            values.forEach(function (value) {
                var b = document.createElement("button");
                b.type = "button";
                b.className = "tp__cell" + (value === selected ? " tp__cell--on" : "");
                b.textContent = pad(value);
                b.setAttribute("aria-label", label(value));
                b.setAttribute("aria-pressed", value === selected ? "true" : "false");
                b.addEventListener("click", function () { onPick(value); });
                grid.appendChild(b);
            });
            box.append(head, grid);
            return box;
        }

        function render() {
            var now = current();
            var hour = chosenHour !== null ? chosenHour : (now ? now[0] : null);
            var minute = now && (chosenHour === null || chosenHour === now[0]) ? now[1] : null;
            panel.textContent = "";
            var hours = [];
            for (var h = 0; h < 24; h++) hours.push(h);
            panel.append(
                section("Hour", hours, hour, function (h) {
                    chosenHour = h;
                    var m = current() ? current()[1] : 0;
                    write(h, m);
                    render();
                    var firstMinute = panel.querySelector(".tp__grid--minutes .tp__cell");
                    if (firstMinute) firstMinute.focus();
                }, function (h) { return "Hour " + pad(h); }),
                section("Minute", MINUTES, minute, function (m) {
                    var h = chosenHour !== null ? chosenHour : (current() ? current()[0] : 0);
                    write(h, m);
                    chosenHour = null;
                    close();
                    input.focus();
                }, function (m) { return "Minute " + pad(m); })
            );
            if (minute !== null && MINUTES.indexOf(minute) === -1) {
                var note = document.createElement("div");
                note.className = "tp__note";
                note.textContent = "Typed " + pad(hour) + ":" + pad(minute) + " — any minute can be typed.";
                panel.appendChild(note);
            }
        }

        function show() {
            if (open === item) return;
            close();
            chosenHour = null;
            render();
            place(item);
            panel.classList.add("tp__panel--open");
            button.setAttribute("aria-expanded", "true");
            open = item;
        }

        button.addEventListener("click", function () {
            if (open === item) { close(); return; }
            show();
            var on = panel.querySelector(".tp__cell--on") || panel.querySelector(".tp__cell");
            if (on) on.focus();
        });
        input.addEventListener("keydown", function (e) {
            if (e.key === "ArrowDown" && e.altKey) {
                e.preventDefault();
                show();
            }
        });
        // Tidy what was typed, e.g. "930" -> "09:30".
        input.addEventListener("blur", function () {
            var value = parse(input.value);
            if (value) input.value = pad(value[0]) + ":" + pad(value[1]);
        });
        input.addEventListener("input", function () {
            if (open === item) render();
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("input[data-timepicker]").forEach(init);
    });
})();
