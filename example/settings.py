#
# coding: utf-8
# Copyright (c) 2019 DATADVANCE
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

import pathlib
import uuid


BASE_DIR = (
    pathlib.Path(__file__).absolute().parent.parent
)  # os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_KEY = str(uuid.uuid4())
DEBUG = True
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "channels"]
ALLOWED_HOSTS = "*"


# In this simple example we use in-process in-memory Channel layer.
# In a real-life cases you need Redis or something familiar.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
ROOT_URLCONF = "example"
ASGI_APPLICATION = "example.application"
