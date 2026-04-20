#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cookie_to_oauth.py
==================
从 Cookie 文件提取 session-token, 调用 /api/auth/session 获取 access_token,
生成与 generate_oauth_json_passwordless.py 兼容的 OAuth JSON 文件。

用法:
    python cookie_to_oauth.py --cookie <cookie文件或目录> --output <输出目录>
    python cookie_to_oauth.py --cookie cookies/ --output oauth_out/ --proxy http://127.0.0.1:7890

Cookie 文件支持格式:
  1. gpt2api cookie_json: {"email":"...", "cookies":{"__Secure-next-auth.session-token":"..."}}
  2. 浏览器导出 JSON: [{"name":"__Secure-next-auth.session-token", "value":"..."}]
  3. 纯 session-token 字符串 (单行)
  4. Netscape cookie.txt
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as curl_requests
    HAS_CURL_CFFI = False

import requests


# ================================================================
#  JWT 工具
# ================================================================

def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = str(token or "").strip().split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        parsed = json.loads(decoded.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_account_id(tokens: dict) -> str:
    direct = str(tokens.get("account_id") or "").strip()
    if direct:
        return direct
    for key in ("access_token", "id_token"):
        p = _decode_jwt_payload(tokens.get(key, ""))
        for field in ("chatgpt_account_id", "account_id"):
            v = str((p.get("https://api.openai.com/auth") or {}).get(field) or p.get(field) or "").strip()
            if v:
                return v
    return ""


def _extract_expired_str(access_token: str) -> str:
    p = _decode_jwt_payload(access_token)
    exp = p.get("exp")
    if isinstance(exp, int) and exp > 0:
        dt = datetime.fromtimestamp(exp, tz=timezone(timedelta(hours=8)))
        return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return ""


def _build_compat_id_token(
    email: str,
    access_token: str,
    account_id_override: str = "",
    plan_type_override: str = "",
) -> str:
    """构造 alg=none 兼容 id_token（Session Fast Path 无真实 id_token 时使用）"""
    p = _decode_jwt_payload(access_token)
    auth_info = p.get("https://api.openai.com/auth") or {}
    resolved_account_id = str(account_id_override or auth_info.get("chatgpt_account_id") or "").strip()
    resolved_plan_type = _normalize_plan_type(
        plan_type_override or auth_info.get("chatgpt_plan_type") or p.get("plan_type") or ""
    )
    id_payload = {
        "email": email,
        "exp": p.get("exp", 0),
        "iat": p.get("iat", 0),
        "sub": p.get("sub", ""),
        "https://api.openai.com/auth": {
            "chatgpt_account_id": resolved_account_id,
            "chatgpt_user_id": auth_info.get("chatgpt_user_id", ""),
            "chatgpt_plan_type": resolved_plan_type,
        },
    }
    header_b64 = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(id_payload, ensure_ascii=False).encode()
    ).rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}."

# ================================================================
#  Cookie 文件解析
# ================================================================

def _parse_cookie_file(path: str) -> dict:
    """
    解析 cookie 文件，返回 {name: value} dict。
    支持四种格式：gpt2api cookie_json / 浏览器导出JSON / Netscape txt / 纯token字符串。
    """
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"文件为空: {path}")

    # 格式1: gpt2api cookie_json {"email":..., "cookies":{...}}
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "cookies" in obj and isinstance(obj["cookies"], dict):
            return obj["cookies"], str(obj.get("email") or "").strip()
    except Exception:
        pass

    # 格式2: 浏览器导出 [{"name":..., "value":...}]
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            result = {}
            for item in obj:
                if isinstance(item, dict) and "name" in item:
                    result[item["name"]] = item.get("value", "")
            if result:
                return result, ""
    except Exception:
        pass

    # 格式3: Netscape cookie.txt (domain \t flag \t path \t secure \t exp \t name \t value)
    if "\t" in text:
        result = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                result[parts[5]] = parts[6]
        if result:
            return result, ""

    # 格式4: 纯 session-token 字符串（单行）
    if len(text) > 20 and "\n" not in text:
        return {"__Secure-next-auth.session-token": text}, ""

    raise ValueError(f"无法识别 cookie 文件格式: {path}")


