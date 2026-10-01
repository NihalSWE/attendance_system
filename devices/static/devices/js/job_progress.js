/* Device users: how the queued writes are going.

   A run of hundreds of commands takes minutes — the device collects a few per
   check-in — so this is a progress card, not a spinner: it survives leaving
   the page, and it stops polling once the run is finished.

   "Load employees onto this device" first prepares the people here, in the
   background (2026-10-01); while that runs, the card's top part counts them,
   and the sending part below fills as the device collects them. */
document.addEventListener("DOMContentLoaded", function () {
    var card = document.querySelector("[data-job]");
    if (!card) return;
    var url = card.dataset.job;
    var device = card.dataset.jobDevice || "";
    var el = function (name) { return card.querySelector("[data-job-" + name + "]"); };
    var timer = null;

    function minutes(seconds) {
        if (!seconds) return "";
        if (seconds < 60) return "about " + seconds + "s left";
        return "about " + Math.ceil(seconds / 60) + " min left";
    }

    function bar(node, percent) {
        node.setAttribute("aria-valuenow", percent);
        node.firstElementChild.style.width = percent + "%";
    }

    function paintLoad(load) {
        var box = el("prep");
        box.hidden = !load;
        if (!load) return;
        el("prep-title").textContent = load.running ? "Preparing employees for " + device
            : load.stopped ? "Preparing stopped by an error"
            : "Employees prepared for " + device;
        el("prep-count").textContent = load.handled;
        el("prep-total").textContent = load.total;
        el("prep-done").textContent = load.prepared;
        el("prep-failed").textContent = load.failed;
        bar(el("prep-bar"), load.percent);
        var list = el("prep-failures");
        list.replaceChildren();
        load.failures.forEach(function (item) {
            var li = document.createElement("li");
            var name = document.createElement("strong");
            name.textContent = item[0];
            li.append(name, ": " + item[1]);
            list.append(li);
        });
        if (load.more_failures) {
            var more = document.createElement("li");
            more.className = "t-faint";
            more.textContent = "and " + load.more_failures + " more";
            list.append(more);
        }
        list.hidden = !load.failures.length;
    }

    function paint(job) {
        var load = job.load;
        var preparing = !!(load && load.running);
        var running = job.running || preparing;
        card.hidden = !job.total && !load;
        paintLoad(load);
        el("send").hidden = !job.total;
        el("title").textContent = (job.running || !job.total ? "Sending to " : "Finished sending to ")
            + device;
        el("count").textContent = job.done + job.refused;
        el("total").textContent = job.total;
        el("done").textContent = job.done;
        el("waiting").textContent = job.waiting + job.sent;
        el("refused").textContent = job.refused;
        el("left").textContent = preparing ? "" : minutes(job.seconds_left);
        bar(el("bar"), job.percent);
        card.classList.toggle("job--running", running);
        card.classList.toggle("job--done", !running && job.total > 0);
        card.classList.toggle("job--refused", job.refused > 0 || !!(load && load.failed));
        return running;
    }

    function poll() {
        fetch(url, {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (job) {
                if (!job) return;
                if (!paint(job) && timer) {
                    clearInterval(timer);
                    timer = null;
                }
            })
            .catch(function () { /* a hiccup: the next tick tries again */ });
    }

    if (card.classList.contains("job--running") || !card.hidden) {
        timer = setInterval(poll, 3000);
        poll();
    }
});
