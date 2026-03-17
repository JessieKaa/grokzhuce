import os, json, random, string, time, re, struct
import threading
import concurrent.futures
import argparse
import traceback
from urllib.parse import urljoin, urlparse
from curl_cffi import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from g import EmailService, TurnstileService, UserAgreementService, NsfwSettingsService

# 加载环境变量
load_dotenv()

# 获取代理配置
PROXY_URL = os.getenv("PROXY_URL", "").strip()
if PROXY_URL:
    PROXIES = {"http": PROXY_URL, "https": PROXY_URL}
    print(f"[*] 使用代理: {PROXY_URL}")
else:
    PROXIES = {}
    print("[*] 未配置代理，使用直连")

# Go IMAP 服务配置
GO_IMAP_SERVICE_URL = os.getenv("GO_IMAP_SERVICE_URL", "").strip()
USE_GO_IMAP = os.getenv("USE_GO_IMAP", "auto").strip().lower()  # auto, true, false

# 基础配置
# 基础 URL（用于 API 请求和 Solver）
base_url = "https://accounts.x.ai"
# 注册页面 URL（用于初始化扫描）
site_url = f"{base_url}/sign-up"
DEFAULT_IMPERSONATE = "chrome120"
CHROME_PROFILES = [
    {"impersonate": "chrome110", "version": "110.0.0.0", "brand": "chrome"},
    {"impersonate": "chrome119", "version": "119.0.0.0", "brand": "chrome"},
    {"impersonate": "chrome120", "version": "120.0.0.0", "brand": "chrome"},
    {"impersonate": "edge99", "version": "99.0.1150.36", "brand": "edge"},
    {"impersonate": "edge101", "version": "101.0.1210.47", "brand": "edge"},
]
def get_random_chrome_profile():
    profile = random.choice(CHROME_PROFILES)
    if profile.get("brand") == "edge":
        chrome_major = profile["version"].split(".")[0]
        chrome_version = f"{chrome_major}.0.0.0"
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_version} Safari/537.36 Edg/{profile['version']}"
        )
    else:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{profile['version']} Safari/537.36"
        )
    return profile["impersonate"], ua


# 动态获取的全局变量
config = {
    "site_key": "0x4AAAAAAAhr9JGVDZbrZOo0",
    "action_id": None,
    "state_tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22(app)%22%2C%7B%22children%22%3A%5B%22(auth)%22%2C%7B%22children%22%3A%5B%22sign-up%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2C%22%2Fsign-up%22%2C%22refresh%22%5D%7D%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
}

post_lock = threading.Lock()
file_lock = threading.Lock()
success_count = 0
start_time = time.time()
target_count = 100
stop_event = threading.Event()
output_file = None

def generate_random_name() -> str:
    length = random.randint(4, 6)
    return random.choice(string.ascii_uppercase) + ''.join(random.choice(string.ascii_lowercase) for _ in range(length - 1))

def generate_random_string(length: int = 15) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

def encode_grpc_message(field_id, string_value):
    key = (field_id << 3) | 2
    value_bytes = string_value.encode('utf-8')
    length = len(value_bytes)
    payload = struct.pack('B', key) + struct.pack('B', length) + value_bytes
    return b'\x00' + struct.pack('>I', len(payload)) + payload

def encode_grpc_message_verify(email, code):
    p1 = struct.pack('B', (1 << 3) | 2) + struct.pack('B', len(email)) + email.encode('utf-8')
    p2 = struct.pack('B', (2 << 3) | 2) + struct.pack('B', len(code)) + code.encode('utf-8')
    payload = p1 + p2
    return b'\x00' + struct.pack('>I', len(payload)) + payload

def send_email_code_grpc(session, email, debug_mode=False):
    url = f"{base_url}/auth_mgmt.AuthManagement/CreateEmailValidationCode"
    data = encode_grpc_message(1, email)
    headers = {"content-type": "application/grpc-web+proto", "x-grpc-web": "1", "x-user-agent": "connect-es/2.1.1", "origin": base_url, "referer": f"{site_url}?redirect=grok-com"}
    try:
        if debug_mode:
            print(f"[DEBUG] [{email}] 正在发送验证码请求...")
        res = session.post(url, data=data, headers=headers, timeout=15)
        if debug_mode:
            print(f"[DEBUG] [{email}] 发送验证码响应: status={res.status_code}, len={len(res.content)}")
        return res.status_code == 200
    except Exception as e:
        print(f"[-] {email} 发送验证码异常: {e}")
        return False

