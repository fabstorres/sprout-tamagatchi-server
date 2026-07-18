import asyncio
import os
import unittest
from unittest.mock import patch

from main import PetState, PicoController, Settings


class FakeSerial:
    def __init__(self):
        self.is_open = True
        self.commands: list[bytes] = []
        self.flush_count = 0

    def write(self, command: bytes) -> None:
        self.commands.append(command)

    def flush(self) -> None:
        self.flush_count += 1

    def close(self) -> None:
        self.is_open = False


def make_settings(**overrides) -> Settings:
    values = {
        "serial_port": "/dev/fake-pico",
        "baud_rate": 115200,
        "sleep_after_seconds": 300,
        "watcher_interval_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


class SettingsTests(unittest.TestCase):
    def test_serial_port_is_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SERIAL_PORT is required"):
                Settings.from_environment()

    def test_settings_are_loaded_from_environment(self):
        environment = {
            "SERIAL_PORT": "/dev/ttyACM7",
            "SERIAL_BAUD_RATE": "9600",
            "SLEEP_AFTER_SECONDS": "12.5",
            "WATCHER_INTERVAL_SECONDS": "0.25",
        }

        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_environment()

        self.assertEqual(settings.serial_port, "/dev/ttyACM7")
        self.assertEqual(settings.baud_rate, 9600)
        self.assertEqual(settings.sleep_after_seconds, 12.5)
        self.assertEqual(settings.watcher_interval_seconds, 0.25)


class PicoControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = PicoController(make_settings())
        self.serial = FakeSerial()
        self.controller._serial = self.serial

    async def test_each_state_writes_the_expected_command(self):
        for state, command in (
            (PetState.IDLE, b"0"),
            (PetState.EATING, b"1"),
            (PetState.SLEEPING, b"2"),
        ):
            await self.controller.set_state(state, record_activity=True)
            self.assertEqual(self.serial.commands[-1], command)
            self.assertEqual(self.controller.current_state, state)

        self.assertEqual(self.serial.flush_count, 3)

    async def test_watcher_sends_sleep_after_inactivity(self):
        self.controller = PicoController(
            make_settings(
                sleep_after_seconds=0.01,
                watcher_interval_seconds=0.001,
            )
        )
        self.controller._serial = self.serial

        watcher = asyncio.create_task(self.controller.watch_for_sleep())
        try:
            async with asyncio.timeout(0.5):
                while self.serial.commands != [b"2"]:
                    await asyncio.sleep(0.001)
        finally:
            watcher.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await watcher

        self.assertEqual(self.controller.current_state, PetState.SLEEPING)


if __name__ == "__main__":
    unittest.main()
