from __future__ import annotations

import argparse
import logging
import signal
import socketserver
import struct
import threading
from dataclasses import dataclass

from pymodbus.client import ModbusSerialClient


LOGGER = logging.getLogger("modbus_rtu_tcp_bridge")
MODBUS_FUNCTION_READ_COILS = 1
MODBUS_FUNCTION_READ_DISCRETE_INPUTS = 2
MODBUS_EXCEPTION_ILLEGAL_FUNCTION = 1
MODBUS_EXCEPTION_ILLEGAL_DATA_ADDRESS = 2
MODBUS_EXCEPTION_SERVER_DEVICE_FAILURE = 4


@dataclass(frozen=True)
class BridgeConfig:
    serial_port: str
    baudrate: int
    parity: str
    stopbits: int
    unit_id: int
    tcp_host: str
    tcp_port: int
    serial_timeout: float


class ModbusRtuBridge:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._client = ModbusSerialClient(
            port=config.serial_port,
            baudrate=config.baudrate,
            parity=config.parity,
            stopbits=config.stopbits,
            timeout=config.serial_timeout,
        )

    def close(self) -> None:
        with self._lock:
            self._client.close()

    def read_bits(self, function_code: int, address: int, count: int, unit_id: int | None = None) -> bytes:
        if count < 1 or count > 2000:
            raise ValueError("Numarul de biti ceruti trebuie sa fie intre 1 si 2000.")

        target_unit_id = self.config.unit_id if unit_id is None else unit_id
        with self._lock:
            if not self._client.connect():
                raise RuntimeError(f"Nu pot deschide portul serial {self.config.serial_port}.")

            if function_code == MODBUS_FUNCTION_READ_COILS:
                response = self._client.read_coils(address, count=count, device_id=target_unit_id)
            elif function_code == MODBUS_FUNCTION_READ_DISCRETE_INPUTS:
                response = self._client.read_discrete_inputs(address, count=count, device_id=target_unit_id)
            else:
                raise NotImplementedError(f"Cod de functie nesuportat: {function_code}")

            if response.isError():
                raise RuntimeError(str(response))

            bits = [bool(bit) for bit in (response.bits or [])[:count]]
            if len(bits) < count:
                bits.extend([False] * (count - len(bits)))

        byte_count = (count + 7) // 8
        payload = bytearray(byte_count)
        for bit_index, bit_value in enumerate(bits):
            if bit_value:
                payload[bit_index // 8] |= 1 << (bit_index % 8)
        return bytes(payload)


class ThreadedModbusTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_cls, bridge: ModbusRtuBridge) -> None:
        self.bridge = bridge
        super().__init__(server_address, handler_cls)


