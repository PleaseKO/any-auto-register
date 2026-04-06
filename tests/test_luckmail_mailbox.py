import unittest
from unittest import mock

from core.base_mailbox import LuckMailMailbox, MailboxAccount, create_mailbox
from core.luckmail.models import PageResult, PurchaseItem, TokenMailItem, TokenMailList


class LuckMailMailboxTests(unittest.TestCase):
    def _build_mailbox(self):
        mailbox = LuckMailMailbox.__new__(LuckMailMailbox)
        mailbox._client = mock.Mock()
        mailbox._project_code = "openai"
        mailbox._email_type = None
        mailbox._domain = None
        mailbox._order_no = None
        mailbox._token = "tok_demo"
        mailbox._email = "demo@example.com"
        mailbox._log_fn = None
        return mailbox

    @mock.patch("time.sleep", return_value=None)
    def test_wait_for_code_skips_excluded_purchase_code_and_keeps_polling_for_fresh_mail(self, _sleep):
        mailbox = self._build_mailbox()
        mailbox.get_current_ids = mock.Mock(return_value={"m1"})
        mailbox._client.user.get_token_mails.side_effect = [
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(message_id="m1", subject="Your OpenAI code is 111111"),
                ],
            ),
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(message_id="m1", subject="Your OpenAI code is 111111"),
                    TokenMailItem(message_id="m2", subject="Your OpenAI code is 222222"),
                ],
            ),
        ]

        code = mailbox.wait_for_code(
            MailboxAccount(email="demo@example.com", account_id="tok_demo"),
            timeout=5,
            exclude_codes={"111111"},
        )

        self.assertEqual(code, "222222")
        mailbox.get_current_ids.assert_called_once()
        self.assertEqual(mailbox._client.user.get_token_mails.call_count, 2)

    @mock.patch("core.base_mailbox.LuckMailMailbox")
    def test_create_mailbox_luckmail_forwards_proxy(self, mock_mailbox_cls):
        create_mailbox(
            "luckmail",
            extra={
                "luckmail_base_url": "https://example.com",
                "luckmail_api_key": "k",
            },
            proxy="socks5://127.0.0.1:7890",
        )
        mock_mailbox_cls.assert_called_once()
        self.assertEqual(
            mock_mailbox_cls.call_args.kwargs.get("proxy"),
            "socks5://127.0.0.1:7890",
        )

    @mock.patch("core.luckmail.LuckMailClient")
    def test_mailbox_constructor_forwards_proxy_to_luckmail_client(self, mock_client_cls):
        LuckMailMailbox(
            base_url="https://example.com",
            api_key="k",
            project_code="openai",
            proxy="socks5://127.0.0.1:7890",
        )
        mock_client_cls.assert_called_once()
        self.assertEqual(
            mock_client_cls.call_args.kwargs.get("proxy_url"),
            "socks5://127.0.0.1:7890",
        )

    def test_get_email_falls_back_to_existing_purchases_when_purchase_api_fails(self):
        mailbox = self._build_mailbox()
        mailbox._token = None
        mailbox._email = None
        mailbox._client.user.purchase_emails.side_effect = RuntimeError("无库存")
        mailbox._client.user.get_purchases.return_value = PageResult(
            list=[
                PurchaseItem(
                    id=1,
                    email_address="fallback@example.com",
                    token="tok_fallback",
                    project_name="openai",
                    price="0.0000",
                    user_disabled=0,
                )
            ],
            total=1,
            page=1,
            page_size=100,
        )

        account = mailbox.get_email()

        self.assertEqual(account.email, "fallback@example.com")
        self.assertEqual(account.account_id, "tok_fallback")
        self.assertEqual(mailbox._token, "tok_fallback")
        self.assertEqual(mailbox._email, "fallback@example.com")

    def test_get_email_raises_when_purchase_api_fails_and_no_existing_purchase(self):
        mailbox = self._build_mailbox()
        mailbox._token = None
        mailbox._email = None
        mailbox._client.user.purchase_emails.side_effect = RuntimeError("无库存")
        mailbox._client.user.get_purchases.return_value = PageResult(
            list=[],
            total=0,
            page=1,
            page_size=100,
        )

        with self.assertRaises(RuntimeError) as ctx:
            mailbox.get_email()

        self.assertIn("回退已购邮箱池", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
