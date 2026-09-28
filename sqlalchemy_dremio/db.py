from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging
import weakref

import pyarrow as pa
from pyarrow import flight

from sqlalchemy_dremio.exceptions import (  # noqa: F401 - DB-API module globals
    DatabaseError,
    DataError,
    Error,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
    Warning,
)
from sqlalchemy_dremio.flight_middleware import CookieMiddlewareFactory
from sqlalchemy_dremio.params import bind
from sqlalchemy_dremio.query import execute

logger = logging.getLogger(__name__)

apilevel = '2.0'
threadsafety = 1
paramstyle = 'qmark'


class DBAPITypeObject(object):
    """PEP 249 type object comparing equal to the Dremio type names in description."""

    def __init__(self, *values):
        self.values = frozenset(values)

    def __eq__(self, other):
        return other in self.values

    def __ne__(self, other):
        return other not in self.values

    def __hash__(self):
        return hash(self.values)


STRING = DBAPITypeObject('VARCHAR')
BINARY = DBAPITypeObject('VARBINARY')
NUMBER = DBAPITypeObject('TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'FLOAT', 'DOUBLE',
                         'DECIMAL', 'BOOLEAN')
DATETIME = DBAPITypeObject('DATE', 'TIME', 'TIMESTAMP')
ROWID = DBAPITypeObject()


def translate_error(exc):
    """Map an Arrow/Flight exception to the matching DB-API exception class."""
    if isinstance(exc, Error):
        return exc
    if isinstance(exc, (flight.FlightUnavailableError, flight.FlightTimedOutError,
                        flight.FlightCancelledError, flight.FlightUnauthenticatedError)):
        cls = OperationalError
    elif isinstance(exc, flight.FlightUnauthorizedError):
        cls = ProgrammingError
    elif isinstance(exc, pa.ArrowNotImplementedError):
        cls = NotSupportedError
    elif isinstance(exc, (pa.ArrowInvalid, pa.ArrowKeyError, pa.ArrowIndexError,
                          pa.ArrowTypeError)):
        # Flight maps gRPC INVALID_ARGUMENT (parse errors, unknown objects,
        # bad casts) to ArrowInvalid.
        cls = ProgrammingError
    else:
        cls = DatabaseError
    error = cls(str(exc))
    error.__cause__ = exc
    return error


def connect(c=None, **properties):
    return Connection(c, **properties)


def check_closed(f):
    """Decorator that checks if connection/cursor is closed."""

    def g(self, *args, **kwargs):
        if self.closed:
            raise InterfaceError(
                '{klass} already closed'.format(klass=self.__class__.__name__))
        return f(self, *args, **kwargs)

    return g


def check_result(f):
    """Decorator that checks if the cursor has results from `execute`."""

    def d(self, *args, **kwargs):
        if self._results is None:
            raise Error('Called before `execute`')
        return f(self, *args, **kwargs)

    return d


def parse_connection_string(connection_string):
    """Parse the legacy ``KEY=value;KEY=value`` form.

    A value cannot contain ``;``; pass keyword properties to ``connect`` instead.
    """
    properties = {}
    for kvpair in connection_string.split(";"):
        if not kvpair:
            continue
        kv = kvpair.split("=", 1)
        properties[kv[0]] = kv[1]
    return properties


