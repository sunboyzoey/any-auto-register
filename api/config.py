from fastapi import APIRouter
from pydantic import BaseModel
from core.config_store import config_store

router = APIRouter(prefix="/config", tags=["config"])

CONFIG_KEYS = [
    "laoudo_auth",
    "laoudo_email",
    "laoudo_account_id",
    "yescaptcha_key",
    "twocaptcha_key",
    "default_executor",
    "default_captcha_solver",
    "default_proxy",
    "duckmail_api_url",
    "duckmail_provider_url",
    "duckmail_bearer",
    "duckmail_domain",
    "duckmail_api_key",
    "freemail_api_url",
    "freemail_admin_token",
    "freemail_username",
    "freemail_password",
    "freemail_domain",
    "moemail_api_url",
    "moemail_api_key",
    "skymail_api_base",
    "skymail_token",
    "skymail_domain",
    "cloudmail_api_base",
    "cloudmail_admin_email",
    "cloudmail_admin_password",
    "cloudmail_domain",
    "cloudmail_subdomain",
    "cloudmail_timeout",
    "mail_provider",
    "mailbox_otp_timeout_seconds",
    "maliapi_base_url",
    "maliapi_api_key",
    "maliapi_domain",
    "maliapi_auto_domain_strategy",
    "applemail_base_url",
    "applemail_pool_dir",
    "applemail_pool_file",
    "applemail_mailboxes",
    "gptmail_base_url",
    "gptmail_api_key",
    "gptmail_domain",
    "opentrashmail_api_url",
    "opentrashmail_domain",
    "opentrashmail_password",
    "cfworker_api_url",
    "cfworker_admin_token",
    "cfworker_custom_auth",
    "cfworker_domain",
    "cfworker_domains",
    "cfworker_enabled_domains",
    "cfworker_subdomain",
    "cfworker_random_subdomain",
    "cfworker_random_name_subdomain",
    "cfworker_fingerprint",
    "smstome_cookie",
    "smstome_country_slugs",
    "smstome_phone_attempts",
    "smstome_otp_timeout_seconds",
    "smstome_poll_interval_seconds",
    "smstome_sync_max_pages_per_country",
    "luckmail_base_url",
    "luckmail_api_key",
    "luckmail_email_type",
    "luckmail_domain",
    "cpa_api_url",
    "cpa_api_key",
    "cpa_cleanup_enabled",
    "cpa_cleanup_interval_minutes",
    "cpa_cleanup_threshold",
    "cpa_cleanup_concurrency",
    "cpa_cleanup_register_delay_seconds",
    "sub2api_api_url",
    "sub2api_api_key",
    "sub2api_group_ids",
    "team_manager_url",
    "team_manager_key",
    "codex_proxy_url",
    "codex_proxy_key",
    "codex_proxy_upload_type",
    "cliproxyapi_base_url",
    "grok2api_url",
    "grok2api_app_key",
    "grok2api_pool",
    "grok2api_quota",
    "kiro_manager_path",
    "kiro_manager_exe",
]


class ConfigUpdate(BaseModel):
    data: dict


class AppleMailImportRequest(BaseModel):
    content: str
    filename: str = ""
    pool_dir: str = ""
    bind_to_config: bool = True


@router.get("")
def get_config():
    all_cfg = config_store.get_all()
    if not all_cfg.get("mail_provider"):
        all_cfg["mail_provider"] = "luckmail"
    if not all_cfg.get("applemail_base_url"):
        all_cfg["applemail_base_url"] = "https://www.appleemail.top"
    if not all_cfg.get("applemail_pool_dir"):
        all_cfg["applemail_pool_dir"] = "mail"
    if not all_cfg.get("applemail_mailboxes"):
        all_cfg["applemail_mailboxes"] = "INBOX,Junk"
    if not all_cfg.get("gptmail_base_url"):
        all_cfg["gptmail_base_url"] = "https://mail.chatgpt.org.uk"
    if not all_cfg.get("luckmail_base_url"):
        all_cfg["luckmail_base_url"] = "https://mails.luckyous.com/"
    # 只返回已知 key，未设置的返回空字符串
    return {k: all_cfg.get(k, "") for k in CONFIG_KEYS}


