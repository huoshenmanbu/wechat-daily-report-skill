import unittest

from scripts.wechat_decrypted_reader import format_app_message


class AppMessageTests(unittest.TestCase):
    def test_shared_link_keeps_title_and_url(self):
        xml = """
        <msg><appmsg><title>项目融资公告</title><type>5</type>
        <url>https://example.com/news?a=1&amp;b=2</url></appmsg></msg>
        """

        message_type, content = format_app_message(xml)

        self.assertEqual(0, message_type)
        self.assertIn("项目融资公告", content)
        self.assertIn("https://example.com/news?a=1&b=2", content)


if __name__ == "__main__":
    unittest.main()
