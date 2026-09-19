/* Confirm before a form is sent, in the project's own modal — never the
   browser's built-in confirmation box.

   <form data-confirm="What will happen, in a sentence or two."
         data-confirm-title="Remove this user?"
         data-confirm-button="Remove"
         data-confirm-tone="danger">          (tone: "danger" or omitted)

   The button that was pressed is kept, so a form with several buttons
   (formaction) still goes where that button said. */
(function () {
    var dialog, titleEl, bodyEl, okBtn, pending = null;

    function build() {
        dialog = document.createElement("dialog");
        dialog.className = "modal modal--confirm";
        dialog.setAttribute("aria-labelledby", "confirm-title");
        dialog.innerHTML =
            '<div class="modal__card">' +
            '  <div class="modal__head"><h2 id="confirm-title"></h2>' +
            '    <button class="modal__close" type="button" data-confirm-cancel aria-label="Close">×</button></div>' +
            '  <div class="modal__body"><p class="confirm__text"></p></div>' +
            '  <div class="modal__foot">' +
            '    <button class="btn btn--ghost" type="button" data-confirm-cancel>Cancel</button>' +
            '    <button class="btn btn--primary" type="button" data-confirm-ok>Continue</button>' +
            '  </div>' +
            '</div>';
        document.body.appendChild(dialog);
        titleEl = dialog.querySelector("#confirm-title");
        bodyEl = dialog.querySelector(".confirm__text");
        okBtn = dialog.querySelector("[data-confirm-ok]");
        dialog.querySelectorAll("[data-confirm-cancel]").forEach(function (b) {
            b.addEventListener("click", function () { dialog.close(); });
        });
        dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
        dialog.addEventListener("close", function () { pending = null; });
        okBtn.addEventListener("click", function () {
            var job = pending;
            pending = null;
            dialog.close();
            if (!job) return;
            job.form.dataset.confirmed = "1";
            if (typeof job.form.requestSubmit === "function") job.form.requestSubmit(job.submitter || undefined);
            else job.form.submit();
        });
    }

    document.addEventListener("submit", function (e) {
        var form = e.target;
        if (!form.matches || !form.matches("form[data-confirm]")) return;
        if (form.dataset.confirmed === "1") {
            delete form.dataset.confirmed;
            return;
        }
        e.preventDefault();
        if (!dialog) build();
        pending = {form: form, submitter: e.submitter};
        titleEl.textContent = form.dataset.confirmTitle || "Are you sure?";
        bodyEl.textContent = form.dataset.confirm;
        okBtn.textContent = form.dataset.confirmButton || "Continue";
        okBtn.className = "btn " + (form.dataset.confirmTone === "danger" ? "btn--danger" : "btn--primary");
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
        okBtn.focus();
    }, true);
})();
