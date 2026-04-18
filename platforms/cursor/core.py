"""
Cursor 注册引擎（Playwright 浏览器自动化）

Cursor 已迁移到 WorkOS 认证系统，需要浏览器环境：
- WorkOS 的 `signals` 设备指纹需要 JS 运行
- Cloudflare Bot Protection 拦截纯协议请求
- 注册方式已改为 passwordless (magic auth/OTP)

流程：
1. 打开 authenticator.cursor.sh/sign-up
2. 填写 first_name + last_name + email → 提交
3. 等待邮箱 OTP
4. 填写 OTP → 提交
5. 等待回调 → 获取 WorkosCursorSessionToken
"""

import random
import re
import string
import time
import urllib.parse
from typing import Callable, Optional

from core.browser_runtime import ensure_browser_display_available, resolve_browser_headless

AUTH = "https://authenticator.cursor.sh"
CURSOR = "https://cursor.com"

FIRST_NAMES = ["James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia", "Mason", "Isabella"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Moore", "Taylor"]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def _rand_password(n=16):
    chars = string.ascii_letters + string.digits + "!@#$"
    return "".join(random.choices(chars, k=n))


class CursorRegister:
    def __init__(self, proxy: str = None, log_fn: Callable = print):
        self.proxy = proxy
        self.log = log_fn

    def _launch_browser(self):
        from patchright.sync_api import sync_playwright

        pw = sync_playwright().start()
        headless, reason = resolve_browser_headless(True, default_headless=True)
        ensure_browser_display_available(headless)
        self.log(f"浏览器模式: {'headless' if headless else 'headed'} ({reason})")

        launch_kwargs = {"headless": headless}
        if self.proxy:
            launch_kwargs["proxy"] = {"server": self.proxy}
        try:
            browser = pw.chromium.launch(**launch_kwargs)
        except Exception:
            browser = pw.chromium.launch(headless=headless)
        return pw, browser

    def register(
        self,
        email: str,
        password: str = None,
        otp_callback: Optional[Callable] = None,
        yescaptcha_key: str = "",
    ) -> dict:
        if not password:
            password = _rand_password()
        first_name = random.choice(FIRST_NAMES)
        last_name = random.choice(LAST_NAMES)

        pw = None
        browser = None
        context = None
        try:
            pw, browser = self._launch_browser()
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=UA,
            )
            page = context.new_page()

            # ── Step 1: 打开注册页 ──
            self.log("Step1: 打开注册页...")
            page.goto(f"{AUTH}/sign-up", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # 等待表单加载
            page.wait_for_selector('input[name="email"]', timeout=15000)
            self.log(f"  页面加载完成: {page.url[:80]}")

            # ── Step 2: 填写表单 ──
            self.log(f"Step2: 填写注册信息 ({first_name} {last_name}, {email})...")

            # 填写 first_name / last_name (如果存在)
            fn_input = page.locator('input[name="first_name"]')
            if fn_input.count() > 0:
                fn_input.fill(first_name)
                page.wait_for_timeout(300)

            ln_input = page.locator('input[name="last_name"]')
            if ln_input.count() > 0:
                ln_input.fill(last_name)
                page.wait_for_timeout(300)

            # 填写 email
            page.locator('input[name="email"]').fill(email)
            page.wait_for_timeout(500)

            # ── Step 3: 提交表单 ──
            self.log("Step3: 提交注册表单...")

            # 等待 Turnstile 加载并通过（如果有）
            self.log("  等待 Turnstile...")
            for _ in range(10):
                cf_frame = None
                for frame in page.frames:
                    if "challenges.cloudflare.com" in frame.url:
                        cf_frame = frame
                        break
                if cf_frame:
                    try:
                        cf_frame.locator("body").click(position={"x": 24, "y": 24}, timeout=2000)
                    except Exception:
                        pass
                # 检查是否有 turnstile token
                has_token = page.evaluate('''() => {
                    const el = document.querySelector('input[name="cf-turnstile-response"]');
                    return el && el.value && el.value.length > 10;
                }''')
                if has_token:
                    self.log("  Turnstile 已通过")
                    break
                page.wait_for_timeout(1000)

            # 点击 Continue 按钮
            page.wait_for_timeout(500)

            # 截图调试
            try:
                page.screenshot(path="cursor_before_submit.png")
                self.log("  截图: cursor_before_submit.png")
            except: pass

            submit_btn = page.get_by_role("button", name="Continue").first
            if submit_btn.count() > 0:
                submit_btn.click()
                self.log("  点击 Continue")
            else:
                submit_btn = page.locator('button[type="submit"]').first
                if submit_btn.count() > 0:
                    submit_btn.click()
                    self.log("  点击 Submit")
                else:
                    page.locator('input[name="email"]').press("Enter")
                    self.log("  按 Enter")

            page.wait_for_timeout(3000)

            # ── Step 3.5: 处理 Turnstile 人机验证 ──
            body_text = page.locator("body").inner_text()
            if "you are human" in body_text.lower() or "verify" in body_text.lower():
                self.log("  检测到 Turnstile 人机验证，调用 YesCaptcha...")

                # 提取 sitekey
                sitekey = page.evaluate('''() => {
                    for (const iframe of document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]')) {
                        try { const k = new URL(iframe.src).searchParams.get('k'); if (k) return k; } catch {}
                    }
                    const el = document.querySelector('[data-sitekey]');
                    return el ? el.getAttribute('data-sitekey') : '';
                }''') or "0x4AAAAAAAMNIvC45A4Wjjln"

                if yescaptcha_key:
                    from core.base_captcha import YesCaptcha
                    self.log(f"  YesCaptcha 解码 Turnstile (sitekey={sitekey[:16]}...)...")
                    try:
                        token = YesCaptcha(yescaptcha_key).solve_turnstile(page.url, sitekey)
                        if token:
                            # 注入 token
                            injected = page.evaluate('''(token) => {
                                let inputs = document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
                                if (inputs.length === 0) {
                                    const el = document.createElement('input');
                                    el.type = 'hidden'; el.name = 'cf-turnstile-response';
                                    document.body.appendChild(el);
                                    inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
                                }
                                for (const el of inputs) {
                                    el.value = token;
                                    el.dispatchEvent(new Event('input', {bubbles: true}));
                                    el.dispatchEvent(new Event('change', {bubbles: true}));
                                }
                                // 也尝试 callback
                                if (window.turnstileCallback) window.turnstileCallback(token);
                                if (window.__cfTurnstileCallback) window.__cfTurnstileCallback(token);
                                return true;
                            }''', token)
                            self.log(f"  Token 已注入: {token[:40]}...")
                            page.wait_for_timeout(2000)

                            # 尝试自动继续（有些 Turnstile 注入后自动提交）
                            new_body = page.locator("body").inner_text()
                            if "you are human" in new_body.lower():
                                # 尝试直接提交表单
                                page.evaluate('''() => {
                                    const forms = document.querySelectorAll('form');
                                    for (const f of forms) { f.submit(); }
                                }''')
                                page.wait_for_timeout(3000)
                    except Exception as e:
                        self.log(f"  YesCaptcha 失败: {e}")
                else:
                    self.log("  ⚠ 未配置 YesCaptcha key，无法解 Turnstile")
                    # 等待手动通过（headed 模式下）
                    for _ in range(15):
                        page.wait_for_timeout(2000)
                        body_text = page.locator("body").inner_text()
                        if "you are human" not in body_text.lower():
                            self.log("  Turnstile 手动通过 ✅")
                            break

            page.wait_for_timeout(2000)

            # 截图 + 检查页面状态
            try:
                page.screenshot(path="cursor_after_turnstile.png")
            except: pass

            current_url = page.url
            body_text = page.locator("body").inner_text()[:200]
            self.log(f"  当前 URL: {current_url[:80]}")
            self.log(f"  当前内容: {body_text[:100]}")

            # 等待 OTP 输入框出现
            page.wait_for_timeout(2000)

            # 检查是否到了 OTP 页面
            otp_ready = False
            for attempt in range(20):
                # 检查各种 OTP 输入框
                if page.locator('input[name="code"]').count() > 0:
                    otp_ready = True
                    break
                if page.locator('input[type="text"][maxlength="6"]').count() > 0:
                    otp_ready = True
                    break
                # WorkOS 的 OTP 输入可能是多个单字符输入框
                if page.locator('input[autocomplete="one-time-code"]').count() > 0:
                    otp_ready = True
                    break
                # 检查页面文字
                body_text = page.locator("body").inner_text()
                if any(k in body_text.lower() for k in ["verification code", "验证码", "enter the code", "check your email"]):
                    otp_ready = True
                    break
                if "verification" in page.url.lower() or "otp" in page.url.lower() or "verify" in page.url.lower():
                    otp_ready = True
                    break
                # 检查密码页（旧流程）
                if page.locator('input[name="password"]').count() > 0:
                    self.log("  检测到密码页面...")
                    page.locator('input[name="password"]').fill(password)
                    page.wait_for_timeout(300)
                    page.locator('button[type="submit"]').click()
                    page.wait_for_timeout(2000)
                    continue
                if attempt % 5 == 4:
                    self.log(f"  等待 OTP 页面... ({attempt+1}/20)")
                page.wait_for_timeout(1000)

            if not otp_ready:
                body = page.locator("body").inner_text()[:300]
                raise RuntimeError(f"未检测到 OTP 输入页: {body}")

            self.log("  OTP 输入页面就绪")

            # ── Step 4: 等待并提交 OTP ──
            self.log("Step4: 等待 OTP...")
            if not otp_callback:
                raise RuntimeError("需要 otp_callback")
            code = otp_callback() or ""
            if not code:
                raise RuntimeError("未获取到验证码")
            self.log(f"  验证码: {code}")

            # 填写 OTP
            otp_input = page.locator('input[name="code"]')
            if otp_input.count() == 0:
                otp_input = page.locator('input[type="text"][maxlength="6"]')
            if otp_input.count() == 0:
                otp_input = page.locator('input[type="text"]').first
            otp_input.fill(code)
            page.wait_for_timeout(500)

            # 提交 OTP
            submit_otp = page.locator('button[type="submit"]')
            if submit_otp.count() > 0:
                submit_otp.click()
            else:
                otp_input.press("Enter")

            # ── Step 5: 等待回调 → 获取 token ──
            self.log("Step5: 等待注册完成...")
            page.wait_for_timeout(3000)

            # 等待跳转到 cursor.com 或获取 token
            for _ in range(15):
                if "cursor.com" in page.url:
                    break
                if "dashboard" in page.url:
                    break
                page.wait_for_timeout(1000)

            # 从 cookies 提取 token
            cookies = context.cookies()
            token = ""
            for c in cookies:
                if c.get("name") == "WorkosCursorSessionToken":
                    token = urllib.parse.unquote(c.get("value", ""))
                    break

            if not token:
                # 等更久
                page.wait_for_timeout(5000)
                cookies = context.cookies()
                for c in cookies:
                    if c.get("name") == "WorkosCursorSessionToken":
                        token = urllib.parse.unquote(c.get("value", ""))
                        break

            self.log(f"✅ Cursor 注册完成: {email}")
            if token:
                self.log(f"  Token: {token[:40]}...")
            return {"email": email, "password": password, "token": token}

        finally:
            try:
                if context: context.close()
            except: pass
            try:
                if browser: browser.close()
            except: pass
            try:
                if pw: pw.stop()
            except: pass
