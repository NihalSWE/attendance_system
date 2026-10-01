/* ERP webhook page (2026-10-01): Copy puts the new secret key on the clipboard. */
document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const source = document.querySelector(button.dataset.copy);
    if (!source) return;
    const text = source.textContent.trim();
    const done = function () {
        const label = button.textContent;
        button.textContent = "Copied";
        setTimeout(function () { button.textContent = label; }, 1800);
    };
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done);
        return;
    }
    // Plain http (a local server): select it, so Ctrl+C copies.
    const range = document.createRange();
    range.selectNodeContents(source);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    try {
        document.execCommand("copy");
        done();
    } catch (error) {
        button.textContent = "Press Ctrl+C";
    }
});
