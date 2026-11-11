"""Test that every reachable grammar reference resolves in each dialect.

Dialect grammars are built by inheritance: a dialect starts from ANSI (or
another parent) and overrides individual segments. A grammar can therefore
refer to a keyword or segment which the dialect never registers. Nothing
catches that until a user's SQL reaches the offending branch, at which point
:meth:`Dialect.ref` raises a ``RuntimeError`` and the user gets an internal
error instead of a parse result or a ``PRS`` violation.

This module walks each dialect's grammar from its root segment and checks
that every ``Ref`` it can reach resolves against the dialect's library.
"""

from collections.abc import Iterator

import pytest

from sqlfluff.core.dialects import dialect_readout, dialect_selector
from sqlfluff.core.dialects.base import Dialect
from sqlfluff.core.parser.grammar.base import BaseGrammar, Ref
from sqlfluff.core.parser.segments.base import BaseSegment

# Refs which are reachable from the root segment of a dialect but which that
# dialect never registers. Every entry here is a latent ``RuntimeError``: it
# will be raised as soon as a user's SQL reaches the offending branch. The
# list should only ever shrink - fix the grammar and remove the entry rather
# than adding new ones.
UNRESOLVED_REFS: dict[str, set[str]] = {
    "bigquery": {
        # Inherited from ANSI: `AccessObjectSegment`/`AccessPermissionSegment`
        # reference CATALOG, and the trigger/cast grammar references keywords
        # BigQuery doesn't have. `AccessSchemaPluralObjectSegment` is also
        # inherited without BigQuery's own object names.
        "ActionKeywordSegment",
        "BeforeKeywordSegment",
        "CatalogKeywordSegment",
        "ConstructorKeywordSegment",
        "DeferrableKeywordSegment",
        "DeferredKeywordSegment",
        "EachKeywordSegment",
        "FormatsKeywordSegment",
        "FunctionsKeywordSegment",
        "InitiallyKeywordSegment",
        "InstanceKeywordSegment",
        "InsteadKeywordSegment",
        "MethodKeywordSegment",
        "OldKeywordSegment",
        "PoliciesKeywordSegment",
        "ProceduresKeywordSegment",
        "ReferencingKeywordSegment",
        "RoutinesKeywordSegment",
        "SequencesKeywordSegment",
        "SpecificKeywordSegment",
        "StagesKeywordSegment",
        "StatementKeywordSegment",
        "StaticKeywordSegment",
        "StreamsKeywordSegment",
        "TablesKeywordSegment",
        "TasksKeywordSegment",
        "ViewsKeywordSegment",
    },
    "clickhouse": {
        # Inherited access/alter grammar uses keywords ClickHouse never
        # registers.
        "ManagementKeywordSegment",
        "RefreshKeywordSegment",
        "SettingKeywordSegment",
    },
    "duckdb": {
        # Inherited from Postgres.
        "Current_sessionKeywordSegment",
        "ExecutionKeywordSegment",
    },
    "greenplum": {
        # Inherited from Postgres.
        "Current_sessionKeywordSegment",
        "ExecutionKeywordSegment",
    },
    "impala": {
        # Inherited CREATE TABLE grammar references an `=` segment and a pool
        # name reference Impala doesn't define.
        "=KeywordSegment",
        "PoolNameReferenceSegment",
    },
    "materialize": {
        # Inherited from Postgres.
        "ConcurrentlyKeywordSegment",
    },
    "oracle": {
        # Inherited ANSI access grammar plus Oracle's own CREATE USER /
        # function grammar use keywords Oracle never registers.
        "AuthenticationKeywordSegment",
        "BuildKeywordSegment",
        "CostKeywordSegment",
        "CredentialKeywordSegment",
        "ExceptionsKeywordSegment",
        "FirewallKeywordSegment",
        "FolderKeywordSegment",
        "GrainedKeywordSegment",
        "GraphKeywordSegment",
        "HashKeywordSegment",
        "ManagementKeywordSegment",
        "MetadataKeywordSegment",
        "NotificationKeywordSegment",
        "RetentionKeywordSegment",
        "ScalarKeywordSegment",
        "SettingsKeywordSegment",
        "StepKeywordSegment",
        "TuningKeywordSegment",
        "TypenameKeywordSegment",
    },
    "postgres": {
        # Inherited from ANSI.
        "Current_sessionKeywordSegment",
        "ExecutionKeywordSegment",
    },
    "redshift": {
        # Inherited from Postgres, which clears the ANSI keyword sets.
        "FormatsKeywordSegment",
        "PoliciesKeywordSegment",
    },
    "snowflake": {
        # Inherited CREATE CAST / CREATE TRIGGER / FETCH grammar.
        "AssignmentKeywordSegment",
        "ConstructorKeywordSegment",
        "EachKeywordSegment",
        "InstanceKeywordSegment",
        "InsteadKeywordSegment",
        "MethodKeywordSegment",
        "NewKeywordSegment",
        "OldKeywordSegment",
        "ReferencingKeywordSegment",
        "SpecificKeywordSegment",
        "StaticKeywordSegment",
        "TiesKeywordSegment",
    },
    "sqlite": {
        # SQLite replaces the ANSI keyword sets, so inherited CREATE TABLE ...
        # AS grammar (`WithNoSchemaBindingClauseSegment`,
        # `WithDataClauseSegment`) and the inherited MERGE grammar reference
        # keywords it never registers.
        "BindingKeywordSegment",
        "DataKeywordSegment",
        "MatchedKeywordSegment",
        "SchemaKeywordSegment",
    },
}

