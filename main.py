import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from enum import Enum
from time import monotonic

import serial
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel


load_dotenv()

logger = logging.getLogger(__name__)


class PetState(str, Enum):
    IDLE = "idle"
    EATING = "eating"
    SLEEPING = "sleeping"


SERIAL_COMMANDS = {
    PetState.IDLE: b"0",
    PetState.EATING: b"1",
    PetState.SLEEPING: b"2",
}


@dataclass(frozen=True)
class Settings:
    serial_port: str
    baud_rate: int
    sleep_after_seconds: float
    watcher_interval_seconds: float

    @classmethod
    def from_environment(cls) -> "Settings":
        serial_port = os.getenv("SERIAL_PORT")
        if not serial_port:
            raise RuntimeError(
                "SERIAL_PORT is required. Copy .env.example to .env and set the Pico's port."
            )

        try:
            baud_rate = int(os.getenv("SERIAL_BAUD_RATE", "115200"))
            sleep_after_seconds = float(os.getenv("SLEEP_AFTER_SECONDS", "300"))
            watcher_interval_seconds = float(
                os.getenv("WATCHER_INTERVAL_SECONDS", "1")
            )
        except ValueError as exc:
            raise RuntimeError("Serial and timer settings must be numeric.") from exc

        if baud_rate <= 0:
            raise RuntimeError("SERIAL_BAUD_RATE must be greater than zero.")
        if sleep_after_seconds <= 0:
            raise RuntimeError("SLEEP_AFTER_SECONDS must be greater than zero.")
        if watcher_interval_seconds <= 0:
            raise RuntimeError("WATCHER_INTERVAL_SECONDS must be greater than zero.")

        return cls(
            serial_port=serial_port,
            baud_rate=baud_rate,
            sleep_after_seconds=sleep_after_seconds,
            watcher_interval_seconds=watcher_interval_seconds,
        )


class PicoController:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.current_state = PetState.IDLE
        self.last_activity = monotonic()
        self._serial: serial.Serial | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        try:
            self._serial = await asyncio.to_thread(
                serial.Serial,
                port=self.settings.serial_port,
                baudrate=self.settings.baud_rate,
                timeout=1,
                write_timeout=1,
            )
        except serial.SerialException as exc:
            raise RuntimeError(
                f"Could not open Pico serial port {self.settings.serial_port!r}: {exc}"
            ) from exc

        logger.info("Connected to Pico on %s", self.settings.serial_port)

    async def close(self) -> None:
        if self._serial is not None and self._serial.is_open:
            await asyncio.to_thread(self._serial.close)
        logger.info("Closed Pico serial connection")

    async def set_state(self, state: PetState, *, record_activity: bool) -> None:
        if self._serial is None or not self._serial.is_open:
            raise serial.SerialException("The Pico serial connection is not open.")

        async with self._write_lock:
            await asyncio.to_thread(self._write_command, SERIAL_COMMANDS[state])
            self.current_state = state
            if record_activity:
                self.last_activity = monotonic()

    def _write_command(self, command: bytes) -> None:
        if self._serial is None:
            raise serial.SerialException("The Pico serial connection is not open.")

        self._serial.write(command)
        self._serial.flush()

    async def watch_for_sleep(self) -> None:
        while True:
            await asyncio.sleep(self.settings.watcher_interval_seconds)

            async with self._write_lock:
                inactive_for = monotonic() - self.last_activity
                if (
                    inactive_for >= self.settings.sleep_after_seconds
                    and self.current_state is not PetState.SLEEPING
                ):
                    try:
                        await asyncio.to_thread(
                            self._write_command,
                            SERIAL_COMMANDS[PetState.SLEEPING],
                        )
                        self.current_state = PetState.SLEEPING
                        logger.info("Pico entered sleeping state after inactivity")
                    except (serial.SerialException, serial.SerialTimeoutException):
                        logger.exception("Could not send sleeping state to Pico")


@asynccontextmanager
async def lifespan(app: FastAPI):
    controller = PicoController(Settings.from_environment())
    await controller.connect()
    app.state.pico = controller
    watcher_task = asyncio.create_task(controller.watch_for_sleep())

    try:
        yield
    finally:
        watcher_task.cancel()
        with suppress(asyncio.CancelledError):
            await watcher_task
        await controller.close()


app = FastAPI(title="Sprout Tamagotchi Server", lifespan=lifespan)


class StateEvent(BaseModel):
    state: PetState


@app.post("/state")
async def set_state(event: StateEvent, request: Request):
    controller: PicoController = request.app.state.pico

    try:
        await controller.set_state(event.state, record_activity=True)
    except (serial.SerialException, serial.SerialTimeoutException) as exc:
        logger.exception("Could not send %s state to Pico", event.state.value)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Pico is not available.",
        ) from exc

    return {"ok": True}
