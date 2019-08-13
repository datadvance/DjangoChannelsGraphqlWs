# Copyright (C) DATADVANCE, 2010-2020
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

"""Check code by running different linters and code style checkers."""

import pathlib

import plumbum


SOURCE_DIRS = ["channels_graphql_ws/", "tests/", "example/"]
PROJECT_ROOT_DIR = pathlib.Path(__file__).absolute().parent.parent


def test_pylint():
    """Run Pylint."""
    pylint = plumbum.local["pylint"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = pylint(*SOURCE_DIRS)
        print("\nPylint:", result)


def test_black():
    """Run Black."""
    black = plumbum.local["black"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = black("--check", *SOURCE_DIRS)
        print("\nBlack:", result)


def test_isort():
    """Run Isort."""
    isort = plumbum.local["isort"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = isort("--check-only", "-rc", *SOURCE_DIRS)
        print("\nIsort:", result)


def test_mypy():
    """Run MyPy."""
    mypy = plumbum.local["mypy"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = mypy(*SOURCE_DIRS)
        print("\nMyPy:", result)


def test_pydocstyle():
    """Run Pydocstyle."""
    pydocstyle = plumbum.local["pydocstyle"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = pydocstyle(*SOURCE_DIRS)
        print("\nPydocstyle:", result)


def test_bandit():
    """Run Bandit."""
    bandit = plumbum.local["bandit"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = bandit("-ll", "-r", *SOURCE_DIRS)
        print("\nBandit:", result)
