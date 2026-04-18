"""
Grok (x.ai) 纯协议注册引擎

注册流程:
1. 访问注册页 → 获取 Next.js action ID + state_tree
2. CreateEmailValidationCode (gRPC-web) → 发送验证码
3. 等待验证码 (通过外部 mailbox callback)
4. VerifyEmailValidationCode (gRPC-web) → 验证邮箱
5. 解决 Turnstile (YesCaptcha API)
6. 提交注册 (Next.js Server Action)
7. SSO cookie 链 → 获取 sso / sso-rw

参考: 53282dd1/grok_register_fixed.py
"""

import json
import random
import re
import secrets
import string
import struct
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests

# ── 常量 ──────────────────────────────────────────────────────

ACCOUNTS_BASE = "https://accounts.x.ai"
GRPC_SERVICE = "auth_mgmt.AuthManagement"
TURNSTILE_SITEKEY = "0x4AAAAAAAhr9JGVDZbrZOo0"
TURNSTILE_WEBSITE_URL = f"{ACCOUNTS_BASE}/sign-up?redirect=grok-com"

_BROWSERS = ["chrome131", "chrome133", "chrome136"]

COMMON_HEADERS = {
    "accept-language": "en-US,en;q=0.9",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

GRPC_HEADERS = {
    "content-type": "application/grpc-web+proto",
    "x-grpc-web": "1",
    "x-user-agent": "connect-es/2.1.1",
    "accept": "*/*",
    "origin": ACCOUNTS_BASE,
    "referer": f"{ACCOUNTS_BASE}/sign-up?redirect=grok-com",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
}

ACTION_ID_REGEX = re.compile(r"7f[a-fA-F0-9]{40}")
DEFAULT_STATE_TREE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%22(app)%22%2C%7B%22children%22"
    "%3A%5B%22(auth)%22%2C%7B%22children%22%3A%5B%22sign-up%22%2C%7B%22"
    "children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2C%22%2Fsign-up%22%2C%22"
    "refresh%22%5D%7D%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2C"
    "null%2Cnull%2Ctrue%5D"
)

_ALLOWED_SSO_HOSTS = frozenset({
    "auth.x.ai", "auth.grok.com", "auth.grokipedia.com",
    "auth.grokusercontent.com", "accounts.x.ai",
})

FIRST_NAMES = [
    "Alex", "Ava", "Ethan", "Emma", "Liam", "Mia", "Noah", "Olivia",
    "Ryan", "Sophia", "James", "Isabella", "Lucas", "Charlotte", "Mason",
]
LAST_NAMES = [
    "Anderson", "Brown", "Clark", "Davis", "Evans", "Garcia", "Harris",
    "Johnson", "Miller", "Smith", "Wilson", "Moore", "Taylor", "Thomas",
]


# ── Protobuf 编码 ────────────────────────────────────────────

def encode_varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def encode_string_field(field_number: int, value: str) -> bytes:
    tag = (field_number << 3) | 2
    data = value.encode("utf-8")
    return encode_varint(tag) + encode_varint(len(data)) + data


def wrap_grpc_web(payload: bytes) -> bytes:
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def parse_grpc_web_response(data: bytes) -> dict:
    result = {"status": None, "payload": b"", "trailers": {}}
    if len(data) < 5:
        return result
    pos = 0
    while pos < len(data):
        if pos + 5 > len(data):
            break
        flag = data[pos]
        length = struct.unpack(">I", data[pos + 1: pos + 5])[0]
        pos += 5
        if pos + length > len(data):
            break
        frame_data = data[pos: pos + length]
        pos += length
        if flag == 0x80:
            trailer_str = frame_data.decode("utf-8", errors="replace")
            for line in trailer_str.strip().split("\r\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    result["trailers"][k.strip()] = v.strip()
            result["status"] = result["trailers"].get("grpc-status", "unknown")
        elif flag == 0x00:
            result["payload"] = frame_data
    return result


# ── 工具 ──────────────────────────────────────────────────────

def _rand_ua() -> Tuple[str, str]:
    """返回随机 (user_agent, sec_ch_ua)"""
    version = random.choice(["131", "133", "136"])
    ua = (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36"
    )
    sec = f'"Not:A-Brand";v="99", "Google Chrome";v="{version}", "Chromium";v="{version}"'
    return ua, sec


def _rand_name() -> Tuple[str, str]:
    return secrets.choice(FIRST_NAMES), secrets.choice(LAST_NAMES)


def _rand_password(length: int = 14) -> str:
    required = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*_-+="),
    ]
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*_-+="
    remaining = [secrets.choice(alphabet) for _ in range(length - len(required))]
    chars = required + remaining
    random.SystemRandom().shuffle(chars)
    return "".join(chars)


