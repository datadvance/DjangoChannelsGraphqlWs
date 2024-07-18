# Copyright (C) DATADVANCE, 2010-2023
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
import pytest

SOURCE_DIRS = ["channels_graphql_ws/", "tests/", "example/"]
PROJECT_ROOT_DIR = pathlib.Path(__file__).absolute().parent.parent


@pytest.mark.parametrize("src_dir", SOURCE_DIRS)
def test_pylint(src_dir):
    """Run Pylint."""

    pylint = plumbum.local["pylint"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        # There is the bug in pylint_django:
        # https://github.com/PyCQA/pylint-django/issues/369#issuecomment-1305725496
        #
        # The `env` parameter should be removed once that bug is fixed.
        result = pylint(src_dir, env={"PYTHONPATH": "."})
        if result:
            print("\nPylint:", result)


@pytest.mark.parametrize("src_dir", SOURCE_DIRS)
def test_black(src_dir):
    """Run Black."""
    black = plumbum.local["black"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = black("--check", src_dir)
        if result:
            print("\nBlack:", result)


@pytest.mark.parametrize("src_dir", SOURCE_DIRS)
def test_isort(src_dir):
    """Run Isort."""
    isort = plumbum.local["isort"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = isort("--check-only", src_dir)
        if result:
            print("\nIsort:", result)


@pytest.mark.parametrize("src_dir", SOURCE_DIRS)
def test_mypy(src_dir):
    """Run MyPy."""
    mypy = plumbum.local["mypy"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = mypy(src_dir)
        if result:
            print("\nMyPy:", result)