@router.put("")
def update_config(body: ConfigUpdate):
    # 只允许更新已知 key
    safe = {k: v for k, v in body.data.items() if k in CONFIG_KEYS}
    config_store.set_many(safe)
    return {"ok": True, "updated": list(safe.keys())}


class CFWorkerTestRequest(BaseModel):
    api_url: str = ""
    admin_token: str = ""
    custom_auth: str = ""


@router.post("/cfworker/test")
def test_cfworker_connection(body: CFWorkerTestRequest):
    """测试 CF Worker 连接，并自动获取全部可用域名"""
    import requests as _requests
    import secrets

    api_url = str(body.api_url or config_store.get("cfworker_api_url", "")).strip().rstrip("/")
    admin_token = str(body.admin_token or config_store.get("cfworker_admin_token", "")).strip()
    custom_auth = str(body.custom_auth or config_store.get("cfworker_custom_auth", "")).strip()

    if not api_url:
        return {"ok": False, "error": "API URL 未配置"}

    # ── Step 1: 调用 /open_api/settings 获取全部域名（公开接口，无需认证）──
    all_domains: list[str] = []
    worker_version = ""
    try:
        resp_settings = _requests.get(f"{api_url}/open_api/settings", timeout=15)
        if resp_settings.status_code == 200:
            settings_data = resp_settings.json() if resp_settings.content else {}
            raw_domains = settings_data.get("domains") or settings_data.get("defaultDomains") or []
            if isinstance(raw_domains, list):
                all_domains = [str(d).strip() for d in raw_domains if str(d).strip()]
            worker_version = str(settings_data.get("version", "")).strip()
    except Exception:
        pass

    # ── Step 2: 验证 Admin 认证（创建测试邮箱）──
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
    }
    if admin_token:
        headers["x-admin-auth"] = admin_token
    if custom_auth:
        headers["x-custom-auth"] = custom_auth

    auth_ok = False
    warning = ""
    detected_domain = ""

    try:
        test_name = f"_conntest_{secrets.token_hex(4)}"
        resp = _requests.post(
            f"{api_url}/admin/new_address",
            headers=headers,
            json={"enablePrefix": True, "name": test_name},
            timeout=15,
        )

        if resp.status_code == 200:
            auth_ok = True
            data = resp.json() if resp.content else {}
            test_email = str(data.get("email") or data.get("address") or "")
            if "@" in test_email:
                detected_domain = test_email.split("@", 1)[1]
        elif resp.status_code in (401, 403):
            # 自动交换 admin_token ↔ custom_auth 重试
            retry_headers = dict(headers)
            if admin_token and custom_auth:
                retry_headers["x-admin-auth"] = custom_auth
                retry_headers["x-custom-auth"] = admin_token
            elif admin_token:
                retry_headers["x-custom-auth"] = admin_token
            elif custom_auth:
                retry_headers["x-admin-auth"] = custom_auth

            resp2 = _requests.post(
                f"{api_url}/admin/new_address",
                headers=retry_headers,
                json={"enablePrefix": True, "name": test_name},
                timeout=15,
            )
            if resp2.status_code == 200:
                auth_ok = True
                data2 = resp2.json() if resp2.content else {}
                test_email = str(data2.get("email") or data2.get("address") or "")
                if "@" in test_email:
                    detected_domain = test_email.split("@", 1)[1]
                warning = "Admin Token 和站点密码可能填反了，建议检查"
            else:
                # 即使 admin 认证失败，如果域名已经通过 open_api 获取到，也算部分成功
                if all_domains:
                    return {
                        "ok": True,
                        "message": f"已获取 {len(all_domains)} 个域名（Admin 认证未通过，注册时可能失败）",
                        "detected_domains": all_domains,
                        "warning": "Admin Token 或站点密码不正确，域名已通过公开接口获取",
                        "version": worker_version,
                    }
                return {
                    "ok": False,
                    "error": "认证失败，请检查 Admin Token 和站点密码",
                }
        else:
            return {"ok": False, "error": f"请求失败 (HTTP {resp.status_code})"}

    except _requests.exceptions.ConnectTimeout:
        return {"ok": False, "error": "连接超时，请检查 API URL"}
    except _requests.exceptions.SSLError as e:
        return {"ok": False, "error": f"SSL 错误: {str(e)[:100]}"}
    except _requests.exceptions.ConnectionError as e:
        msg = str(e)
        if "NameResolutionError" in msg or "getaddrinfo" in msg:
            return {"ok": False, "error": "域名解析失败，请检查 API URL"}
        return {"ok": False, "error": f"无法连接: {msg[:150]}"}
    except Exception as e:
        return {"ok": False, "error": f"请求异常: {str(e)[:150]}"}

    # ── 合并结果 ──
    # 如果 open_api 没有获取到域名，至少用 detected_domain
    if not all_domains and detected_domain:
        all_domains = [detected_domain]
    elif detected_domain and detected_domain not in all_domains:
        all_domains.insert(0, detected_domain)

    result = {
        "ok": True,
        "message": f"连接成功，共 {len(all_domains)} 个可用域名",
        "detected_domains": all_domains,
    }
    if worker_version:
        result["version"] = worker_version
    if warning:
        result["warning"] = warning
    return result


