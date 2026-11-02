// Bangladeshi mobile numbers (2026-11-02): boxes marked data-bd-phone take
// digits only, at most 11, and say at once when the number cannot be a BD
// mobile number (01 then 3-9, 11 digits). inventory.forms.bd_number checks
// again when the form is saved.
(function () {
    "use strict";
    var BANGLA = "০১২৩৪৫৬৭৮৯";
    // Every start a BD mobile number can have: "", "0", "01", "013".. "019".
    var START = /^(0(1([3-9]\d{0,8})?)?)?$/;
    var WHOLE = /^01[3-9]\d{8}$/;

    function digitsOf(text) {
        return text.replace(/[০-৯]/g, function (d) { return String(BANGLA.indexOf(d)); })
                   .replace(/\D/g, "").slice(0, 11);
    }

    function problem(value, finished) {
        if (!value) return "";
        if (!START.test(value)) return "A Bangladeshi mobile number starts with 013 to 019.";
        if (finished && !WHOLE.test(value)) return "Write all 11 digits, e.g. 01711000000.";
        return "";
    }

    document.querySelectorAll("input[data-bd-phone]").forEach(function (input) {
        // The Add and Edit modals each load this file: set each box up once.
        if (input.dataset.bdReady) return;
        input.dataset.bdReady = "1";
        var field = input.closest(".field");
        var note = document.createElement("span");
        // Its own classes: the shared "typing clears the red mark" listener
        // (fetch_forms.js) would remove a field__error / ui-field--error at once.
        note.className = "bd-phone__error";
        note.hidden = true;
        if (field) field.appendChild(note);

        function show(finished) {
            var digits = digitsOf(input.value);
            if (digits !== input.value) input.value = digits;
            var text = problem(digits, finished);
            note.textContent = text;
            note.hidden = !text;
            if (field) field.classList.toggle("bd-phone--wrong", Boolean(text));
            input.setAttribute("aria-invalid", text ? "true" : "false");
            return !text;
        }

        input.addEventListener("input", function () { show(false); });
        input.addEventListener("blur", function () { show(true); });
        if (input.form) {
            input.form.addEventListener("submit", function (event) {
                if (!show(true)) {
                    event.preventDefault();
                    input.focus();
                }
            });
        }
    });
})();
