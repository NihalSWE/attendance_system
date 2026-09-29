/* Show only the times a missing case needs: one scan for a check-in or a
   check-out, two when both are missing, and for a whole day only the day
   (the shift's times are used; a scan's day is its own date). Without
   JavaScript every field shows. */
document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select[data-missing-kind]").forEach(function (select) {
        const form = select.closest("form");
        const field = name => [...form.querySelectorAll(`[data-missing-time="${name}"]`)]
            .map(input => input.closest(".field")).filter(Boolean)[0];
        const sync = function () {
            const kind = select.value;
            const at = field("at"), out = field("at_out"), day = field("work_date");
            if (at) at.hidden = kind === "whole_day";
            if (out) out.hidden = kind !== "both";
            if (day) day.hidden = kind !== "whole_day";
        };
        select.addEventListener("change", sync);
        if (window.jQuery) window.jQuery(select).on("change", sync);
        sync();
    });
});
