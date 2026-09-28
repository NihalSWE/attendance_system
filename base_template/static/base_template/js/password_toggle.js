/* Show or hide a password (Nihal, 2026-09-28): every password box gets an eye
   button inside its right edge. The box keeps its name and value, so forms
   post exactly as before; without this script it is a plain password box. */
(function () {
    "use strict";

    var EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
    var EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M3 3l18 18"/><path d="M10.6 5.1A10.7 10.7 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.1"/>' +
        '<path d="M6.6 6.6A17.4 17.4 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/>' +
        '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

    function enhance(input) {
        if (input.dataset.pwReady) return;
        input.dataset.pwReady = "1";
        var wrap = document.createElement("span");
        wrap.className = "pw-field";
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);

        var button = document.createElement("button");
        button.type = "button";
        button.className = "pw-toggle";
        button.setAttribute("aria-label", "Show password");
        button.setAttribute("aria-pressed", "false");
        button.innerHTML = EYE;
        button.addEventListener("click", function () {
            var show = input.type === "password";
            input.type = show ? "text" : "password";
            button.innerHTML = show ? EYE_OFF : EYE;
            button.setAttribute("aria-label", show ? "Hide password" : "Show password");
            button.setAttribute("aria-pressed", show ? "true" : "false");
            input.focus();
        });
        wrap.appendChild(button);
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll('input[type="password"]').forEach(enhance);
    });
})();