class Connection(object):

    def __init__(self, connection_string=None, **properties):
        # Properties come from DremioDialect_flight.create_connect_args() as
        # keyword arguments, so values such as passwords may contain any
        # character. A legacy semicolon-delimited string is still accepted.
        if connection_string:
            properties = dict(parse_connection_string(connection_string), **properties)

        connection_args = {}

        # Connect to the server endpoint with an encrypted TLS connection by default.
        protocol = 'tls'
        if 'UseEncryption' in properties and str(properties['UseEncryption']).lower() == 'false':
            protocol = 'tcp'
        else:
            # Specify the trusted certificates
            connection_args['disable_server_verification'] = False
            if 'TrustedCerts' in properties:
                with open(properties['TrustedCerts'], "rb") as root_certs:
                    connection_args["tls_root_certs"] = root_certs.read()
            # Or disable server verification entirely
            elif 'DisableCertificateVerification' in properties and \
                    str(properties['DisableCertificateVerification']).lower() == 'true':
                connection_args['disable_server_verification'] = True

        # Enabling cookie middleware for stateful connectivity.
        client_cookie_middleware = CookieMiddlewareFactory()

        client = flight.FlightClient('grpc+{0}://{1}:{2}'.format(protocol, properties['HOST'], properties['PORT']),
            middleware=[client_cookie_middleware], **connection_args)

        # Authenticate either using basic username/password or using the Token parameter.
        headers = []
        try:
            if 'UID' in properties:
                bearer_token = client.authenticate_basic_token(properties['UID'], properties['PWD'])
                headers.append(bearer_token)
            else:
                headers.append((b'authorization', "Bearer {}".format(properties['Token']).encode('utf-8')))
        except Exception as exc:
            _close_client(client)
            raise translate_error(exc)

        # Propagate Dremio-specific headers.
        def add_header(properties, headers, header_name):
            if header_name in properties:
                headers.append((header_name.lower().encode('utf-8'), properties[header_name].encode('utf-8')))

        add_header(properties, headers, 'Schema')
        add_header(properties, headers, 'routing_queue')
        add_header(properties, headers, 'routing_tag')
        add_header(properties, headers, 'quoting')
        add_header(properties, headers, 'routing_engine')

        self.flightclient = client
        self.options = flight.FlightCallOptions(headers=headers)

        self.closed = False
        self.cursors = weakref.WeakSet()

    @check_closed
    def rollback(self):
        pass

    @check_closed
    def close(self):
        """Close the connection now, releasing its Flight client."""
        self.closed = True
        for cursor in list(self.cursors):
            try:
                cursor.close()
            except Error:
                pass  # already closed
        _close_client(self.flightclient)

    @check_closed
    def commit(self):
        pass

    @check_closed
    def cursor(self):
        """Return a new Cursor Object using the connection."""
        cursor = Cursor(self.flightclient, self.options)
        self.cursors.add(cursor)

        return cursor

    @check_closed
    def execute(self, query, params=None):
        cursor = self.cursor()
        return cursor.execute(query, params)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.commit()  # no-op
        self.close()


def _close_client(client):
    close = getattr(client, 'close', None)
    if close is not None:
        try:
            close()
        except Exception:  # pragma: no cover - best effort on shutdown
            logger.debug('Error closing Flight client', exc_info=True)


class Cursor(object):
    """Connection cursor."""

    def __init__(self, flightclient=None, options=None):
        self.flightclient = flightclient
        self.options = options

        # This read/write attribute specifies the number of rows to fetch at a
        # time with .fetchmany(). It defaults to 1 meaning to fetch a single
        # row at a time.
        self.arraysize = 1

        self.closed = False

        # this is updated only after a query
        self.description = None

        # this is set to a list of rows after a successful query
        self._results = None

    @property
    @check_result
    @check_closed
    def rowcount(self):
        return len(self._results)

    @check_closed
    def close(self):
        """Close the cursor."""
        self.closed = True

    @check_closed
    def execute(self, query, params=None):
        self.description = None
        self._results = None
        query = bind(query, params)
        try:
            self._results, self.description = execute(
                query, self.flightclient, self.options)
        except Exception as exc:
            raise translate_error(exc)
        return self

    @check_closed
    def executemany(self, query, seq_of_parameters=None):
        """Compatibility wrapper for DBAPI executemany.

        ``df.to_sql`` and other helpers expect the ``executemany`` method to
        accept the SQL statement and a sequence of parameters.  Dremio does not
        support parameterized execution, so this method simply raises a
        ``NotSupportedError`` regardless of the parameters passed.
        """

        raise NotSupportedError(
            '`executemany` is not supported, use `execute` instead')

    @check_result
    @check_closed
    def fetchone(self):
        """
        Fetch the next row of a query result set, returning a single sequence,
        or `None` when no more data is available.
        """
        try:
            return self._results.pop(0)
        except IndexError:
            return None

    @check_result
    @check_closed
    def fetchmany(self, size=None):
        """
        Fetch the next set of rows of a query result, returning a sequence of
        sequences (e.g. a list of tuples). An empty sequence is returned when
        no more rows are available.
        """
        size = size or self.arraysize
        out = self._results[:size]
        self._results = self._results[size:]
        return out

    @check_result
    @check_closed
    def fetchall(self):
        """
        Fetch all (remaining) rows of a query result, returning them as a
        sequence of sequences (e.g. a list of tuples). Note that the cursor's
        arraysize attribute can affect the performance of this operation.
        """
        out = self._results[:]
        self._results = []
        return out

    @check_closed
    def setinputsizes(self, sizes):
        # not supported
        pass

    @check_closed
    def setoutputsizes(self, sizes):
        # not supported
        pass

    @check_closed
    def __iter__(self):
        return iter(self._results)
