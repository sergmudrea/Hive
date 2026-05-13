#!/bin/bash
#
# install_llm_ferma.sh
# Автоматическая установка Ollama и загрузка всех LLM-моделей
# Архитектор: Кронос | Тимлид: Мастер
# Запуск: bash install_llm_ferma.sh [server_role]
# server_role: brain-1, brain-2, brain-3 (определяет какие модели грузить)

set -euo pipefail

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[LLM-FERMA]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[FATAL]${NC} $1"; exit 1; }

SERVER_ROLE="${1:-brain-1}"

log "Установка LLM-фермы. Роль сервера: ${SERVER_ROLE}"

# ============================================================================
# ШАГ 1: Обновление системы и установка зависимостей
# ============================================================================
log "Шаг 1/6: Обновление системы..."

apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq curl wget ca-certificates gnupg lsb-release ufw htop nvtop

# ============================================================================
# ШАГ 2: Установка драйверов NVIDIA
# ============================================================================
log "Шаг 2/6: Установка драйверов NVIDIA..."

if ! command -v nvidia-smi &> /dev/null; then
    wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
    dpkg -i cuda-keyring_1.1-1_all.deb
    apt-get update -qq
    apt-get install -y -qq cuda-drivers cuda-toolkit-12-4
    rm cuda-keyring_1.1-1_all.deb
    warn "Перезагрузка сервера через 5 секунд для применения драйверов..."
    sleep 5
    reboot
    exit 0
else
    log "NVIDIA драйверы уже установлены."
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
fi

# ============================================================================
# ШАГ 3: Установка Ollama
# ============================================================================
log "Шаг 3/6: Установка Ollama..."

if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
else
    log "Ollama уже установлен. Версия: $(ollama --version)"
fi

# ============================================================================
# ШАГ 4: Настройка Ollama как сервиса
# ============================================================================
log "Шаг 4/6: Настройка сервиса Ollama..."

cat > /etc/systemd/system/ollama.service << 'EOF'
[Unit]
Description=Ollama LLM Service
After=network-online.target nvidia-persistenced.service
Wants=network-online.target

[Service]
Type=simple
User=ollama
Group=ollama
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_ORIGINS=*"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=3"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_DEBUG=0"
Environment="CUDA_VISIBLE_DEVICES=0"
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ollama
systemctl restart ollama

sleep 3

if systemctl is-active --quiet ollama; then
    log "Ollama сервис запущен."
else
    err "Ollama сервис не запустился. Проверь: systemctl status ollama"
fi

# ============================================================================
# ШАГ 5: Настройка файрвола
# ============================================================================
log "Шаг 5/6: Настройка файрвола..."

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow from 10.0.0.0/24 to any port 11434 comment 'LLM API для HiveMind'
ufw allow from 10.0.0.0/24 to any port 22 comment 'SSH управление'
ufw --force enable

log "Файрвол настроен. Открыты порты: 11434 (Ollama), 22 (SSH) только для внутренней сети."

# ============================================================================
# ШАГ 6: Загрузка моделей
# ============================================================================
log "Шаг 6/6: Загрузка LLM-моделей..."

pull_model() {
    local model="$1"
    log "Загрузка модели: ${model}..."
    if ollama list | grep -q "${model}"; then
        log "Модель ${model} уже загружена."
    else
        ollama pull "${model}"
        log "Модель ${model} загружена."
    fi
}

case "${SERVER_ROLE}" in
    brain-1)
        log "Сервер Brain-1: DeepSeek-R1 + Command R+"
        pull_model "deepseek-r1:70b"
        pull_model "command-r-plus:latest"
        ;;
    brain-2)
        log "Сервер Brain-2: Qwen 2.5 + Mistral Large"
        pull_model "qwen2.5:72b"
        pull_model "mistral-large:latest"
        ;;
    brain-3)
        log "Сервер Brain-3: Llama 4 Behemoth"
        pull_model "llama4:behemoth"
        ;;
    *)
        err "Неизвестная роль сервера: ${SERVER_ROLE}. Допустимые: brain-1, brain-2, brain-3"
        ;;
esac

# ============================================================================
# ПРОВЕРКА
# ============================================================================
log "=============================================="
log "Установка завершена. Проверка:"
log "=============================================="

ollama list

echo ""
log "GPU статус:"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>/dev/null || warn "GPU не обнаружен"

echo ""
log "Открытые порты:"
ss -tlnp | grep 11434 || warn "Порт 11434 не слушается"

echo ""
log "Сервер ${SERVER_ROLE} готов к работе."
log "Для ручной проверки выполни: curl http://localhost:11434/api/generate -d '{\"model\":\"deepseek-r1:70b\",\"prompt\":\"test\",\"stream\":false}'"
