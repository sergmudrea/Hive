#!/bin/bash
#
# hive_build.sh
# Компиляция HiveMind Core, настройка systemd, первый запуск
# Архитектор: Кронос | Тимлид: Мастер
# Запуск: bash hive_build.sh [deploy|start|stop|status|logs]

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[HIVE-BUILD]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[FATAL]${NC} $1"; exit 1; }

PROJECT_DIR="/opt/hivemind"
GO_VERSION="1.22"
SERVICE_NAME="hivemind"
BINARY_PATH="${PROJECT_DIR}/hivemind"
CONFIG_PATH="${PROJECT_DIR}/hive_config.json"
ENV_FILE="${PROJECT_DIR}/.env"
LOG_DIR="/var/log/hivemind"

# ============================================================================
# УСТАНОВКА ЗАВИСИМОСТЕЙ
# ============================================================================

install_dependencies() {
    log "Установка зависимостей..."

    # Go
    if ! command -v go &> /dev/null; then
        log "Установка Go ${GO_VERSION}..."
        wget -q "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -O /tmp/go.tar.gz
        tar -C /usr/local -xzf /tmp/go.tar.gz
        echo 'export PATH=$PATH:/usr/local/go/bin' >> /etc/profile
        export PATH=$PATH:/usr/local/go/bin
        rm /tmp/go.tar.gz
    fi
    log "Go версия: $(go version)"

    # git, make, gcc
    apt-get update -qq
    apt-get install -y -qq git make gcc libc6-dev

    # tor
    apt-get install -y -qq tor
    systemctl enable tor
    systemctl start tor
}

# ============================================================================
# КОМПИЛЯЦИЯ
# ============================================================================

compile_hivemind() {
    log "Компиляция HiveMind Core..."

    mkdir -p "${PROJECT_DIR}"
    cd "${PROJECT_DIR}"

    # Копируем исходник (предполагаем что он в /tmp или рядом)
    if [ ! -f "hivemind.go" ]; then
        if [ -f "/tmp/hivemind.go" ]; then
            cp /tmp/hivemind.go .
        elif [ -f "./hivemind.go" ]; then
            :
        else
            err "hivemind.go не найден. Помести файл в ${PROJECT_DIR}/hivemind.go"
        fi
    fi

    # Инициализация модуля
    if [ ! -f "go.mod" ]; then
        go mod init hivemind
        go get golang.org/x/crypto/ssh
        go get golang.org/x/net/proxy
    fi

    go mod tidy
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
        -ldflags="-s -w -X main.Version=1.0.0 -X main.BuildTime=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        -o hivemind \
        hivemind.go

    chmod +x hivemind
    log "Бинарник собран: $(file hivemind)"
    log "Размер: $(du -h hivemind | cut -f1)"
}

# ============================================================================
# НАСТРОЙКА ОКРУЖЕНИЯ
# ============================================================================

setup_env() {
    log "Настройка переменных окружения..."

    if [ ! -f "${ENV_FILE}" ]; then
        # Генерация ключа шифрования
        ENCRYPTION_KEY=$(openssl rand -base64 32)

        cat > "${ENV_FILE}" << EOF
# HiveMind Environment
HIVE_ENCRYPTION_KEY=${ENCRYPTION_KEY}
HIVE_SWARM_ID=SWARM-$(openssl rand -hex 4)
HIVE_GITHUB_TOKENS=
HIVE_DNS_DOMAIN=
HIVE_TELEGRAM_BOT_KEY=
HIVE_TELEGRAM_CHAT_ID=
HIVE_QUEEN_PASSWORD=$(openssl rand -base64 16)
HIVE_QUEEN_SSH_PORT=2222
HIVE_SOCKS5_PROXY=127.0.0.1:9050
HIVE_BRAIN_RECON=http://10.0.0.2:11434/api/generate
HIVE_BRAIN_EXPLOIT=http://10.0.0.3:11435/api/generate
HIVE_BRAIN_SOCIAL=http://10.0.0.3:11436/api/generate
HIVE_BRAIN_PIVOT=http://10.0.0.4:11437/api/generate
HIVE_BRAIN_REPORT=http://10.0.0.2:11438/api/generate
HIVE_HONEYCOMB_DIR=${PROJECT_DIR}/honeycomb
EOF
        chmod 600 "${ENV_FILE}"
        log ".env файл создан: ${ENV_FILE}"
    else
        log ".env файл уже существует."
    fi

    # Загружаем переменные
    set -a
    source "${ENV_FILE}"
    set +a
}

