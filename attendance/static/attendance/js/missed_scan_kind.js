/* Show only the times a missing case needs: one scan for a check-in or a
   check-out, two when both are missing, none for a whole day (the shift's
   times are used). Without JavaScript every field shows. */
document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select[data-missing-kind]").forEach(function (select) {
        const form = select.closest("form");
        const field = name => [...form.querySelectorAll(`[data-missing-time="${name}"]`)]
            .map(input => input.closest(".field")).filter(Boolean)[0];
        const sync = function () {
            const kind = select.value;
            const at = field("at"), out = field("at_out");
            if (at) at.hidden = kind === "whole_day";
            if (out) out.hidden = kind !== "both";
        };
        select.addEventListener("change", sync);
        if (window.jQuery) window.jQuery(select).on("change", sync);
        sync();
    });
});
