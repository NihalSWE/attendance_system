/* ============================================================================
   Holiday year calendar.

   Each day is a real checkbox (visually hidden, like the day picker). The
   "Selected dates" list is what posts: one row per date, with a hidden date and
   a name box. This script keeps the two in step, and:

   - Shift-click selects or clears every day between the last day touched and
     this one (existing company-wide holidays are skipped);
   - a date right after a selected, named date takes that name, so a holiday
     of several days is typed once;
   - arrow keys move between days (one Tab stop for the whole calendar);
   - changing year keeps the selection: with dates selected, the year buttons
     post the form back instead of following their link.
   ========================================================================= */
(function () {
    "use strict";

    var form = document.getElementById("holiday-year-form");
    if (!form) {
        return;
    }
    var grid = form.querySelector("[data-year-calendar]");
    var list = form.querySelector("[data-rows]");
    var template = document.getElementById("yc-row-template");
    var countBadge = form.querySelector("[data-selected-count]");
    var saveButton = form.querySelector("[data-save]");
    var emptyHint = form.querySelector("[data-empty]");
    var goYear = form.querySelector('input[name="go_year"]');
    var days = Array.prototype.slice.call(grid.querySelectorAll(".yc-day__input"));
    var byDate = {};
    days.forEach(function (input) {
        byDate[input.value] = input;
    });

    var anchor = null;       // index of the last day touched, for Shift-click
    var shiftHeld = false;   // Shift state of the click or key that caused a change

    // ------------------------------------------------------------ rows

    function rows() {
        return Array.prototype.slice.call(list.querySelectorAll(".yc-row"));
    }

    function rowFor(iso) {
        return list.querySelector('.yc-row[data-date="' + iso + '"]');
    }

    function previousDay(iso) {
        var d = new Date(iso + "T00:00:00Z");
        d.setUTCDate(d.getUTCDate() - 1);
        return d.toISOString().slice(0, 10);
    }

    function addRow(input) {
        var iso = input.value;
        if (rowFor(iso)) {
            return;
        }
        var row = template.content.firstElementChild.cloneNode(true);
        row.dataset.date = iso;
        row.querySelector('input[name="date"]').value = iso;
        row.querySelector(".yc-row__date").textContent = input.dataset.label;
        var name = row.querySelector(".yc-row__name");
        name.setAttribute("aria-label", "Holiday name for " + input.dataset.label);
        row.querySelector(".yc-row__remove").setAttribute("aria-label", "Remove " + input.dataset.label);
        var before = rowFor(previousDay(iso));
        if (before) {
            name.value = before.querySelector(".yc-row__name").value;
        }
        // Keep the list in date order; ISO dates sort as strings.
        var next = rows().find(function (other) {
            return other.dataset.date > iso;
        });
        list.insertBefore(row, next || null);
    }

    function removeRow(iso) {
        var row = rowFor(iso);
        if (row) {
            row.remove();
        }
    }

    function refresh() {
        var count = rows().length;
        countBadge.textContent = count;
        saveButton.disabled = count === 0;
        saveButton.textContent = count
            ? "Add " + count + " holiday" + (count === 1 ? "" : "s")
            : "Add holidays";
        emptyHint.hidden = count > 0;
    }

    function apply(input) {
        if (input.checked) {
            addRow(input);
        } else {
            removeRow(input.value);
        }
    }

    // ------------------------------------------------------- selecting

    // Shift+mousedown would start a text selection across the calendar, and a
    // browser may then not pass a Shift-click on a label to its checkbox. So a
    // Shift-click on a day is handled here: the day is toggled directly and
    // the change handler below extends it to the range.
    grid.addEventListener("mousedown", function (event) {
        if (event.shiftKey && event.target.closest(".yc-day")) {
            event.preventDefault();
        }
    });

    grid.addEventListener("click", function (event) {
        var label = event.target.closest(".yc-day");
        var input = label && label.querySelector(".yc-day__input");
        shiftHeld = event.shiftKey;
        if (!input || !event.shiftKey || event.target === input) {
            return;  // a plain click: the label toggles its checkbox itself
        }
        event.preventDefault();
        if (input.disabled) {
            return;
        }
        input.checked = !input.checked;
        input.dispatchEvent(new Event("change", { bubbles: true }));
    }, true);

    grid.addEventListener("change", function (event) {
        var input = event.target;
        if (!input.classList.contains("yc-day__input")) {
            return;
        }
        var index = days.indexOf(input);
        if (shiftHeld && anchor !== null && anchor !== index) {
            var from = Math.min(anchor, index);
            var to = Math.max(anchor, index);
            for (var i = from; i <= to; i += 1) {
                var day = days[i];
                if (!day.disabled) {
                    day.checked = input.checked;
                    apply(day);
                }
            }
        } else {
            apply(input);
        }
        anchor = index;
        shiftHeld = false;
        refresh();
    });

    list.addEventListener("click", function (event) {
        var button = event.target.closest(".yc-row__remove");
        if (!button) {
            return;
        }
        var row = button.closest(".yc-row");
        var input = byDate[row.dataset.date];
        if (input) {
            input.checked = false;
        }
        row.remove();
        refresh();
    });

    // -------------------------------------------------------- keyboard

    // One Tab stop for the calendar: today if shown, else the first
    // selectable day. Arrows move a day or a week; Space toggles.
    function focusable(index) {
        return days[index] && !days[index].disabled;
    }

    function setTabStop(input) {
        days.forEach(function (day) {
            day.tabIndex = -1;
        });
        input.tabIndex = 0;
    }

    var today = grid.querySelector(".yc-day--today .yc-day__input:not(:disabled)");
    var first = today || days.find(function (day) {
        return !day.disabled;
    });
    if (first) {
        setTabStop(first);
    }

    grid.addEventListener("focusin", function (event) {
        if (event.target.classList.contains("yc-day__input")) {
            setTabStop(event.target);
        }
    });

    grid.addEventListener("keydown", function (event) {
        var input = event.target;
        if (!input.classList.contains("yc-day__input")) {
            return;
        }
        if (event.key === " ") {
            shiftHeld = event.shiftKey;
            return;
        }
        var step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
        if (!step) {
            return;
        }
        event.preventDefault();
        var index = days.indexOf(input) + step;
        // Skip past existing company-wide holidays, which cannot be selected.
        while (days[index] && !focusable(index)) {
            index += step > 0 ? 1 : -1;
        }
        if (focusable(index)) {
            days[index].focus();
        }
    });

    // ------------------------------------------------------ year change

    form.querySelectorAll("[data-go-year]").forEach(function (link) {
        link.addEventListener("click", function (event) {
            if (!rows().length) {
                return;  // nothing to keep: follow the link
            }
            event.preventDefault();
            goYear.value = link.dataset.goYear;
            form.submit();
        });
    });

    refresh();
})();
