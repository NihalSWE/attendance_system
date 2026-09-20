/* Device users: how the queued writes are going.

   A run of hundreds of commands takes minutes — the device collects a few per
   check-in — so this is a progress card, not a spinner: it survives leaving
   the page, and it stops polling once the run is finished. */
document.addEventListener("DOMContentLoaded", function () {
    var card = document.querySelector("[data-job]");
    if (!card) return;
    var url = card.dataset.job;
    var el = function (name) { return card.querySelector("[data-job-" + name + "]"); };
    var timer = null;

    function minutes(seconds) {
        if (!seconds) return "";
        if (seconds < 60) return "about " + seconds + "s left";
        return "about " + Math.ceil(seconds / 60) + " min left";
    }

    function paint(job) {
        card.hidden = !job.total;
        if (!job.total) return;
        el("title").textContent = (job.running ? "Sending to " : "Finished sending to ")
            + card.dataset.jobDevice;
        el("count").textContent = job.done + job.refused;
        el("total").textContent = job.total;
        el("done").textContent = job.done;
        el("waiting").textContent = job.waiting + job.sent;
        el("refused").textContent = job.refused;
        el("left").textContent = minutes(job.seconds_left);
        el("bar").setAttribute("aria-valuenow", job.percent);
        el("bar").firstElementChild.style.width = job.percent + "%";
        card.classList.toggle("job--running", job.running);
        card.classList.toggle("job--done", !job.running && job.total > 0);
        card.classList.toggle("job--refused", job.refused > 0);
    }

    function poll() {
        fetch(url, {headers: {"X-Requested-With": "XMLHttpRequest"}})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (job) {
                if (!job) return;
                paint(job);
                if (!job.running && timer) {
                    clearInterval(timer);
                    timer = null;
                }
            })
            .catch(function () { /* a hiccup: the next tick tries again */ });
    }

    card.dataset.jobDevice = (el("title").textContent.split(" to ")[1] || "").trim();
    if (card.classList.contains("job--running") || !card.hidden) {
        timer = setInterval(poll, 5000);
        poll();
    }
});
