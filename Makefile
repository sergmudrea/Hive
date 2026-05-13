# ============================================================================
# HIVEMIND SWARM MAKEFILE
# Полный цикл: установка, сборка, тестирование, развертывание
# Архитектор: Кронос | Тимлид: Мастер
# ============================================================================

.PHONY: help install build test deploy clean pack-bee init-dns init-telegram init-github status logs self-destruct

# Конфигурация
SHELL := /bin/bash
GO := go
PYTHON := python3
DOCKER := docker
PROJECT_DIR := $(shell pwd)
BUILD_DIR := $(PROJECT_DIR)/builds
HIVE_BINARY := $(PROJECT_DIR)/hivemind
CONFIG_FILE := $(PROJECT_DIR)/hive_config.json
ENV_FILE := $(PROJECT_DIR)/.env

# Цвета
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
CYAN := \033[0;36m
NC := \033[0m

# ============================================================================
# ПОМОЩЬ
# ============================================================================

help: ## Показать этот help
	@echo "$(CYAN)HiveMind Swarm — Система управления децентрализованным Red Team роем$(NC)"
	@echo ""
	@echo "$(GREEN)Основные команды:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(YELLOW)Переменные окружения:$(NC)"
	@echo "  HIVE_ENCRYPTION_KEY    — ключ шифрования (обязательно)"
	@echo "  HIVE_SWARM_ID          — идентификатор роя"
	@echo "  NJALLA_API_TOKEN       — токен Njalla для покупки доменов"
	@echo "  SMS_ACTIVATE_API_KEY   — ключ SMS-activate для Telegram"

# ============================================================================
# УСТАНОВКА ЗАВИСИМОСТЕЙ
# ============================================================================

install: install-go install-python install-tools ## Установить все зависимости

install-go: ## Установить Go
	@echo "$(GREEN)[INSTALL] Установка Go...$(NC)"
	@if ! command -v go &> /dev/null; then \
		wget -q https://go.dev/dl/go1.22.0.linux-amd64.tar.gz -O /tmp/go.tar.gz && \
		sudo tar -C /usr/local -xzf /tmp/go.tar.gz && \
		echo 'export PATH=$$PATH:/usr/local/go/bin' >> ~/.bashrc && \
		export PATH=$$PATH:/usr/local/go/bin && \
		rm /tmp/go.tar.gz && \
		echo "Go установлен: $$(go version)"; \
	else \
		echo "Go уже установлен: $$(go version)"; \
	fi

install-python: ## Установить Python зависимости
	@echo "$(GREEN)[INSTALL] Установка Python зависимостей...$(NC)"
	@$(PYTHON) -m pip install -q requests pycryptodome psutil dnspython
	@$(PYTHON) -m pip install -q pyinstaller nuitka 2>/dev/null || true

install-tools: ## Установить системные инструменты
	@echo "$(GREEN)[INSTALL] Установка системных инструментов...$(NC)"
	@sudo apt-get update -qq
	@sudo apt-get install -y -qq tor obfs4proxy git make gcc openssl curl jq

# ============================================================================
# СБОРКА
# ============================================================================

build: build-hive build-bee ## Собрать всё

build-hive: ## Собрать HiveMind Core
	@echo "$(GREEN)[BUILD] Компиляция HiveMind Core...$(NC)"
	@cd $(PROJECT_DIR) && \
		[ -f go.mod ] || $(GO) mod init hivemind && \
		$(GO) get golang.org/x/crypto/ssh && \
		$(GO) get golang.org/x/net/proxy && \
		$(GO) mod tidy && \
		CGO_ENABLED=0 GOOS=linux GOARCH=amd64 $(GO) build \
			-ldflags="-s -w" \
			-o hivemind \
			hivemind.go
	@echo "$(GREEN)[BUILD] HiveMind собран: $(HIVE_BINARY)$(NC)"
	@ls -lh $(HIVE_BINARY)

build-bee: ## Собрать Worker Bee (тестовый)
	@echo "$(GREEN)[BUILD] Сборка Worker Bee...$(NC)"
	@$(PYTHON) bee_packer.py test-client --target linux --no-obfuscate --output $(BUILD_DIR)

pack-bee: ## Упаковать Worker Bee для клиента
	@echo "$(GREEN)[PACK] Упаковка Worker Bee...$(NC)"
	@read -p "Имя клиента: " client; \
	 read -p "Целевая ОС [linux/windows/macos]: " target; \
	 $(PYTHON) bee_packer.py $$client --target $$target --output $(BUILD_DIR)

# ============================================================================
# ТЕСТИРОВАНИЕ
# ============================================================================

test: test-swarm ## Запустить все тесты

test-swarm: ## Комплексное тестирование роя
	@echo "$(GREEN)[TEST] Запуск батареи тестов...$(NC)"
	@$(PYTHON) test_swarm.py

test-quick: ## Быстрый тест (основные проверки)
	@$(PYTHON) test_swarm.py --quick

test-dead-drops: ## Тестировать только Dead Drops
	@$(PYTHON) test_swarm.py --dead-drops-only

test-brain: ## Тестировать только Brain Units
	@$(PYTHON) test_swarm.py --brain-only

test-bee: ## Тестировать только Worker Bee
	@$(PYTHON) test_swarm.py --bee-only

test-core: ## Тестировать только HiveMind Core
	@$(PYTHON) test_swarm.py --core-only

test-e2e: ## Сквозное тестирование
	@$(PYTHON) test_swarm.py --e2e-only

# ============================================================================
# РАЗВЕРТЫВАНИЕ
# ============================================================================

deploy: deploy-hive deploy-brains deploy-dead-drops ## Полное развертывание

deploy-hive: ## Развернуть HiveMind Core
	@echo "$(GREEN)[DEPLOY] Развертывание HiveMind Core...$(NC)"
	@bash hive_build.sh deploy

deploy-brains: ## Развернуть LLM ферму
	@echo "$(GREEN)[DEPLOY] Развертывание LLM фермы...$(NC)"
	@read -p "Роль сервера [brain-1/brain-2/brain-3]: " role; \
	 bash install_llm_ferma.sh $$role

deploy-dead-drops: ## Развернуть Dead Drop инфраструктуру
	@echo "$(GREEN)[DEPLOY] Настройка Dead Drops...$(NC)"
	@$(PYTHON) dead_drop_server.py &
	@echo "Dead Drop сервер запущен в фоне"

# ============================================================================
# ИНИЦИАЛИЗАЦИЯ DEAD DROPS
# ============================================================================

init-github: ## Создать GitHub аккаунты для Dead Drops
	@echo "$(GREEN)[INIT] Создание GitHub аккаунтов...$(NC)"
	@$(PYTHON) dd_github_init.py create --count 5

init-telegram: ## Создать Telegram ботов для Dead Drops
	@echo "$(GREEN)[INIT] Создание Telegram ботов...$(NC)"
	@$(PYTHON) dd_telegram_init.py create --count 5 --simulate

init-dns: ## Настроить DNS Dead Drops
	@echo "$(GREEN)[INIT] Настройка DNS Dead Drops...$(NC)"
	@bash dd_dns_init.sh full

# ============================================================================
# УПРАВЛЕНИЕ
# ============================================================================

start: ## Запустить HiveMind
	@echo "$(GREEN)[START] Запуск HiveMind...$(NC)"
	@sudo systemctl start hivemind 2>/dev/null || ./hivemind &
	@sleep 2
	@make status

stop: ## Остановить HiveMind
	@echo "$(YELLOW)[STOP] Остановка HiveMind...$(NC)"
	@sudo systemctl stop hivemind 2>/dev/null || pkill -f hivemind

restart: stop start ## Перезапустить HiveMind

status: ## Статус HiveMind
	@echo "$(CYAN)[STATUS] Статус HiveMind...$(NC)"
	@sudo systemctl status hivemind --no-pager 2>/dev/null || \
		(ps aux | grep hivemind | grep -v grep && echo "HiveMind запущен (без systemd)") || \
		echo "$(YELLOW)HiveMind не запущен$(NC)"

logs: ## Логи HiveMind
	@sudo journalctl -u hivemind -f 2>/dev/null || tail -f /var/log/hivemind/hivemind.log 2>/dev/null || \
		echo "$(YELLOW)Логи не найдены$(NC)"

# ============================================================================
# HONEYCOMB
# ============================================================================

honeycomb: ## Открыть Honeycomb Viewer
	@$(PYTHON) honeycomb_viewer.py

honeycomb-list: ## Список последних записей
	@$(PYTHON) honeycomb_viewer.py list --limit 20

honeycomb-stats: ## Статистика Honeycomb
	@$(PYTHON) honeycomb_viewer.py stats

honeycomb-export: ## Экспорт Honeycomb
	@read -p "Файл для экспорта: " output; \
	 $(PYTHON) honeycomb_viewer.py export --format json --output $$output

# ============================================================================
# ОЧИСТКА
# ============================================================================

clean: ## Очистить сборки
	@echo "$(YELLOW)[CLEAN] Очистка сборок...$(NC)"
	@rm -rf $(BUILD_DIR)
	@rm -f $(HIVE_BINARY)
	@echo "Очищено."

clean-all: clean ## Полная очистка (включая Honeycomb)
	@echo "$(RED)[CLEAN] Полная очистка...$(NC)"
	@rm -rf $(PROJECT_DIR)/honeycomb
	@rm -f $(PROJECT_DIR)/*.db
	@rm -f $(PROJECT_DIR)/.env
	@echo "Всё очищено."

# ============================================================================
# БЕЗОПАСНОСТЬ
# ============================================================================

self-destruct: ## Запустить протокол самоуничтожения
	@echo "$(RED)[DESTRUCT] ПРОТОКОЛ САМОУНИЧТОЖЕНИЯ$(NC)"
	@echo "$(RED)Это действие необратимо.$(NC)"
	@read -p "Подтверди паролем Queen: " pass; \
	 if [ "$$pass" = "destroy_all" ]; then \
		echo "Самоуничтожение через 5 секунд..."; \
		sleep 5; \
		make clean-all; \
		pkill -9 -f hivemind; \
		pkill -9 -f workerbee; \
		pkill -9 -f dead_drop_server; \
		shred -u $(PROJECT_DIR)/hive_ssh_key 2>/dev/null; \
		echo "Рой уничтожен."; \
	else \
		echo "Неверный пароль. Отмена."; \
	fi

# ============================================================================
# DOCKER
# ============================================================================

docker-build: ## Собрать Docker образы
	@echo "$(GREEN)[DOCKER] Сборка образов...$(NC)"
	@$(DOCKER) compose build

docker-up: ## Запустить тестовое окружение
	@echo "$(GREEN)[DOCKER] Запуск тестового окружения...$(NC)"
	@$(DOCKER) compose up -d
	@echo "Тестовое окружение запущено."
	@$(DOCKER) compose ps

docker-down: ## Остановить тестовое окружение
	@echo "$(YELLOW)[DOCKER] Остановка окружения...$(NC)"
	@$(DOCKER) compose down

docker-logs: ## Логи Docker окружения
	@$(DOCKER) compose logs -f

docker-test: ## Запустить тесты в Docker
	@echo "$(GREEN)[DOCKER] Запуск тестов в контейнере...$(NC)"
	@$(DOCKER) compose run --rm test-runner python3 test_swarm.py

# ============================================================================
# ГЕНЕРАЦИЯ КЛЮЧЕЙ
# ============================================================================

gen-key: ## Сгенерировать ключ шифрования
	@echo "$(GREEN)Ключ шифрования (AES-256):$(NC)"
	@openssl rand -base64 32
	@echo ""
	@echo "$(YELLOW)Добавь в .env:$(NC)"
	@echo "HIVE_ENCRYPTION_KEY=<ключ выше>"

gen-swarm-id: ## Сгенерировать Swarm ID
	@echo "$(GREEN)Swarm ID:$(NC)"
	@echo "SWARM-$$(openssl rand -hex 4)"

gen-password: ## Сгенерировать пароль Queen
	@echo "$(GREEN)Пароль Queen:$(NC)"
	@openssl rand -base64 16

# ============================================================================
# ПРОВЕРКА ОКРУЖЕНИЯ
# ============================================================================

check: ## Проверить готовность окружения
	@echo "$(CYAN)=== Проверка окружения ===$(NC)"
	@echo -n "Go:           "; command -v go &>/dev/null && echo "$(GREEN)✓$(NC)" || echo "$(RED)✗$(NC)"
	@echo -n "Python:       "; command -v python3 &>/dev/null && echo "$(GREEN)✓$(NC)" || echo "$(RED)✗$(NC)"
	@echo -n "Docker:       "; command -v docker &>/dev/null && echo "$(GREEN)✓$(NC)" || echo "$(YELLOW)○$(NC)"
	@echo -n "Tor:          "; command -v tor &>/dev/null && echo "$(GREEN)✓$(NC)" || echo "$(YELLOW)○$(NC)"
	@echo -n "OpenSSL:      "; command -v openssl &>/dev/null && echo "$(GREEN)✓$(NC)" || echo "$(RED)✗$(NC)"
	@echo -n "jq:           "; command -v jq &>/dev/null && echo "$(GREEN)✓$(NC)" || echo "$(YELLOW)○$(NC)"
	@echo -n "hivemind.go:  "; [ -f hivemind.go ] && echo "$(GREEN)✓$(NC)" || echo "$(RED)✗$(NC)"
	@echo -n "workerbee.py: "; [ -f workerbee.py ] && echo "$(GREEN)✓$(NC)" || echo "$(RED)✗$(NC)"
	@echo -n "dead_drop_server.py: "; [ -f dead_drop_server.py ] && echo "$(GREEN)✓$(NC)" || echo "$(RED)✗$(NC)"
	@echo -n "hive_config.json: "; [ -f hive_config.json ] && echo "$(GREEN)✓$(NC)" || echo "$(RED)✗$(NC)"
	@echo -n "HIVE_ENCRYPTION_KEY: "; [ -n "$$HIVE_ENCRYPTION_KEY" ] && echo "$(GREEN)✓$(NC)" || echo "$(RED)✗ (обязательно)$(NC)"

# ============================================================================
# ИНФОРМАЦИЯ
# ============================================================================

info: ## Информация о проекте
	@echo "$(CYAN)=============================================$(NC)"
	@echo "$(CYAN)  HIVEMIND SWARM v1.0$(NC)"
	@echo "$(CYAN)  Децентрализованный Red Team Рой$(NC)"
	@echo "$(CYAN)=============================================$(NC)"
	@echo ""
	@echo "$(GREEN)Архитекторы:$(NC) Кронос, Мастер"
	@echo "$(GREEN)Компания:$(NC) Cybersecurity Research & Penetration Testing SRL (Молдова)"
	@echo ""
	@echo "$(GREEN)Файлы проекта:$(NC)"
	@ls -1 *.go *.py *.sh *.json *.yml Makefile 2>/dev/null | while read f; do \
		echo "  $$f ($$(wc -l < $$f) строк)"; \
	done
	@echo ""
	@echo "$(GREEN)Команды:$(NC)"
	@echo "  make install     — установить зависимости"
	@echo "  make build       — собрать проект"
	@echo "  make test        — запустить тесты"
	@echo "  make deploy      — развернуть рой"
	@echo "  make honeycomb   — посмотреть результаты"
