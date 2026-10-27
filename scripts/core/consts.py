"""Общие константы: регулярки ссылок и имён."""

import re

POST_RE = re.compile(r"/@([A-Za-z0-9._]+)/post/([A-Za-z0-9_-]+)")


USERNAME_RE = re.compile(r"^[A-Za-z0-9._]+$")
