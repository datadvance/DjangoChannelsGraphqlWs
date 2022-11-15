# Copyright (C) DATADVANCE, 2010-2022
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


"""Django settings for the test project `example`."""

# This is super-minimal configuration which is just enough for unit
# tests and to illustrate main principles in the `example.py`.

# import os
import asyncio
import pathlib
import sys
import uuid
from typing import List

# NOTE: On Windows with Python 3.8 enable selector back (default seems
# to be changed in Python 3.8), otherwise we get `NotImplementedError`
# which callstack goes to the import of the `daphne.server` module:
# ```
# ...
# File "...\channels\apps.py", line 6, in <module>
#   import daphne.server
# File "...\daphne\server.py", line 20, in <module>
#   asyncioreactor.install(twisted_loop)
# File "...\twisted\internet\asyncioreactor.py", line 320, in install
#   reactor = AsyncioSelectorReactor(eventloop)
# File "...\twisted\internet\asyncioreactor.py", line 69, in __init__
#   super().__init__()
# File "...\twisted\internet\base.py", line 571, in __init__
#   self.installWaker()
# File "...\twisted\internet\posixbase.py", line 286, in installWaker
#   self.addReader(self.waker)
# File "...\twisted\internet\asyncioreactor.py", line 151, in addReader
#   self._asyncioEventloop.add_reader(fd, callWithLogger, reader,
# File "C:\Python38\lib\asyncio\events.py", line 501, in add_reader
#   raise NotImplementedError
# ```
# NOTE: Actually this is already done in the `event_loop` fixture in the
# `conftest.py` file, but this file imports earlier by the Pytest plugin
# `pytest_django`. So we do this here as well.
if sys.platform == "win32" and sys.version_info.minor >= 8:
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()  # pylint: disable=no-member
    )


BASE_DIR = pathlib.Path(__file__).absolute().parent.parent
SECRET_KEY = str(uuid.uuid4())
DEBUG = True
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.messages.context_processors.messages",
                "django.contrib.auth.context_processors.auth",
            ]
        },
    }
]
INSTALLED_APPS: List[str] = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.auth",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "channels",
]
ALLOWED_HOSTS = ["*"]
STATIC_URL = "/static/"
STATICFILES_FINDERS = ["django.contrib.staticfiles.finders.AppDirectoriesFinder"]
# In this simple example we use in-process in-memory Channel layer.
# In a real-life cases you need Redis or something familiar.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
ROOT_URLCONF = "example"
ASGI_APPLICATION = "example.application"

# The database config is only needed to make serialization tests work.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "db.sqlite3"}}