def _delay(low=0.3, high=1.0):
    time.sleep(random.uniform(low, high))


def _extract_action_id_from_js(js_text: str) -> Optional[str]:
    if not js_text:
        return None
    m = ACTION_ID_REGEX.search(js_text)
    return m.group(0) if m else None


def _extract_action_id_from_html(html: str) -> Optional[str]:
    if not html:
        return None
    m = re.search(r'"(7f[a-fA-F0-9]{40})"', html)
    return m.group(1) if m else None


# ── 注册器 ────────────────────────────────────────────────────

class GrokProtocolRegister:
    """纯协议 Grok 注册（curl_cffi + gRPC-web）"""

    def __init__(
        self,
        proxy: Optional[str] = None,
        log_fn: Callable = print,
        yescaptcha_key: str = "",
        turnstile_timeout: int = 120,
    ):
        self.proxy = proxy
        self.log = log_fn
        self.yescaptcha_key = yescaptcha_key
        self.turnstile_timeout = turnstile_timeout

        # 随机指纹
        self._browser = random.choice(_BROWSERS)
        self._ua, self._sec_ch_ua = _rand_ua()
        self._headers = {
            **COMMON_HEADERS,
            "user-agent": self._ua,
            "sec-ch-ua": self._sec_ch_ua,
        }
        self._grpc_headers = {
            **GRPC_HEADERS,
            "user-agent": self._ua,
            "sec-ch-ua": self._sec_ch_ua,
        }

        self.session = curl_requests.Session(impersonate=self._browser)
        if proxy:
            self.session.proxies = {"https": proxy, "http": proxy}

        self.state_tree = ""
        self.turnstile_sitekey = TURNSTILE_SITEKEY
        self.turnstile_website_url = TURNSTILE_WEBSITE_URL

    # ── gRPC ──

    def _grpc_call(self, method: str, payload: bytes) -> dict:
        url = f"{ACCOUNTS_BASE}/{GRPC_SERVICE}/{method}"
        body = wrap_grpc_web(payload)
        resp = self.session.post(url, headers=self._grpc_headers, data=body)
        if resp.status_code != 200:
            raise RuntimeError(f"gRPC {method} failed: HTTP {resp.status_code} {resp.text[:300]}")
        parsed = parse_grpc_web_response(resp.content)
        grpc_status = parsed.get("status")
        if grpc_status and grpc_status != "0":
            msg = parsed["trailers"].get("grpc-message", "unknown")
            raise RuntimeError(f"gRPC {method} error: {msg}")
        return parsed

    # ── SSO Cookie 链 ──

    def _follow_sso_chain(self, url: str) -> Dict[str, str]:
        collected: Dict[str, str] = {}
        if not url:
            return collected

        self.log("[SSO] 获取 SSO cookies...")

        # 尝试自动重定向
        try:
            self.session.get(
                url,
                headers={
                    **self._headers,
                    "accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "referer": f"{ACCOUNTS_BASE}/",
                    "sec-fetch-site": "cross-site",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-dest": "document",
                    "upgrade-insecure-requests": "1",
                },
                allow_redirects=True,
            )
        except Exception:
            pass

        for name in ("sso", "sso-rw"):
            jar = getattr(self.session.cookies, "jar", None)
            if jar:
                for cookie in jar:
                    if getattr(cookie, "name", "") == name and getattr(cookie, "value", ""):
                        collected[name] = cookie.value

        if not collected.get("sso"):
            # 手动逐跳
            hop_url = url
            for _ in range(8):
                try:
                    resp = self.session.get(
                        hop_url,
                        headers={**self._headers, "accept": "text/html,*/*;q=0.8", "referer": f"{ACCOUNTS_BASE}/"},
                        allow_redirects=False,
                    )
                except Exception:
                    break
                for name, value in resp.cookies.items():
                    if name in ("sso", "sso-rw") and value:
                        collected[name] = value
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location", "")
                    if not location:
                        break
                    if location.startswith("/"):
                        p = urlparse(hop_url)
                        location = f"{p.scheme}://{p.netloc}{location}"
                    if urlparse(location).netloc not in _ALLOWED_SSO_HOSTS:
                        break
                    hop_url = location
                else:
                    break

        if collected.get("sso"):
            self.log(f"[SSO] OK: sso={collected['sso'][:40]}...")
        return collected

    # ── Turnstile ──

    def _solve_turnstile(self) -> str:
        if not self.yescaptcha_key:
            raise RuntimeError("需要 YesCaptcha Key 来解决 Turnstile 验证码")

        from core.base_captcha import YesCaptcha
        solver = YesCaptcha(self.yescaptcha_key)
        self.log("[Turnstile] 调用 YesCaptcha 解码...")
        token = solver.solve_turnstile(self.turnstile_website_url, self.turnstile_sitekey)
        if not token:
            raise RuntimeError("Turnstile 验证码求解失败")
        self.log(f"[Turnstile] OK: {token[:40]}...")
        return token

    # ── 注册步骤 ──

    def _visit_signup(self) -> str:
        """Step 1: 访问注册页获取 action ID"""
        self.log("Step1: 访问注册页...")

        # 临时 session 提取配置
        tmp = curl_requests.Session(impersonate=self._browser)
        if self.proxy:
            tmp.proxies = {"https": self.proxy, "http": self.proxy}

        resp = tmp.get(
            TURNSTILE_WEBSITE_URL,
            headers={**self._headers, "accept": "text/html,*/*;q=0.8", "referer": "https://grok.com/"},
            allow_redirects=True,
        )
        page_url = str(resp.url or TURNSTILE_WEBSITE_URL)
        self.turnstile_website_url = page_url

        # 提取 action ID (从 JS chunks)
        action_id = None
        script_paths = re.findall(r'<script[^>]+src="(/_next/static/chunks/[^"]+\.js)"', resp.text)
        for path in reversed(script_paths):
            try:
                chunk_resp = tmp.get(
                    f"{ACCOUNTS_BASE}{path}",
                    headers={**self._headers, "accept": "*/*", "referer": page_url},
                )
                if chunk_resp.status_code == 200:
                    action_id = _extract_action_id_from_js(chunk_resp.text)
                    if action_id:
                        break
            except Exception:
                continue

        if not action_id:
            action_id = _extract_action_id_from_html(resp.text)
        if not action_id:
            raise RuntimeError("无法提取 Next.js action ID")

        # 提取 sitekey
        sk_match = re.search(r'sitekey["\s:]+["\']?(0x[0-9A-Za-z]+)', resp.text)
        if sk_match:
            self.turnstile_sitekey = sk_match.group(1)

        # 提取 state_tree
        tree_match = re.search(r'next-router-state-tree":"([^"]+)"', resp.text)
        if tree_match:
            self.state_tree = tree_match.group(1)

        try:
            tmp.close()
        except Exception:
            pass

        # 重建主 session 获取干净 __cf_bm
        self.session = curl_requests.Session(impersonate=self._browser)
        if self.proxy:
            self.session.proxies = {"https": self.proxy, "http": self.proxy}
        try:
            self.session.get(ACCOUNTS_BASE, headers={**self._headers, "accept": "text/html,*/*;q=0.8"}, timeout=10)
        except Exception:
            pass

        self.log(f"  action_id: {action_id[:16]}...")
        return action_id

    def _send_email_code(self, email: str) -> None:
        """Step 2: 发送验证码"""
        self.log(f"Step2: 发送验证码到 {email}...")
        payload = encode_string_field(1, email)
        self._grpc_call("CreateEmailValidationCode", payload)
        self.log("  验证码已发送")

    def _verify_email_code(self, email: str, code: str) -> None:
        """Step 3: 验证邮箱验证码"""
        self.log(f"Step3: 验证邮箱验证码 {code}...")
        payload = encode_string_field(1, email) + encode_string_field(2, code)
        self._grpc_call("VerifyEmailValidationCode", payload)
        self.log("  验证通过")

    def _submit_registration(
        self, email: str, password: str, given_name: str, family_name: str,
        turnstile_token: str, action_id: str, email_code: str,
    ) -> dict:
        """Step 5: 提交注册"""
        self.log("Step5: 提交注册...")

        payload = [{
            "emailValidationCode": email_code,
            "createUserAndSessionRequest": {
                "email": email,
                "givenName": given_name,
                "familyName": family_name,
                "clearTextPassword": password,
                "tosAcceptedVersion": "$undefined",
            },
            "turnstileToken": turnstile_token,
            "promptOnDuplicateEmail": True,
        }]

        tree_val = self.state_tree or DEFAULT_STATE_TREE
        cf_bm = ""
        try:
            cf_bm = self.session.cookies.get("__cf_bm", "")
        except Exception:
            pass

        headers = {
            "user-agent": self._ua,
            "accept": "text/x-component",
            "content-type": "text/plain;charset=UTF-8",
            "origin": ACCOUNTS_BASE,
            "referer": f"{ACCOUNTS_BASE}/sign-up",
            "cookie": f"__cf_bm={cf_bm}",
            "next-router-state-tree": tree_val,
            "next-action": action_id,
        }

        resp = self.session.post(f"{ACCOUNTS_BASE}/sign-up", json=payload, headers=headers)
        resp_text = resp.text or ""
        self.log(f"  HTTP {resp.status_code}, body={len(resp_text)} chars")

        # 解析 RSC 响应获取 verify_url
        verify_url = ""
        action_error = ""
        for raw_line in resp_text.splitlines():
            if ":" not in raw_line:
                continue
            _, line_payload = raw_line.split(":", 1)
            line_payload = line_payload.strip()
            if not line_payload.startswith("{"):
                continue
            try:
                obj = json.loads(line_payload)
            except Exception:
                continue
            if isinstance(obj, dict):
                if obj.get("url"):
                    verify_url = str(obj["url"]).replace("\\/", "/")
                if obj.get("error"):
                    action_error = str(obj["error"])

        if not verify_url:
            m = re.search(r'(https://[^"\s]+set-cookie\?q=[^:"\s]+)', resp_text)
            if m:
                verify_url = m.group(1).replace("\\/", "/")

        if action_error:
            self.log(f"  Server Action error: {action_error}")
            raise RuntimeError(f"注册失败: {action_error}")

        # 跟随 SSO cookie 链
        sso_cookies = self._follow_sso_chain(verify_url) if verify_url else {}

        return {
            "status_code": resp.status_code,
            "sso": sso_cookies.get("sso", ""),
            "sso_rw": sso_cookies.get("sso-rw", ""),
            "verify_url": verify_url,
        }

    def _create_session_fallback(self, email: str, password: str) -> Dict[str, str]:
        """SSO fallback: 通过 createSession RPC 登录获取 SSO"""
        self.log("[SSO] 注册响应无 SSO，尝试 createSession 登录...")
        turnstile_token = self._solve_turnstile()

        payload = {
            "rpc": "createSession",
            "req": {
                "createSessionRequest": {
                    "credentials": {
                        "case": "emailAndPassword",
                        "value": {"email": email, "clearTextPassword": password},
                    },
                },
                "turnstileToken": turnstile_token,
            },
        }
        resp = self.session.post(
            f"{ACCOUNTS_BASE}/api/rpc",
            headers={
                **self._headers,
                "accept": "application/json",
                "content-type": "application/json",
                "origin": ACCOUNTS_BASE,
                "referer": TURNSTILE_WEBSITE_URL,
            },
            json=payload,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"createSession failed: HTTP {resp.status_code}")

        data = resp.json()
        cookie_setter_url = str(data.get("cookieSetterUrl") or "").strip()
        if not cookie_setter_url:
            raise RuntimeError("createSession 无 cookieSetterUrl")

        return self._follow_sso_chain(cookie_setter_url)

    # ── Stripe 支付链接 ──

    def get_payment_link(self, email: str, sso: str, sso_rw: str) -> str:
        """注册成功后获取 Grok SuperGrok/Pro 支付链接"""
        cookies = {"sso": sso, "sso-rw": sso_rw, "i18nextLng": "en"}
        base_headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://grok.com",
            "referer": "https://grok.com/",
            "user-agent": self._ua,
        }

        import uuid

        # Step 1: 创建 Stripe 客户
        self.log("[Payment] Step1: 创建 Stripe 客户...")
        billing_name = f"{secrets.choice(FIRST_NAMES)} {secrets.choice(LAST_NAMES)}"
        try:
            resp1 = curl_requests.post(
                "https://grok.com/rest/subscriptions/customer/new",
                headers={**base_headers, "x-xai-request-id": str(uuid.uuid4())},
                cookies=cookies,
                json={"billingInfo": {"name": billing_name, "email": email}},
                impersonate=self._browser,
                timeout=20,
                proxies={"https": self.proxy, "http": self.proxy} if self.proxy else None,
            )
            if resp1.status_code not in (200, 201, 204):
                self.log(f"[Payment] 创建客户失败: HTTP {resp1.status_code}")
                return ""
            self.log(f"[Payment] Step1: OK")
        except Exception as e:
            self.log(f"[Payment] 创建客户异常: {e}")
            return ""

        # Step 2: 创建订阅获取支付链接
        self.log("[Payment] Step2: 获取支付链接...")
        try:
            resp2 = curl_requests.post(
                "https://grok.com/rest/subscriptions/subscribe/new",
                headers={**base_headers, "x-xai-request-id": str(uuid.uuid4())},
                cookies=cookies,
                json={
                    "stripeHosted": {
                        "successUrl": "https://grok.com/?checkout=success&tier=SUBSCRIPTION_TIER_GROK_PRO&interval=monthly#subscribe"
                    },
                    "priceId": "price_1R6nQ9HJohyvID2ck7FNrVdw",
                    "campaignId": "subcamp_HeAxW",
                    "ignoreExistingActiveSubscriptions": False,
                    "subscriptionType": "MONTHLY",
                    "requestedTier": "REQUESTED_TIER_GROK_PRO",
                },
                impersonate=self._browser,
                timeout=20,
                proxies={"https": self.proxy, "http": self.proxy} if self.proxy else None,
            )
            if resp2.status_code != 200:
                self.log(f"[Payment] 创建订阅失败: HTTP {resp2.status_code}")
                return ""
            data = resp2.json() if resp2.content else {}
            payment_link = str(data.get("url") or data.get("checkoutUrl") or "").strip()
            if payment_link:
                self.log(f"[Payment] ✅ 支付链接已获取")
                return payment_link
            else:
                self.log(f"[Payment] 未返回支付链接: {json.dumps(data, ensure_ascii=False)[:200]}")
                return ""
        except Exception as e:
            self.log(f"[Payment] 获取支付链接异常: {e}")
            return ""

    # ── 公开接口 ──

    def register(
        self,
        email: str,
        password: Optional[str] = None,
        otp_callback: Optional[Callable[[], str]] = None,
    ) -> dict:
        """
        完整注册流程。

        Args:
            email: 注册邮箱
            password: 密码（留空随机生成）
            otp_callback: 获取验证码的回调函数

        Returns:
            dict: {"email", "password", "given_name", "family_name", "sso", "sso_rw"}
        """
        if not password:
            password = _rand_password()
        given_name, family_name = _rand_name()

        # Step 1: 访问注册页
        action_id = self._visit_signup()
        _delay(0.3, 0.8)

        # Step 2: 发送验证码
        self._send_email_code(email)

        # Step 3: 等待验证码
        if not otp_callback:
            raise RuntimeError("需要 otp_callback 获取验证码")
        code = otp_callback() or ""
        if not code:
            raise RuntimeError("未获取到验证码")
        code = code.strip().replace("-", "").upper()

        # Step 4: 验证邮箱
        _delay(0.2, 0.5)
        self._verify_email_code(email, code)

        # Step 5: 解决 Turnstile
        _delay(0.2, 0.5)
        turnstile_token = self._solve_turnstile()

        # Step 6: 提交注册
        _delay(0.3, 0.8)
        result = self._submit_registration(
            email, password, given_name, family_name,
            turnstile_token, action_id, code,
        )

        # Step 7: SSO fallback
        if result.get("status_code") == 200 and not result.get("sso"):
            try:
                _delay(0.5, 1.0)
                fallback = self._create_session_fallback(email, password)
                result["sso"] = fallback.get("sso", "")
                result["sso_rw"] = fallback.get("sso-rw", "")
            except Exception as e:
                self.log(f"[SSO] fallback 失败: {e}")

        if not result.get("sso"):
            raise RuntimeError("注册成功但未获取到 SSO cookie")

        self.log(f"✅ Grok 注册成功: {email}")

        # Step 8: 获取 Stripe 支付链接
        payment_link = ""
        try:
            _delay(0.3, 0.8)
            payment_link = self.get_payment_link(email, result["sso"], result.get("sso_rw", ""))
        except Exception as e:
            self.log(f"[Payment] 获取支付链接失败: {e}")

        return {
            "email": email,
            "password": password,
            "given_name": given_name,
            "family_name": family_name,
            "sso": result["sso"],
            "sso_rw": result.get("sso_rw", ""),
            "cashier_url": payment_link,
        }
