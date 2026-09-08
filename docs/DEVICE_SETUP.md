# Connecting a device

How an administrator gets a ZKTeco SenseFace 2A sending punches to this server,
and how a developer tests the same path without hardware.

The device **pushes outbound** to us. We never poll it on port 4370. The office
therefore needs ordinary outbound internet and nothing else — no static IP, no
inbound port forwarding, no VPN.

---

## 1. In the browser, first

1. **Devices → Register device.** Name it something an administrator would
   recognise on site ("Main Entrance"), and enter the serial number *exactly*
   as printed on the unit — that serial is how an inbound push is matched to
   this record.
2. Choose the branch the device physically sits in, and the device model.
3. Save. The **communication key is shown once** on the device page. Copy it
   before leaving that screen: it is stored only as a hash and cannot be shown
   again. If it is lost, edit the device to issue a new one.

The device page then lists every value to type into the terminal. Read it off
that screen rather than from this document, because it reflects the address the
server is actually reachable on.

## 2. On the device

Enter the values from the "Enter these on the device" table. On ZKTeco ADMS
firmware they live under the comms/network menu — typically
`Menu → Comm. → Cloud Server Setting` (wording varies by firmware).

The device builds the request path itself; only the address and port are
configurable. The full endpoint is shown on the device page for testing with a
browser or curl.

> The menu labels are from the published ZKTeco ADMS protocol and have **not**
> yet been confirmed on this SenseFace 2A. Walking the real device's menus and
> correcting them is outstanding work.

## 3. Reaching a development machine

A device on the office LAN cannot reach `localhost` on a developer's laptop, so
local testing needs a tunnel:

```bash
ngrok http 8000
```

Then:

- add the tunnel hostname to `ALLOWED_HOSTS` in `.env`;
- confirm the origin is covered by `CSRF_TRUSTED_ORIGINS` (`*.ngrok-free.dev`
  and `*.ngrok-free.app` are trusted by default);
- open the device page **on the tunnel hostname**, so the setup table shows the
  tunnel address rather than `127.0.0.1`.

The device page warns when it is being viewed on localhost, because the values
shown there are unusable by a device.

The ingestion endpoints are CSRF-exempt and authenticate the device themselves.
CSRF is not weakened anywhere else.

## 4. Without any hardware

Register a device and enroll an employee in the browser, then send a scenario:

```bash
python manage.py simulate_device --serial SF2A-7781 --comm-key <key> --scenario single_day
```

The simulator speaks the same HTTP the firmware does, so it exercises device
authentication, routing and the acknowledgement body. It deliberately cannot
create a device: registering hardware is the browser's job.

Available scenarios: `single_day`, `replay`, `resend_new_stamp`,
`rapid_repeat`, `offline_backlog`, `unknown_user`, `malformed`. Pass `--date`
for a byte-identical repeat run.

**Simulator results are not evidence about the physical device.** They show the
server handles that input correctly, nothing more.

## 5. Checking it worked

On the device page:

- **Sync health** shows last message received, last punch received, consecutive
  errors, estimated backlog and clock offset.
- **Raw message log** shows exactly what arrived, including payloads that could
  not be parsed.
- **Punches** shows each punch and, when it does not count, why.

A device that reaches us but whose punches do not count is usually an
enrollment problem, not a connection problem: check the unresolved queue.

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing in the message log | Wrong address or port on the device, or the device cannot reach the internet. Confirm with the full endpoint from a browser. |
| `401` in the server log | Serial not registered, device retired/suspended, or the communication key does not match. Re-issue the key and re-enter it. |
| Punches arrive but are `unknown_employee` | No enrollment maps that device user number on the punch date. |
| Punches arrive but are `unauthorized_device` | The company is in assigned-devices mode and the enrollment is recognition-only. Grant it on the enrollment. |
| Punch times are wrong by a fixed number of hours | The device timezone and the timezone recorded here disagree. |
| The same serial is registered twice | Two companies each registered it. Ingestion refuses an ambiguous serial rather than guessing. |
