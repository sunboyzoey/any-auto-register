"""Outlook 邮箱账号池管理 API"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlmodel import Session, select, func, col

from core.db import engine, OutlookAccountModel

router = APIRouter(prefix="/outlook", tags=["outlook"])


def _utcnow():
    return datetime.now(timezone.utc)


# ── 请求 / 响应模型 ──────────────────────────────────────────

class OutlookBatchImportRequest(BaseModel):
    data: str
    enabled: bool = True


class OutlookAccountUpdateRequest(BaseModel):
    password: Optional[str] = None
    client_id: Optional[str] = None
    refresh_token: Optional[str] = None
    mail_access_type: Optional[str] = None
    gpt_register_status: Optional[str] = None
    grok_register_status: Optional[str] = None
    trae_register_status: Optional[str] = None
    kiro_register_status: Optional[str] = None
    obl_register_status: Optional[str] = None
    cursor_register_status: Optional[str] = None
    enabled: Optional[bool] = None


class OutlookBatchDeleteRequest(BaseModel):
    ids: List[int]


class OutlookBatchUpdateStatusRequest(BaseModel):
    ids: List[int]
    gpt_register_status: Optional[str] = None
    grok_register_status: Optional[str] = None
    enabled: Optional[bool] = None


# ── 列表 & 统计 ──────────────────────────────────────────────

@router.get("/accounts")
def list_outlook_accounts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    keyword: Optional[str] = Query(None),
    mail_access_type: Optional[str] = Query(None),
    gpt_register_status: Optional[str] = Query(None),
    grok_register_status: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
):
    """分页查询 Outlook 邮箱账号列表"""
    with Session(engine) as session:
        query = select(OutlookAccountModel)

        if keyword:
            query = query.where(col(OutlookAccountModel.email).contains(keyword))
        if mail_access_type is not None:
            query = query.where(OutlookAccountModel.mail_access_type == mail_access_type)
        if gpt_register_status is not None:
            query = query.where(OutlookAccountModel.gpt_register_status == gpt_register_status)
        if grok_register_status is not None:
            query = query.where(OutlookAccountModel.grok_register_status == grok_register_status)
        if enabled is not None:
            query = query.where(OutlookAccountModel.enabled == enabled)

        # total
        count_query = select(func.count()).select_from(query.subquery())
        total = session.exec(count_query).one()

        # items
        items_query = query.order_by(col(OutlookAccountModel.id).desc()).offset((page - 1) * page_size).limit(page_size)
        items = session.exec(items_query).all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [_serialize(acc) for acc in items],
        }


@router.get("/accounts/stats")
def outlook_stats():
    """Outlook 邮箱池统计"""
    with Session(engine) as session:
        total = session.exec(select(func.count()).select_from(OutlookAccountModel)).one()
        enabled_count = session.exec(
            select(func.count()).select_from(OutlookAccountModel).where(OutlookAccountModel.enabled == True)
        ).one()

        # 按取码类型统计
        graph_count = session.exec(
            select(func.count()).select_from(OutlookAccountModel).where(OutlookAccountModel.mail_access_type == "graph")
        ).one()
        imap_pop_count = session.exec(
            select(func.count()).select_from(OutlookAccountModel).where(OutlookAccountModel.mail_access_type == "imap_pop")
        ).one()
        unknown_count = session.exec(
            select(func.count()).select_from(OutlookAccountModel).where(
                OutlookAccountModel.mail_access_type.not_in(["graph", "imap_pop"])  # type: ignore
            )
        ).one()

        # 按注册状态统计
        gpt_registered = session.exec(
            select(func.count()).select_from(OutlookAccountModel).where(OutlookAccountModel.gpt_register_status == "已注册")
        ).one()
        gpt_unregistered = session.exec(
            select(func.count()).select_from(OutlookAccountModel).where(OutlookAccountModel.gpt_register_status == "未注册")
        ).one()
        grok_registered = session.exec(
            select(func.count()).select_from(OutlookAccountModel).where(OutlookAccountModel.grok_register_status == "已注册")
        ).one()
        grok_unregistered = session.exec(
            select(func.count()).select_from(OutlookAccountModel).where(OutlookAccountModel.grok_register_status == "未注册")
        ).one()

        return {
            "total": total,
            "enabled": enabled_count,
            "disabled": total - enabled_count,
            "mail_access_type": {
                "graph": graph_count,
                "imap_pop": imap_pop_count,
                "unknown": unknown_count,
            },
            "gpt": {
                "registered": gpt_registered,
                "unregistered": gpt_unregistered,
                "in_progress": total - gpt_registered - gpt_unregistered,
            },
            "grok": {
                "registered": grok_registered,
                "unregistered": grok_unregistered,
                "in_progress": total - grok_registered - grok_unregistered,
            },
        }


# ── 导出（必须在 /accounts/{account_id} 之前注册） ──────────

@router.get("/accounts/export")
def export_outlook_accounts(
    mail_access_type: Optional[str] = Query(None),
    gpt_register_status: Optional[str] = Query(None),
    grok_register_status: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
):
    """导出 Outlook 邮箱账号（----分隔文本）"""
    with Session(engine) as session:
        query = select(OutlookAccountModel)
        if mail_access_type is not None:
            query = query.where(OutlookAccountModel.mail_access_type == mail_access_type)
        if gpt_register_status is not None:
            query = query.where(OutlookAccountModel.gpt_register_status == gpt_register_status)
        if grok_register_status is not None:
            query = query.where(OutlookAccountModel.grok_register_status == grok_register_status)
        if enabled is not None:
            query = query.where(OutlookAccountModel.enabled == enabled)
        accounts = session.exec(query.order_by(col(OutlookAccountModel.id))).all()

    lines = []
    for acc in accounts:
        parts = [acc.email, acc.password]
        if acc.refresh_token or acc.client_id:
            parts.extend([acc.refresh_token, acc.client_id, acc.mail_access_type or ""])
        lines.append("----".join(parts))

    return {"total": len(lines), "data": "\n".join(lines)}


# ── 单条操作 ─────────────────────────────────────────────────

@router.get("/accounts/{account_id}")
def get_outlook_account(account_id: int):
    with Session(engine) as session:
        acc = session.get(OutlookAccountModel, account_id)
        if not acc:
            return {"detail": "账号不存在"}, 404
        return _serialize(acc)


@router.put("/accounts/{account_id}")
def update_outlook_account(account_id: int, body: OutlookAccountUpdateRequest):
    with Session(engine) as session:
        acc = session.get(OutlookAccountModel, account_id)
        if not acc:
            return {"detail": "账号不存在"}, 404
        for field_name in ("password", "client_id", "refresh_token", "mail_access_type",
                           "gpt_register_status", "grok_register_status",
                           "trae_register_status", "kiro_register_status", "obl_register_status",
                           "cursor_register_status", "enabled"):
            value = getattr(body, field_name, None)
            if value is not None:
                setattr(acc, field_name, value)
        acc.updated_at = _utcnow()
        session.add(acc)
        session.commit()
        session.refresh(acc)
        return _serialize(acc)


@router.delete("/accounts/{account_id}")
def delete_outlook_account(account_id: int):
    with Session(engine) as session:
        acc = session.get(OutlookAccountModel, account_id)
        if not acc:
            return {"detail": "账号不存在"}, 404
        session.delete(acc)
        session.commit()
        return {"ok": True}


# ── 批量操作 ─────────────────────────────────────────────────

@router.post("/accounts/batch-delete")
def batch_delete_outlook(body: OutlookBatchDeleteRequest):
    deleted = 0
    with Session(engine) as session:
        for aid in body.ids:
            acc = session.get(OutlookAccountModel, aid)
            if acc:
                session.delete(acc)
                deleted += 1
        session.commit()
    return {"deleted": deleted}


@router.post("/accounts/batch-update-status")
def batch_update_status(body: OutlookBatchUpdateStatusRequest):
    updated = 0
    with Session(engine) as session:
        for aid in body.ids:
            acc = session.get(OutlookAccountModel, aid)
            if not acc:
                continue
            if body.gpt_register_status is not None:
                acc.gpt_register_status = body.gpt_register_status
            if body.grok_register_status is not None:
                acc.grok_register_status = body.grok_register_status
            if body.enabled is not None:
                acc.enabled = body.enabled
            acc.updated_at = _utcnow()
            session.add(acc)
            updated += 1
        session.commit()
    return {"updated": updated}


@router.post("/accounts/delete-all")
def delete_all_outlook(
    mail_access_type: Optional[str] = Query(None),
    gpt_register_status: Optional[str] = Query(None),
    grok_register_status: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
):
    """按条件批量清空"""
    with Session(engine) as session:
        query = select(OutlookAccountModel)
        if mail_access_type is not None:
            query = query.where(OutlookAccountModel.mail_access_type == mail_access_type)
        if gpt_register_status is not None:
            query = query.where(OutlookAccountModel.gpt_register_status == gpt_register_status)
        if grok_register_status is not None:
            query = query.where(OutlookAccountModel.grok_register_status == grok_register_status)
        if enabled is not None:
            query = query.where(OutlookAccountModel.enabled == enabled)
        accounts = session.exec(query).all()
        deleted = len(accounts)
        for acc in accounts:
            session.delete(acc)
        session.commit()
    return {"deleted": deleted}


# ── 批量导入（含取码类型探测） ─────────────────────────────────

@router.post("/batch-import")
def batch_import_outlook(request: OutlookBatchImportRequest):
    """
    批量导入 Outlook 邮箱账户

    固定格式（每行一个，字段用 ---- 分隔）：
        邮箱----密码----刷新令牌----Client ID

    导入后自动探测取码类型（Graph / IMAP / POP），探测完成后返回结果。
    """
    from core.outlook_probe import classify_mail_access_type

    lines = (request.data or "").splitlines()
    success = 0
    failed = 0
    deleted_bad = 0
    graph_count = 0
    imap_pop_count = 0
    accounts: List[Dict[str, Any]] = []
    errors: List[str] = []

    for idx, raw_line in enumerate(lines, start=1):
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("----")]
        if len(parts) != 4:
            failed += 1
            errors.append(f"行 {idx}: 格式错误，应为 邮箱----密码----刷新令牌----Client ID")
            continue

        email, password, refresh_token, client_id = parts
        if not email or "@" not in email:
            failed += 1
            errors.append(f"行 {idx}: 无效的邮箱地址: {email}")
            continue

        # 探测取码类型
        mail_access_type = classify_mail_access_type(email, refresh_token, client_id)
        if mail_access_type is None:
            # 取码不可达，跳过并清理已有记录
            with Session(engine) as session:
                existing = session.exec(
                    select(OutlookAccountModel).where(OutlookAccountModel.email == email)
                ).first()
                if existing:
                    session.delete(existing)
                    session.commit()
            deleted_bad += 1
            errors.append(f"行 {idx}: {email} 取码类型探测失败（Graph/IMAP/POP 均不可达），已跳过")
            continue

        if mail_access_type == "graph":
            graph_count += 1
        elif mail_access_type == "imap_pop":
            imap_pop_count += 1

        # 入库
        try:
            with Session(engine) as session:
                existing = session.exec(
                    select(OutlookAccountModel).where(OutlookAccountModel.email == email)
                ).first()

                if existing:
                    existing.password = password
                    existing.refresh_token = refresh_token
                    existing.client_id = client_id
                    existing.mail_access_type = mail_access_type
                    existing.enabled = bool(request.enabled)
                    existing.updated_at = _utcnow()
                    session.add(existing)
                    session.commit()
                    session.refresh(existing)
                    accounts.append({
                        "id": existing.id,
                        "email": existing.email,
                        "mail_access_type": mail_access_type,
                        "updated": True,
                    })
                else:
                    account = OutlookAccountModel(
                        email=email,
                        password=password,
                        refresh_token=refresh_token,
                        client_id=client_id,
                        mail_access_type=mail_access_type,
                        enabled=bool(request.enabled),
                        created_at=_utcnow(),
                        updated_at=_utcnow(),
                    )
                    session.add(account)
                    session.commit()
                    session.refresh(account)
                    accounts.append({
                        "id": account.id,
                        "email": account.email,
                        "mail_access_type": mail_access_type,
                    })
                success += 1
        except Exception as e:
            failed += 1
            errors.append(f"行 {idx}: 入库失败: {str(e)}")

    return {
        "success": success,
        "failed": failed,
        "deleted_bad": deleted_bad,
        "graph_count": graph_count,
        "imap_pop_count": imap_pop_count,
        "accounts": accounts,
        "errors": errors,
    }


# ── 取码类型探测 ─────────────────────────────────────────────

class OutlookProbeRequest(BaseModel):
    ids: List[int]


@router.post("/accounts/probe")
def probe_mail_access_type(body: OutlookProbeRequest):
    """对指定账号重新探测取码类型"""
    from core.outlook_probe import classify_mail_access_type

    results: List[Dict[str, Any]] = []
    with Session(engine) as session:
        for aid in body.ids:
            acc = session.get(OutlookAccountModel, aid)
            if not acc:
                results.append({"id": aid, "ok": False, "error": "不存在"})
                continue
            mail_type = classify_mail_access_type(acc.email, acc.refresh_token, acc.client_id)
            if mail_type:
                acc.mail_access_type = mail_type
                acc.updated_at = _utcnow()
                session.add(acc)
                results.append({"id": aid, "ok": True, "email": acc.email, "mail_access_type": mail_type})
            else:
                results.append({"id": aid, "ok": False, "email": acc.email, "error": "不可达"})
        session.commit()

    ok_count = sum(1 for r in results if r.get("ok"))
    return {"total": len(results), "ok": ok_count, "failed": len(results) - ok_count, "results": results}


# ── 辅助 ─────────────────────────────────────────────────────

_MAIL_ACCESS_TYPE_META = {
    "graph": ("Graph API", "success"),
    "imap_pop": ("IMAP/POP", "warning"),
    "": ("未检测", "default"),
}

_REGISTER_STATUS_META = {
    "未注册": ("未注册", "default"),
    "进行中": ("进行中", "processing"),
    "已注册": ("已注册", "success"),
}


def _serialize(acc: OutlookAccountModel) -> Dict[str, Any]:
    mail_type = str(acc.mail_access_type or "").strip()
    mail_label, mail_color = _MAIL_ACCESS_TYPE_META.get(mail_type, ("未知", "default"))

    def _status(val):
        s = str(val or "未注册")
        label, color = _REGISTER_STATUS_META.get(s, ("未知", "default"))
        return s, label, color

    gpt_s, gpt_l, gpt_c = _status(acc.gpt_register_status)
    grok_s, grok_l, grok_c = _status(acc.grok_register_status)
    trae_s, trae_l, trae_c = _status(getattr(acc, "trae_register_status", "未注册"))
    kiro_s, kiro_l, kiro_c = _status(getattr(acc, "kiro_register_status", "未注册"))
    obl_s, obl_l, obl_c = _status(getattr(acc, "obl_register_status", "未注册"))
    cursor_s, cursor_l, cursor_c = _status(getattr(acc, "cursor_register_status", "未注册"))

    return {
        "id": acc.id,
        "email": acc.email,
        "password": acc.password,
        "client_id": acc.client_id or "",
        "refresh_token": acc.refresh_token or "",
        "mail_access_type": mail_type,
        "mail_access_type_label": mail_label,
        "mail_access_type_color": mail_color,
        "gpt_register_status": gpt_s,
        "gpt_register_status_color": gpt_c,
        "grok_register_status": grok_s,
        "grok_register_status_color": grok_c,
        "trae_register_status": trae_s,
        "trae_register_status_color": trae_c,
        "kiro_register_status": kiro_s,
        "kiro_register_status_color": kiro_c,
        "obl_register_status": obl_s,
        "obl_register_status_color": obl_c,
        "cursor_register_status": cursor_s,
        "cursor_register_status_color": cursor_c,
        "has_oauth": bool(acc.client_id and acc.refresh_token),
        "enabled": acc.enabled,
        "created_at": acc.created_at.isoformat() if acc.created_at else "",
        "updated_at": acc.updated_at.isoformat() if acc.updated_at else "",
        "last_used": acc.last_used.isoformat() if acc.last_used else "",
    }
