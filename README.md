# Sprout Tamagotchi Server

A write-only FastAPI server that controls the Sprout Tamagotchi over USB serial.
It forwards state changes to a Raspberry Pi Pico and automatically puts the
character to sleep after a period of inactivity.

## Serial protocol

The server sends one ASCII byte for each state supported by the Pico firmware:

| API state | Serial byte | Animation |
| --- | --- | --- |
| `idle` | `0` | Idle |
| `eating` | `1` | Eating |
| `sleeping` | `2` | Sleeping |

There is intentionally no read endpoint. The physical Tamagotchi displays the
state after receiving its command.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- A Raspberry Pi Pico running the Sprout Tamagotchi firmware
- The Pico connected to the server host over USB

## Setup

Install the project dependencies:

```bash
uv sync
```

Create the local environment file:

```bash
cp .env.example .env
```

Find the Pico's serial port. On Linux it will commonly be `/dev/ttyACM0`:

```bash
ls /dev/ttyACM*
```

Set that path in `.env`:

```dotenv
SERIAL_PORT=/dev/ttyACM0
SERIAL_BAUD_RATE=115200
SLEEP_AFTER_SECONDS=300
WATCHER_INTERVAL_SECONDS=1
```

Only `SERIAL_PORT` is required. The values shown for the other settings are
their defaults.

Common serial-port formats include:

- Linux: `/dev/ttyACM0`
- macOS: `/dev/cu.usbmodem...`
- Windows: `COM3`

On Linux, a permission error usually means the current user needs serial-port
access. Add the user to the `dialout` group, then sign out and back in:

```bash
sudo usermod -aG dialout "$USER"
```

Close Thonny, `screen`, or any serial monitor connected to the Pico before
starting the server. Only one program can normally own the serial port.

## Run the server

Connect the Pico, then start FastAPI:

```bash
uv run fastapi dev main.py
```

The server opens the configured serial port during startup. It exits with a
clear error if `SERIAL_PORT` is missing or the Pico cannot be opened. The serial
connection and background sleep watcher are closed cleanly during shutdown.

Use one server process. Multiple workers cannot safely share one USB serial
device.

## Send state commands

Set the Tamagotchi to eating:

```bash
curl -X POST http://127.0.0.1:8000/state \
  -H "Content-Type: application/json" \
  -d '{"state":"eating"}'
```

Set it back to idle:

```bash
curl -X POST http://127.0.0.1:8000/state \
  -H "Content-Type: application/json" \
  -d '{"state":"idle"}'
```

Sleeping can also be requested directly:

```bash
curl -X POST http://127.0.0.1:8000/state \
  -H "Content-Type: application/json" \
  -d '{"state":"sleeping"}'
```

A successful request returns HTTP `200 OK`:

```json
{"ok": true}
```

Unknown states are rejected with HTTP `422`. If a serial write fails, the
server returns HTTP `503`.

Every accepted state request counts as activity and resets the inactivity
timer. When no state has been received for `SLEEP_AFTER_SECONDS`, the server
sends `2` once. A later request wakes or changes the Tamagotchi and starts the
timer again.

Interactive API documentation is available while the server is running at
<http://127.0.0.1:8000/docs>.

## Tests

The tests use a fake serial device, so the Pico does not need to be connected:

```bash
uv run python -m unittest discover -s tests -v
```

## Project files

- `main.py` — FastAPI app, serial controller, and inactivity watcher
- `.env.example` — environment configuration template
- `tests/test_main.py` — state-command and sleep-watcher tests
