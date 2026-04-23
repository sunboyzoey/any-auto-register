import unittest
from unittest.mock import patch

from api.tasks import RegisterTaskRequest, _create_task_record, _run_register, _task_store
from core.base_mailbox import BaseMailbox, MailboxAccount
from core.base_platform import Account, BasePlatform


class _FakeMailbox(BaseMailbox):
    def get_email(self) -> MailboxAccount:
        return MailboxAccount(email="demo@example.com")

    def get_current_ids(self, account: MailboxAccount) -> set:
        return set()

    def wait_for_code(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set = None,
        code_pattern: str = None,
        **kwargs,
    ) -> str:
        def poll_once():
            return None

        return self._run_polling_wait(
            timeout=timeout,
            poll_interval=0.01,
            poll_once=poll_once,
        )


class _FakePlatform(BasePlatform):
    name = "fake"
    display_name = "Fake"
    last_proxy = None

    def __init__(self, config=None, mailbox=None):
        super().__init__(config)
        self.mailbox = mailbox
        type(self).last_proxy = getattr(config, "proxy", None)

    def register(self, email: str, password: str = None) -> Account:
        account = self.mailbox.get_email()
        self.mailbox.wait_for_code(account, timeout=1)
        return Account(
            platform="fake",
            email=account.email,
            password=password or "pw",
        )

    def check_valid(self, account: Account) -> bool:
        return True


class RegisterTaskControlFlowTests(unittest.TestCase):
    def _build_request(self):
        return RegisterTaskRequest(
            platform="fake",
            count=1,
            concurrency=1,
            proxy="http://proxy.local:8080",
            extra={"mail_provider": "fake"},
        )

    def _run_with_control(self, task_id: str, *, stop: bool = False, skip: bool = False):
        req = self._build_request()
        _create_task_record(task_id, req, "manual", None)
        _FakePlatform.last_proxy = None
        if stop:
            _task_store.request_stop(task_id)
        if skip:
            _task_store.request_skip_current(task_id)

        with (
            patch("core.registry.get", return_value=_FakePlatform),
            patch("core.base_mailbox.create_mailbox", return_value=_FakeMailbox()),
            patch("core.db.save_account", side_effect=lambda account: account),
            patch("api.tasks._save_task_log"),
        ):
            _run_register(task_id, req)

        return _task_store.snapshot(task_id)

    def _run_request_with_mocks(
        self,
        task_id: str,
        req: RegisterTaskRequest,
        *,
        config_values: dict[str, str] | None = None,
        pool_proxy: str | None = None,
    ):
        _create_task_record(task_id, req, "manual", None)
        _FakePlatform.last_proxy = None

        config_values = config_values or {}

        def _config_get(key, default=""):
            return config_values.get(key, default)

        with (
            patch("core.registry.get", return_value=_FakePlatform),
            patch("core.base_mailbox.create_mailbox", return_value=_FakeMailbox()),
            patch("core.db.save_account", side_effect=lambda account: account),
            patch("api.tasks._save_task_log"),
            patch("core.config_store.config_store.get", side_effect=_config_get),
            patch("core.proxy_pool.proxy_pool.get_next", return_value=pool_proxy),
            patch("core.proxy_pool.proxy_pool.report_success"),
            patch("core.proxy_pool.proxy_pool.report_fail"),
        ):
            _run_register(task_id, req)

        return _task_store.snapshot(task_id)

    def test_skip_current_marks_attempt_as_skipped(self):
        snapshot = self._run_with_control("task-control-skip", skip=True)

        self.assertEqual(snapshot["status"], "done")
        self.assertEqual(snapshot["success"], 0)
        self.assertEqual(snapshot["skipped"], 1)
        self.assertEqual(snapshot["errors"], [])

    def test_stop_marks_task_as_stopped(self):
        snapshot = self._run_with_control("task-control-stop", stop=True)

        self.assertEqual(snapshot["status"], "stopped")
        self.assertEqual(snapshot["success"], 0)
        self.assertEqual(snapshot["skipped"], 0)
        self.assertEqual(snapshot["errors"], [])

    def test_auto_proxy_can_be_disabled_globally(self):
        req = RegisterTaskRequest(
            platform="fake",
            count=1,
            concurrency=1,
            extra={"mail_provider": "fake"},
        )

        snapshot = self._run_request_with_mocks(
            "task-control-no-auto-proxy",
            req,
            config_values={
                "register_auto_use_proxy": "0",
                "default_proxy": "http://default.proxy:8080",
            },
            pool_proxy="http://pool.proxy:8080",
        )

        self.assertEqual(snapshot["status"], "done")
        self.assertIsNone(_FakePlatform.last_proxy)

    def test_no_proxy_when_pool_and_default_are_empty(self):
        req = RegisterTaskRequest(
            platform="fake",
            count=1,
            concurrency=1,
            extra={"mail_provider": "fake"},
        )

        snapshot = self._run_request_with_mocks(
            "task-control-no-fallback-local-proxy",
            req,
            config_values={"register_auto_use_proxy": "1", "default_proxy": ""},
            pool_proxy=None,
        )

        self.assertEqual(snapshot["status"], "done")
        self.assertIsNone(_FakePlatform.last_proxy)


if __name__ == "__main__":
    unittest.main()
