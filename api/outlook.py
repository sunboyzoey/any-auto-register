"""Outlook 邮箱账号池管理 API"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select, func, col

from core.db import engine, OutlookAccountModel

router = APIRouter(prefix="/outlook", tags=["outlook"])
MAX_FINISHED_IMPORT_TASKS = 20


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


@dataclass
class OutlookImportTaskRecord:
    id: str
    total: int
    status: str = "pending"
    progress: str = "0/0"
    processed: int = 0
    success: int = 0
    failed: int = 0
    deleted_bad: int = 0
    graph_count: int = 0
    imap_pop_count: int = 0
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "processed": self.processed,
            "success": self.success,
            "failed": self.failed,
            "deleted_bad": self.deleted_bad,
            "graph_count": self.graph_count,
            "imap_pop_count": self.imap_pop_count,
            "errors": list(self.errors),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class OutlookImportTaskStore:
    def __init__(self, *, max_finished_tasks: int = MAX_FINISHED_IMPORT_TASKS):
        self._lock = threading.Lock()
        self._records: Dict[str, OutlookImportTaskRecord] = {}
        self.max_finished_tasks = max_finished_tasks

    def create(self, task_id: str, *, total: int) -> OutlookImportTaskRecord:
        with self._lock:
            record = OutlookImportTaskRecord(
                id=task_id,
                total=total,
                progress=f"0/{total}",
            )
            self._records[task_id] = record
            return record

    def exists(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._records

    def snapshot(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self._records[task_id]
            return record.to_dict()

    def list_snapshots(self) -> list[Dict[str, Any]]:
        with self._lock:
            records = list(self._records.values())
            records.sort(key=lambda record: record.updated_at, reverse=True)
            return [record.to_dict() for record in records]

    def mark_running(self, task_id: str) -> None:
        with self._lock:
            record = self._records[task_id]
            record.status = "running"
            record.updated_at = time.time()

    def update(
        self,
        task_id: str,
        *,
        processed: Optional[int] = None,
        success: Optional[int] = None,
        failed: Optional[int] = None,
        deleted_bad: Optional[int] = None,
        graph_count: Optional[int] = None,
        imap_pop_count: Optional[int] = None,
        append_error: Optional[str] = None,
    ) -> None:
        with self._lock:
            record = self._records[task_id]
            if processed is not None:
                record.processed = processed
                record.progress = f"{processed}/{record.total}"
            if success is not None:
                record.success = success
            if failed is not None:
                record.failed = failed
            if deleted_bad is not None:
                record.deleted_bad = deleted_bad
            if graph_count is not None:
                record.graph_count = graph_count
            if imap_pop_count is not None:
                record.imap_pop_count = imap_pop_count
            if append_error:
                record.errors.append(append_error)
            record.updated_at = time.time()

    def finish(self, task_id: str, *, status: str = "done") -> None:
        with self._lock:
            record = self._records[task_id]
            record.status = status
            record.progress = f"{record.processed}/{record.total}"
            record.updated_at = time.time()
            self._cleanup_finished_locked()

    def fail(self, task_id: str, message: str) -> None:
        with self._lock:
            record = self._records[task_id]
            record.status = "failed"
            record.errors.append(message)
            record.updated_at = time.time()
            self._cleanup_finished_locked()

    def _cleanup_finished_locked(self) -> None:
        finished = [
            record_id
            for record_id, record in self._records.items()
            if record.status in {"done", "failed"}
        ]
        overflow = len(finished) - self.max_finished_tasks
        if overflow <= 0:
            return
        finished.sort(key=lambda record_id: self._records[record_id].updated_at)
        for record_id in finished[:overflow]:
            self._records.pop(record_id, None)


_import_task_store = OutlookImportTaskStore()


def _count_import_lines(data: str) -> int:
    total = 0
    for raw_line in (data or "").splitlines():
        line = str(raw_line or "").strip()
        if line and not line.startswith("#"):
            total += 1
    return total


def _ensure_import_task_exists(task_id: str) -> None:
    if not _import_task_store.exists(task_id):
        raise HTTPException(404, "导入任务不存在")


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


# ── 批量导入（后台任务） ───────────────────────────────────────

def _execute_outlook_batch_import(task_id: str, request: OutlookBatchImportRequest) -> None:
    from core.outlook_probe import classify_mail_access_type

    _import_task_store.mark_running(task_id)

    lines = (request.data or "").splitlines()
    processed = 0
    success = 0
    failed = 0
    deleted_bad = 0
    graph_count = 0
    imap_pop_count = 0

    try:
        for idx, raw_line in enumerate(lines, start=1):
            line = str(raw_line or "").strip()
            if not line or line.startswith("#"):
                continue

            error_message = ""
            parts = [part.strip() for part in line.split("----")]
            if len(parts) != 4:
                failed += 1
                error_message = f"行 {idx}: 格式错误，应为 邮箱----密码----刷新令牌----Client ID"
            else:
                email, password, refresh_token, client_id = parts
                if not email or "@" not in email:
                    failed += 1
                    error_message = f"行 {idx}: 无效的邮箱地址: {email}"
                else:
                    mail_access_type = classify_mail_access_type(email, refresh_token, client_id)
                    if mail_access_type is None:
                        with Session(engine) as session:
                            existing = session.exec(
                                select(OutlookAccountModel).where(OutlookAccountModel.email == email)
                            ).first()
                            if existing:
                                session.delete(existing)
                                session.commit()
                        deleted_bad += 1
                        error_message = (
                            f"行 {idx}: {email} 取码类型探测失败（Graph/IMAP/POP 均不可达），已跳过"
                        )
                    else:
                        if mail_access_type == "graph":
                            graph_count += 1
                        elif mail_access_type == "imap_pop":
                            imap_pop_count += 1

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
                            success += 1
                        except Exception as e:
                            failed += 1
                            error_message = f"行 {idx}: 入库失败: {str(e)}"

            processed += 1
            _import_task_store.update(
                task_id,
                processed=processed,
                success=success,
                failed=failed,
                deleted_bad=deleted_bad,
                graph_count=graph_count,
                imap_pop_count=imap_pop_count,
                append_error=error_message or None,
            )

        _import_task_store.finish(task_id, status="done")
    except Exception as e:
        _import_task_store.fail(task_id, f"导入任务异常: {e}")


@router.post("/batch-import")
def batch_import_outlook(request: OutlookBatchImportRequest):
    """
    批量导入 Outlook 邮箱账户。

    固定格式（每行一个，字段用 ---- 分隔）：
        邮箱----密码----刷新令牌----Client ID

    导入后自动在后台探测取码类型（Graph / IMAP / POP）。
    """
    total = _count_import_lines(request.data)
    task_id = f"outlook_import_{int(time.time() * 1000)}"
    _import_task_store.create(task_id, total=total)
    thread = threading.Thread(
        target=_execute_outlook_batch_import,
        args=(task_id, request),
        daemon=True,
        name=f"outlook-import-{task_id}",
    )
    thread.start()
    return {"task_id": task_id, "total": total}


@router.get("/import-tasks/{task_id}")
def get_import_task(task_id: str):
    _ensure_import_task_exists(task_id)
    return _import_task_store.snapshot(task_id)


@router.get("/import-tasks")
def list_import_tasks(limit: int = Query(20, ge=1, le=100)):
    items = _import_task_store.list_snapshots()[:limit]
    return {"total": len(items), "items": items}


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
