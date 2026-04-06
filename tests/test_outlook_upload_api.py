import unittest

from api.outlook import _parse_register_machine_upload_payload


class OutlookRegisterMachineUploadTests(unittest.TestCase):
    def test_parse_data_template(self):
        total, rows, errors = _parse_register_machine_upload_payload(
            {
                "data": "demo@hotmail.com----pass123----cid----rtok"
            }
        )

        self.assertEqual(total, 1)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "demo@hotmail.com")
        self.assertEqual(rows[0]["password"], "pass123")
        self.assertEqual(rows[0]["client_id"], "cid")
        self.assertEqual(rows[0]["refresh_token"], "rtok")

    def test_parse_placeholder_template(self):
        total, rows, errors = _parse_register_machine_upload_payload(
            {
                "a": "demo@hotmail.com",
                "p": "pass123",
                "c": "cid",
                "t": "rtok",
            }
        )

        self.assertEqual(total, 1)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "demo@hotmail.com")
        self.assertEqual(rows[0]["password"], "pass123")

    def test_parse_combined_templates_dedupes_email(self):
        total, rows, errors = _parse_register_machine_upload_payload(
            {
                "data": "demo@hotmail.com----pass123\nfresh@hotmail.com----pass999",
                "a": "demo@hotmail.com",
                "p": "pass123",
            }
        )

        self.assertEqual(total, 3)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("请求内重复邮箱" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
