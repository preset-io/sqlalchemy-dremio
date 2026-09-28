from sqlalchemy import exc, schema, types, pool, util
from sqlalchemy.engine import default, reflection
from sqlalchemy.sql import compiler, text
from pyarrow import flight

from sqlalchemy_dremio import db as dbapi_module

_dialect_name = "dremio+flight"

# SQLAlchemy 2.0 has DOUBLE; 1.4 does not.
_DOUBLE = getattr(types, "DOUBLE", types.FLOAT)

# Keys are the DATA_TYPE values Dremio reports in INFORMATION_SCHEMA."COLUMNS"
# and DESCRIBE (upper-cased before lookup).
_type_map = {
    'BOOLEAN': types.BOOLEAN,
    'TINYINT': types.SMALLINT,
    'SMALLINT': types.SMALLINT,
    'INT': types.INTEGER,
    'INTEGER': types.INTEGER,
    'BIGINT': types.BIGINT,
    'FLOAT': types.FLOAT,
    'REAL': types.FLOAT,
    'DOUBLE': _DOUBLE,
    'DOUBLE PRECISION': _DOUBLE,
    'DECIMAL': types.DECIMAL,
    'NUMERIC': types.DECIMAL,
    'DATE': types.DATE,
    'TIME': types.TIME,
    'TIMESTAMP': types.TIMESTAMP,
    'CHAR': types.CHAR,
    'CHARACTER': types.CHAR,
    'VARCHAR': types.VARCHAR,
    'CHARACTER VARYING': types.VARCHAR,
    'BINARY': types.VARBINARY,
    'VARBINARY': types.VARBINARY,
    'BINARY VARYING': types.VARBINARY,
    'INTERVAL': types.Interval,
    'ANY': types.NullType,
    'NULL': types.NullType,
    # Complex types have no portable SQLAlchemy equivalent; their values are
    # returned as Python lists/dicts.
    'LIST': types.NullType,
    'ARRAY': types.NullType,
    'STRUCT': types.NullType,
    'ROW': types.NullType,
    'MAP': types.NullType,
}


def _resolve_type(data_type, precision=None, scale=None):
    name = (data_type or '').strip().upper()
    if name.startswith('INTERVAL'):
        return types.Interval()
    base = name.split('(', 1)[0].strip()
    type_cls = _type_map.get(base)
    if type_cls is None:
        util.warn("Did not recognize Dremio type '%s'" % data_type)
        return types.NullType()
    if type_cls is types.DECIMAL:
        return types.DECIMAL(precision=precision, scale=scale)
    return type_cls()


def _string_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


class DremioExecutionContext(default.DefaultExecutionContext):
    pass


class DremioCompiler(compiler.SQLCompiler):
    def visit_char_length_func(self, fn, **kw):
        return 'length{}'.format(self.function_argspec(fn, **kw))

    def visit_table(self, table, asfrom=False, **kwargs):

        if asfrom:
            if table.schema is not None and table.schema != "":
                fixed_schema = ".".join(["\"" + i.replace('"', '') + "\"" for i in table.schema.split(".")])
                fixed_table = fixed_schema + ".\"" + table.name.replace("\"", "") + "\""
            else:
                fixed_table = "\"" + table.name.replace("\"", "") + "\""
            return fixed_table
        else:
            return ""

    def visit_tablesample(self, tablesample, asfrom=False, **kw):
        print(tablesample)


class DremioDDLCompiler(compiler.DDLCompiler):
    def get_column_specification(self, column, **kwargs):
        colspec = self.preparer.format_column(column)
        colspec += " " + self.dialect.type_compiler.process(column.type)
        if column is column.table._autoincrement_column and \
            True and \
            (
                column.default is None or \
                isinstance(column.default, schema.Sequence)
            ):
            colspec += " IDENTITY"
            if isinstance(column.default, schema.Sequence) and \
                column.default.start > 0:
                colspec += " " + str(column.default.start)
        else:
            default = self.get_column_default_string(column)
            if default is not None:
                colspec += " DEFAULT " + default

        if not column.nullable:
            colspec += " NOT NULL"
        return colspec