def verify_email_code_grpc(session, email, code, debug_mode=False):
    url = f"{base_url}/auth_mgmt.AuthManagement/VerifyEmailValidationCode"
    data = encode_grpc_message_verify(email, code)
    headers = {"content-type": "application/grpc-web+proto", "x-grpc-web": "1", "x-user-agent": "connect-es/2.1.1", "origin": base_url, "referer": f"{site_url}?redirect=grok-com"}
    try:
        if debug_mode:
            print(f"[DEBUG] [{email}] 正在验证验证码: {code}")
        res = session.post(url, data=data, headers=headers, timeout=15)
        if debug_mode:
            print(f"[DEBUG] [{email}] 验证验证码响应: status={res.status_code}, len={len(res.content)}")
        return res.status_code == 200
    except Exception as e:
        print(f"[-] {email} 验证验证码异常: {e}")
        return False

def get_code_via_go_service(email, timeout=120, debug_mode=False):
    """通过 Go IMAP 服务获取验证码"""
    if not GO_IMAP_SERVICE_URL:
        if debug_mode:
            print(f"[DEBUG] [{email}] GO_IMAP_SERVICE_URL 未配置")
        return None

    endpoint = f"{GO_IMAP_SERVICE_URL.rstrip('/')}/code"
    try:
        with requests.Session(impersonate="chrome120", proxies=PROXIES) as session:
            for sec in range(max(1, timeout)):
                try:
                    params = {
                        "email": email,
                        "allow_fallback": "0",
                        "consume": "1",
                        "rescan": "0",
                    }
                    resp = session.get(endpoint, params=params, timeout=8)
                    if resp.status_code != 200:
                        if debug_mode and sec % 10 == 0:
                            print(f"[DEBUG] [{email}] GET /code status={resp.status_code}")
                    else:
                        data = resp.json()
                        if data.get("ok") and data.get("code"):
                            if debug_mode:
                                print(f"[DEBUG] [{email}] Go IMAP 获取到验证码: {data.get('code')} uid={data.get('uid')}")
                            return str(data["code"]).strip()
                except Exception as poll_error:
                    if debug_mode and sec % 10 == 0:
                        print(f"[DEBUG] [{email}] Go IMAP 轮询异常: {poll_error}")

                if sec % 5 == 0 and debug_mode:
                    print(f"[DEBUG] [{email}] Go IMAP 轮询中... {sec}s")
                time.sleep(1)

            if debug_mode:
                print(f"[DEBUG] [{email}] Go IMAP 超时，未获取到验证码")
            return None
    except Exception as e:
        if debug_mode:
            print(f"[DEBUG] [{email}] Go IMAP 异常: {e}")
        return None

def get_verification_code(email, email_service, timeout=120, debug_mode=False):
    """
    统一的验证码获取接口

    根据 USE_GO_IMAP 环境变量决定使用哪种方式：
    - "true": 仅使用 Go IMAP
    - "false": 仅使用 FreeMail
    - "auto": 优先 Go IMAP，失败则回退到 FreeMail（默认）
    """
    if USE_GO_IMAP == "true":
        # 仅使用 Go IMAP
        if debug_mode:
            print(f"[DEBUG] [{email}] 使用 Go IMAP 服务获取验证码")
        return get_code_via_go_service(email, timeout, debug_mode)

    elif USE_GO_IMAP == "false":
        # 仅使用 FreeMail
        if debug_mode:
            print(f"[DEBUG] [{email}] 使用 FreeMail 服务获取验证码")
        if email_service:
            return email_service.fetch_verification_code(email, debug=debug_mode)
        else:
            print(f"[-] [{email}] EmailService 未初始化")
            return None

    else:  # "auto" 或其他值
        # 自动选择：优先 Go IMAP，失败则回退到 FreeMail
        if GO_IMAP_SERVICE_URL:
            if debug_mode:
                print(f"[DEBUG] [{email}] 尝试使用 Go IMAP 服务获取验证码")
            code = get_code_via_go_service(email, timeout, debug_mode)
            if code:
                return code
            if debug_mode:
                print(f"[DEBUG] [{email}] Go IMAP 失败，回退到 FreeMail")

        if debug_mode:
            print(f"[DEBUG] [{email}] 使用 FreeMail 服务获取验证码")
        if email_service:
            return email_service.fetch_verification_code(email, debug=debug_mode)
        else:
            print(f"[-] [{email}] EmailService 未初始化且 Go IMAP 不可用")
            return None

