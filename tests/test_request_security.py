"""Browser request boundaries without provider or database connections."""
import os
import unittest
from unittest.mock import patch

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-request-security-only")
from fastapi import HTTPException
from starlette.requests import Request
from config import settings
from utils.request_security import require_json_mutation


class JsonMutationSecurityTests(unittest.TestCase):
    def request(self, method="POST", headers=None):
        return Request({"type":"http", "method":method, "scheme":"https", "path":"/private", "query_string":b"",
                        "server":("api.example.test",443), "headers":[(k.lower().encode(),v.encode()) for k,v in (headers or {}).items()]})

    def check(self, headers, expected=None, method="POST"):
        with patch.object(settings,"allowed_origins","https://app.example.test,https://example.test"):
            if expected:
                with self.assertRaises(HTTPException) as caught:
                    require_json_mutation(self.request(method,headers))
                self.assertEqual(caught.exception.status_code,expected)
            else:
                require_json_mutation(self.request(method,headers))

    def test_simple_content_types_and_missing_media_type_are_rejected(self):
        for media in (None,"text/plain","application/x-www-form-urlencoded","multipart/form-data","application/jsonp"):
            headers={"Cookie":"ildr_token=synthetic", "Origin":"https://evil.example"}
            if media: headers["Content-Type"]=media
            self.check(headers,415)

    def test_cookie_requires_complete_trusted_origin(self):
        base={"Content-Type":"application/json", "Cookie":"ildr_token=synthetic"}
        for origin in (None,"null","https://evil.example","https://app.example.test.evil.example","http://app.example.test","https://app.example.test:444"):
            self.check({**base,**({"Origin":origin} if origin else {})},403)
        for origin in ("https://app.example.test","https://example.test","https://api.example.test"):
            self.check({**base,"Origin":origin})

    def test_bearer_client_without_ambient_cookie_origin_is_supported(self):
        self.check({"Content-Type":"Application/JSON; charset=utf-8","Authorization":"Bearer synthetic"})
        self.check({"Content-Type":"application/json","Authorization":"Bearer synthetic","Cookie":"ildr_token=synthetic"})
        self.check({"Content-Type":"application/json","Authorization":"Bearer synthetic","Origin":"null"},403)

    def test_empty_or_other_authorization_cannot_hide_cookie_authentication(self):
        for authorization in ("Bearer ","bearer   ","Basic synthetic",""):
            self.check({"Content-Type":"application/json","Cookie":"ildr_token=synthetic","Authorization":authorization},403)

    def test_read_only_and_preflight_requests_are_unchanged(self):
        for method in ("GET","HEAD","OPTIONS"):
            self.check({"Cookie":"ildr_token=synthetic","Origin":"null"},method=method)


if __name__=="__main__":
    unittest.main()
