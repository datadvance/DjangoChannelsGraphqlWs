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

"""Serializer to support Django models as subscription events."""

import datetime
import logging

import django.core.serializers
import django.db
import msgpack

# Module logger.
LOG = logging.getLogger(__name__)


class Serializer:
    """Serialize/deserialize Python collection with Django models.

    Serialize/deserialize the data with the MessagePack like Redis
    Channels layer backend does.

    If `data` contains Django models, then it is serialized by the
    Django serialization utilities. For details see:
        Django serialization:
            https://docs.djangoproject.com/en/dev/topics/serialization/
        MessagePack:
            https://github.com/msgpack/msgpack-python
    """

    @staticmethod
    def serialize(data):
        """Serialize the `data`."""

        def encode_extra_types(obj):
            """MessagePack hook to serialize extra types.

            The recipe took from the MessagePack for Python docs:
            https://github.com/msgpack/msgpack-python#packingunpacking-of-custom-data-type

            Supported types:
            - Django models (through `django.core.serializers`).
            - Python `datetime` types:
              - `datetime.datetime`
              - `datetime.date`
              - `datetime.time`

            """
            if isinstance(obj, django.db.models.Model):
                return {
                    "__djangomodel__": True,
                    "as_str": django.core.serializers.serialize("json", [obj]),
                }
            if isinstance(obj, datetime.datetime):
                return {"__datetime__": True, "as_str": obj.isoformat()}
            if isinstance(obj, datetime.date):
                return {"__date__": True, "as_str": obj.isoformat()}
            if isinstance(obj, datetime.time):
                return {"__time__": True, "as_str": obj.isoformat()}
            return obj

        return msgpack.packb(data, default=encode_extra_types, use_bin_type=True)

    @staticmethod
    def deserialize(data):
        """Deserialize the `data`."""

        def decode_extra_types(obj):
            """MessagePack hook to deserialize extra types."""
            if "__djangomodel__" in obj:
                obj = next(
                    django.core.serializers.deserialize("json", obj["as_str"])
                ).object
            elif "__datetime__" in obj:
                obj = datetime.datetime.fromisoformat(obj["as_str"])
            elif "__date__" in obj:
                obj = datetime.date.fromisoformat(obj["as_str"])
            elif "__time__" in obj:
                obj = datetime.time.fromisoformat(obj["as_str"])
            return obj

        return msgpack.unpackb(data, object_hook=decode_extra_types, raw=False)