@router.post("/applemail/import")
def import_applemail_pool(body: AppleMailImportRequest):
    from core.applemail_pool import load_applemail_pool_snapshot, save_applemail_pool_json

    pool_dir = str(body.pool_dir or config_store.get("applemail_pool_dir", "mail")).strip() or "mail"
    result = save_applemail_pool_json(
        body.content,
        pool_dir=pool_dir,
        filename=body.filename,
    )

    if body.bind_to_config:
        config_store.set_many(
            {
                "applemail_pool_dir": pool_dir,
                "applemail_pool_file": result["filename"],
            }
        )

    snapshot = load_applemail_pool_snapshot(
        pool_file=result["filename"],
        pool_dir=pool_dir,
    )

    return {
        **result,
        "pool_dir": pool_dir,
        "bound_to_config": body.bind_to_config,
        "items": snapshot["items"],
        "truncated": snapshot["truncated"],
    }


@router.get("/cfworker/domains")
def get_cfworker_domains():
    """返回 CF Worker 的可用域名列表（优先从 Worker 实时获取，兜底读全局配置）"""
    import json
    import requests as _requests

    api_url = config_store.get("cfworker_api_url", "").strip().rstrip("/")
    configured = bool(api_url)

    # 优先从 Worker 实时获取
    live_domains: list[str] = []
    if api_url:
        try:
            resp = _requests.get(f"{api_url}/open_api/settings", timeout=10)
            if resp.status_code == 200:
                data = resp.json() if resp.content else {}
                raw = data.get("domains") or data.get("defaultDomains") or []
                if isinstance(raw, list):
                    live_domains = [str(d).strip() for d in raw if str(d).strip()]
        except Exception:
            pass

    if live_domains:
        return {"configured": configured, "api_url": api_url, "domains": live_domains, "source": "live"}

    # 兜底：从全局配置读取
    raw = config_store.get("cfworker_enabled_domains", "")
    domains = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                domains = [str(d).strip() for d in parsed if str(d).strip()]
        except (json.JSONDecodeError, TypeError):
            domains = [d.strip() for d in raw.split(",") if d.strip()]

    return {"configured": configured, "api_url": api_url, "domains": domains, "source": "config"}


@router.get("/applemail/pool")
def get_applemail_pool_snapshot(
    pool_dir: str = "",
    pool_file: str = "",
):
    from core.applemail_pool import load_applemail_pool_snapshot

    resolved_pool_dir = str(pool_dir or config_store.get("applemail_pool_dir", "mail")).strip() or "mail"
    resolved_pool_file = str(pool_file or config_store.get("applemail_pool_file", "")).strip()
    try:
        snapshot = load_applemail_pool_snapshot(
            pool_file=resolved_pool_file,
            pool_dir=resolved_pool_dir,
        )
    except Exception:
        snapshot = {
            "filename": resolved_pool_file,
            "path": "",
            "count": 0,
            "items": [],
            "truncated": False,
        }
    return {
        **snapshot,
        "pool_dir": resolved_pool_dir,
    }
