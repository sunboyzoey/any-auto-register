import tempfile
import unittest
from unittest import mock

from sqlmodel import SQLModel, Session, create_engine, select

from api.outlook import (
    OutlookBatchImportRequest,
    _execute_outlook_batch_import,
    _import_task_store,
)
from core.db import OutlookAccountModel


class OutlookImportTaskTests(unittest.TestCase):
    def test_execute_outlook_batch_import_tracks_progress_and_results(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            engine = create_engine(f"sqlite:///{db_file.name}")
            SQLModel.metadata.create_all(engine)
            task_id = "outlook_import_test_case"
            _import_task_store.create(task_id, total=3)
            request = OutlookBatchImportRequest(
                data=(
                    "first@example.com----pw1----rt1----cid1\n"
                    "bad-line\n"
                    "second@example.com----pw2----rt2----cid2\n"
                ),
                enabled=True,
            )

            with mock.patch("api.outlook.engine", engine), mock.patch(
                "core.outlook_probe.classify_mail_access_type",
                side_effect=["graph", None],
            ):
                _execute_outlook_batch_import(task_id, request)

            snapshot = _import_task_store.snapshot(task_id)
            self.assertEqual(snapshot["status"], "done")
            self.assertEqual(snapshot["processed"], 3)
            self.assertEqual(snapshot["success"], 1)
            self.assertEqual(snapshot["failed"], 1)
            self.assertEqual(snapshot["deleted_bad"], 1)
            self.assertEqual(snapshot["graph_count"], 1)
            self.assertEqual(snapshot["imap_pop_count"], 0)
            self.assertEqual(len(snapshot["errors"]), 2)

            with Session(engine) as session:
                accounts = session.exec(select(OutlookAccountModel)).all()
                self.assertEqual(len(accounts), 1)
                self.assertEqual(accounts[0].email, "first@example.com")


if __name__ == "__main__":
    unittest.main()
