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

"""Dict wrapper to access keys as attributes."""


class DictAsObject:
    """Dict wrapper to access keys as attributes."""

    def __init__(self, scope):
        """Remember given `scope`."""
        self._scope = scope

    def _asdict(self):
        """Provide inner Channels scope object."""
        return self._scope

    # ------------------------------------------------ WRAPPER FUNCTIONS
    def __getattr__(self, name):
        """Route attributes to the scope object."""
        if name.startswith("_"):
            raise AttributeError()
        try:
            return self._scope[name]
        except KeyError as ex:
            raise AttributeError() from ex

    def __setattr__(self, name, value):
        """Route attributes to the scope object."""
        if name.startswith("_"):
            super().__setattr__(name, value)
        self._scope[name] = value

    # ----------------------------------------------------- DICT WRAPPER
    def __getitem__(self, key):
        """Wrap dict method."""
        return self._scope[key]

    def __setitem__(self, key, value):
        """Wrap dict method."""
        self._scope[key] = value

    def __delitem__(self, key):
        """Wrap dict method."""
        del self._scope[key]

    def __contains__(self, item):
        """Wrap dict method."""
        return item in self._scope

    def __str__(self):
        """Wrap dict method."""
        return self._scope.__str__()

    def __repr__(self):
        """Wrap dict method."""
        return self._scope.__repr__()
