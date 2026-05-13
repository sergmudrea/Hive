#!/usr/bin/env python3
"""
HONEYCOMB VIEWER v1.0
CLI-утилита для чтения результатов Red Team операций
Архитектор: Кронос | Тимлид: Мастер

ВОЗМОЖНОСТИ:
- Просмотр всех записей Honeycomb
- Фильтрация по клиенту, задаче, дате
- Экспорт в JSON, CSV, Markdown
- Интерактивный режим с поиском
- Статистика по операциям
"""

import os
import sys
import json
import sqlite3
import argparse
import textwrap
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

HONEYCOMB_DIR = os.environ.get("HIVE_HONEYCOMB_DIR", "./honeycomb")
DB_PATH = os.path.join(HONEYCOMB_DIR, "honeycomb.db")


# ============================================================================
# ЦВЕТА ДЛЯ ТЕРМИНАЛА
# ============================================================================

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

    @staticmethod
    def disable():
        for attr in dir(Colors):
            if not attr.startswith('_') and attr != 'disable':
                setattr(Colors, attr, '')


# ============================================================================
# БАЗА ДАННЫХ
# ============================================================================

class HoneycombDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        if not os.path.exists(self.db_path):
            print(f"{Colors.YELLOW}[!] База данных не найдена: {self.db_path}{Colors.RESET}")
            print(f"{Colors.DIM}    Проверь HIVE_HONEYCOMB_DIR или запусти HiveMind Core{Colors.RESET}")
            sys.exit(1)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        if self.conn:
            self.conn.close()

    def get_records(self, client_id: Optional[str] = None,
                    task_type: Optional[str] = None,
                    days: Optional[int] = None,
                    limit: int = 100,
                    offset: int = 0) -> List[Dict[str, Any]]:
        query = "SELECT * FROM honeycomb WHERE 1=1"
        params = []

        if client_id:
            query += " AND client_id = ?"
            params.append(client_id)
        if task_type:
            query += " AND type = ?"
            params.append(task_type)
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            query += " AND created_at >= ?"
            params.append(cutoff)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> Dict[str, Any]:
        stats = {}

        cursor = self.conn.execute("SELECT COUNT(*) as total FROM honeycomb")
        stats["total_records"] = cursor.fetchone()["total"]

        cursor = self.conn.execute(
            "SELECT client_id, COUNT(*) as count FROM honeycomb GROUP BY client_id"
        )
        stats["by_client"] = {row["client_id"]: row["count"] for row in cursor.fetchall()}

        cursor = self.conn.execute(
            "SELECT type, COUNT(*) as count FROM honeycomb GROUP BY type"
        )
        stats["by_type"] = {row["type"]: row["count"] for row in cursor.fetchall()}

        cursor = self.conn.execute(
            "SELECT date(created_at) as day, COUNT(*) as count "
            "FROM honeycomb GROUP BY day ORDER BY day DESC LIMIT 30"
        )
        stats["by_day"] = {row["day"]: row["count"] for row in cursor.fetchall()}

        cursor = self.conn.execute("SELECT MIN(created_at) as first, MAX(created_at) as last FROM honeycomb")
        row = cursor.fetchone()
        stats["first_record"] = row["first"]
        stats["last_record"] = row["last"]

        return stats

    def get_clients(self) -> List[str]:
        cursor = self.conn.execute("SELECT DISTINCT client_id FROM honeycomb ORDER BY client_id")
        return [row["client_id"] for row in cursor.fetchall()]

    def get_task_types(self) -> List[str]:
        cursor = self.conn.execute("SELECT DISTINCT type FROM honeycomb ORDER BY type")
        return [row["type"] for row in cursor.fetchall()]

    def search(self, query_str: str, limit: int = 50) -> List[Dict[str, Any]]:
        search_term = f"%{query_str}%"
        cursor = self.conn.execute(
            "SELECT * FROM honeycomb WHERE data LIKE ? OR client_id LIKE ? OR task_id LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (search_term, search_term, search_term, limit)
        )
        return [dict(row) for row in cursor.fetchall()]


# ============================================================================
# ФОРМАТТЕРЫ ВЫВОДА
# ============================================================================