def _extract_session_token(cookie_dict: dict) -> str:
    """从 cookie dict 提取 session-token，自动合并 .0/.1/.2/.3 分片"""
    st = cookie_dict.get("__Secure-next-auth.session-token", "")
    if st:
        return st
    parts = []
    for i in range(4):
        val = cookie_dict.get(f"__Secure-next-auth.session-token.{i}", "")
        if val:
            parts.append(val)
        else:
            break
    return "".join(parts)

# ================================================================
#  核心：session-token → access_token
# ================================================================

CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"


def _fetch_access_token(session_token: str, proxy: str = "", retries: int = 2) -> dict:
    """
    用 session-token 调用 GET /api/auth/session 取 access_token。
    返回完整 JSON 响应 dict，含 accessToken / user 等字段。
    """
    headers = {
        "accept": "application/json",
        "referer": "https://chatgpt.com/",
        "user-agent": CHROME_UA,
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None

    for attempt in range(retries + 1):
        try:
            if HAS_CURL_CFFI:
                s = curl_requests.Session(impersonate="chrome136", verify=False)
                if proxies:
                    s.proxies = proxies
                s.cookies.set("__Secure-next-auth.session-token", session_token, domain="chatgpt.com")
                resp = s.get("https://chatgpt.com/api/auth/session", headers=headers, timeout=30)
            else:
                s = requests.Session()
                if proxies:
                    s.proxies = proxies
                s.cookies.set("__Secure-next-auth.session-token", session_token, domain="chatgpt.com")
                resp = s.get("https://chatgpt.com/api/auth/session", headers=headers, timeout=30)

            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError("响应不是 JSON dict")
            return data
        except Exception as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"获取 access_token 失败 (尝试 {retries+1} 次): {e}") from e


def _normalize_plan_type(value) -> str:
    plan_type = str(value or "").strip().lower()
    aliases = {
        "workspace": "workspace",
        "team": "team",
        "business": "business",
        "enterprise": "enterprise",
        "free": "free",
        "plus": "plus",
        "pro": "pro",
    }
    return aliases.get(plan_type, plan_type)


def _is_workspace_plan_type(plan_type: str) -> bool:
    return _normalize_plan_type(plan_type) in {"team", "business", "enterprise", "workspace"}


def _fetch_accounts_check(access_token: str, account_id: str = "", proxy: str = "", retries: int = 1) -> dict:
    headers = {
        "accept": "*/*",
        "authorization": (
            access_token if str(access_token or "").strip().lower().startswith("bearer ")
            else f"Bearer {str(access_token or '').strip()}"
        ),
        "origin": "https://chatgpt.com",
        "referer": "https://chatgpt.com/",
        "user-agent": CHROME_UA,
    }
    clean_account_id = str(account_id or "").strip()
    if clean_account_id:
        headers["chatgpt-account-id"] = clean_account_id
    proxies = {"http": proxy, "https": proxy} if proxy else None

    for attempt in range(retries + 1):
        try:
            if HAS_CURL_CFFI:
                s = curl_requests.Session(impersonate="chrome136", verify=False)
                if proxies:
                    s.proxies = proxies
                resp = s.get(
                    "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                    headers=headers,
                    timeout=30,
                )
            else:
                s = requests.Session()
                if proxies:
                    s.proxies = proxies
                resp = s.get(
                    "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                    headers=headers,
                    timeout=30,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError("响应不是 JSON dict")
            return data
        except Exception as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"获取 accounts/check 失败 (尝试 {retries+1} 次): {e}") from e


