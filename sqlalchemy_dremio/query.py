from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import pyarrow as pa
from pyarrow import flight


def _type_name(arrow_type):
    """Dremio SQL type name for an Arrow result column (the DB-API type_code)."""
    types = pa.types
    if types.is_boolean(arrow_type):
        return "BOOLEAN"
    if types.is_int8(arrow_type) or types.is_uint8(arrow_type):
        return "TINYINT"
    if types.is_int16(arrow_type) or types.is_uint16(arrow_type):
        return "SMALLINT"
    if types.is_int32(arrow_type) or types.is_uint32(arrow_type):
        return "INTEGER"
    if types.is_int64(arrow_type) or types.is_uint64(arrow_type):
        return "BIGINT"
    if types.is_float16(arrow_type) or types.is_float32(arrow_type):
        return "FLOAT"
    if types.is_float64(arrow_type):
        return "DOUBLE"
    if types.is_decimal(arrow_type):
        return "DECIMAL"
    if types.is_date(arrow_type):
        return "DATE"
    if types.is_time(arrow_type):
        return "TIME"
    if types.is_timestamp(arrow_type):
        return "TIMESTAMP"
    if types.is_interval(arrow_type) or types.is_duration(arrow_type):
        return "INTERVAL"
    if types.is_string(arrow_type) or types.is_large_string(arrow_type):
        return "VARCHAR"
    if (
        types.is_binary(arrow_type)
        or types.is_large_binary(arrow_type)
        or types.is_fixed_size_binary(arrow_type)
    ):
        return "VARBINARY"
    if types.is_list(arrow_type) or types.is_large_list(arrow_type):
        return "LIST"
    if types.is_struct(arrow_type):
        return "STRUCT"
    if types.is_map(arrow_type):
        return "MAP"
    if types.is_null(arrow_type):
        return "NULL"
    return str(arrow_type).upper()


def run_query(query, flightclient=None, options=None):
    info = flightclient.get_flight_info(flight.FlightDescriptor.for_command(query), options)
    tables = []
    for endpoint in info.endpoints:
        reader = flightclient.do_get(endpoint.ticket, options)
        tables.append(reader.read_all())
    if not tables:
        return info.schema.empty_table()
    return pa.concat_tables(tables) if len(tables) > 1 else tables[0]


def execute(query, flightclient=None, options=None):
    """Run ``query`` and return ``(rows, description)``.

    Values are converted straight from Arrow to Python objects (not through
    pandas), so types are exact: BIGINT stays ``int`` next to NULLs, DECIMAL is
    ``decimal.Decimal``, DATE is ``datetime.date``, TIMESTAMP of any unit is
    ``datetime.datetime`` and NULL is ``None``.
    """
    table = run_query(query, flightclient, options)
    columns = [column.to_pylist() for column in table.columns]
    rows = [list(row) for row in zip(*columns)]

    description = []
    for field in table.schema:
        precision = getattr(field.type, "precision", None)
        scale = getattr(field.type, "scale", None)
        description.append(
            (field.name, _type_name(field.type), None, None, precision, scale, field.nullable)
        )

    return rows, description