# ============================================================================
# SYSTEMD СЕРВИС
# ============================================================================

setup_systemd() {
    log "Настройка systemd сервиса..."

    cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=HiveMind Core - Red Team Swarm Orchestrator
After=network-online.target tor.service
Wants=network-online.target tor.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${BINARY_PATH}
ExecStop=/bin/kill -SIGTERM \$MAINPID
Restart=always
RestartSec=10
LimitNOFILE=65535
StandardOutput=append:${LOG_DIR}/hivemind.log
StandardError=append:${LOG_DIR}/hivemind-error.log

# Безопасность
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=${PROJECT_DIR}/honeycomb
ReadWritePaths=${LOG_DIR}

[Install]
WantedBy=multi-user.target
EOF

    mkdir -p "${LOG_DIR}"
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
    log "Сервис ${SERVICE_NAME} настроен."
}

# ============================================================================
# SSH КЛЮЧ ДЛЯ QUEEN API
# ============================================================================

setup_ssh_key() {
    log "Генерация SSH ключа для Queen API..."

    if [ ! -f "${PROJECT_DIR}/hive_ssh_key" ]; then
        ssh-keygen -t ed25519 -f "${PROJECT_DIR}/hive_ssh_key" -N "" -C "hivemind-queen" -q
        chmod 600 "${PROJECT_DIR}/hive_ssh_key"
        log "SSH ключ сгенерирован: ${PROJECT_DIR}/hive_ssh_key"
        log "Публичный ключ:"
        cat "${PROJECT_DIR}/hive_ssh_key.pub"
    else
        log "SSH ключ уже существует."
    fi
}

# ============================================================================
# ЗАПУСК / ОСТАНОВКА / СТАТУС
# ============================================================================

start_service() {
    log "Запуск HiveMind..."
    systemctl start "${SERVICE_NAME}"
    sleep 2
    status_service
}

stop_service() {
    log "Остановка HiveMind..."
    systemctl stop "${SERVICE_NAME}"
}

status_service() {
    echo "=============================================="
    echo "HiveMind Core Status"
    echo "=============================================="
    systemctl status "${SERVICE_NAME}" --no-pager 2>/dev/null || true
    echo ""
    echo "Активные процессы:"
    ps aux | grep hivemind | grep -v grep || echo "  (нет)"
    echo ""
    echo "Порты:"
    ss -tlnp | grep 2222 || echo "  (порт Queen API не слушается)"
    echo ""
    echo "Последние логи:"
    journalctl -u "${SERVICE_NAME}" --no-pager -n 10 2>/dev/null || tail -10 "${LOG_DIR}/hivemind.log" 2>/dev/null || echo "  (логов нет)"
}

show_logs() {
    if [ -f "${LOG_DIR}/hivemind.log" ]; then
        tail -f "${LOG_DIR}/hivemind.log"
    else
        journalctl -u "${SERVICE_NAME}" -f
    fi
}

# ============================================================================
# ПОЛНОЕ РАЗВЕРТЫВАНИЕ
# ============================================================================

deploy() {
    log "=============================================="
    log "ПОЛНОЕ РАЗВЕРТЫВАНИЕ HIVEMIND CORE"
    log "=============================================="

    install_dependencies
    compile_hivemind
    setup_env
    setup_ssh_key
    setup_systemd
    start_service

    log "=============================================="
    log "Развертывание завершено!"
    log "=============================================="
    log "SSH ключ для подключения: ${PROJECT_DIR}/hive_ssh_key"
    log "Подключение Queen: ssh -i ${PROJECT_DIR}/hive_ssh_key -p 2222 root@localhost"
    log "Пароль Queen: смотри в ${ENV_FILE}"
    log ""
    log "Проверка статуса: bash hive_build.sh status"
    log "Просмотр логов:  bash hive_build.sh logs"
}

# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

case "${1:-deploy}" in
    deploy)
        deploy
        ;;
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    status)
        status_service
        ;;
    logs)
        show_logs
        ;;
    compile)
        compile_hivemind
        ;;
    env)
        setup_env
        cat "${ENV_FILE}"
        ;;
    *)
        echo "Использование: $0 {deploy|start|stop|status|logs|compile|env}"
        exit 1
        ;;
esac