class OutputFormatter:
    @staticmethod
    def format_record(record: Dict[str, Any], index: int = 0) -> str:
        lines = []
        lines.append(f"{Colors.BOLD}{Colors.CYAN}─── Запись #{index} ───{Colors.RESET}")
        lines.append(f"{Colors.BOLD}Клиент:{Colors.RESET}    {record.get('client_id', 'N/A')}")
        lines.append(f"{Colors.BOLD}Задача:{Colors.RESET}     {record.get('task_id', 'N/A')}")
        lines.append(f"{Colors.BOLD}Тип:{Colors.RESET}        {record.get('type', 'N/A')}")
        lines.append(f"{Colors.BOLD}Создано:{Colors.RESET}    {record.get('created_at', 'N/A')}")
        lines.append(f"{Colors.BOLD}Данные:{Colors.RESET}")
        data = record.get('data', '')

        try:
            parsed = json.loads(data)
            formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
            if len(formatted) > 2000:
                formatted = formatted[:2000] + f"\n{Colors.DIM}... (обрезано, всего {len(formatted)} символов){Colors.RESET}"
        except:
            formatted = data if len(data) <= 2000 else data[:2000] + f"\n{Colors.DIM}... (обрезано){Colors.RESET}"

        lines.append(textwrap.indent(formatted, "  "))
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_table(records: List[Dict[str, Any]]) -> str:
        if not records:
            return f"{Colors.DIM}Нет записей для отображения.{Colors.RESET}"

        lines = []
        header = f"{'ID':<6} {'Клиент':<20} {'Тип':<15} {'Дата':<20} {'Данные (первые 60 символов)'}"
        lines.append(f"{Colors.BOLD}{Colors.CYAN}{header}{Colors.RESET}")
        lines.append("-" * len(header))

        for i, record in enumerate(records):
            client = record.get('client_id', '')[:19]
            rtype = record.get('type', '')[:14]
            created = record.get('created_at', '')[:19]
            data = record.get('data', '')[:57]

            try:
                parsed = json.loads(data)
                data = json.dumps(parsed, ensure_ascii=False)[:57]
            except:
                pass

            lines.append(f"{i:<6} {client:<20} {rtype:<15} {created:<20} {data}")

        return "\n".join(lines)

    @staticmethod
    def format_stats(stats: Dict[str, Any]) -> str:
        lines = []
        lines.append(f"{Colors.BOLD}{Colors.HEADER}═══ СТАТИСТИКА HONEYCOMB ═══{Colors.RESET}")
        lines.append(f"{Colors.BOLD}Всего записей:{Colors.RESET} {stats['total_records']}")
        lines.append(f"{Colors.BOLD}Период:{Colors.RESET} {stats['first_record']} — {stats['last_record']}")
        lines.append("")

        if stats.get("by_client"):
            lines.append(f"{Colors.BOLD}По клиентам:{Colors.RESET}")
            for client, count in sorted(stats["by_client"].items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {client}: {count}")
            lines.append("")

        if stats.get("by_type"):
            lines.append(f"{Colors.BOLD}По типам задач:{Colors.RESET}")
            for rtype, count in sorted(stats["by_type"].items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {rtype}: {count}")
            lines.append("")

        if stats.get("by_day"):
            lines.append(f"{Colors.BOLD}Активность по дням (последние 30):{Colors.RESET}")
            for day, count in list(stats["by_day"].items())[:10]:
                lines.append(f"  {day}: {'█' * min(count, 50)} {count}")

        return "\n".join(lines)


# ============================================================================
# ЭКСПОРТ
# ============================================================================

class Exporter:
    @staticmethod
    def to_json(records: List[Dict[str, Any]], output_path: str):
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2, ensure_ascii=False, default=str)
        print(f"{Colors.GREEN}[✓] Экспортировано {len(records)} записей в {output_path}{Colors.RESET}")

    @staticmethod
    def to_csv(records: List[Dict[str, Any]], output_path: str):
        import csv
        if not records:
            print(f"{Colors.YELLOW}[!] Нет записей для экспорта{Colors.RESET}")
            return

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)
        print(f"{Colors.GREEN}[✓] Экспортировано {len(records)} записей в {output_path}{Colors.RESET}")

    @staticmethod
    def to_markdown(records: List[Dict[str, Any]], output_path: str):
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("# Honeycomb Records\n\n")
            f.write(f"*Экспортировано: {datetime.now().isoformat()}*\n\n")
            f.write(f"**Всего записей:** {len(records)}\n\n")
            f.write("---\n\n")

            for i, record in enumerate(records):
                f.write(f"## Запись {i+1}\n\n")
                f.write(f"| Поле | Значение |\n")
                f.write(f"|------|----------|\n")
                f.write(f"| Клиент | {record.get('client_id', 'N/A')} |\n")
                f.write(f"| Задача | {record.get('task_id', 'N/A')} |\n")
                f.write(f"| Тип | {record.get('type', 'N/A')} |\n")
                f.write(f"| Создано | {record.get('created_at', 'N/A')} |\n\n")

                data = record.get('data', '')
                try:
                    parsed = json.loads(data)
                    data = json.dumps(parsed, indent=2, ensure_ascii=False)
                except:
                    pass

                f.write(f"```json\n{data}\n```\n\n")
                f.write("---\n\n")

        print(f"{Colors.GREEN}[✓] Экспортировано {len(records)} записей в {output_path}{Colors.RESET}")


