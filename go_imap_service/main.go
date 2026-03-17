package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	imap "github.com/BrianLeishman/go-imap"
)

type cachedCode struct {
	Code     string    `json:"code"`
	UID      int64     `json:"uid"`
	Subject  string    `json:"subject"`
	From     string    `json:"from"`
	CachedAt time.Time `json:"cached_at"`
}

type recentEvent struct {
	Time       time.Time `json:"time"`
	UID        int64     `json:"uid"`
	From       string    `json:"from"`
	Subject    string    `json:"subject"`
	Code       string    `json:"code"`
	Recipients []string  `json:"recipients"`
	Cached     bool      `json:"cached"`
	Reason     string    `json:"reason"`
}

type service struct {
	mu     sync.Mutex
	scanMu sync.Mutex

	host       string
	port       int
	username   string
	password   string
	mailbox    string
	senderHint string
	domain     string

	pollInterval time.Duration
	maxBackoff   time.Duration
	scanLimit    int

	lastUID  int64
	cache    map[string]cachedCode
	fallback *cachedCode
	dialer   *imap.Dialer
	box      string

	recent      []recentEvent
	recentLimit int
}

func getenv(key, def string) string {
	v := strings.TrimSpace(os.Getenv(key))
	if v == "" {
		return def
	}
	return v
}

func parseIntEnv(key string, def int) int {
	raw := getenv(key, "")
	if raw == "" {
		return def
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return def
	}
	return v
}

func newService() *service {
	host := getenv("IMAP_HOST", "imap.gmail.com")
	port := parseIntEnv("IMAP_PORT", 993)
	pollSec := parseIntEnv("GO_IMAP_POLL_INTERVAL", 3)
	maxBackoffSec := parseIntEnv("GO_IMAP_MAX_BACKOFF", 60)
	if maxBackoffSec < pollSec {
		maxBackoffSec = pollSec
	}
	scanLimit := parseIntEnv("GO_IMAP_SCAN_LIMIT", 80)
	if scanLimit < 20 {
		scanLimit = 20
	}
	recentLimit := parseIntEnv("GO_IMAP_RECENT_LIMIT", 80)
	if recentLimit < 20 {
		recentLimit = 20
	}
	return &service{
		host:         host,
		port:         port,
		username:     getenv("IMAP_USERNAME", ""),
		password:     getenv("IMAP_PASSWORD", ""),
		mailbox:      getenv("IMAP_MAILBOX", defaultMailboxByHost(host)),
		senderHint:   strings.ToLower(getenv("IMAP_SENDER_HINT", "noreply@x.ai")),
		domain:       strings.ToLower(getenv("EMAIL_DOMAIN", "")),
		pollInterval: time.Duration(max(1, pollSec)) * time.Second,
		maxBackoff:   time.Duration(max(1, maxBackoffSec)) * time.Second,
		scanLimit:    scanLimit,
		cache:        make(map[string]cachedCode),
		recent:       make([]recentEvent, 0, recentLimit),
		recentLimit:  recentLimit,
	}
}

func defaultMailboxByHost(host string) string {
	return "INBOX"
}

func (s *service) mailboxCandidates() []string {
	box := strings.TrimSpace(s.mailbox)
	host := strings.ToLower(strings.TrimSpace(s.host))

	// Gmail: default to INBOX because All Mail can be huge and slow to EXAMINE.
	if strings.Contains(host, "gmail.com") {
		if box == "" {
			return []string{"INBOX"}
		}
		l := strings.ToLower(box)
		switch l {
		case "[gmail]/all", "all", "all mail":
			box = "[Gmail]/All Mail"
		}
		return []string{box}
	}

	if box == "" {
		box = "INBOX"
	}
	return []string{box}
}

func fieldString(obj any, name string) string {
	v := reflect.ValueOf(obj)
	if v.Kind() == reflect.Pointer {
		v = v.Elem()
	}
	if !v.IsValid() || v.Kind() != reflect.Struct {
		return ""
	}
	f := v.FieldByName(name)
	if !f.IsValid() {
		return ""
	}
	if f.Kind() == reflect.String {
		return f.String()
	}
	return fmt.Sprintf("%v", f.Interface())
}

func fieldInt64(obj any, name string) int64 {
	v := reflect.ValueOf(obj)
	if v.Kind() == reflect.Pointer {
		v = v.Elem()
	}
	if !v.IsValid() || v.Kind() != reflect.Struct {
		return 0
	}
	f := v.FieldByName(name)
	if !f.IsValid() {
		return 0
	}
	switch f.Kind() {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return f.Int()
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return int64(f.Uint())
	default:
		return 0
	}
}

