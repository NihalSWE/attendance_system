/* Forms that save without reloading the page (2026-10-27, plan §4.3;
   the server side is base_template/fetch.py).

   <form data-fetch> is sent with fetch. While it saves, its button says so and
   cannot be pressed twice. The answer appears just above the Save button:
   green when saved (then the page goes on, or the modal closes), red when not,
   with every wrong box outlined and its reason under it. What was typed stays,
   also when the connection drops. */
(function () {
    const NO_ANSWER = "Not saved: no connection, or the server did not answer. "
        + "Try again; what you typed is still here.";

    function messageBox(form, button) {
        let box = form.querySelector("[data-form-message]");
        if (box) return box;
        box = document.createElement("div");
        box.className = "ui-message";
        box.setAttribute("data-form-message", "");
        box.setAttribute("role", "status");
        box.hidden = true;
        // Just above the buttons: the modal's foot, a form's action row, or the button.
        const anchor = (button && (button.closest(".modal__foot, .ui-form-actions") || button))
            || form.lastElementChild;
        anchor.parentNode.insertBefore(box, anchor);
        return box;
    }

    function show(box, kind, text, more) {
        box.className = "ui-message ui-message--" + kind;
        box.textContent = "";
        const words = document.createElement("div");
        words.textContent = text;
        if (more && more.length) {
            const list = document.createElement("ul");
            more.forEach(function (reason) {
                const item = document.createElement("li");
                item.textContent = reason;
                list.appendChild(item);
            });
            words.appendChild(list);
        }
        box.appendChild(words);
        box.setAttribute("role", kind === "error" ? "alert" : "status");
        box.hidden = false;
    }

    function clear(form, box) {
        box.hidden = true;
        form.querySelectorAll(".ui-field--error").forEach(el => el.classList.remove("ui-field--error"));
        form.querySelectorAll(".field__error").forEach(el => el.remove());
    }

    function busy(button, on) {
        if (!button) return;
        if (on) {
            button.dataset.before = button.innerHTML;     // icon and words, back after
            button.setAttribute("aria-busy", "true");
            button.textContent = "Saving…";
        } else if (button.getAttribute("aria-busy") === "true") {
            button.removeAttribute("aria-busy");
            button.innerHTML = button.dataset.before;
        }
    }

    // Go on to the page the answer names. Back to this same page (the recipe
    // card, #recipe) a plain assign() only scrolls to the anchor and never
    // reloads, so the button kept saying "Saving…" though all was saved
    // (2026-10-31): reload instead, keeping the anchor.
    function goTo(address) {
        const target = new URL(address, window.location.href);
        const here = window.location;
        if (target.origin === here.origin && target.pathname === here.pathname && target.search === here.search) {
            if (target.hash !== here.hash) history.replaceState(history.state, "", target.hash || here.pathname + here.search);
            here.reload();
            return;
        }
        here.assign(target.href);
    }

    // Mark each named box; return the reasons that have no box on the page.
    function markFields(form, errors) {
        const loose = [];
        let first = null;
        Object.entries(errors || {}).forEach(function ([name, reasons]) {
            const input = form.querySelector('[name="' + CSS.escape(name) + '"]');
            const field = input && input.closest(".field");
            if (!field || field.hidden) {
                loose.push(...reasons);
                return;
            }
            field.classList.add("ui-field--error");
            reasons.forEach(function (reason) {
                const span = document.createElement("span");
                span.className = "field__error";
                span.textContent = reason;
                field.appendChild(span);
            });
            first = first || input;
        });
        if (first) first.focus({preventScroll: false});
        return loose;
    }

    document.addEventListener("submit", async function (event) {
        const form = event.target.closest("form[data-fetch]");
        if (!form) return;
        event.preventDefault();
        const button = event.submitter || form.querySelector('[type="submit"]');
        if (button && button.getAttribute("aria-busy") === "true") return;
        const box = messageBox(form, button);
        clear(form, box);
        const body = new FormData(form);
        if (button && button.name) body.append(button.name, button.value);
        busy(button, true);
        let reply = null;
        try {
            // A button can send the form elsewhere (formaction: Passed / Failed).
            const target = (button && button.getAttribute("formaction")) || form.getAttribute("action");
            const response = await fetch(target || window.location.href, {
                method: "POST",
                body: body,
                credentials: "same-origin",
                headers: {"X-Requested-With": "fetch", "Accept": "application/json"},
            });
            reply = await response.json();
        } catch (error) {
            reply = null;
        }
        if (!reply) {
            busy(button, false);
            show(box, "error", NO_ANSWER);
            return;
        }
        if (reply.ok) {
            show(box, "ok", reply.message || "Saved.");
            form.dispatchEvent(new CustomEvent("ui:saved", {bubbles: true, detail: reply}));
            if (reply.redirect) {
                window.setTimeout(() => goTo(reply.redirect), 700);
                return;
            }
            busy(button, false);
            const dialog = form.closest("dialog[open]");
            if (dialog) window.setTimeout(() => dialog.close(), 900);
            return;
        }
        busy(button, false);
        const loose = markFields(form, reply.errors);
        show(box, "error", reply.message || "Not saved.", (reply.form_errors || []).concat(loose));
    });

    // Typing in a box marked red takes its mark away.
    document.addEventListener("input", function (event) {
        const field = event.target.closest && event.target.closest(".field.ui-field--error");
        if (!field) return;
        field.classList.remove("ui-field--error");
        field.querySelectorAll(".field__error").forEach(el => el.remove());
    });
})();
