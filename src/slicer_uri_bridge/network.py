from __future__ import annotations

import urllib.request
import urllib.response

USER_AGENT = "OrcaSlicer/2.4.0-dev"


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def return_response(self, req, fp, code, msg, headers):
        return urllib.response.addinfourl(fp, headers, req.full_url, code=code)

    http_error_301 = http_error_302 = http_error_303 = http_error_307 = http_error_308 = return_response


def has_control_chars(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