func fromString(obj any) string {
	v := reflect.ValueOf(obj)
	if v.Kind() == reflect.Pointer {
		v = v.Elem()
	}
	if !v.IsValid() || v.Kind() != reflect.Struct {
		return ""
	}
	f := v.FieldByName("From")
	if !f.IsValid() {
		return ""
	}
	if f.Kind() == reflect.Map {
		keys := f.MapKeys()
		parts := make([]string, 0, len(keys))
		for _, k := range keys {
			parts = append(parts, strings.ToLower(fmt.Sprintf("%v", k.Interface())))
		}
		return strings.Join(parts, ",")
	}
	return strings.ToLower(fmt.Sprintf("%v", f.Interface()))
}

func extractCode(text string) string {
	patterns := []*regexp.Regexp{
		regexp.MustCompile(`(?i)\b([A-Z0-9]{3}-[A-Z0-9]{3})\b`),
		regexp.MustCompile(`\b(\d{6})\b`),
		regexp.MustCompile(`(?i)\b([A-Z0-9]{6})\b`),
	}
	for i, re := range patterns {
		matches := re.FindAllStringSubmatch(text, -1)
		for _, m := range matches {
			if len(m) < 2 {
				continue
			}
			code := strings.ToUpper(strings.ReplaceAll(m[1], "-", ""))
			if code == "RFC822" || code == "SEARCH" || code == "INBOX" || code == "FETCH" {
				continue
			}
			if i == 2 {
				idx := strings.Index(strings.ToUpper(text), code)
				if idx >= 0 {
					l := max(0, idx-80)
					r := min(len(text), idx+80)
					w := strings.ToLower(text[l:r])
					if !strings.Contains(w, "code") && !strings.Contains(w, "verify") && !strings.Contains(w, "confirm") && !strings.Contains(w, "验证码") {
						continue
					}
				}
			}
			return code
		}
	}
	return ""
}

func extractRecipients(text, domain string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, 4)
	for _, m := range regexp.MustCompile(`[\w\.-]+@[\w\.-]+`).FindAllString(text, -1) {
		e := strings.ToLower(strings.TrimSpace(m))
		if e != "" && !seen[e] {
			seen[e] = true
			out = append(out, e)
		}
	}
	if domain != "" {
		re := regexp.MustCompile(`(?i)\b(?:to|for)\s+([a-z0-9._%+-]{3,64})\b`)
		for _, sm := range re.FindAllStringSubmatch(text, -1) {
			if len(sm) < 2 {
				continue
			}
			local := strings.ToLower(strings.TrimSpace(sm[1]))
			if local == "" || strings.Contains(local, "@") {
				continue
			}
			e := local + "@" + domain
			if !seen[e] {
				seen[e] = true
				out = append(out, e)
			}
		}
	}
	return out
}

func extractRecipientsFromMessage(msg *imap.Email) []string {
	if msg == nil {
		return nil
	}
	v := reflect.ValueOf(msg)
	if v.Kind() == reflect.Pointer {
		v = v.Elem()
	}
	if !v.IsValid() || v.Kind() != reflect.Struct {
		return nil
	}
	f := v.FieldByName("To")
	if !f.IsValid() || f.Kind() != reflect.Map {
		return nil
	}
	out := make([]string, 0, f.Len())
	seen := map[string]bool{}
	for _, k := range f.MapKeys() {
		key := strings.ToLower(strings.TrimSpace(fmt.Sprintf("%v", k.Interface())))
		if key == "" || !strings.Contains(key, "@") || seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, key)
	}
	return out
}

func (s *service) connectAndSelectOnce() error {
	s.mu.Lock()
	if s.dialer != nil {
		s.mu.Unlock()
		return nil
	}
	s.mu.Unlock()

	var lastErr error
	for _, box := range s.mailboxCandidates() {
		log.Printf("[go-imap] connect start host=%s port=%d box=%s", s.host, s.port, box)
		d, err := newDialerWithTimeout(s.username, s.password, s.host, s.port, 60*time.Second)
		if err != nil {
			lastErr = err
			log.Printf("[go-imap] connect failed box=%s err=%v", box, err)
			continue
		}
		log.Printf("[go-imap] connect ok, selecting mailbox box=%s", box)
		err = selectFolderWithTimeout(d, box, 60*time.Second)
		if err != nil {
			lastErr = err
			log.Printf("[go-imap] mailbox select failed: %s err=%v", box, err)
			_ = d.Close()
			continue
		}

		s.mu.Lock()
		if s.dialer == nil {
			s.dialer = d
			s.box = box
			s.mu.Unlock()
			log.Printf("[go-imap] mailbox selected once: %s", box)
			return nil
		}
		s.mu.Unlock()
		_ = d.Close()
		return nil
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("failed to select mailbox")
	}
	return lastErr
}

