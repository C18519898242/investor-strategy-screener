import logging
import unittest

from screener.sync.errors import SyncError
from screener.sync.retry import call_with_retry


class CallWithRetryTest(unittest.TestCase):
    def setUp(self):
        logging.getLogger("screener").addHandler(logging.NullHandler())

    def tearDown(self):
        logging.getLogger("screener").handlers.clear()

    def test_rate_limited_waits_thirty_seconds_each_time(self):
        waits, attempts = [], []

        def operation():
            attempts.append(1)
            if len(attempts) < 3:
                raise Exception("[rate_limited] 请求额度已达到限制；建议等待30秒后重试")
            return "ok"

        self.assertEqual(call_with_retry(operation, sleep=waits.append), "ok")
        self.assertEqual(waits, [30, 30])
        self.assertEqual(len(attempts), 3)

    def test_upstream_failure_waits_five_then_fifteen(self):
        waits, attempts = [], []

        def operation():
            attempts.append(1)
            if len(attempts) < 3:
                raise Exception("[upstream_pool_exhausted] 数据当前不可用")
            return "ok"

        call_with_retry(operation, sleep=waits.append)

        self.assertEqual(waits, [5, 15])

    def test_read_timeout_is_retried_like_an_upstream_failure(self):
        waits, attempts = [], []

        def operation():
            attempts.append(1)
            if len(attempts) < 2:
                raise Exception("HTTPSConnectionPool: Read timed out.")
            return "ok"

        call_with_retry(operation, sleep=waits.append)

        self.assertEqual(waits, [5])

    def test_gives_up_after_three_attempts(self):
        def operation():
            raise Exception("[upstream_pool_exhausted] 数据当前不可用")

        with self.assertRaisesRegex(SyncError, "重试 3 次后仍失败"):
            call_with_retry(operation, sleep=lambda seconds: None)

    def test_does_not_retry_an_unknown_error(self):
        waits = []

        def operation():
            raise Exception("[invalid_params] 业务参数缺失或格式不合法")

        with self.assertRaises(Exception) as caught:
            call_with_retry(operation, sleep=waits.append)

        self.assertNotIsInstance(caught.exception, SyncError)
        self.assertIn("invalid_params", str(caught.exception))
        self.assertEqual(waits, [])

    def test_rate_limited_logs_a_warning_before_waiting(self):
        attempts = []

        def operation():
            attempts.append(1)
            if len(attempts) < 2:
                raise Exception("[rate_limited] 请求额度已达到限制；建议等待30秒后重试")
            return "ok"

        with self.assertLogs("screener.sync.retry", level="WARNING") as captured:
            call_with_retry(operation, sleep=lambda seconds: None)

        self.assertEqual(
            captured.output,
            ["WARNING:screener.sync.retry:第 1 次调用失败（rate_limited），30 秒后重试"],
        )

    def test_unknown_error_logs_no_warning(self):
        def operation():
            raise Exception("[invalid_params] 业务参数缺失或格式不合法")

        with self.assertNoLogs("screener.sync.retry", level="WARNING"):
            with self.assertRaises(Exception) as caught:
                call_with_retry(operation, sleep=lambda seconds: None)

        self.assertIn("invalid_params", str(caught.exception))
