"""Select the real MetaTrader 5 transport without importing it at startup."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib
import importlib.util
from pathlib import Path
import platform
import socket
import threading
from typing import Any, Callable, Mapping


SUPPORTED_BACKENDS = {"auto", "native", "bridge"}


@dataclass(frozen=True, slots=True)
class RuntimeDiagnostics:
    operating_system: str
    backend: str
    dependency: str
    dependency_installed: bool
    credentials_file: str
    credentials_present: bool
    bridge_endpoint: str | None
    bridge_reachable: bool | None
    ready: bool
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MT5Runtime:
    """Synchronous MT5 API facade used from worker threads by the async broker."""

    def __init__(
        self,
        backend: str = "auto",
        *,
        bridge_host: str = "127.0.0.1",
        bridge_port: int = 18813,
        system_name: str | None = None,
        module_loader: Callable[[str], Any] | None = None,
    ) -> None:
        requested = backend.lower()
        if requested not in SUPPORTED_BACKENDS:
            raise ValueError(f"Unknown MT5 backend {backend!r}; use auto, native, or bridge")
        self.system_name = system_name or platform.system()
        self.backend = self.resolve_backend(requested, self.system_name)
        self.bridge_host = bridge_host
        self.bridge_port = bridge_port
        self._load = module_loader or importlib.import_module
        self.api: Any = None
        self._bridge_module: Any = None
        self._bridge_handle: Any = None
        self._lock = threading.RLock()

    @staticmethod
    def resolve_backend(requested: str, system_name: str) -> str:
        if requested != "auto":
            return requested
        if system_name == "Windows":
            return "native"
        if system_name == "Darwin":
            return "bridge"
        raise RuntimeError(
            f"Automatic live MT5 transport is not supported on {system_name}; "
            "choose --backend bridge explicitly or run backtesting"
        )

    @property
    def dependency(self) -> str:
        return "MetaTrader5" if self.backend == "native" else "mt5_mac_bridge"

    def connect(self, credentials: Mapping[str, object]) -> None:
        login = credentials.get("login")
        password = credentials.get("password")
        server = credentials.get("server")
        path = credentials.get("path")
        if not login or not password or not server:
            raise ValueError("Credentials require login, password, and server for a DEMO account")

        if self.backend == "native":
            try:
                mt5 = self._load("MetaTrader5")
            except (ImportError, ModuleNotFoundError) as error:
                raise RuntimeError("Install the Windows dependency with: pip install '.[windows]'") from error
            kwargs: dict[str, object] = {
                "login": int(login),
                "password": str(password),
                "server": str(server),
            }
            if path:
                kwargs["path"] = str(path)
            if not mt5.initialize(**kwargs):
                error = mt5.last_error()
                mt5.shutdown()
                raise ConnectionError(f"Native MT5 initialize failed: {error}")
            if not mt5.login(int(login), password=str(password), server=str(server)):
                error = mt5.last_error()
                mt5.shutdown()
                raise ConnectionError(f"Native MT5 login failed: {error}")
            self.api = mt5
            return

        try:
            bridge = self._load("mt5_mac_bridge")
        except (ImportError, ModuleNotFoundError) as error:
            raise RuntimeError("Install the macOS dependency with: pip install '.[macos]'") from error
        try:
            handle = bridge.init(
                backend="bridge",
                host=self.bridge_host,
                port=self.bridge_port,
                login=str(login),
                password=str(password),
                server=str(server),
                path=str(path) if path else None,
                register=False,
            )
        except Exception as error:
            raise ConnectionError(
                f"macOS MT5 bridge connection failed at {self.bridge_host}:{self.bridge_port}: {error}"
            ) from error
        self._bridge_module = bridge
        self._bridge_handle = handle
        self.api = handle.mt5

    def close(self) -> None:
        with self._lock:
            if self.api is None:
                return
            try:
                if self.backend == "bridge" and self._bridge_module is not None:
                    self._bridge_module.shutdown(self._bridge_handle)
                elif hasattr(self.api, "shutdown"):
                    self.api.shutdown()
            finally:
                self.api = None
                self._bridge_handle = None

    def call(self, name: str, *args: object, **kwargs: object) -> Any:
        with self._lock:
            if self.api is None:
                raise ConnectionError("MetaTrader 5 is not connected")
            return getattr(self.api, name)(*args, **kwargs)

    def constant(self, name: str, default: int | None = None) -> int:
        with self._lock:
            if self.api is None:
                raise ConnectionError("MetaTrader 5 is not connected")
            try:
                return int(getattr(self.api, name))
            except AttributeError:
                if default is not None:
                    return default
                raise RuntimeError(f"MT5 transport does not expose required constant {name}") from None

    def diagnostics(self, credentials_file: Path) -> RuntimeDiagnostics:
        try:
            installed = importlib.util.find_spec(self.dependency) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            installed = False
        reachable: bool | None = None
        endpoint: str | None = None
        if self.backend == "bridge":
            endpoint = f"{self.bridge_host}:{self.bridge_port}"
            try:
                with socket.create_connection((self.bridge_host, self.bridge_port), timeout=0.5):
                    reachable = True
            except OSError:
                reachable = False
        credentials_present = credentials_file.exists()
        ready = installed and credentials_present and (reachable is not False)
        if not installed:
            message = f"Install the {self.backend} platform dependency"
        elif not credentials_present:
            message = "Create the DEMO credentials file"
        elif reachable is False:
            message = "Start the local macOS MT5 bridge"
        else:
            message = "Platform prerequisites are ready; account safety is checked on connection"
        return RuntimeDiagnostics(
            operating_system=self.system_name,
            backend=self.backend,
            dependency=self.dependency,
            dependency_installed=installed,
            credentials_file=str(credentials_file),
            credentials_present=credentials_present,
            bridge_endpoint=endpoint,
            bridge_reachable=reachable,
            ready=ready,
            message=message,
        )