# ============================================================================
# ИНТЕРАКТИВНЫЙ РЕЖИМ
# ============================================================================

class InteractiveViewer:
    def __init__(self, db: HoneycombDB):
        self.db = db
        self.current_filter = {}
        self.commands = {
            "help": self.cmd_help,
            "list": self.cmd_list,
            "stats": self.cmd_stats,
            "filter": self.cmd_filter,
            "clear": self.cmd_clear,
            "search": self.cmd_search,
            "show": self.cmd_show,
            "export": self.cmd_export,
            "quit": self.cmd_quit,
        }

    def run(self):
        print(f"{Colors.BOLD}{Colors.HEADER}Honeycomb Viewer — Интерактивный режим{Colors.RESET}")
        print(f"{Colors.DIM}Введи 'help' для списка команд{Colors.RESET}")

        while True:
            try:
                cmd_input = input(f"\n{Colors.GREEN}honeycomb>{Colors.RESET} ").strip()
                if not cmd_input:
                    continue

                parts = cmd_input.split()
                cmd_name = parts[0].lower()
                args = parts[1:]

                if cmd_name in self.commands:
                    self.commands[cmd_name](args)
                else:
                    print(f"{Colors.RED}Неизвестная команда: {cmd_name}{Colors.RESET}")
                    print(f"{Colors.DIM}Введи 'help' для списка команд{Colors.RESET}")

            except KeyboardInterrupt:
                print("\n")
                break
            except EOFError:
                break

    def cmd_help(self, args):
        print(f"""
{Colors.BOLD}Доступные команды:{Colors.RESET}

  {Colors.CYAN}list [limit]{Colors.RESET}        — Показать записи (по умолчанию: 20)
  {Colors.CYAN}show <index>{Colors.RESET}         — Показать полную запись по индексу
  {Colors.CYAN}stats{Colors.RESET}               — Статистика Honeycomb
  {Colors.CYAN}filter client <id>{Colors.RESET}  — Фильтр по клиенту
  {Colors.CYAN}filter type <type>{Colors.RESET}  — Фильтр по типу задачи
  {Colors.CYAN}filter days <N>{Colors.RESET}     — Записи за последние N дней
  {Colors.CYAN}clear{Colors.RESET}               — Сбросить фильтры
  {Colors.CYAN}search <query>{Colors.RESET}      — Поиск по данным
  {Colors.CYAN}export json <file>{Colors.RESET}  — Экспорт в JSON
  {Colors.CYAN}export csv <file>{Colors.RESET}   — Экспорт в CSV
  {Colors.CYAN}export md <file>{Colors.RESET}    — Экспорт в Markdown
  {Colors.CYAN}quit{Colors.RESET}                — Выход
""")

    def cmd_list(self, args):
        limit = int(args[0]) if args else 20
        records = self.db.get_records(
            client_id=self.current_filter.get("client"),
            task_type=self.current_filter.get("type"),
            days=self.current_filter.get("days"),
            limit=limit
        )
        print(OutputFormatter.format_table(records))

    def cmd_stats(self, args):
        stats = self.db.get_stats()
        print(OutputFormatter.format_stats(stats))

    def cmd_filter(self, args):
        if len(args) < 2:
            print(f"{Colors.YELLOW}Использование: filter <client|type|days> <value>{Colors.RESET}")
            print(f"{Colors.DIM}Текущие фильтры: {self.current_filter or 'нет'}{Colors.RESET}")
            return

        filter_type = args[0].lower()
        filter_value = args[1]

        if filter_type == "days":
            filter_value = int(filter_value)

        self.current_filter[filter_type] = filter_value
        print(f"{Colors.GREEN}[✓] Фильтр {filter_type} = {filter_value}{Colors.RESET}")
        print(f"{Colors.DIM}Текущие фильтры: {self.current_filter}{Colors.RESET}")

    def cmd_clear(self, args):
        self.current_filter = {}
        print(f"{Colors.GREEN}[✓] Фильтры сброшены{Colors.RESET}")

    def cmd_search(self, args):
        if not args:
            print(f"{Colors.YELLOW}Использование: search <query>{Colors.RESET}")
            return

        query = " ".join(args)
        records = self.db.search(query)
        print(f"{Colors.BOLD}Результаты поиска: '{query}' ({len(records)} найдено){Colors.RESET}")
        print(OutputFormatter.format_table(records))

    def cmd_show(self, args):
        if not args:
            print(f"{Colors.YELLOW}Использование: show <index>{Colors.RESET}")
            return

        try:
            index = int(args[0])
            records = self.db.get_records(
                client_id=self.current_filter.get("client"),
                task_type=self.current_filter.get("type"),
                days=self.current_filter.get("days"),
                limit=1,
                offset=index
            )
            if records:
                print(OutputFormatter.format_record(records[0], index))
            else:
                print(f"{Colors.YELLOW}Запись с индексом {index} не найдена{Colors.RESET}")
        except ValueError:
            print(f"{Colors.RED}Индекс должен быть числом{Colors.RESET}")

    def cmd_export(self, args):
        if len(args) < 2:
            print(f"{Colors.YELLOW}Использование: export <json|csv|md> <filename>{Colors.RESET}")
            return

        fmt = args[0].lower()
        filename = args[1]

        records = self.db.get_records(
            client_id=self.current_filter.get("client"),
            task_type=self.current_filter.get("type"),
            days=self.current_filter.get("days"),
            limit=10000
        )

        if fmt == "json":
            Exporter.to_json(records, filename)
        elif fmt == "csv":
            Exporter.to_csv(records, filename)
        elif fmt == "md":
            Exporter.to_markdown(records, filename)
        else:
            print(f"{Colors.RED}Неизвестный формат: {fmt}{Colors.RESET}")

    def cmd_quit(self, args):
        print(f"{Colors.DIM}Выход...{Colors.RESET}")
        sys.exit(0)


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Honeycomb Viewer — просмотр результатов Red Team операций",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  %(prog)s                          — интерактивный режим
  %(prog)s list                     — показать последние 20 записей
  %(prog)s list --limit 50          — показать 50 записей
  %(prog)s stats                    — статистика
  %(prog)s export json results.json — экспорт в JSON
  %(prog)s filter client acme-corp  — фильтр по клиенту
  %(prog)s search "admin password"  — поиск
        """
    )

    parser.add_argument("action", nargs="?",
                       choices=["list", "stats", "search", "export", "interactive"],
                       default="interactive",
                       help="Действие")
    parser.add_argument("query", nargs="?", help="Поисковый запрос или файл экспорта")
    parser.add_argument("--limit", type=int, default=20, help="Лимит записей")
    parser.add_argument("--client", help="Фильтр по клиенту")
    parser.add_argument("--type", help="Фильтр по типу задачи")
    parser.add_argument("--days", type=int, help="Записи за последние N дней")
    parser.add_argument("--format", choices=["json", "csv", "md"],
                       default="json", help="Формат экспорта")
    parser.add_argument("--output", help="Файл для экспорта")
    parser.add_argument("--no-color", action="store_true", help="Отключить цвета")
    parser.add_argument("--db", default=DB_PATH, help="Путь к базе Honeycomb")

    args = parser.parse_args()

    if args.no_color:
        Colors.disable()

    db_path = args.db
    db = HoneycombDB(db_path)
    db.connect()

    try:
        if args.action == "interactive":
            viewer = InteractiveViewer(db)
            viewer.run()

        elif args.action == "list":
            records = db.get_records(
                client_id=args.client,
                task_type=args.type,
                days=args.days,
                limit=args.limit
            )
            print(OutputFormatter.format_table(records))

        elif args.action == "stats":
            stats = db.get_stats()
            print(OutputFormatter.format_stats(stats))

        elif args.action == "search":
            if not args.query:
                print(f"{Colors.RED}Укажи поисковый запрос{Colors.RESET}")
                sys.exit(1)
            records = db.search(args.query, limit=args.limit)
            print(OutputFormatter.format_table(records))

        elif args.action == "export":
            if not args.output:
                print(f"{Colors.RED}Укажи файл для экспорта через --output{Colors.RESET}")
                sys.exit(1)

            records = db.get_records(
                client_id=args.client,
                task_type=args.type,
                days=args.days,
                limit=10000
            )

            if args.format == "json":
                Exporter.to_json(records, args.output)
            elif args.format == "csv":
                Exporter.to_csv(records, args.output)
            elif args.format == "md":
                Exporter.to_markdown(records, args.output)

    finally:
        db.close()


if __name__ == "__main__":
    main()
