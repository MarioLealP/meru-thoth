"""The change tracking: every write names who and why, every real change keeps the old version, nothing is deleted.

It lives in the database, not the app, so no code path can skip it - not a buggy endpoint, not a script, not
psql. The app passes who and why per transaction with `thoth.db.write_as`, which sets two settings these functions
read: `thoth.actor` and `thoth.reason`. Deleting needs a third, `thoth.purge = on`, which only Charon's yearly
purge sets. Only Anubis may set status retired (D53).
"""

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

# Columns that change without the thing changing: scans bump last_seen, and the stamp is ours.
_NOT_A_CHANGE = "array['last_seen', 'changed_at', 'changed_by', 'change_reason']"

track_changes = PGFunction(
    schema="public",
    signature="thoth_track_changes()",
    definition=f"""
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    actor text := nullif(current_setting('thoth.actor', true), '');
    reason text := nullif(current_setting('thoth.reason', true), '');
BEGIN
    IF actor IS NULL OR reason IS NULL THEN
        RAISE EXCEPTION 'thoth: a write to % needs thoth.actor and thoth.reason', TG_TABLE_NAME
            USING HINT = 'open the transaction with thoth.db.write_as()';
    END IF;

    -- Only Anubis gives the final verdict (D53).
    IF TG_OP <> 'DELETE' AND NEW.status = 'retired' AND actor <> 'anubis'
       AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'retired') THEN
        RAISE EXCEPTION 'thoth: only anubis retires (% tried to retire a % row)', actor, TG_TABLE_NAME;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF current_setting('thoth.purge', true) IS DISTINCT FROM 'on' THEN
            RAISE EXCEPTION 'thoth: % rows are never deleted - set status to retired', TG_TABLE_NAME;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF to_jsonb(NEW) - {_NOT_A_CHANGE} = to_jsonb(OLD) - {_NOT_A_CHANGE} THEN
            RETURN NEW;  -- only last_seen moved: same version, keep its stamp
        END IF;
        EXECUTE format('INSERT INTO %1$I SELECT (jsonb_populate_record(null::%1$I, $1)).*', TG_TABLE_NAME || '_history')
            USING to_jsonb(OLD) || jsonb_build_object(
                'history_id', gen_random_uuid(), 'valid_from', OLD.changed_at, 'valid_to', now());
    END IF;

    NEW.changed_at := now();
    NEW.changed_by := actor;
    NEW.change_reason := reason;
    RETURN NEW;
END
$$""",
)

append_only = PGFunction(
    schema="public",
    signature="thoth_append_only()",
    definition="""
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('thoth.purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'thoth: % is append-only', TG_TABLE_NAME;
END
$$""",
)


def _trigger(table: str, function: str, events: str) -> PGTrigger:
    return PGTrigger(
        schema="public",
        signature=f"{table}_{function.removeprefix('thoth_')}",
        on_entity=f"public.{table}",
        definition=f"BEFORE {events} ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{function}()",
    )


ENTITIES = [
    track_changes,
    append_only,
    _trigger("item", "thoth_track_changes", "INSERT OR UPDATE OR DELETE"),
    _trigger("relation", "thoth_track_changes", "INSERT OR UPDATE OR DELETE"),
    _trigger("item_history", "thoth_append_only", "UPDATE OR DELETE"),
    _trigger("relation_history", "thoth_append_only", "UPDATE OR DELETE"),
]
