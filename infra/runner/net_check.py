"""容器内断网自检：能连上外网则以非 0 退出。

在 `--network=none` 下运行时应当打印 `network_enforced=true` 并返回 0。
"""

from __future__ import annotations

import socket
import sys

TARGETS: tuple[tuple[str, int], ...] = (("1.1.1.1", 443), ("pypi.org", 443), ("github.com", 443))


def is_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    leaked = [f"{host}:{port}" for host, port in TARGETS if is_reachable(host, port)]
    if leaked:
        print("network_enforced=false reachable=" + ",".join(leaked))
        return 1
    print("network_enforced=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())