import unittest
from macromellow.web import allowed_request_origin

class OriginSecurityTests(unittest.TestCase):
    def test_local_requests(self):
        self.assertTrue(allowed_request_origin({"Origin": "http://127.0.0.1:8000", "Host": "127.0.0.1:8000"}, "127.0.0.1"))
    def test_tailscale_served_https(self):
        headers = {"Host": "tp-industry.tail4cab83.ts.net", "Origin": "https://tp-industry.tail4cab83.ts.net"}
        self.assertTrue(allowed_request_origin(headers, "127.0.0.1"))
        self.assertFalse(allowed_request_origin(headers, "192.0.2.1"))
    def test_forwarded_https(self):
        headers = {"Host": "127.0.0.1:8000", "Origin": "https://tp-industry.tail4cab83.ts.net", "X-Forwarded-Host": "tp-industry.tail4cab83.ts.net", "X-Forwarded-Proto": "https"}
        self.assertTrue(allowed_request_origin(headers, "127.0.0.1"))
        self.assertFalse(allowed_request_origin({**headers, "X-Forwarded-Proto": "http"}, "127.0.0.1"))
    def test_unrelated_origins(self):
        headers = {"Host": "tp-industry.tail4cab83.ts.net", "Origin": "https://evil.example"}
        self.assertFalse(allowed_request_origin(headers, "127.0.0.1"))
        self.assertFalse(allowed_request_origin({"Host": "tp-industry.tail4cab83.ts.net", "Origin": "https://tp-industry.tail4cab83.ts.net.evil.com"}, "127.0.0.1"))

if __name__ == "__main__":
    unittest.main()