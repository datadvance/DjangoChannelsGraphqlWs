#
# coding: utf-8
# Copyright (c) 2018 DATADVANCE
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

import os
import tempfile
import uuid


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_KEY = str(uuid.uuid4())
DEBUG = True
INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'channels',
]


# Unit-test use in-memory ASGI while example uses Redis.
if 'TESTING' in os.environ:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        }
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [('redis', 6379)],
            }
        }
    }

# Example stores data in `db.sqlite3`. Test database is stored in a
# temporary file which is removed when tests finish. There is a reason
# for that:
# 1. By default Django uses in-memory database for running tests (when
# SQLite engine is used):
# https://docs.djangoproject.com/en/dev/topics/testing/overview/#the-test-database
# 2. In order to use the same in-memory database from different DB
# connections, Django enables SQLite shared cache:
# https://www.sqlite.org/sharedcache.html
# 3. From experiments I figured out that concurrent read/write access to
# the SQLite database with shared cache immediately leads to the errors
# like: `<class 'django.db.utils.OperationalError'> database table is
# locked`. Looks like shared cache does not respect the `timeout` value.
test_db_tmpdir = tempfile.TemporaryDirectory()
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
        'OPTIONS': {
            # Experiment showed that value `2147483.647` is the maximum
            # allowed SQLite timeout. Setting `timeout` to the larger
            # one will simply disable it.
            'timeout': 2147483.647
        },
        'TEST': {
            'NAME': os.path.join(test_db_tmpdir.name, 'test_db.sqlite'),
        }
    }
}

ROOT_URLCONF = 'django_example.example'
ASGI_APPLICATION = 'django_example.example.application'