class DremioIdentifierPreparer(compiler.IdentifierPreparer):
    reserved_words = compiler.RESERVED_WORDS.copy()
    dremio_reserved = {'abs', 'all', 'allocate', 'allow', 'alter', 'and', 'any', 'are', 'array',
                       'array_max_cardinality', 'as', 'asensitivelo', 'asymmetric', 'at', 'atomic', 'authorization',
                       'avg', 'begin', 'begin_frame', 'begin_partition', 'between', 'bigint', 'binary', 'bit', 'blob',
                       'boolean', 'both', 'by', 'call', 'called', 'cardinality', 'cascaded', 'case', 'cast', 'ceil',
                       'ceiling', 'char', 'char_length', 'character', 'character_length', 'check', 'classifier',
                       'clob', 'close', 'coalesce', 'collate', 'collect', 'column', 'commit', 'condition', 'connect',
                       'constraint', 'contains', 'convert', 'corr', 'corresponding', 'count', 'covar_pop',
                       'covar_samp', 'create', 'cross', 'cube', 'cume_dist', 'current', 'current_catalog',
                       'current_date', 'current_default_transform_group', 'current_path', 'current_role',
                       'current_row', 'current_schema', 'current_time', 'current_timestamp',
                       'current_transform_group_for_type', 'current_user', 'cursor', 'cycle', 'date', 'day',
                       'deallocate', 'dec', 'decimal', 'declare', 'default', 'define', 'delete', 'dense_rank',
                       'deref', 'describe', 'deterministic', 'disallow', 'disconnect', 'distinct', 'double', 'drop',
                       'dynamic', 'each', 'element', 'else', 'empty', 'end', 'end-exec', 'end_frame', 'end_partition',
                       'equals', 'escape', 'every', 'except', 'exec', 'execute', 'exists', 'exp', 'explain', 'extend',
                       'external', 'extract', 'false', 'fetch', 'filter', 'first_value', 'float', 'floor', 'for',
                       'foreign', 'frame_row', 'free', 'from', 'full', 'function', 'fusion', 'get', 'global', 'grant',
                       'group', 'grouping', 'groups', 'having', 'hold', 'hour', 'identity', 'import', 'in',
                       'indicator', 'initial', 'inner', 'inout', 'insensitive', 'insert', 'int', 'integer',
                       'intersect', 'intersection', 'interval', 'into', 'is', 'join', 'lag', 'language', 'large',
                       'last_value', 'lateral', 'lead', 'leading', 'left', 'like', 'like_regex', 'limit', 'ln',
                       'local', 'localtime', 'localtimestamp', 'lower', 'match', 'matches', 'match_number',
                       'match_recognize', 'max', 'measures', 'member', 'merge', 'method', 'min', 'minute', 'mod',
                       'modifies', 'module', 'month', 'more', 'multiset', 'national', 'natural', 'nchar', 'nclob',
                       'new', 'next', 'no', 'none', 'normalize', 'not', 'nth_value', 'ntile', 'null', 'nullif',
                       'numeric', 'occurrences_regex', 'octet_length', 'of', 'offset', 'old', 'omit', 'on', 'one',
                       'only', 'open', 'or', 'order', 'out', 'outer', 'over', 'overlaps', 'overlay', 'parameter',
                       'partition', 'pattern', 'per', 'percent', 'percentile_cont', 'percentile_disc', 'percent_rank',
                       'period', 'permute', 'portion', 'position', 'position_regex', 'power', 'precedes', 'precision',
                       'prepare', 'prev', 'primary', 'procedure', 'range', 'rank', 'reads', 'real', 'recursive',
                       'ref', 'references', 'referencing', 'regr_avgx', 'regr_avgy', 'regr_count', 'regr_intercept',
                       'regr_r2', 'regr_slope', 'regr_sxx', 'regr_sxy', 'regr_syy', 'release', 'reset', 'result',
                       'return', 'returns', 'revoke', 'right', 'rollback', 'rollup', 'row', 'row_number', 'rows',
                       'running', 'savepoint', 'scope', 'scroll', 'search', 'second', 'seek', 'select', 'sensitive',
                       'session_user', 'set', 'minus', 'show', 'similar', 'skip', 'smallint', 'some', 'specific',
                       'specifictype', 'sql', 'sqlexception', 'sqlstate', 'sqlwarning', 'sqrt', 'start', 'static',
                       'stddev_pop', 'stddev_samp', 'stream', 'submultiset', 'subset', 'substring', 'substring_regex',
                       'succeeds', 'sum', 'symmetric', 'system', 'system_time', 'system_user', 'table', 'tablesample',
                       'then', 'time', 'timestamp', 'timezone_hour', 'timezone_minute', 'tinyint', 'to', 'trailing',
                       'translate', 'translate_regex', 'translation', 'treat', 'trigger', 'trim', 'trim_array',
                       'true', 'truncate', 'uescape', 'union', 'unique', 'unknown', 'unnest', 'update', 'upper',
                       'upsert', 'user', 'using', 'value', 'values', 'value_of', 'var_pop', 'var_samp', 'varbinary',
                       'varchar', 'varying', 'versioning', 'when', 'whenever', 'where', 'width_bucket', 'window',
                       'with', 'within', 'without', 'year'}

    dremio_unique = dremio_reserved - reserved_words
    reserved_words.update(list(dremio_unique))

    def __init__(self, dialect):
        super(DremioIdentifierPreparer, self). \
            __init__(dialect, initial_quote='"', final_quote='"')


class DremioExecutionContext_flight(DremioExecutionContext):
    pass