class ModbusTcpBridgeHandler(socketserver.BaseRequestHandler):
    server: ThreadedModbusTcpServer

    def handle(self) -> None:
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        LOGGER.debug("Client conectat: %s", peer)
        try:
            while True:
                header = self._recv_exact(7)
                if not header:
                    return

                transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
                if protocol_id != 0:
                    LOGGER.warning("Protocol ID invalid %s de la %s", protocol_id, peer)
                    return

                if length < 2:
                    LOGGER.warning("Cadru Modbus TCP prea scurt de la %s", peer)
                    return

                pdu = self._recv_exact(length - 1)
                if not pdu:
                    return

                function_code = pdu[0]
                try:
                    response_pdu = self._handle_pdu(function_code, pdu, unit_id)
                except ValueError as exc:
                    LOGGER.warning("Cerere invalida de la %s: %s", peer, exc)
                    response_pdu = bytes([function_code | 0x80, MODBUS_EXCEPTION_ILLEGAL_DATA_ADDRESS])
                except NotImplementedError as exc:
                    LOGGER.warning("Cerere nesuportata de la %s: %s", peer, exc)
                    response_pdu = bytes([function_code | 0x80, MODBUS_EXCEPTION_ILLEGAL_FUNCTION])
                except Exception as exc:
                    LOGGER.exception("Eroare bridge pentru %s", peer)
                    response_pdu = bytes([function_code | 0x80, MODBUS_EXCEPTION_SERVER_DEVICE_FAILURE])
                    LOGGER.error("Detaliu eroare pentru %s: %s", peer, exc)

                response = struct.pack(">HHHB", transaction_id, 0, len(response_pdu) + 1, unit_id) + response_pdu
                self.request.sendall(response)
        finally:
            LOGGER.debug("Client deconectat: %s", peer)

    def _handle_pdu(self, function_code: int, pdu: bytes, unit_id: int) -> bytes:
        if function_code not in {MODBUS_FUNCTION_READ_COILS, MODBUS_FUNCTION_READ_DISCRETE_INPUTS}:
            raise NotImplementedError(f"Function code {function_code} nu este suportat de bridge.")
        if len(pdu) < 5:
            raise ValueError("PDU incomplet.")

        address = int.from_bytes(pdu[1:3], "big")
        count = int.from_bytes(pdu[3:5], "big")
        payload = self.server.bridge.read_bits(function_code, address, count, unit_id=unit_id)
        return bytes([function_code, len(payload)]) + payload

    def _recv_exact(self, size: int) -> bytes | None:
        data = bytearray()
        while len(data) < size:
            chunk = self.request.recv(size - len(data))
            if not chunk:
                return None if not data else bytes(data)
            data.extend(chunk)
        return bytes(data)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expune un dispozitiv Modbus RTU (RS485) ca Modbus TCP pentru dashboard-ul Laser1."
    )
    parser.add_argument("--serial-port", default="COM9", help="Portul serial Windows pe care este adaptorul RS485.")
    parser.add_argument("--baudrate", type=int, default=9600, help="Baud rate-ul Modbus RTU.")
    parser.add_argument("--parity", choices=["N", "E", "O"], default="N", help="Parity Modbus RTU.")
    parser.add_argument("--stopbits", type=int, choices=[1, 2], default=1, help="Stop bits Modbus RTU.")
    parser.add_argument("--unit-id", type=int, default=1, help="Adresa slave Modbus RTU.")
    parser.add_argument("--tcp-host", default="0.0.0.0", help="IP-ul local pe care asculta bridge-ul TCP.")
    parser.add_argument("--tcp-port", type=int, default=502, help="Portul Modbus TCP expus in retea.")
    parser.add_argument(
        "--serial-timeout",
        type=float,
        default=1.5,
        help="Timeout serial pentru cererile RTU, in secunde.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivelul de logare.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = BridgeConfig(
        serial_port=args.serial_port,
        baudrate=args.baudrate,
        parity=args.parity,
        stopbits=args.stopbits,
        unit_id=args.unit_id,
        tcp_host=args.tcp_host,
        tcp_port=args.tcp_port,
        serial_timeout=args.serial_timeout,
    )
    bridge = ModbusRtuBridge(config)
    server = ThreadedModbusTcpServer((config.tcp_host, config.tcp_port), ModbusTcpBridgeHandler, bridge)

    stop_event = threading.Event()

    def shutdown_handler(signum, _frame) -> None:
        LOGGER.info("Primit semnalul %s. Oprim bridge-ul...", signum)
        stop_event.set()
        server.shutdown()

    signal.signal(signal.SIGINT, shutdown_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown_handler)

    LOGGER.info(
        "Bridge pornit: RTU %s / %s 8%s%s / unit %s -> TCP %s:%s",
        config.serial_port,
        config.baudrate,
        config.parity,
        config.stopbits,
        config.unit_id,
        config.tcp_host,
        config.tcp_port,
    )
    LOGGER.info("Suporta citiri pentru Coils (FC1) si Discrete Inputs (FC2).")

    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop_event.set()
        server.server_close()
        bridge.close()
        LOGGER.info("Bridge oprit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
