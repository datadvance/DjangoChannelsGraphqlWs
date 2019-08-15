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
import sys
import tempfile
import textwrap

import plumbum
import pytest


SOURCE_DIRS = ["channels_graphql_ws/", "tests/", "example/"]
PROJECT_ROOT_DIR = pathlib.Path(__file__).absolute().parent.parent


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=textwrap.dedent(
        """
        I could not manage this to work in Windows:
        1. I had to disable spelling on Windows cause Pyenchant which
           does the spellcheking job for Pylint cannot be simply
           installed with `pip install` in Windows. Unfortunately it is
           not enough to tell Pylint `--disable=spelling`, cause it
           still complains about `spelling-dict=en_US`, so I wiped out
           spelling from its configuration file manually here.
        2. Even then, I could not run it cause in Travis I've got
           permission denied error for both standard temp directory, and
           for CWD. I just tired trying fixing this. Not a big deal, we
           run linters in Linux and Mac anyway.
        """
    ),
)
@pytest.mark.parametrize("src_dir", SOURCE_DIRS)
def test_pylint(src_dir):
    """Run Pylint."""

    pylint = plumbum.local["pylint"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = pylint(src_dir)
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
        result = isort("--check-only", "-rc", src_dir)
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


# There is an issue in Pydocstyle that it crashes on nested async def:
# https://github.com/PyCQA/pydocstyle/issues/370. We have such things in
# `tests/` so mark them as xfail.
@pytest.mark.parametrize(
    "src_dir",
    [d for d in SOURCE_DIRS if d != "tests/"]
    + [pytest.param("tests/", marks=pytest.mark.xfail(reason="Pydocstyle issue #370"))],
)
def test_pydocstyle(src_dir):
    """Run Pydocstyle."""

    pydocstyle = plumbum.local["pydocstyle"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = pydocstyle(src_dir)
        if result:
            print("\nPydocstyle:", result)


@pytest.mark.parametrize("src_dir", SOURCE_DIRS)
def test_bandit(src_dir):
    """Run Bandit."""
    bandit = plumbum.local["bandit"]
    with plumbum.local.cwd(PROJECT_ROOT_DIR):
        result = bandit("-ll", "-r", src_dir)
        if result:
            print("\nBandit:", result)