class DremioDialect_flight(default.DefaultDialect):

    name = _dialect_name
    driver = _dialect_name
    supports_statement_cache = True
    supports_sane_rowcount = False
    supports_sane_multi_rowcount = False
    # The driver returns decimal.Decimal for DECIMAL columns.
    supports_native_decimal = True
    supports_native_boolean = True
    poolclass = pool.SingletonThreadPool
    statement_compiler = DremioCompiler
    ddl_compiler = DremioDDLCompiler
    preparer = DremioIdentifierPreparer
    execution_ctx_cls = DremioExecutionContext
    # Parameters are rendered client-side by the DB-API module (qmark).
    default_paramstyle = 'qmark'

    def create_connect_args(self, url):
        opts = url.translate_connect_args(username='user')
        properties = {'HOST': opts['host'], 'PORT': opts['port']}

        if 'user' in opts:
            properties['UID'] = opts['user']
            properties['PWD'] = opts.get('password', '')

        if 'database' in opts:
            properties['Schema'] = opts['database']

        # Clone the query dictionary with lower-case keys.
        lc_query_dict = {k.lower(): v for k, v in url.query.items()}

        for property_name in ('UseEncryption', 'DisableCertificateVerification', 'TrustedCerts',
                              'routing_queue', 'routing_tag', 'quoting', 'routing_engine',
                              'Token'):
            if property_name.lower() in lc_query_dict:
                properties[property_name] = lc_query_dict[property_name.lower()]

        # Keyword properties, not a ";"-joined string, so any value (for
        # example a password containing ";" or "=") reaches Flight intact.
        return [[], properties]

    @classmethod
    def import_dbapi(cls):
        import sqlalchemy_dremio.db as module
        return module

    @classmethod
    def dbapi(cls):
        # SQLAlchemy 1.4 entry point; 2.x calls import_dbapi() instead.
        return cls.import_dbapi()

    def is_disconnect(self, e, connection, cursor):
        if isinstance(e, dbapi_module.InterfaceError):
            return 'closed' in str(e)
        if isinstance(e, dbapi_module.OperationalError):
            return isinstance(e.__cause__, flight.FlightUnavailableError)
        return False

    def get_indexes(self, connection, table_name, schema=None, **kw):
        return []

    def get_pk_constraint(self, connection, table_name, schema=None, **kw):
        return {"constrained_columns": [], "name": None}

    def get_foreign_keys(self, connection, table_name, schema=None, **kw):
        return []

    def _columns_from_information_schema(self, connection, table_name, schema):
        sql = (
            'SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE '
            'FROM INFORMATION_SCHEMA."COLUMNS" '
            'WHERE TABLE_SCHEMA = {0} AND TABLE_NAME = {1} '
            'ORDER BY ORDINAL_POSITION'
        ).format(_string_literal(schema), _string_literal(table_name))
        return [tuple(row) for row in connection.execute(text(sql))]

    def _columns_from_describe(self, connection, table_name):
        # No schema: let Dremio resolve the name against the session context
        # (the URL's Schema), as DESCRIBE always has.
        result = connection.execute(
            text('DESCRIBE {0}'.format(_quote_identifier(table_name))))
        keys = [k.upper() for k in result.keys()]
        out = []
        for row in result:
            record = dict(zip(keys, row))
            out.append((
                record.get('COLUMN_NAME', row[0]),
                record.get('DATA_TYPE', row[1]),
                record.get('NUMERIC_PRECISION'),
                record.get('NUMERIC_SCALE'),
                record.get('IS_NULLABLE', 'YES'),
            ))
        return out

    @reflection.cache
    def get_columns(self, connection, table_name, schema=None, **kw):
        if schema:
            described = self._columns_from_information_schema(connection, table_name, schema)
        else:
            try:
                described = self._columns_from_describe(connection, table_name)
            except exc.DBAPIError:
                if not self.has_table(connection, table_name, schema):
                    raise exc.NoSuchTableError(table_name)
                raise
        if not described:
            raise exc.NoSuchTableError(
                table_name if not schema else '{0}.{1}'.format(schema, table_name))
        result = []
        for name, data_type, precision, scale, nullable in described:
            result.append({
                "name": name,
                "type": _resolve_type(data_type, precision, scale),
                "default": None,
                "comment": None,
                "nullable": str(nullable).upper() != 'NO',
            })
        return result

    def _table_names(self, connection, schema, views):
        sql = 'SELECT TABLE_NAME FROM INFORMATION_SCHEMA."TABLES" WHERE TABLE_TYPE {0} \'VIEW\''.format(
            '=' if views else '<>')
        if schema is not None:
            sql += ' AND TABLE_SCHEMA = ' + _string_literal(schema)
        return [row[0] for row in connection.execute(text(sql))]

    @reflection.cache
    def get_table_names(self, connection, schema=None, **kw):
        return self._table_names(connection, schema, views=False)

    @reflection.cache
    def get_view_names(self, connection, schema=None, **kw):
        return self._table_names(connection, schema, views=True)

    @reflection.cache
    def get_schema_names(self, connection, **kw):
        return [row[0] for row in connection.execute(text('SHOW SCHEMAS'))]

    @reflection.cache
    def has_table(self, connection, table_name, schema=None, **kw):
        sql = 'SELECT COUNT(*) FROM INFORMATION_SCHEMA."TABLES" WHERE TABLE_NAME = ' + \
            _string_literal(table_name)
        if schema is not None and schema != "":
            sql += ' AND TABLE_SCHEMA = ' + _string_literal(schema)
        return connection.execute(text(sql)).scalar() > 0
