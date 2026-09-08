"""A stand-in for a physical device, speaking the same HTTP the firmware does.

It posts over real HTTP to the real endpoint rather than calling services
directly, so a run exercises device authentication, the CSRF exemption, URL
routing and the acknowledgement body exactly as the SenseFace 2A will.

Uses urllib from the standard library: the simulator must not add a dependency
to a requirements file owned by another developer.
"""

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class Exchange:
    """One request/response pair, for reporting what actually happened."""

    label: str
    url: str
    status: int
    response_body: str
    sent_rows: int


class SimulatedDevice:
    def __init__(self, *, base_url, serial, comm_key="", key_id="", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.serial = serial
        self.comm_key = comm_key
        self.key_id = key_id
        self.timeout = timeout

    def _url(self, path, **params):
        query = {"SN": self.serial, **params}
        if self.comm_key:
            query["key"] = self.comm_key
        if self.key_id:
            query["key_id"] = self.key_id
        return f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"

    def _send(self, url, *, data=None, method="GET", label="", sent_rows=0):
        request = urllib.request.Request(
            url,
            data=data.encode("utf-8") if data is not None else None,
            method=method,
            headers={"Content-Type": "text/plain", "User-Agent": "device-simulator"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            status = exc.code
        return Exchange(
            label=label, url=url, status=status, response_body=body.strip(),
            sent_rows=sent_rows,
        )

    def handshake(self):
        """What the device does on boot, to learn how it should behave."""
        return self._send(
            self._url("/iclock/cdata", options="all", pushver="2.4.1"),
            label="handshake",
        )

    def poll_for_commands(self):
        return self._send(self._url("/iclock/getrequest"), label="command poll")

    def send_batch(self, batch):
        return self._send(
            self._url("/iclock/cdata", table="ATTLOG", Stamp=batch.stamp),
            data=batch.body(),
            method="POST",
            label=batch.label or f"batch {batch.stamp}",
            sent_rows=len(batch.rows),
        )

    def run_scenario(self, scenario):
        return [self.send_batch(batch) for batch in scenario.batches]
