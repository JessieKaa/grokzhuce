# 快速启动指南

## 使用 Go IMAP 服务（推荐）

### 1. 配置环境变量

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env 文件
vim .env
```

配置以下关键参数：

```bash
# 使用 Go IMAP 模式
USE_GO_IMAP=true

# Go IMAP 服务地址
GO_IMAP_SERVICE_URL=http://localhost:8080

# 邮箱域名（你的真实邮箱域名）
EMAIL_DOMAIN=example.com

# 可选：代理配置
PROXY_URL=http://127.0.0.1:7890
```

### 2. 启动 Go IMAP 服务

确保你的 Go IMAP 服务已经运行在 `http://localhost:8080`。

### 3. 运行注册脚本

```bash
# 单线程注册 1 个账号
python grok.py -t 1 -n 1

# 5 线程注册 10 个账号
python grok.py -t 5 -n 10

# 调试模式
python grok.py -t 1 -n 1 --debug
```

### 4. 查看输出

成功时会显示：

```
============================================================
Grok 注册机
============================================================
[*] 使用代理: http://127.0.0.1:7890
[*] Go IMAP 服务: http://localhost:8080
[*] 验证码获取方式: Go IMAP (仅)
[*] 正在初始化...
[+] Action ID: 7f1234567890abcdef...
[*] 开始注册，目标数量: 1
[*] [ThreadPoolExecutor-0_0] 开始注册: random123@example.com
[✓] 注册成功: 1/1 | random123@example.com | SSO: abc123... | 平均: 45.2s | NSFW: ✓
```

---

## 使用 FreeMail 服务（备用）

### 1. 配置环境变量

```bash
# 编辑 .env 文件
vim .env
```

配置以下参数：

```bash
# 使用 FreeMail 模式
USE_GO_IMAP=false

# FreeMail 配置
WORKER_DOMAIN=your-worker-domain.workers.dev
FREEMAIL_TOKEN=your-freemail-token

# 可选：代理配置
PROXY_URL=http://127.0.0.1:7890
```

### 2. 运行注册脚本

```bash
python grok.py -t 5 -n 10
```

---

## 自动模式（智能切换）

### 1. 配置环境变量

```bash
# 自动模式（默认）
USE_GO_IMAP=auto

# 配置 Go IMAP（优先使用）
GO_IMAP_SERVICE_URL=http://localhost:8080
EMAIL_DOMAIN=example.com

# 配置 FreeMail（作为备用）
WORKER_DOMAIN=your-worker-domain.workers.dev
FREEMAIL_TOKEN=your-freemail-token
```

### 2. 运行注册脚本

```bash
python grok.py -t 5 -n 10
```

程序会：
1. 首先尝试使用 Go IMAP 服务
2. 如果 Go IMAP 失败，自动回退到 FreeMail
3. 确保最大的成功率

---

## 常见问题

### Q1: 提示 "Missing: WORKER_DOMAIN or FREEMAIL_TOKEN"

**原因**：使用 FreeMail 模式但未配置相关环境变量。

**解决方案**：
- 方案 1：切换到 Go IMAP 模式
  ```bash
  export USE_GO_IMAP=true
  export GO_IMAP_SERVICE_URL=http://localhost:8080
  export EMAIL_DOMAIN=example.com
  ```
- 方案 2：配置 FreeMail 环境变量
  ```bash
  export WORKER_DOMAIN=your-worker-domain.workers.dev
  export FREEMAIL_TOKEN=your-token
  ```

### Q2: 提示 "使用 Go IMAP 模式但未配置 EMAIL_DOMAIN 环境变量"

**解决方案**：
```bash
export EMAIL_DOMAIN=example.com
```

### Q3: Go IMAP 服务无法连接

**检查步骤**：
1. 确认服务是否运行：
   ```bash
   curl http://localhost:8080/health
   ```
2. 检查防火墙设置
3. 检查环境变量是否正确：
   ```bash
   echo $GO_IMAP_SERVICE_URL
   ```

### Q4: 验证码获取超时

**解决方案**：
1. 增加超时时间（修改代码中的 `timeout=120` 参数）
2. 检查邮箱服务是否正常
3. 启用调试模式查看详细日志：
   ```bash
   python grok.py -t 1 -n 1 --debug
   ```

---

## 性能优化建议

### 单机部署

```bash
# 推荐配置
python grok.py -t 5 -n 50
```

- 线程数：5-10
- 适合个人使用

### 服务器部署

```bash
# 高性能配置
python grok.py -t 20 -n 200
```

- 线程数：10-20
- 需要稳定的网络和足够的资源

### 注意事项

1. **线程数不宜过高**：过多线程可能导致 IP 被限制
2. **使用代理**：建议配置代理以提高成功率
3. **Go IMAP 优先**：Go IMAP 比 FreeMail 更稳定可靠
4. **监控日志**：及时发现和处理异常

---

## 测试验证码获取

使用 `main.py` 测试验证码获取功能：

```bash
# 测试 Go IMAP
python main.py --email test@example.com --method go_imap --debug

# 测试 FreeMail
python main.py --email test@example.com --method freemail --debug

# 自动模式
python main.py --email test@example.com --method auto --debug
```

---

## 输出文件

注册成功后，SSO token 会保存到：

- `grok.py`: `keys.txt`
- `cf_version/main.py`: `keys_cf.txt`

文件格式：每行一个 SSO token

```
sso_token_1
sso_token_2
sso_token_3
...
```
