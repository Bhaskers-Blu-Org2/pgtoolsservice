# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from typing import Callable, Dict # noqa
from psycopg2 import sql

from pgsqltoolsservice.edit_data.update_management import RowEdit, RowUpdate # noqa
from pgsqltoolsservice.query_execution.result_set import ResultSet # noqa
from pgsqltoolsservice.edit_data.contracts import (
    EditCellResponse, InitializeEditParams, EditInitializerFilter, EditRow, EditCell)
from pgsqltoolsservice.edit_data import SmoEditTableMetadataFactory, EditTableMetadata
from pgsqltoolsservice.query_execution.query import ExecutionState, Query
from pgsqltoolsservice.query_execution.contracts.common import ResultSetSubset


class DataEditSessionExecutionState:

    def __init__(self, query: Query, message: str=None):
        self.query = query
        self.message = message


class DataEditorSession():
    """ This class will hold the logic to maintain the edit session and handle the operations """

    def __init__(self, metadata_factory: SmoEditTableMetadataFactory):
        self._session_cache: Dict[int, RowEdit] = {}
        self._metadata_factory = metadata_factory
        self._next_row_id = None
        self._is_initialized = False

        self._result_set: ResultSet = None
        self.table_metadata: EditTableMetadata = None

    def initialize(self, initailize_edit_params: InitializeEditParams, connection: 'psycopg2.extensions.connection',
                   query_executer: Callable, on_success: Callable, on_failure: Callable):
        """ This method creates the metadata for the object to be edited and creates the query to be
        executed and calls query executer with it """

        self.table_metadata = self._metadata_factory.get(
            connection, initailize_edit_params.schema_name, initailize_edit_params.object_name,
            initailize_edit_params.object_type)

        query_executer(self._construct_initialize_query(connection,
                       self.table_metadata, initailize_edit_params.filters),
                       lambda execution_state: self.on_query_execution_complete(execution_state, on_success, on_failure))

    def on_query_execution_complete(self, execution_state: DataEditSessionExecutionState, on_success: Callable, on_failure: Callable):
        try:
            if execution_state.query is None:
                message = execution_state.message
                raise Exception(message)

            self._validate_query_for_session(execution_state.query)
            self._result_set = execution_state.query.batches[0].result_set
            self._next_row_id = self._result_set.row_count
            self._is_initialized = True
            self.table_metadata.extend(self._result_set.columns)

            on_success()

        except Exception as err:
            on_failure(err)

    def update_cell(self, row_id: int, column_index: int, new_value: str) -> EditCellResponse:

        edit_row = self._session_cache.get(row_id)

        if edit_row is None:
            edit_row = RowUpdate(row_id, self._result_set, self.table_metadata)
            self._session_cache[row_id] = edit_row

        result = edit_row.set_cell_value(column_index, new_value)

        return result

    def _validate_query_for_session(self, query: Query):

        if query.execution_state is not ExecutionState.EXECUTED:
            raise Exception('Execution not completed')

    def _construct_initialize_query(self, connection: 'psycopg2.extensions.connection', metadata: EditTableMetadata, filters: EditInitializerFilter):
        column_names = [sql.Identifier(column.escaped_name) for column in metadata.column_metadata]

        if filters.limit_results is not None and filters.limit_results > 0:
            limit_clause = ' '.join([' LIMIT', str(filters.limit_results)])

        query = sql.SQL('SELECT {0} FROM {1} {2}').format(
            sql.SQL(', ').join(column_names),
            sql.Identifier(metadata.escaped_multipart_name),
            sql.SQL(limit_clause)
        )
        return query.as_string(connection)

    def get_rows(self, owner_uri, start_index: int, end_index: int):
        if start_index < self._result_set.row_count:
            subset = ResultSetSubset.from_result_set(self._result_set, start_index, end_index)
        else:
            subset = ResultSetSubset()

        edit_rows = []
        for index, row in enumerate(subset.rows):
            row_id = start_index + index
            cache = self._session_cache.get(row_id)
            if cache is not None:
                edit_rows.append(cache.get_edit_row(subset.rows[0]))
            else:
                edit_row = EditRow(row_id, [EditCell(cell.display_value, False) for cell in row])
                edit_rows.append(edit_row)

        return edit_rows
