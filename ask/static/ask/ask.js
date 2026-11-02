// Ask (2026-11-02): "Thinking…" while the question is read, and the Change
// dialog. The top bar's box (base_template/includes/topbar.html) sends a
// question here and a name to the employee search.
(function () {
    "use strict";

    function thinking(form) {
        var note = form.querySelector("[data-ask-thinking]");
        if (note) note.hidden = false;
        form.classList.add("is-thinking");
    }

    // A question rather than a name: three words or more, or a question mark.
    function isQuestion(text) {
        return /[?？]\s*$/.test(text) || text.split(/\s+/).filter(Boolean).length >= 3;
    }

    document.querySelectorAll("form[data-ask-form]").forEach(function (form) {
        form.addEventListener("submit", function () {
            var input = form.querySelector("input[name=q]");
            var text = input ? input.value.trim() : "";
            var ask = form.getAttribute("data-ask-url");
            if (!text) return;
            if (!ask) {
                thinking(form);   // the Ask page's own box
            } else if (form.hasAttribute("data-ask-only") || isQuestion(text)) {
                form.action = ask;
                thinking(form);
            }
        });
    });

    document.querySelectorAll("[data-open-dialog]").forEach(function (button) {
        var dialog = document.getElementById(button.getAttribute("data-open-dialog"));
        if (!dialog || !dialog.classList.contains("modal")) return;
        button.addEventListener("click", function () {
            if (typeof dialog.showModal === "function") dialog.showModal();
        });
        dialog.querySelectorAll("[data-close-dialog]").forEach(function (close) {
            close.addEventListener("click", function () { dialog.close(); });
        });
    });
})();
