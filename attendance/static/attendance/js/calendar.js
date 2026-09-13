/* The attendance calendar's day panel.

   Every day is already a <button> in the markup, so the keyboard reaches it
   and Enter works without this file; what this adds is opening the day's
   history in place instead of loading a page. If the script does not load the
   squares are still readable — they just do not open.

   The panel is one element reused for every day. It is a drawer on desktop
   and a full-width sheet on a phone, which is the same element at two widths,
   so there is nothing here that cares which. */
document.addEventListener("DOMContentLoaded", function () {
    const calendar = document.querySelector("[data-attendance-calendar]");
    const panel = document.querySelector("[data-day-panel]");
    if (!calendar || !panel) return;

    const content = panel.querySelector("[data-panel-content]");
    const body = panel.querySelector(".cal-panel__body");
    const template = calendar.dataset.dayUrlTemplate;
    if (!content || !template) return;

    let lastTrigger = null;

    function urlFor(day) {
        /* The view builds the template with a placeholder date, so the shape
           of the URL stays the router's business, not this file's. */
        return template.replace("0000-00-00", day);
    }

    function open(day, trigger) {
        lastTrigger = trigger || null;
        content.innerHTML =
            '<div class="cal-panel__body-inner t-muted">Loading…</div>';
        panel.hidden = false;
        document.body.style.overflow = "hidden";
        if (body) body.focus();

        fetch(urlFor(day), {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(function (response) {
                return response.ok ? response.text() : null;
            })
            .then(function (html) {
                if (html === null) {
                    content.innerHTML =
                        '<div class="cal-panel__body-inner t-muted">' +
                        "That day could not be loaded.</div>";
                    return;
                }
                content.innerHTML = html;
            })
            .catch(function () {
                content.innerHTML =
                    '<div class="cal-panel__body-inner t-muted">' +
                    "That day could not be loaded.</div>";
            });
    }

    function close() {
        panel.hidden = true;
        document.body.style.overflow = "";
        content.innerHTML = "";
        /* Back to the square that opened it, so tabbing does not restart at
           the top of the page. */
        if (lastTrigger) lastTrigger.focus();
        lastTrigger = null;
    }

    calendar.addEventListener("click", function (event) {
        const square = event.target.closest("[data-day]");
        if (!square || !calendar.contains(square)) return;
        open(square.dataset.day, square);
    });

    /* Close on the button, on the scrim, and on Escape — the three ways
       people expect a drawer to shut. The close button is inside content that
       is replaced on every open, so it is caught by delegation. */
    panel.addEventListener("click", function (event) {
        if (event.target.closest("[data-panel-close]")) close();
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !panel.hidden) close();
    });
});
