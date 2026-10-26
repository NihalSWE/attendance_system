/* Shared controls follow field semantics, not the number of options. */
document.addEventListener("DOMContentLoaded", function () {
    const $ = window.jQuery;
    // Search by what is shown or by the option's data-code: an employee is
    // found by their Employee ID even where only the name shows (2026-10-26).
    function matches(params, data) {
        const term = $.trim(params.term || "").toLowerCase();
        if (!term) return data;
        if (data.children && data.children.length) {
            const kids = data.children.map(child => matches(params, child)).filter(Boolean);
            if (!kids.length) return null;
            const copy = $.extend({}, data, true);
            copy.children = kids;
            return copy;
        }
        const text = (data.text || "").toLowerCase();
        const code = (data.element && data.element.getAttribute("data-code") || "").toLowerCase();
        return text.indexOf(term) > -1 || (code && code.indexOf(term) > -1) ? data : null;
    }
    if ($ && $.fn.select2) {
        $(".js-select2").each(function () {
            var $el = $(this);
            var count = $el.find("option").length;
            // In a modal <dialog> the list opens on the dialog, not behind it.
            var $dialog = $el.closest("dialog");
            $el.select2({
                dropdownParent: $dialog.length ? $dialog : $(document.body),
                theme: "paper",
                width: "100%",
                // A short fixed list needs no search box; a long or
                // database-backed one does.
                // data-search="always": a search box however short the list.
                minimumResultsForSearch: ($el.data("search") === "always" || count > 8) ? 0 : Infinity,
                matcher: matches,
                closeOnSelect: !$el.prop("multiple"),
                placeholder: $el.data("placeholder") || null,
                allowClear: !$el.prop("required") && !$el.prop("multiple")
            });
        });
    }
    document.querySelectorAll("input[data-phone-prefix]").forEach(function (input) {
        function normalize() {
            let digits = input.value.replace(/[^0-9]/g, "");
            if (digits.startsWith("0088")) digits = digits.slice(2);
            if (!digits.startsWith("88")) digits = "88" + digits;
            input.value = "+" + digits.slice(0, 15);
        }
        input.addEventListener("input", normalize);
        input.addEventListener("blur", normalize);
        normalize();
    });
    document.addEventListener("keydown", function (event) {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || input.type !== "number") return;
        if (event.ctrlKey || event.metaKey || event.altKey || event.key.length !== 1) return;
        const decimal = input.step === "any" || (input.step && Number(input.step) % 1 !== 0);
        const signed = input.min === "" || Number(input.min) < 0;
        if (/^[0-9]$/.test(event.key)) return;
        if (decimal && event.key === "." && !input.value.includes(".")) return;
        if (signed && event.key === "-" && !input.value.includes("-")) return;
        event.preventDefault();
    });
    document.addEventListener("paste", function (event) {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || input.type !== "number") return;
        const text = event.clipboardData.getData("text").trim();
        const decimal = input.step === "any" || (input.step && Number(input.step) % 1 !== 0);
        const valid = decimal ? /^-?[0-9]+(\.[0-9]+)?$/.test(text) : /^-?[0-9]+$/.test(text);
        if (!valid || (text.startsWith("-") && input.min !== "" && Number(input.min) >= 0)) event.preventDefault();
    });
});