def _iter_accounts_from_check(payload: dict) -> list:
    if not isinstance(payload, dict):
        return []
    accounts = payload.get("accounts")
    if not isinstance(accounts, (dict, list)):
        return []

    items = accounts.items() if isinstance(accounts, dict) else enumerate(accounts)
    normalized = []
    for key, raw in items:
        if not isinstance(raw, dict):
            continue
        account = raw.get("account") if isinstance(raw.get("account"), dict) else raw
        account_id = str(
            account.get("account_id")
            or account.get("id")
            or raw.get("account_id")
            or raw.get("id")
            or key
            or ""
        ).strip()
        if not account_id:
            continue
        plan_type = _normalize_plan_type(
            account.get("plan_type")
            or raw.get("plan_type")
            or account.get("planType")
            or raw.get("planType")
            or ""
        )
        workspace_name = str(
            account.get("name")
            or raw.get("name")
            or account.get("workspace_name")
            or raw.get("workspace_name")
            or ""
        ).strip()
        role = str(
            (raw.get("account_user") or {}).get("role")
            or raw.get("role")
            or ""
        ).strip().lower()
        normalized.append(
            {
                "account_id": account_id,
                "plan_type": plan_type,
                "workspace_name": workspace_name,
                "role": role,
            }
        )
    return normalized


def _pick_best_account_from_check(payload: dict, default_account_id: str = "") -> dict:
    clean_default = str(default_account_id or "").strip()
    candidates = _iter_accounts_from_check(payload)
    default_candidate = None
    workspace_candidate = None

    for item in candidates:
        if item["account_id"] == clean_default:
            default_candidate = dict(item)
        if item["account_id"] == "default":
            continue
        plan_type = _normalize_plan_type(item.get("plan_type"))
        looks_like_workspace = _is_workspace_plan_type(plan_type)
        if not looks_like_workspace and item.get("workspace_name") and plan_type not in {"", "free"}:
            looks_like_workspace = True
        if not looks_like_workspace:
            continue
        if workspace_candidate is None:
            workspace_candidate = dict(item)
            continue
        current_score = (
            100 if _is_workspace_plan_type(item.get("plan_type")) else 0,
            10 if item.get("workspace_name") else 0,
            5 if item.get("role") in {"account-owner", "owner", "admin"} else 0,
        )
        best_score = (
            100 if _is_workspace_plan_type(workspace_candidate.get("plan_type")) else 0,
            10 if workspace_candidate.get("workspace_name") else 0,
            5 if workspace_candidate.get("role") in {"account-owner", "owner", "admin"} else 0,
        )
        if current_score > best_score:
            workspace_candidate = dict(item)

    if workspace_candidate:
        return {
            "account_id": workspace_candidate.get("account_id", ""),
            "plan_type": _normalize_plan_type(workspace_candidate.get("plan_type")) or "workspace",
            "workspace_name": str(workspace_candidate.get("workspace_name") or "").strip(),
            "source": "workspace_check",
        }

    if default_candidate:
        return {
            "account_id": default_candidate.get("account_id", ""),
            "plan_type": _normalize_plan_type(default_candidate.get("plan_type")) or "free",
            "workspace_name": "",
            "source": "session",
        }

    if clean_default:
        return {
            "account_id": clean_default,
            "plan_type": "free",
            "workspace_name": "",
            "source": "session",
        }

    return {
        "account_id": "",
        "plan_type": "",
        "workspace_name": "",
        "source": "session",
    }


def _resolve_account_identity(access_token: str, session_account_id: str, session_plan_type: str, proxy: str = "") -> dict:
    resolved = {
        "account_id": str(session_account_id or "").strip(),
        "plan_type": _normalize_plan_type(session_plan_type) or "free",
        "workspace_name": "",
        "source": "session",
    }
    if not str(access_token or "").strip():
        return resolved
    try:
        payload = _fetch_accounts_check(access_token, account_id=session_account_id, proxy=proxy)
        picked = _pick_best_account_from_check(payload, default_account_id=session_account_id)
        if str(picked.get("account_id") or "").strip():
            resolved = {
                "account_id": str(picked.get("account_id") or "").strip(),
                "plan_type": _normalize_plan_type(picked.get("plan_type")) or resolved["plan_type"],
                "workspace_name": str(picked.get("workspace_name") or "").strip(),
                "source": str(picked.get("source") or "session"),
            }
        return resolved
    except Exception:
        resolved["source"] = "session_fallback"
        return resolved

