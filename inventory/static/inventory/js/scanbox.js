/* The scan box: one input that takes a barcode scanner, typing, or the phone camera.

   A USB or Bluetooth scanner acts as a keyboard: it types the code and
   presses Enter (some send Tab). The code goes to the lookup
   (data-lookup-url), and the answer is
     - announced as a "scan:result" event on the box (detail = the answer),
       for a document page to add or increase its line, and
     - shown under the box when it has [data-scan-result].
   The input is cleared and keeps the focus, ready for the next scan.

   [data-scan-camera] opens the camera (html5-qrcode, loaded only when
   asked). The browser allows the camera on https:// pages and on
   localhost; elsewhere the button explains that.

   Everything shown comes from the server's JSON and is written as text,
   never as HTML. */
(function () {
    "use strict";

    var CAMERA_LIBRARY = "https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js";
    var loading = null;

    function loadCamera() {
        if (window.Html5Qrcode) return Promise.resolve();
        if (loading) return loading;
        loading = new Promise(function (resolve, reject) {
            var script = document.createElement("script");
            script.src = CAMERA_LIBRARY;
            script.onload = resolve;
            script.onerror = function () { loading = null; reject(new Error("load")); };
            document.head.appendChild(script);
        });
        return loading;
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    var KIND = {package: "Package", batch: "Batch", item: "Item", location: "Bin",
                pack: "Pack barcode", gs1: "GS1 barcode"};

    function render(target, answer, scanned) {
        var card = el("div", "scan-answer" + (answer.found ? "" : " scan-answer--missing"));
        var head = el("div", "scan-answer__head");
        if (answer.found) {
            head.appendChild(el("span", "badge badge--info", KIND[answer.kind] || answer.kind));
        } else {
            head.appendChild(el("span", "badge badge--danger", "Not found"));
        }
        head.appendChild(el("span", "scan-answer__code", answer.code || scanned));
        card.appendChild(head);
        card.appendChild(el("p", "scan-answer__message", answer.message || ""));
        if (answer.found) {
            var facts = el("dl", "inv-facts");
            function fact(label, value) {
                if (!value) return;
                facts.appendChild(el("dt", "", label));
                facts.appendChild(el("dd", "", value));
            }
            if (answer.item) fact("Item", answer.item.code + " · " + answer.item.name);
            if (answer.batch) {
                fact("Batch", answer.batch.batch_no + (answer.batch.status !== "released"
                    ? " (" + answer.batch.status_label + ")" : ""));
                fact("Expiry", answer.batch.expiry_date);
            }
            if (answer.quantity) {
                var qty = answer.quantity + " " + answer.uom;
                if (answer.item && answer.uom !== answer.item.base_uom && answer.quantity_base) {
                    qty += " = " + answer.quantity_base + " " + answer.item.base_uom;
                }
                fact("Quantity", qty);
            }
            if (answer.warehouse) {
                fact("Where", answer.warehouse.code + (answer.location ? " / " + answer.location.code : ""));
            }
            if (answer.label) fact("Label", answer.label.status_label + ", printed " + answer.label.printed_count + "×");
            if (facts.childNodes.length) card.appendChild(facts);
            (answer.warnings || []).forEach(function (warning) {
                card.appendChild(el("div", "alert alert--warning", warning));
            });
            if (answer.item && target.dataset.itemUrl) {
                var link = el("a", "btn btn--ghost btn--sm", "Open the item");
                link.href = target.dataset.itemUrl.replace("/0/", "/" + answer.item.id + "/");
                card.appendChild(link);
            }
        }
        target.insertBefore(card, target.firstChild);
        while (target.childNodes.length > 10) target.removeChild(target.lastChild);
    }

    function setUp(box) {
        var input = box.querySelector("[data-scan-input]");
        var result = box.querySelector("[data-scan-result]");
        var url = box.dataset.lookupUrl;
        // Scans can arrive faster than the answers (a scanner fires one after
        // another): each Enter takes what was typed at once, and the lookups
        // run one at a time, in order.
        var queue = [];
        var busy = false;

        function submit(code) {
            code = (code || "").trim();
            if (code) queue.push(code);
            pump();
        }

        function pump() {
            if (busy || !queue.length) return;
            busy = true;
            var code = queue.shift();
            fetch(url + "?code=" + encodeURIComponent(code), {
                credentials: "same-origin", headers: {"Accept": "application/json"}
            }).then(function (response) {
                return response.json().catch(function () {
                    return {found: false, code: code, message: "The server did not answer properly."};
                });
            }).catch(function () {
                return {found: false, code: code, message: "No connection: try again."};
            }).then(function (answer) {
                busy = false;
                if (result) render(result, answer, code);
                box.dispatchEvent(new CustomEvent("scan:result", {detail: answer, bubbles: true}));
                if (!document.querySelector("dialog[open]")) input.focus();
                pump();
            });
        }
        box.scan = submit;

        input.addEventListener("keydown", function (event) {
            if ((event.key === "Enter" || (event.key === "Tab" && input.value)) && !event.isComposing) {
                event.preventDefault();
                var code = input.value;
                input.value = "";
                submit(code);
            }
        });

        var cameraButton = box.querySelector("[data-scan-camera]");
        var dialog = box.querySelector("[data-scan-camera-dialog]");
        if (!cameraButton || !dialog) return;
        var view = dialog.querySelector("[data-scan-camera-view]");
        var note = dialog.querySelector("[data-scan-camera-note]");
        var scanner = null;

        function stop() {
            if (scanner) {
                var current = scanner;
                scanner = null;
                current.stop().catch(function () {}).then(function () { current.clear(); });
            }
        }
        dialog.addEventListener("close", function () { stop(); input.focus(); });
        cameraButton.addEventListener("click", function () {
            note.textContent = "";
            if (typeof dialog.showModal === "function") dialog.showModal();
            if (!window.isSecureContext) {
                note.textContent = "The camera works only on an https:// address (or localhost). " +
                    "Use a barcode scanner, or type the code.";
                return;
            }
            note.textContent = "Starting the camera…";
            loadCamera().then(function () {
                scanner = new window.Html5Qrcode(view.id);
                return scanner.start({facingMode: "environment"}, {fps: 10, qrbox: {width: 260, height: 160}},
                    function (text) {
                        dialog.close();
                        submit(text);
                    }, function () {});
            }).then(function () {
                note.textContent = "Point the camera at the barcode or QR code.";
            }).catch(function () {
                note.textContent = "The camera could not start. Allow the camera for this site, " +
                    "or use a barcode scanner.";
                stop();
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-scan-box]").forEach(setUp);
    });
})();
