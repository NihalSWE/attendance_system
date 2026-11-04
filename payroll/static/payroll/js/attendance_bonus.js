/* Attendance bonus (2026-11-04): two "only one of these" rules on the page.

   - Weekly workers get the bonus every week OR once a month: ticking one of
     the two switches (data-only-one="the other's name") unticks the other
     and says so. The server refuses both as well.
   - A department's or an employee's bonus is a % OR a flat amount: typing
     in one box (data-either="the other's name") empties the other.
*/
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    document.querySelectorAll("[data-only-one]").forEach(function (box) {
        box.addEventListener("change", function () {
            if (!box.checked) return;
            const other = box.form && box.form.querySelector('[name="' + box.dataset.onlyOne + '"]');
            if (!other || !other.checked) return;
            other.checked = false;
            other.dispatchEvent(new Event("change", {bubbles: true}));
            const note = box.form.querySelector("[data-only-one-note]");
            if (note) note.hidden = false;
        });
    });

    document.querySelectorAll("[data-either]").forEach(function (box) {
        box.addEventListener("input", function () {
            if (!box.value.trim()) return;
            const scope = box.closest("form, tr") || document;
            const other = scope.querySelector('[name="' + box.dataset.either + '"]');
            if (other && other.value) other.value = "";
        });
    });

    // An employee on a monthly salary: the % as an amount, or the amount as a %.
    document.querySelectorAll("form[data-bonus-base]").forEach(function (form) {
        const base = Number(form.dataset.bonusBase);
        const percent = form.querySelector("[data-bonus-percent]");
        const amount = form.querySelector("[data-bonus-amount]");
        const text = form.querySelector("[data-bonus-same-text]");
        if (!base || !percent || !amount || !text) return;
        const money = n => n.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
        function say() {
            if (percent.value) {
                text.textContent = percent.value + "% is " + money(base * Number(percent.value) / 100) + " a month.";
            } else if (amount.value) {
                text.textContent = money(Number(amount.value)) + " is " +
                    (Number(amount.value) / base * 100).toFixed(2).replace(/\.?0+$/, "") + "% of it.";
            } else {
                text.textContent = "type a % or an amount to see the other.";
            }
        }
        percent.addEventListener("input", say);
        amount.addEventListener("input", say);
        say();
    });
});