# ================================================================
#  生成 OAuth JSON 文件
# ================================================================

def _sanitize_filename(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "account"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text or "account"


def process_cookie_file(cookie_path: str, output_dir: str, proxy: str = "",
                        email_hint: str = "") -> dict:
    """
    处理单个 cookie 文件，生成 OAuth JSON。
    返回结果 dict: {success, file_path, email, ...}
    """
    cookie_path = os.path.abspath(cookie_path)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # 1. 解析 cookie 文件
    cookie_dict, email_from_file = _parse_cookie_file(cookie_path)
    email = str(email_hint or email_from_file or "").strip()

    # 2. 提取 session-token
    session_token = _extract_session_token(cookie_dict)
    if not session_token:
        raise RuntimeError("cookie 文件中未找到 __Secure-next-auth.session-token")

    # 3. 调用 /api/auth/session
    print(f"  [*] 请求 /api/auth/session ...", file=sys.stderr)
    data = _fetch_access_token(session_token, proxy=proxy)

    access_token = str(data.get("accessToken") or "").strip()
    if not access_token:
        raise RuntimeError(f"响应中无 accessToken，完整响应: {json.dumps(data, ensure_ascii=False)[:300]}")

    # 4. 从 JWT 解析 email / account_id
    jwt_p = _decode_jwt_payload(access_token)
    auth_info = jwt_p.get("https://api.openai.com/auth") or {}
    if not email:
        user_node = data.get("user") or {}
        email = str(user_node.get("email") or jwt_p.get("email") or "").strip()
    session_account_id = str(
        auth_info.get("chatgpt_account_id")
        or data.get("account_id")
        or ""
    ).strip()
    session_plan_type = _normalize_plan_type(
        auth_info.get("chatgpt_plan_type")
        or data.get("plan_type")
        or jwt_p.get("plan_type")
        or ""
    )
    resolved_identity = _resolve_account_identity(
        access_token=access_token,
        session_account_id=session_account_id,
        session_plan_type=session_plan_type,
        proxy=proxy,
    )
    account_id = str(resolved_identity.get("account_id") or session_account_id or "").strip()
    resolved_plan_type = _normalize_plan_type(resolved_identity.get("plan_type")) or session_plan_type or "free"
    workspace_name = str(resolved_identity.get("workspace_name") or "").strip()
    resolved_account_source = str(resolved_identity.get("source") or "session").strip() or "session"
    user_id = str(
        auth_info.get("chatgpt_user_id")
        or jwt_p.get("sub")
        or ""
    ).strip()

    # 5. 构造兼容 id_token
    id_token = _build_compat_id_token(
        email,
        access_token,
        account_id_override=account_id,
        plan_type_override=resolved_plan_type,
    )

    # 6. 时间字段
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_cst = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
    generated_at = now_utc.isoformat().replace("+00:00", "Z")
    last_refresh = now_cst.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    expired = _extract_expired_str(access_token) or last_refresh

    # 7. 确定输出文件名
    name_base = _sanitize_filename(email) if email else _sanitize_filename(os.path.basename(cookie_path))
    file_path = os.path.join(output_dir, f"{name_base}.json")

    # 8. 组装 payload（与 generate_oauth_json_passwordless.py 格式兼容）
    payload = {
        "type": "codex",
        "email": email,
        "expired": expired,
        "id_token": id_token,
        "account_id": account_id,
        "access_token": access_token,
        "last_refresh": last_refresh,
        "refresh_token": session_token,   # session_token 作为刷新凭证
        "session_token": session_token,
        "chatgpt_account_id": account_id,
        "chatgpt_user_id": user_id,
        "resolved_plan_type": resolved_plan_type,
        "workspace_name": workspace_name,
        "resolved_account_source": resolved_account_source,
        "session_account_id": session_account_id,
        "generated_at": generated_at,
        "provider": "cookie",
        "oauth_flow": "session_fast_path",
        "oauth_token_response": {
            "access_token": access_token,
            "id_token": id_token,
            "refresh_token": session_token,
            "token_type": "Bearer",
        },
    }

    with open(file_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    return {
        "success": True,
        "email": email,
        "account_id": account_id,
        "file_path": file_path,
        "expired": expired,
        "resolved_plan_type": resolved_plan_type,
        "workspace_name": workspace_name,
        "resolved_account_source": resolved_account_source,
    }

# ================================================================
#  批量处理
# ================================================================

def process_path(input_path: str, output_dir: str, proxy: str = "",
                email_hint: str = "") -> list:
    """
    input_path 可以是单个 cookie 文件或目录。
    目录时递归处理所有 .json / .txt 文件。
    返回结果列表。
    """
    p = Path(input_path)
    if p.is_file():
        return [_run_one(str(p), output_dir, proxy, email_hint)]

    if p.is_dir():
        results = []
        files = sorted(p.rglob("*.json")) + sorted(p.rglob("*.txt"))
        if not files:
            print(f"[!] 目录中未找到 .json/.txt 文件: {input_path}", file=sys.stderr)
            return []
        for f in files:
            results.append(_run_one(str(f), output_dir, proxy, ""))
        return results

    raise FileNotFoundError(f"路径不存在: {input_path}")


def _run_one(cookie_path: str, output_dir: str, proxy: str, email_hint: str) -> dict:
    fname = os.path.basename(cookie_path)
    print(f"\n[>] {fname}", file=sys.stderr)
    try:
        result = process_cookie_file(cookie_path, output_dir, proxy=proxy, email_hint=email_hint)
        print(f"  [+] 成功: {result['email']}  →  {result['file_path']}", file=sys.stderr)
        print(f"      account_id: {result['account_id']}", file=sys.stderr)
        print(f"      expired:    {result['expired']}", file=sys.stderr)
        return result
    except Exception as e:
        print(f"  [-] 失败: {e}", file=sys.stderr)
        if os.environ.get("COOKIE_OAUTH_VERBOSE"):
            traceback.print_exc(file=sys.stderr)
        return {"success": False, "cookie_file": cookie_path, "error": str(e)}


# ================================================================
#  CLI 入口
# ================================================================

def main():
    parser = argparse.ArgumentParser(
        description="从 Cookie 文件生成 OAuth JSON (兼容 gpt2api/CPA 格式)"
    )
    parser.add_argument("--cookie", "-c", required=True,
                        help="Cookie 文件路径或目录")
    parser.add_argument("--output", "-o", default="oauth_out",
                        help="输出目录 (默认: ./oauth_out)")
    parser.add_argument("--proxy", "-p", default="",
                        help="HTTP 代理, 如 http://127.0.0.1:7890")
    parser.add_argument("--email", "-e", default="",
                        help="手动指定 email (单文件时可用)")
    parser.add_argument("--json-output", action="store_true",
                        help="将结果汇总以 JSON 格式输出到 stdout")
    args = parser.parse_args()

    proxy = str(args.proxy or os.environ.get("PROXY", "")).strip()
    results = process_path(args.cookie, args.output, proxy=proxy, email_hint=args.email)

    ok = [r for r in results if r.get("success")]
    fail = [r for r in results if not r.get("success")]

    print(f"\n{'='*50}", file=sys.stderr)
    print(f"  完成  成功: {len(ok)}  失败: {len(fail)}", file=sys.stderr)
    print(f"  输出目录: {os.path.abspath(args.output)}", file=sys.stderr)
    print(f"{'='*50}", file=sys.stderr)

    if args.json_output:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
