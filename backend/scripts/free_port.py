"""İlk boş TCP portunu yazdırır (start.bat): python scripts/free_port.py 8000 → 8000, doluysa / Windows ayırmışsa 8001 …"""

import socket
import sys


def free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


start = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
print(next((p for p in range(start, start + 200) if free(p)), start))
