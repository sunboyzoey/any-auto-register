"""数据库模型 - SQLite via SQLModel"""
from datetime import datetime, timezone
import os
from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session, select
import json


def _utcnow():
    return datetime.now(timezone.utc)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///account_manager.db")
engine = create_engine(DATABASE_URL)


class AccountModel(SQLModel, table=True):
    __tablename__ = "accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = Field(index=True)
    email: str = Field(index=True)
    password: str
    user_id: str = ""
    region: str = ""
    token: str = ""
    status: str = "registered"
    trial_end_time: int = 0
    cashier_url: str = ""
    extra_json: str = "{}"   # JSON 存储平台自定义字段
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_extra(self) -> dict:
        return json.loads(self.extra_json or "{}")

    def set_extra(self, d: dict):
        self.extra_json = json.dumps(d, ensure_ascii=False)


class TaskLog(SQLModel, table=True):
    __tablename__ = "task_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str
    email: str
    status: str        # success | failed
    error: str = ""
    detail_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow)


class OutlookAccountModel(SQLModel, table=True):
    __tablename__ = "outlook_accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, sa_column_kwargs={"unique": True})
    password: str
    client_id: str = ""
    refresh_token: str = ""
    mail_access_type: str = ""          # graph | imap_pop | ""
    gpt_register_status: str = "未注册"  # 未注册 | 进行中 | 已注册
    grok_register_status: str = "未注册" # 未注册 | 进行中 | 已注册
    trae_register_status: str = "未注册"
    kiro_register_status: str = "未注册"
    obl_register_status: str = "未注册"  # OpenBlockLabs
    cursor_register_status: str = "未注册"
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_used: Optional[datetime] = None


class ProxyModel(SQLModel, table=True):
    __tablename__ = "proxies"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(unique=True)
    region: str = ""
    success_count: int = 0
    fail_count: int = 0
    is_active: bool = True
    last_checked: Optional[datetime] = None


class ScheduledJobModel(SQLModel, table=True):
    """定时注册计划"""
    __tablename__ = "scheduled_jobs"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""                        # "每日注册 ChatGPT ×5"
    platform: str = Field(index=True)     # chatgpt / grok / trae / kiro / openblocklabs
    cron_hour: int = 9                    # 0-23 每天几点执行
    cron_minute: int = 0                  # 0-59 分钟
    count: int = 1                        # 每次注册数量
    concurrency: int = 1                  # 并发数
    mail_provider: str = "outlook"        # outlook / cfworker
    proxy: str = ""
    config_json: str = "{}"               # 额外配置 JSON
    enabled: bool = True
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TaskQueueModel(SQLModel, table=True):
    """持久化任务队列"""
    __tablename__ = "task_queue"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[int] = None          # 关联 scheduled_jobs (手动任务为 None)
    task_id: str = Field(index=True)      # 兼容 RegisterTaskStore 的 task_id
    platform: str = Field(index=True)
    source: str = "manual"                # manual / scheduled / cpa_replenish
    status: str = "pending"               # pending → running → done / failed / interrupted
    total: int = 1
    success: int = 0
    failed: int = 0
    skipped: int = 0
    progress: str = "0/0"
    config_json: str = "{}"               # 注册配置快照
    error: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


def save_account(account) -> 'AccountModel':
    """从 base_platform.Account 存入数据库（同平台同邮箱则更新）"""
    with Session(engine) as session:
        existing = session.exec(
            select(AccountModel)
            .where(AccountModel.platform == account.platform)
            .where(AccountModel.email == account.email)
        ).first()
        if existing:
            existing.password = account.password
            existing.user_id = account.user_id or ""
            existing.region = account.region or ""
            existing.token = account.token or ""
            existing.status = account.status.value
            existing.extra_json = json.dumps(account.extra or {}, ensure_ascii=False)
            existing.cashier_url = (account.extra or {}).get("cashier_url", "")
            existing.updated_at = _utcnow()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing
        m = AccountModel(
            platform=account.platform,
            email=account.email,
            password=account.password,
            user_id=account.user_id or "",
            region=account.region or "",
            token=account.token or "",
            status=account.status.value,
            extra_json=json.dumps(account.extra or {}, ensure_ascii=False),
            cashier_url=(account.extra or {}).get("cashier_url", ""),
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        return m


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_outlook_accounts()


def _migrate_outlook_accounts():
    """为已有的 outlook_accounts 表补齐新增字段。"""
    new_columns = [
        ("mail_access_type", "VARCHAR NOT NULL DEFAULT ''"),
        ("gpt_register_status", "VARCHAR NOT NULL DEFAULT '未注册'"),
        ("grok_register_status", "VARCHAR NOT NULL DEFAULT '未注册'"),
        ("trae_register_status", "VARCHAR NOT NULL DEFAULT '未注册'"),
        ("kiro_register_status", "VARCHAR NOT NULL DEFAULT '未注册'"),
        ("obl_register_status", "VARCHAR NOT NULL DEFAULT '未注册'"),
        ("cursor_register_status", "VARCHAR NOT NULL DEFAULT '未注册'"),
    ]
    import sqlite3 as _sqlite3
    raw_url = str(DATABASE_URL).replace("sqlite:///", "", 1)
    try:
        conn = _sqlite3.connect(raw_url)
        existing = {
            row[1].lower()
            for row in conn.execute("PRAGMA table_info(outlook_accounts)").fetchall()
        }
        for col_name, col_ddl in new_columns:
            if col_name.lower() not in existing:
                conn.execute(f"ALTER TABLE outlook_accounts ADD COLUMN {col_name} {col_ddl}")
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_session():
    with Session(engine) as session:
        yield session