def safe_delete_email(email_service, email, debug_mode=False):
    """安全地删除邮箱（仅在使用 FreeMail 时）"""
    if email_service and email and USE_GO_IMAP != "true":
        try:
            safe_delete_email(email_service, email, debug_mode)
            if debug_mode:
                print(f"[DEBUG] 已删除邮箱: {email}")
        except Exception as e:
            if debug_mode:
                print(f"[DEBUG] 删除邮箱失败: {e}")

def register_single_thread(debug_mode=False, single_run=False):
    thread_id = threading.current_thread().name
    # 错峰启动，防止瞬时并发过高
    sleep_time = random.uniform(0, 2)
    if debug_mode:
        print(f"[DEBUG] [{thread_id}] 线程启动，错峰等待 {sleep_time:.2f}s")
    time.sleep(sleep_time)

    # 根据配置决定是否需要初始化 EmailService
    email_service = None
    need_email_service = (USE_GO_IMAP != "true")  # 仅在非纯 Go IMAP 模式下需要

    try:
        if debug_mode:
            print(f"[DEBUG] [{thread_id}] 正在初始化服务...")

        # 只在需要时初始化 EmailService
        if need_email_service:
            try:
                email_service = EmailService()
                if debug_mode:
                    print(f"[DEBUG] [{thread_id}] EmailService 初始化成功")
            except Exception as e:
                if USE_GO_IMAP == "false":
                    # 如果强制使用 FreeMail 但初始化失败，则报错退出
                    print(f"[-] [{thread_id}] EmailService 初始化失败: {e}")
                    if debug_mode:
                        traceback.print_exc()
                    return
                else:
                    # auto 模式下，EmailService 初始化失败不影响，可以用 Go IMAP
                    if debug_mode:
                        print(f"[DEBUG] [{thread_id}] EmailService 初始化失败，将仅使用 Go IMAP: {e}")
                    email_service = None

        turnstile_service = TurnstileService()
        user_agreement_service = UserAgreementService()
        nsfw_service = NsfwSettingsService()
        if debug_mode:
            print(f"[DEBUG] [{thread_id}] 服务初始化成功")
    except Exception as e:
        print(f"[-] [{thread_id}] 服务初始化失败: {e}")
        if debug_mode:
            traceback.print_exc()
        return

    # 修正：直接从 config 获取
    final_action_id = config["action_id"]
    if not final_action_id:
        print("[-] 线程退出：缺少 Action ID")
        return

    current_email = None  # 追踪当前邮箱，确保异常时能删除
    use_freemail = (USE_GO_IMAP != "true")  # 是否使用 FreeMail 创建临时邮箱

    while True:
        try:
            if stop_event.is_set():
                if current_email and use_freemail:
                    safe_delete_email(email_service, current_email, debug_mode)
                return
            impersonate_fingerprint, account_user_agent = get_random_chrome_profile()
            with requests.Session(impersonate=impersonate_fingerprint, proxies=PROXIES) as session:
                # 预热连接
                try: session.get(base_url, timeout=10)
                except: pass

                password = generate_random_string()

                # 获取邮箱地址
                if use_freemail and email_service:
                    # 使用 FreeMail 创建临时邮箱
                    try:
                        if debug_mode:
                            print(f"[DEBUG] [{thread_id}] 正在创建临时邮箱...")
                        jwt, email = email_service.create_email()
                        current_email = email
                        if debug_mode:
                            print(f"[DEBUG] [{thread_id}] 邮箱创建成功: {email}")
                    except Exception as e:
                        print(f"[-] [{thread_id}] 邮箱服务抛出异常: {e}")
                        if debug_mode:
                            traceback.print_exc()
                        jwt, email, current_email = None, None, None

                    if not email:
                        print(f"[-] [{thread_id}] 邮箱创建失败，5秒后重试...")
                        time.sleep(5)
                        continue
                else:
                    # 使用 Go IMAP，从环境变量获取邮箱
                    email_domain = os.getenv("EMAIL_DOMAIN", "")
                    if not email_domain:
                        print(f"[-] [{thread_id}] 使用 Go IMAP 模式但未配置 EMAIL_DOMAIN 环境变量")
                        return

                    # 生成随机邮箱前缀
                    email_prefix = generate_random_string(10)
                    email = f"{email_prefix}@{email_domain}"
                    current_email = email
                    if debug_mode:
                        print(f"[DEBUG] [{thread_id}] 使用邮箱: {email}")

                if stop_event.is_set():
                    safe_delete_email(email_service, email, debug_mode)
                    current_email = None
                    return

                print(f"[*] [{thread_id}] 开始注册: {email}")

                # Step 1: 发送验证码
                if debug_mode:
                    print(f"[DEBUG] [{thread_id}] Step 1: 发送验证码...")
                if not send_email_code_grpc(session, email, debug_mode):
                    print(f"[-] [{thread_id}] 发送验证码失败，删除邮箱: {email}")
                    safe_delete_email(email_service, email, debug_mode)
                    current_email = None
                    time.sleep(5)
                    continue
                if debug_mode:
                    print(f"[DEBUG] [{thread_id}] 验证码发送成功")

                # Step 2: 获取验证码
                if debug_mode:
                    print(f"[DEBUG] [{thread_id}] Step 2: 获取验证码...")
                verify_code = get_verification_code(email, email_service, timeout=120, debug_mode=debug_mode)
                if debug_mode:
                    print(f"[DEBUG] [{thread_id}] 获取到验证码: {verify_code}")
                if not verify_code:
                    print(f"[-] [{thread_id}] 获取验证码失败，删除邮箱: {email}")
                    safe_delete_email(email_service, email, debug_mode)
                    current_email = None
                    continue

                # Step 3: 验证验证码
                if debug_mode:
                    print(f"[DEBUG] [{thread_id}] Step 3: 验证验证码...")
                if not verify_email_code_grpc(session, email, verify_code, debug_mode):
                    print(f"[-] [{thread_id}] 验证验证码失败，删除邮箱: {email}")
                    safe_delete_email(email_service, email, debug_mode)
                    current_email = None
                    continue
                if debug_mode:
                    print(f"[DEBUG] [{thread_id}] 验证码验证成功")

                # Step 4: 注册重试循环
                if debug_mode:
                    print(f"[DEBUG] [{thread_id}] Step 4: 开始注册流程...")
                for attempt in range(3):
                    if debug_mode:
                        print(f"[DEBUG] [{thread_id}] 注册尝试 {attempt + 1}/3")
                    if stop_event.is_set():
                        safe_delete_email(email_service, email, debug_mode)
                        current_email = None
                        return
                    if debug_mode:
                        print(f"[DEBUG] [{thread_id}] 创建 Turnstile 任务...")
                    task_id = turnstile_service.create_task(site_url, config["site_key"])
                    if debug_mode:
                        print(f"[DEBUG] [{thread_id}] Task ID: {task_id}")
                    token = turnstile_service.get_response(task_id)
                    if debug_mode:
                        print(f"[DEBUG] [{thread_id}] 获取到 Token: {'成功' if token and token != 'CAPTCHA_FAIL' else '失败'} (len={len(token) if token else 0})")

                    if not token or token == "CAPTCHA_FAIL":
                        continue

                    headers = {
                        "user-agent": account_user_agent, "accept": "text/x-component", "content-type": "text/plain;charset=UTF-8",
                        "origin": site_url, "referer": f"{site_url}/sign-up", "cookie": f"__cf_bm={session.cookies.get('__cf_bm','')}",
                        "next-router-state-tree": config["state_tree"], "next-action": final_action_id
                    }
                    payload = [{
                        "emailValidationCode": verify_code,
                        "createUserAndSessionRequest": {
                            "email": email, "givenName": generate_random_name(), "familyName": generate_random_name(),
                            "clearTextPassword": password, "tosAcceptedVersion": "$undefined"
                        },
                        "turnstileToken": token, "promptOnDuplicateEmail": True
                    }]

                    with post_lock:
                        if debug_mode:
                            print(f"[DEBUG] [{thread_id}] 发送注册请求...")
                        res = session.post(f"{site_url}/sign-up", json=payload, headers=headers)
                        if debug_mode:
                            print(f"[DEBUG] [{thread_id}] 注册响应: status={res.status_code}, len={len(res.text)}")

                    if res.status_code == 200:
                        match = re.search(r'(https://[^" \s]+set-cookie\?q=[^:" \s]+)1:', res.text)
                        if not match:
                            print(f"[-] [{thread_id}] 未找到 verify_url，响应: {res.text[:200]}...")
                            safe_delete_email(email_service, email, debug_mode)
                            current_email = None
                            break
                        if match:
                            verify_url = match.group(1)
                            session.get(verify_url, allow_redirects=True)
                            sso = session.cookies.get("sso")
                            sso_rw = session.cookies.get("sso-rw")
                            if debug_mode:
                                print(f"[DEBUG] [{thread_id}] SSO: {sso[:20] if sso else None}..., sso-rw: {'存在' if sso_rw else '无'}")
                            if not sso:
                                print(f"[-] [{thread_id}] 未获取到 SSO cookie")
                                safe_delete_email(email_service, email, debug_mode)
                                current_email = None
                                break

                            if debug_mode:
                                print(f"[DEBUG] [{thread_id}] 接受用户协议...")
                            tos_result = user_agreement_service.accept_tos_version(
                                sso=sso,
                                sso_rw=sso_rw or "",
                                impersonate=impersonate_fingerprint,
                                user_agent=account_user_agent,
                                proxies=PROXIES,
                            )
                            tos_hex = tos_result.get("hex_reply") or ""
                            if debug_mode:
                                print(f"[DEBUG] [{thread_id}] TOS 结果: ok={tos_result.get('ok')}, hex={tos_hex[:20] if tos_hex else None}...")
                            if not tos_result.get("ok") or not tos_hex:
                                print(f"[-] [{thread_id}] TOS 接受失败")
                                safe_delete_email(email_service, email, debug_mode)
                                current_email = None
                                break

                            if debug_mode:
                                print(f"[DEBUG] [{thread_id}] 启用 NSFW...")
                            nsfw_result = nsfw_service.enable_nsfw(
                                sso=sso,
                                sso_rw=sso_rw or "",
                                impersonate=impersonate_fingerprint,
                                user_agent=account_user_agent,
                                proxies=PROXIES,
                            )
                            nsfw_hex = nsfw_result.get("hex_reply") or ""
                            nsfw_ok = nsfw_result.get("ok", False)
                            if debug_mode:
                                print(f"[DEBUG] [{thread_id}] NSFW 结果: ok={nsfw_ok}, hex={nsfw_hex[:20] if nsfw_hex else None}...")
                            if not nsfw_ok:
                                print(f"[!] [{thread_id}] NSFW 启用失败，但继续保存账号")

                            # 立即进行二次验证 (enable_unhinged)
                            if debug_mode:
                                print(f"[DEBUG] [{thread_id}] 启用 Unhinged...")
                            unhinged_result = nsfw_service.enable_unhinged(
                                sso=sso,
                                sso_rw=sso_rw or "",
                                proxies=PROXIES,
                            )
                            unhinged_ok = unhinged_result.get("ok", False)
                            if debug_mode:
                                print(f"[DEBUG] [{thread_id}] Unhinged 结果: ok={unhinged_ok}")
                            
                            # NSFW 和 Unhinged 失败不阻断主流程
                            if not unhinged_ok:
                                print(f"[!] [{thread_id}] Unhinged 启用失败")

                            with file_lock:
                                global success_count
                                if success_count >= target_count:
                                    if not stop_event.is_set():
                                        stop_event.set()
                                    print(f"[*] 已达到目标数量，删除邮箱: {email}")
                                    safe_delete_email(email_service, email, debug_mode)
                                    current_email = None
                                    break
                                try:
                                    with open(output_file, "a") as f: f.write(sso + "\n")
                                except Exception as write_err:
                                    print(f"[-] 写入文件失败: {write_err}")
                                    safe_delete_email(email_service, email, debug_mode)
                                    current_email = None
                                    break
                                success_count += 1
                                avg = (time.time() - start_time) / success_count
                                nsfw_tag = "✓" if unhinged_ok else "✗"
                                print(f"[✓] 注册成功: {success_count}/{target_count} | {email} | SSO: {sso[:15]}... | 平均: {avg:.1f}s | NSFW: {nsfw_tag}")
                                safe_delete_email(email_service, email, debug_mode)
                                current_email = None
                                if success_count >= target_count and not stop_event.is_set():
                                    stop_event.set()
                                    print(f"[*] 已达到目标数量: {success_count}/{target_count}，停止新注册")
                            break  # 跳出 for 循环，继续 while True 注册下一个

                    time.sleep(3)
                else:
                    # 如果重试 3 次都失败 (for 循环没有被 break)
                    safe_delete_email(email_service, email, debug_mode)
                    current_email = None
                    time.sleep(5)

        except Exception as e:
            print(f"[-] [{thread_id}] 异常: {str(e)[:100]}")
            if debug_mode:
                traceback.print_exc()
            # 异常时确保删除邮箱
            if current_email:
                try:
                    safe_delete_email(email_service, current_email, debug_mode)
                except Exception as del_err:
                    if debug_mode:
                        print(f"[DEBUG] [{thread_id}] 删除邮箱失败: {del_err}")
                current_email = None
            if single_run:
                raise  # debug模式下单次运行就抛出
            time.sleep(5)

