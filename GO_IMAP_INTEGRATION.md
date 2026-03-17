# Go IMAP 服务集成说明

## 概述

现在 `grok.py` 和 `main.py` 都支持通过 Go IMAP 服务获取验证码，可以灵活切换验证码获取方式。

## 环境变量配置

### 必需配置

无必需配置，默认使用 FreeMail 服务。

### 可选配置

```bash
# Go IMAP 服务地址（可选）
GO_IMAP_SERVICE_URL=http://localhost:8080

# 验证码获取方式（可选，默认 auto）
# - auto: 自动选择（优先 Go IMAP，失败则回退到 FreeMail）
# - true: 仅使用 Go IMAP
# - false: 仅使用 FreeMail
USE_GO_IMAP=auto

# 使用 Go IMAP 时需要配置邮箱域名
# 程序会自动生成随机前缀 + @EMAIL_DOMAIN 的邮箱地址
EMAIL_DOMAIN=example.com

# 代理配置（可选）
PROXY_URL=http://127.0.0.1:7890

# FreeMail 配置（仅在使用 FreeMail 时需要）
WORKER_DOMAIN=your-worker-domain
FREEMAIL_TOKEN=your-freemail-token
```

## 使用方式

### 1. grok.py

#### 基本使用

```bash
# 使用默认配置（FreeMail）
python grok.py -t 5 -n 10

# 配置 Go IMAP 服务后自动使用
export GO_IMAP_SERVICE_URL=http://localhost:8080
python grok.py -t 5 -n 10

# 强制仅使用 Go IMAP
export GO_IMAP_SERVICE_URL=http://localhost:8080
export USE_GO_IMAP=true
python grok.py -t 5 -n 10

# 强制仅使用 FreeMail（即使配置了 Go IMAP）
export GO_IMAP_SERVICE_URL=http://localhost:8080
export USE_GO_IMAP=false
python grok.py -t 5 -n 10
```

#### 启动时的输出示例

```
============================================================
Grok 注册机
============================================================
[*] 使用代理: http://127.0.0.1:7890
[*] Go IMAP 服务: http://localhost:8080
[*] 验证码获取方式: 自动 (优先 Go IMAP，回退 FreeMail)
[*] 正在初始化...
```

### 2. main.py

`main.py` 提供了一个独立的验证码获取测试工具。

#### 命令行参数

```bash
# 查看帮助
python main.py --help

# 自动选择方式（默认）
python main.py --email test@example.com --method auto

# 强制使用 Go IMAP
python main.py --email test@example.com --method go_imap

# 强制使用 FreeMail
python main.py --email test@example.com --method freemail

# 启用调试输出
python main.py --email test@example.com --method auto --debug

# 自定义超时时间
python main.py --email test@example.com --method auto --timeout 60
```

#### 在代码中使用

```python
from main import get_verification_code

# 自动选择最佳方式
code = get_verification_code("test@example.com", method="auto")

# 指定特定方式
code = get_verification_code("test@example.com", method="go_imap", timeout=60)

# 启用调试
code = get_verification_code("test@example.com", method="auto", debug=True)
```

## 工作原理

### 验证码获取流程

#### 1. auto 模式（默认）

```
1. 检查是否配置了 GO_IMAP_SERVICE_URL
2. 如果配置了，尝试使用 Go IMAP 服务
3. 如果 Go IMAP 失败或未配置，回退到 FreeMail
```

#### 2. go_imap 模式

```
1. 仅使用 Go IMAP 服务
2. 如果未配置 GO_IMAP_SERVICE_URL，直接返回失败
```

#### 3. freemail 模式

```
1. 仅使用 FreeMail 服务
2. 忽略 GO_IMAP_SERVICE_URL 配置
```

### Go IMAP 服务 API

#### 获取验证码

```http
GET /code?email={email}&allow_fallback=0&consume=1&rescan=0
```

**响应示例**：

```json
{
  "ok": true,
  "code": "123456",
  "uid": "12345"
}
```

## 优势对比

### Go IMAP 服务

**优点**：
- 独立服务，不依赖 Python 邮箱库
- 支持多种邮箱提供商
- 可以集中管理邮箱账号
- 支持邮件缓存和重试机制

**缺点**：
- 需要额外部署服务
- 需要配置邮箱账号

### FreeMail 服务

**优点**：
- 无需额外配置
- 临时邮箱，用完即删
- 不需要真实邮箱账号

**缺点**：
- 依赖第三方临时邮箱服务
- 可能不稳定
- 有使用限制

## 推荐配置

### 开发/测试环境

```bash
# 使用 FreeMail，简单快速
USE_GO_IMAP=false
```

### 生产环境

```bash
# 使用 Go IMAP，更稳定可靠
GO_IMAP_SERVICE_URL=http://your-go-imap-service:8080
USE_GO_IMAP=true
```

### 混合环境

```bash
# 自动模式，兼顾稳定性和容错性
GO_IMAP_SERVICE_URL=http://your-go-imap-service:8080
USE_GO_IMAP=auto
```

## 故障排查

### Go IMAP 服务无法连接

```bash
# 检查服务是否运行
curl http://localhost:8080/health

# 检查网络连接
ping your-go-imap-service

# 检查环境变量
echo $GO_IMAP_SERVICE_URL
```

### 验证码获取超时

```bash
# 增加超时时间
python main.py --email test@example.com --timeout 180

# 启用调试查看详细信息
python main.py --email test@example.com --debug
```

### FreeMail 服务失败

```bash
# 切换到 Go IMAP
export USE_GO_IMAP=true
python grok.py -t 1 -n 1
```

## 更新日志

### 2026-03-13

- ✅ `grok.py` 添加 Go IMAP 支持
- ✅ `main.py` 重构为验证码获取工具
- ✅ 支持三种获取方式：auto、go_imap、freemail
- ✅ 添加环境变量配置支持
- ✅ 添加详细的调试日志输出
