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

**Two command dialects** (`devices/services/protocol.py`). The SenseFace 2A
(PushSDK 3.x, `DeviceType=acc`) takes the table form, `DATA QUERY
tablename=user,…`. The SenseFace 3A (announces `pushver=2.4.1`,
`DeviceType=att`) answers that with `Return=-1004` and takes `DATA QUERY
USERINFO` (every user, sent back as `USER PIN=…` lines) and `DATA QUERY ATTLOG
StartTime=…⇥EndTime=…` (attendance history). The device page offers only what
the device speaks. A 2.x device does not re-send scans made while it could not
reach the server, so after a gap of 10 minutes the server asks for them on its
next poll (measured 2026-09-15).

## 6. Changing the server address from the software

Devices → the device → **Edit** has a **Server address** field. Changing it
never writes the address straight to the terminal, because a wrong value there
puts the device out of reach of every screen we have and the only fix is to
walk to it.

Instead the server proves the address first:

1. **The address is checked before the device is told anything.** The server
   fetches its own `/iclock/serveraddress-check` endpoint at the address you
   typed, with a one-time token, and requires a reply signed with this
   deployment's `SECRET_KEY`. That answers the question that matters — does
   this address reach *this* software — rather than the weaker "does something
   answer". A typo, a wrong port, http/https swapped, a tunnel that is not
   running, or a hostname missing from `ALLOWED_HOSTS` all fail here, and the
   device is never contacted. This is the clean revert: nothing changed.
2. **Only then is the change queued** for the device's next check-in. The
   saved address stays as it was.
3. **The device picks it up** on its next poll, which depends on its push
   interval.
4. **The device checks in at the new address.** The new address is saved only
   now, and only because a request actually arrived on that hostname — not
   because the device answered `Return=0`, which it does before it tries.
5. **If it never arrives**, the page says which of the two failures happened:
   the device kept checking in at the old address (it ignored the command,
   nothing on it changed), or it went silent (it switched and cannot reach us,
   which only the terminal can undo — the page then names the exact old
   address, port and menu path to type back in).

The panel on the device page updates itself while this runs. Only one change
per device at a time.

### What this firmware actually exposes

Measured against the SenseFace 2A in the office, firmware
`ZAM70-NF24HA-Ver3.0.15`, PushSDK `Ver 3.0.4S-20240809`, `DeviceType=acc`.
`GET OPTIONS <comma-separated names>` is answered by the device POSTing to
`/iclock/querydata?type=options&tablename=options` with the values it
recognises; an option it does not know, or holds empty, is simply omitted.

| Option | Meaning | Verified |
|---|---|---|
| `IclockSvrIP` | Server address. Holds a hostname, not only an IP. | Yes — written, then read back |
| `IclockSvrPort` | Server port | Yes — read `8081`, written `443`, read back |
| `IclockSvrFun` | Cloud server enabled (`1`) | Read only |
| `AutoServerFunOn` | Auto-server feature flag (`1`) | Read only |
| `EnableProxyServer` / `ProxyServerIP` / `ProxyServerPort` | Proxy, off by default (`0` / `0.0.0.0` / `0`) | Read only |
| `Delay` | Push interval, seconds | Read only |
| `WebSite` | Present, value is a single space. Unused. | Read only |

**There is no backup or secondary server option on this firmware.** Roughly 350
candidate names were probed (`ServerIP`, `WebServerIP`, `AdmsIP`, `SvrAddr`,
`IclockSvrBackupIP`, tilde-prefixed variants, and every plausible
prefix × suffix combination) and nothing beyond the table above came back.
`INFO` and `CHECK` return large blocks that do not include the server address
either, and `DATA QUERY tablename=options|config|network` is refused with
`Return=-629`. There is a proxy, which is a different thing.

That shapes the recovery story: **the software cannot ask the device where it
is pointing.** It can only observe the address an incoming request arrived on,
which is exactly what step 4 does.

### Two traps, both measured

**One option per `SET OPTION` command.** Sending

    SET OPTION IclockSvrIP=host\tIclockSvrPort=443

answers `Return=0` and then reads back as

    IclockSvrIP=host\tIclockSvrPort=443 , IclockSvrPort=8081

— the whole tab-separated string became the value of the first option and the
port never changed. This is the same shape as the lowercase `DATA UPDATE user`
field-name trap: the firmware answers `0` and quietly does the wrong thing. The
code sends two commands, port first, handed over in the same reply.

**`Return=0` is not proof.** It means the command was accepted, not that it was
understood or applied. Nothing in this flow treats a command result as success;
only a request arriving at the new address does.

### What was proved on the hardware, and what was not

Proved, against the live device through the ngrok tunnel:

- `IclockSvrIP` is the correct option name — it read back empty before the
  write and held the hostname after it.
- One option per command works; the tab-separated pair does not.
- Step 1 stops a bad address without touching the device. Tested with a tunnel
  that is not running (404), an unresolvable host, a wrong port (timeout),
  http against an https-only tunnel (307), and a hostname resolving to the
  server but missing from `ALLOWED_HOSTS` (400, reported as exactly that). No
  command was queued in any of them.
- The full success path end to end: check passes, two commands queue, the
  device takes them, answers `Return=0`, and the address is saved when its
  request arrives.

Not proved on the hardware, and worth doing with someone standing at the
terminal:

- A change to a **different** address. The success path above pointed the
  device at the address it was already using, so every branch was safe. It
  exercised the plumbing and the option names, not a genuine move.
- The deliberate *lost device* case — pointing it somewhere it cannot reach,
  confirming the "set it back on the device" instructions, and typing the old
  address back in by hand.

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing in the message log | Wrong address or port on the device, or the device cannot reach the internet. Confirm with the full endpoint from a browser. |
| `401` in the server log | Serial not registered, device retired/suspended, or the communication key does not match. Re-issue the key and re-enter it. |
| Punches arrive but are `unknown_employee` | No enrollment maps that device user number on the punch date. |
| Punches arrive but are `unauthorized_device` | The company is in assigned-devices mode and the enrollment is recognition-only. Grant it on the enrollment. |
| Punch times are wrong by a fixed number of hours | The device timezone and the timezone recorded here disagree. |
| The same serial is registered twice | Two companies each registered it. Ingestion refuses an ambiguous serial rather than guessing. |
| A server address change says "not reachable" | The address does not reach this server. Nothing was sent to the device; fix the address and try again. |
| A change ends as "did not apply the change" | The device kept checking in at the old address. Its settings are unchanged; check the firmware supports `IclockSvrIP`. |
| A change ends as "not reachable at the new address" | The device switched and cannot reach where it was sent. No screen can fix this — use the address, port and menu path the page names, at the terminal. |
