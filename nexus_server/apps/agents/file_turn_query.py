"""Backend-specific JSON membership; authorization remains in the file views.

Both paths require a file block with the exact Run-owned download URL. Text
mentions are not references, and reusing a file never changes its first turn.
"""
from django.db import connections, NotSupportedError
from django.db.models import CharField, Exists, F, Func, Lookup, OuterRef, Q, Value
from django.db.models.functions import Cast, Concat, JSONObject, Substr


class SQLiteFileReference(Lookup):
    lookup_name = "run_file_reference"

    def as_sqlite(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        # CASE avoids interpreting scalar legacy entries as JSON documents.
        item = "CASE WHEN nexus_file_block.type = 'object' THEN nexus_file_block.value ELSE '{}' END"
        sql = (
            "EXISTS (SELECT 1 FROM json_each(CASE WHEN json_type(" + lhs + ") = 'array' THEN "
            + lhs + " ELSE '[]' END) AS nexus_file_block WHERE json_extract(" + item
            + ", '$.type') = 'file' AND json_extract(" + item + ", '$.url') = " + rhs + ")"
        )
        return sql, [*lhs_params, *lhs_params, *rhs_params]

    def as_sql(self, compiler, connection):
        raise NotSupportedError("Run file references require PostgreSQL or SQLite.")


def input_turn_queryset(queryset, run, turn):
    vendor = connections[queryset.db].vendor
    if vendor == "sqlite":
        # SQLite stores UUIDField values as 32 hex digits, unlike PostgreSQL's
        # hyphenated UUID cast. Match the URL actually persisted in messages.
        identity = Cast(OuterRef("pk"), CharField())
        file_id = Concat(Substr(identity, 1, 8), Value("-"), Substr(identity, 9, 4), Value("-"),
            Substr(identity, 13, 4), Value("-"), Substr(identity, 17, 4), Value("-"), Substr(identity, 21, 12))
        url = Concat(Value(f"/api/v1/agent-runs/{run.pk}/files/"), file_id, Value("/download/"))
        usage = run.messages.filter(SQLiteFileReference(F("content_blocks"), url), turn_index=int(turn))
        return queryset.annotate(_used=Exists(usage)).filter(Q(turn_index=int(turn)) | Q(_used=True))
    if vendor != "postgresql":
        raise NotSupportedError("Run file references require PostgreSQL or SQLite.")
    # Preserve the existing PostgreSQL query (including JSONB containment).
    url = Concat(Value(f"/api/v1/agent-runs/{run.pk}/files/"), Cast(OuterRef("pk"), CharField()), Value("/download/"))
    usage = run.messages.filter(turn_index=int(turn), content_blocks__contains=Func(
        JSONObject(type=Value("file"), url=url), function="jsonb_build_array"))
    queryset = queryset.annotate(_used=Exists(usage)).filter(Q(turn_index=int(turn)) | Q(_used=True))
    return queryset