# The dialects to test, one parametrised case each.
DIALECT_LABELS = [dialect.label for dialect in dialect_readout()]


def _grammar_children(grammar: BaseGrammar) -> Iterator[object]:
    """Yield the matchables nested directly within a grammar.

    ``Ref`` and ``BaseGrammar`` store their common children on the same
    attributes. A few grammars hold additional matchables on their own
    attributes, which are listed here so the crawl doesn't miss them.
    """
    yield from getattr(grammar, "_elements", ())
    yield from getattr(grammar, "terminators", ())
    for attr in (
        "exclude",  # Ref & AnyNumberOf
        "delimiter",  # Delimited
        "start_bracket",  # Bracketed
        "end_bracket",  # Bracketed
        "_meta",  # Conditional
    ):
        yield getattr(grammar, attr, None)


def _unresolved_refs(dialect: Dialect) -> set[str]:
    """Return the names of all refs reachable from the dialect's root."""
    unresolved: set[str] = set()
    # Guard against recursion: grammars are a graph, not a tree, and a Ref
    # may point back at a grammar we're already walking.
    seen_grammars: set[int] = set()
    seen_segments: set[type[BaseSegment]] = set()
    seen_refs: set[str] = set()

    def visit(matchable: object) -> None:
        if matchable is None:
            return
        if isinstance(matchable, type):
            # Segment classes are matchables too. Follow their grammar.
            if not issubclass(matchable, BaseSegment):
                return
            if matchable in seen_segments:
                return
            seen_segments.add(matchable)
            visit(getattr(matchable, "match_grammar", None))
            return
        if isinstance(matchable, Ref):
            name = matchable._ref
            if name in seen_refs:
                pass
            else:
                seen_refs.add(name)
                try:
                    # Resolve it exactly as the parser would at runtime.
                    target = dialect.ref(name)
                except RuntimeError:
                    unresolved.add(name)
                else:
                    visit(target)
            # Refs also carry an exclude pattern and terminators.
            for child in _grammar_children(matchable):
                visit(child)
            return
        if isinstance(matchable, BaseGrammar):
            if id(matchable) in seen_grammars:
                return
            seen_grammars.add(id(matchable))
            for child in _grammar_children(matchable):
                visit(child)
            return
        # Parsers and other matchables (e.g. PrecededByMatcher) hold no refs.

    visit(dialect.get_root_segment())
    return unresolved


@pytest.mark.parametrize("dialect_label", DIALECT_LABELS)
def test__dialect__grammar_refs_resolve(dialect_label: str) -> None:
    """Every Ref reachable from a dialect's root segment should resolve."""
    unresolved = _unresolved_refs(dialect_selector(dialect_label))
    expected = UNRESOLVED_REFS.get(dialect_label, set())

    newly_broken = unresolved - expected
    assert not newly_broken, (
        f"{dialect_label} grammar refers to elements which are not registered "
        f"in the dialect: {sorted(newly_broken)}. These will raise a "
        "RuntimeError when a user's SQL reaches them."
    )

    fixed = expected - unresolved
    assert not fixed, (
        f"{dialect_label} no longer fails to resolve {sorted(fixed)}. Remove "
        "them from UNRESOLVED_REFS so the allowlist keeps shrinking."
    )
