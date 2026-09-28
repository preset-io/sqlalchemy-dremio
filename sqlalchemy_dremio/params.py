"""Client-side ``qmark`` parameter binding.

Arrow Flight's ``GetFlightInfo(CommandDescriptor)`` carries SQL text only, so
bound values have to be rendered into the statement as SQL literals before it
is sent. Placeholders are found with a small scanner that skips string
literals, quoted identifiers and comments, so a ``?`` inside any of them (or
inside a value that has already been rendered) is never treated as a
placeholder. Each value is rendered exactly once, as a typed literal.
"""

import datetime
import decimal
import math
import uuid
from collections.abc import Mapping

from sqlalchemy_dremio.exceptions import ProgrammingError


def _quote_string(value):
    # Dremio (Calcite) string literals escape only the quote character itself;
    # a backslash is an ordinary character.
    return "'" + value.replace("'", "''") + "'"


def _format_time(value):
    text = value.strftime("%H:%M:%S")
    if value.microsecond:
        text += ".%06d" % value.microsecond
    return text


def render_literal(value):
    """Return ``value`` as a Dremio SQL literal."""
    if value is None:
        return "NULL"
    # bool before int: bool is an int subclass.
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "CAST('NaN' AS DOUBLE)"
        if math.isinf(value):
            return "CAST('%sInfinity' AS DOUBLE)" % ("-" if value < 0 else "")
        # A bare 0.1 is an exact DECIMAL literal; keep the float a DOUBLE.
        return "CAST(%s AS DOUBLE)" % repr(value)
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            raise ProgrammingError("Dremio DECIMAL cannot represent %r" % value)
        return format(value, "f")
    # datetime before date: datetime is a date subclass.
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            # Dremio TIMESTAMP has no zone and is interpreted as UTC.
            value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return "TIMESTAMP '%s %s'" % (value.strftime("%Y-%m-%d"), _format_time(value))
    if isinstance(value, datetime.date):
        return "DATE '%s'" % value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.time):
        if value.tzinfo is not None:
            raise ProgrammingError("Dremio TIME has no zone: %r" % value)
        return "TIME '%s'" % _format_time(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "FROM_HEX('%s')" % bytes(value).hex()
    if isinstance(value, str):
        return _quote_string(value)
    if isinstance(value, uuid.UUID):
        return _quote_string(str(value))
    raise ProgrammingError(
        "Unsupported parameter type %s for Dremio" % type(value).__name__
    )


def _placeholders(statement):
    """Yield the index of every ``?`` placeholder outside literals and comments."""
    i = 0
    length = len(statement)
    while i < length:
        char = statement[i]
        if char in ("'", '"'):
            # Quoted string or identifier; a doubled quote is an escaped quote.
            i += 1
            while i < length:
                if statement[i] == char:
                    if i + 1 < length and statement[i + 1] == char:
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif char == "-" and statement.startswith("--", i):
            end = statement.find("\n", i)
            i = length if end < 0 else end + 1
        elif char == "/" and statement.startswith("/*", i):
            end = statement.find("*/", i + 2)
            i = length if end < 0 else end + 2
        elif char == "`":
            end = statement.find("`", i + 1)
            i = length if end < 0 else end + 1
        elif char == "?":
            yield i
            i += 1
        else:
            i += 1


def bind(statement, parameters):
    """Render positional ``parameters`` into ``statement``'s ``?`` placeholders."""
    if parameters is None:
        return statement
    if isinstance(parameters, (str, bytes, Mapping)):
        raise ProgrammingError(
            "Dremio uses the qmark paramstyle; parameters must be a sequence"
        )
    parameters = list(parameters)
    if not parameters:
        # No values to bind: send the statement exactly as written.
        return statement
    positions = list(_placeholders(statement))
    if len(positions) != len(parameters):
        raise ProgrammingError(
            "Statement has %d placeholder(s) but %d parameter(s) were supplied"
            % (len(positions), len(parameters))
        )
    pieces = []
    last = 0
    for position, value in zip(positions, parameters):
        pieces.append(statement[last:position])
        pieces.append(render_literal(value))
        last = position + 1
    pieces.append(statement[last:])
    return "".join(pieces)