func selectFolderWithTimeout(d *imap.Dialer, box string, timeout time.Duration) error {
	errCh := make(chan error, 1)
	go func() {
		errCh <- d.SelectFolder(box)
	}()
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case err := <-errCh:
		return err
	case <-timer.C:
		_ = d.Close()
		select {
		case err := <-errCh:
			if err != nil {
				return fmt.Errorf("select timeout after %s (after close: %w)", timeout, err)
			}
		default:
		}
		return fmt.Errorf("select timeout after %s", timeout)
	}
}

func newDialerWithTimeout(username, password, host string, port int, timeout time.Duration) (*imap.Dialer, error) {
	type result struct {
		d   *imap.Dialer
		err error
	}
	ch := make(chan result, 1)
	go func() {
		d, err := imap.New(username, password, host, port)
		ch <- result{d: d, err: err}
	}()
	select {
	case r := <-ch:
		return r.d, r.err
	case <-time.After(timeout):
		return nil, fmt.Errorf("connect timeout after %s", timeout)
	}
}

func (s *service) pushRecent(ev recentEvent) {
	s.recent = append(s.recent, ev)
	if len(s.recent) > s.recentLimit {
		s.recent = s.recent[len(s.recent)-s.recentLimit:]
	}
}

func (s *service) scanAndUpdate(onlyNew bool) error {
	s.scanMu.Lock()
	defer s.scanMu.Unlock()

	if err := s.connectAndSelectOnce(); err != nil {
		return err
	}
	s.mu.Lock()
	d := s.dialer
	box := s.box
	s.mu.Unlock()

	uids, err := d.GetUIDs("ALL")
	if err != nil {
		return err
	}
	if len(uids) == 0 {
		return nil
	}
	sort.Ints(uids)
	if len(uids) > s.scanLimit {
		uids = uids[len(uids)-s.scanLimit:]
	}

	emails, err := d.GetEmails(uids...)
	if err != nil {
		return err
	}

	items := make([]*imap.Email, 0, len(emails))
	for _, v := range emails {
		items = append(items, v)
	}
	sort.Slice(items, func(i, j int) bool { return fieldInt64(items[i], "UID") < fieldInt64(items[j], "UID") })

	s.mu.Lock()
	defer s.mu.Unlock()

	baseUID := int64(0)
	if onlyNew {
		baseUID = s.lastUID
	}
	maxUID := s.lastUID
	fresh := 0
	for _, msg := range items {
		uid := fieldInt64(msg, "UID")
		if uid > maxUID {
			maxUID = uid
		}
		if uid <= baseUID {
			continue
		}

		from := fromString(msg)
		subject := fieldString(msg, "Subject")
		html := fieldString(msg, "HTML")
		textBody := fieldString(msg, "Text")
		all := strings.Join([]string{subject, html, textBody, fmt.Sprintf("%v", msg)}, "\n")

		ev := recentEvent{
			Time:    time.Now(),
			UID:     uid,
			From:    from,
			Subject: subject,
		}

		if s.senderHint != "" && !strings.Contains(from, s.senderHint) {
			ev.Reason = "sender_mismatch"
			s.pushRecent(ev)
			continue
		}

		code := extractCode(all)
		ev.Code = code
		if code == "" {
			ev.Reason = "code_not_found"
			s.pushRecent(ev)
			continue
		}

		recipients := extractRecipientsFromMessage(msg)
		if len(recipients) == 0 {
			recipients = extractRecipients(all, s.domain)
		}
		ev.Recipients = recipients
		entry := cachedCode{Code: code, UID: uid, Subject: subject, From: from, CachedAt: time.Now()}

		if len(recipients) == 0 {
			s.fallback = &entry
			ev.Cached = true
			ev.Reason = "fallback_cached"
			s.pushRecent(ev)
			fresh++
			continue
		}

		for _, r := range recipients {
			old, ok := s.cache[r]
			if !ok || uid >= old.UID {
				s.cache[r] = entry
			}
		}
		ev.Cached = true
		ev.Reason = "recipient_cached"
		s.pushRecent(ev)
		fresh++
	}
	if maxUID > s.lastUID {
		s.lastUID = maxUID
	}
	log.Printf("[go-imap] poll mailbox=%s scanned=%d cached=%d fresh=%d last_uid=%d only_new=%v", box, len(items), len(s.cache), fresh, s.lastUID, onlyNew)
	return nil
}

func (s *service) runPoller() {
	backoff := s.pollInterval
	failed := 0
	for {
		if err := s.scanAndUpdateWithTimeout(true, 40*time.Second); err != nil {
			failed++
			if failed == 1 {
				backoff = s.pollInterval
			} else {
				backoff *= 2
				if backoff > s.maxBackoff {
					backoff = s.maxBackoff
				}
			}
			log.Printf("[go-imap] poll error: %v", err)
			log.Printf("[go-imap] poll backoff=%s failures=%d", backoff, failed)
			time.Sleep(backoff)
			continue
		}
		failed = 0
		backoff = s.pollInterval
		time.Sleep(s.pollInterval)
	}
}

