/* Fields that only apply to some choices of another field.

   A form field says when it applies, on its widget:

     data-show-when="occurrence_mode:within_period,consecutive_workdays"
         Show this field's .field block only while the form's
         occurrence_mode is one of those values.

     data-show-when="default_break_minutes:>0"
         Show it only while that number is above 0 (updates as you type).

     data-label-when="deduction_method:fixed_minutes=Minutes to deduct|day_fraction=Days of pay"
         Change the field's label to match the chosen value.

   Hidden fields still post (the server ignores a value that does not
   apply), so nothing is lost if a person switches back. Works with the
   project's custom selects, which fire "change" on the real <select>.
*/
(function () {
    "use strict";

    function parse(rule) {
        var split = rule.indexOf(":");
        return {name: rule.slice(0, split).trim(), rest: rule.slice(split + 1)};
    }

    function valueOf(form, name) {
        var control = form.querySelector('[name="' + name + '"]');
        if (!control) return null;
        if (control.type === "radio") {
            var on = form.querySelector('[name="' + name + '"]:checked');
            return on ? on.value : "";
        }
        return control.value;
    }

    function setLabel(field, text) {
        var label = field.querySelector(".field__label");
        if (!label) return;
        // Keep the required star (a child span); replace only the text.
        var node = label.firstChild;
        while (node && node.nodeType !== Node.TEXT_NODE) node = node.nextSibling;
        if (node) node.textContent = text + " ";
        else label.insertBefore(document.createTextNode(text + " "), label.firstChild);
    }

    function bind(el) {
        var form = el.form || el.closest("form");
        var field = el.closest(".field");
        if (!form || !field) return;
        var names = [];
        var update = function () {
            if (el.dataset.showWhen) {
                var show = parse(el.dataset.showWhen);
                var value = valueOf(form, show.name);
                var rest = show.rest.trim();
                if (rest.charAt(0) === ">") {
                    var number = parseFloat(value);
                    field.hidden = !(number > parseFloat(rest.slice(1)));
                } else {
                    var allowed = rest.split(",").map(function (v) { return v.trim(); });
                    field.hidden = allowed.indexOf(value) === -1;
                }
            }
            if (el.dataset.labelWhen) {
                var labels = parse(el.dataset.labelWhen);
                var current = valueOf(form, labels.name);
                labels.rest.split("|").forEach(function (pair) {
                    var eq = pair.indexOf("=");
                    if (pair.slice(0, eq).trim() === current) setLabel(field, pair.slice(eq + 1).trim());
                });
            }
        };
        [el.dataset.showWhen, el.dataset.labelWhen].forEach(function (rule) {
            if (rule) names.push(parse(rule).name);
        });
        names.forEach(function (name) {
            form.querySelectorAll('[name="' + name + '"]').forEach(function (control) {
                control.addEventListener("change", update);
                control.addEventListener("input", update);
            });
        });
        update();
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-show-when], [data-label-when]").forEach(bind);
    });
})();
