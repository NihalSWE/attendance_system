/* Register / Edit device: the serial number is checked as it is typed
   (2026-10-03) - free, already on a device of this company, or already used
   by another company - with the words saving would give. */
document.addEventListener("DOMContentLoaded", function () {
    const input = document.querySelector("input[data-serial-check]");
    if (!input) return;
    const note = document.createElement("p");
    note.className = "field__help serial-check";
    note.setAttribute("aria-live", "polite");
    input.closest(".field") ? input.closest(".field").appendChild(note)
                            : input.insertAdjacentElement("afterend", note);
    let timer = null;
    let asked = "";

    function show(ok, message) {
        note.textContent = message || "";
        note.classList.toggle("serial-check--taken", ok === false);
        note.classList.toggle("serial-check--free", ok === true && !!message);
        input.setAttribute("aria-invalid", ok === false ? "true" : "false");
    }

    function check() {
        const serial = input.value.trim();
        if (serial === asked) return;
        asked = serial;
        if (!serial) { show(null, ""); return; }
        const url = new URL(input.dataset.serialCheck, window.location.origin);
        url.searchParams.set("serial", serial);
        if (input.dataset.device) url.searchParams.set("device", input.dataset.device);
        fetch(url, {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (answer) {
                if (answer && input.value.trim() === serial) show(answer.ok, answer.message);
            })
            .catch(function () { /* saving checks it anyway */ });
    }

    input.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(check, 400);
    });
    input.addEventListener("blur", check);
    if (input.value.trim() && !input.dataset.device) check();
});