func (s *service) scanAndUpdateWithTimeout(onlyNew bool, timeout time.Duration) error {
	errCh := make(chan error, 1)
	go func() {
		errCh <- s.scanAndUpdate(onlyNew)
	}()
	select {
	case err := <-errCh:
		return err
	case <-time.After(timeout):
		return fmt.Errorf("scan timeout after %s", timeout)
	}
}

func writeJSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(data)
}

func (s *service) getCode(email string, consume bool, allowFallback bool) (cachedCode, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := strings.ToLower(strings.TrimSpace(email))
	if v, ok := s.cache[key]; ok {
		if consume {
			delete(s.cache, key)
		}
		return v, true
	}
	if allowFallback && s.fallback != nil {
		v := *s.fallback
		if consume {
			s.fallback = nil
		}
		return v, true
	}
	return cachedCode{}, false
}

func (s *service) stats() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	return map[string]any{
		"cache_size":    len(s.cache),
		"last_uid":      s.lastUID,
		"has_fallback":  s.fallback != nil,
		"poll_interval": s.pollInterval.String(),
		"max_backoff":   s.maxBackoff.String(),
		"scan_limit":    s.scanLimit,
		"mailbox":       s.box,
		"connected":     s.dialer != nil,
	}
}

func (s *service) recentEvents(limit int) []recentEvent {
	s.mu.Lock()
	defer s.mu.Unlock()
	if limit <= 0 || limit > len(s.recent) {
		limit = len(s.recent)
	}
	start := len(s.recent) - limit
	out := make([]recentEvent, limit)
	copy(out, s.recent[start:])
	return out
}

func loadDotenv() {
	wd, err := os.Getwd()
	if err != nil {
		return
	}
	path := filepath.Join(wd, ".env")
	f, err := os.Open(path)
	if err != nil {
		return // 文件不存在或不可读时静默跳过
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		idx := strings.Index(line, "=")
		if idx <= 0 {
			continue
		}
		key := strings.TrimSpace(line[:idx])
		val := strings.TrimSpace(line[idx+1:])
		if key == "" {
			continue
		}
		// 去掉值两端可选的双引号
		if len(val) >= 2 && (val[0] == '"' && val[len(val)-1] == '"' || val[0] == '\'' && val[len(val)-1] == '\'') {
			val = val[1 : len(val)-1]
		}
		if os.Getenv(key) == "" {
			_ = os.Setenv(key, val)
		}
	}
}

func main() {
	loadDotenv()
	s := newService()
	addr := getenv("GO_IMAP_SERVICE_ADDR", "127.0.0.1:18080")

	if err := s.connectAndSelectOnce(); err != nil {
		log.Fatalf("[go-imap] startup connect/select failed: %v", err)
	}

	go s.runPoller()

	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "service": "go-imap"})
	})

	http.HandleFunc("/stats", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "stats": s.stats()})
	})

	http.HandleFunc("/debug/recent", func(w http.ResponseWriter, r *http.Request) {
		limit := parseIntEnv("GO_IMAP_RECENT_LIMIT", 30)
		if q := strings.TrimSpace(r.URL.Query().Get("limit")); q != "" {
			if v, err := strconv.Atoi(q); err == nil {
				limit = v
			}
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "events": s.recentEvents(limit)})
	})

	http.HandleFunc("/code", func(w http.ResponseWriter, r *http.Request) {
		emailAddr := strings.TrimSpace(r.URL.Query().Get("email"))
		if emailAddr == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "missing email"})
			return
		}
		consume := r.URL.Query().Get("consume") != "0"
		allowFallback := r.URL.Query().Get("allow_fallback") == "1"
		rescan := r.URL.Query().Get("rescan") == "1"

		if rescan {
			if err := s.scanAndUpdateWithTimeout(false, 40*time.Second); err != nil {
				writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
				return
			}
		}

		if res, ok := s.getCode(emailAddr, consume, allowFallback); ok {
			writeJSON(w, http.StatusOK, map[string]any{
				"ok":        true,
				"code":      res.Code,
				"uid":       res.UID,
				"subject":   res.Subject,
				"from":      res.From,
				"cached_at": res.CachedAt.Unix(),
			})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "not_found": true})
	})

	log.Printf("[go-imap] service start at %s host=%s port=%d mailbox=%s poll=%s", addr, s.host, s.port, s.mailbox, s.pollInterval)
	if err := http.ListenAndServe(addr, nil); err != nil {
		log.Fatal(err)
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