def main():
    parser = argparse.ArgumentParser(description='Grok 注册机')
    parser.add_argument('-t', '--threads', type=int, default=1, help='并发数 (默认1)')
    parser.add_argument('-n', '--number', type=int, default=1, help='注册数量 (默认1)')
    parser.add_argument('--debug', action='store_true', help='调试模式：显示详细错误堆栈')
    parser.add_argument('--single', action='store_true', help='单线程单次运行模式：出错立即抛出')
    parser.add_argument('--no-input', action='store_true', help='非交互模式，使用默认参数')
    args = parser.parse_args()

    print("=" * 60 + "\nGrok 注册机\n" + "=" * 60)

    # 显示验证码获取方式配置
    if GO_IMAP_SERVICE_URL:
        print(f"[*] Go IMAP 服务: {GO_IMAP_SERVICE_URL}")
    if USE_GO_IMAP == "true":
        print("[*] 验证码获取方式: Go IMAP (仅)")
    elif USE_GO_IMAP == "false":
        print("[*] 验证码获取方式: FreeMail (仅)")
    else:
        if GO_IMAP_SERVICE_URL:
            print("[*] 验证码获取方式: 自动 (优先 Go IMAP，回退 FreeMail)")
        else:
            print("[*] 验证码获取方式: FreeMail")

    # 1. 扫描参数
    print("[*] 正在初始化...")
    start_url = site_url
    print(f"[DEBUG] 请求 URL: {start_url}")
    with requests.Session(impersonate=DEFAULT_IMPERSONATE, proxies=PROXIES) as s:
        try:
            print("[DEBUG] 正在获取页面...")
            html = s.get(start_url, timeout=30).text
            print(f"[DEBUG] 页面获取成功，长度: {len(html)}")
            # Key
            key_match = re.search(r'sitekey":"(0x4[a-zA-Z0-9_-]+)"', html)
            if key_match: 
                config["site_key"] = key_match.group(1)
                print(f"[DEBUG] Site Key: {config['site_key']}")
            # Tree
            tree_match = re.search(r'next-router-state-tree":"([^"]+)"', html)
            if tree_match: 
                config["state_tree"] = tree_match.group(1)
                print(f"[DEBUG] State Tree 已获取")
            # Action ID
            print("[DEBUG] 正在解析 JS 文件...")
            soup = BeautifulSoup(html, 'html.parser')
            js_urls = [urljoin(start_url, script['src']) for script in soup.find_all('script', src=True) if '_next/static' in script['src']]
            print(f"[DEBUG] 找到 {len(js_urls)} 个 JS 文件")
            for js_url in js_urls:
                print(f"[DEBUG] 正在请求 JS: {js_url}")
                js_content = s.get(js_url, timeout=30, proxies=PROXIES).text
                match = re.search(r'7f[a-fA-F0-9]{40}', js_content)
                if match:
                    config["action_id"] = match.group(0)
                    print(f"[+] Action ID: {config['action_id']}")
                    break
        except Exception as e:
            print(f"[-] 初始化扫描失败: {e}")
            if args.debug:
                traceback.print_exc()
            return

    if not config["action_id"]:
        print("[-] 错误: 未找到 Action ID")
        return

    # 2. 启动
    if args.no_input or args.threads or args.number:
        t = args.threads
        total = args.number
    else:
        # 交互式输入（保留原来的逻辑作为fallback）
        try:
            t = int(input("\n并发数 (默认8): ").strip() or 8)
        except: t = 8

        try:
            total = int(input("注册数量 (默认100): ").strip() or 100)
        except: total = 100

    global target_count, output_file
    target_count = max(1, total)

    from datetime import datetime
    os.makedirs("keys", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"keys/grok_{timestamp}_{target_count}.txt"

    print(f"[*] 启动 {t} 个线程，目标 {target_count} 个")
    print(f"[*] 输出: {output_file}")
    print(f"[*] 调试模式: {'开启' if args.debug else '关闭'}")
    
    if args.single:
        # 单线程单次运行模式
        print("[*] 单线程单次运行模式")
        register_single_thread(debug_mode=args.debug, single_run=True)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=t) as executor:
            futures = [executor.submit(register_single_thread, args.debug, args.single) for _ in range(t)]
            concurrent.futures.wait(futures)

if __name__ == "__main__":
    main()