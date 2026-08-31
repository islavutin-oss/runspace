-- exec_sql — raw-SQL bridge for helpers.supabase_db.
--
-- Apply once per Supabase project that uses the SqlConn facade.
-- Lets the supabase-py client run an arbitrary statement via
-- client.rpc('exec_sql', {'q': ...}): SELECT/WITH and RETURNING
-- statements return a JSON array of rows; plain DML returns
-- [{"rowcount": N}].
--
-- SECURITY: arbitrary SQL — execute is granted ONLY to service_role
-- (a server-side secret key). anon / authenticated (public frontend
-- keys) are revoked.

create or replace function exec_sql(q text)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  res json;
  n   integer;
begin
  if q ~* '^\s*(select|with)\s' then
    execute format('select coalesce(json_agg(t), ''[]''::json) from (%s) t', q)
      into res;
    return res;
  elsif q ~* '\mreturning\M' then
    execute format('with _t as (%s) select coalesce(json_agg(_t), ''[]''::json) from _t', q)
      into res;
    return res;
  else
    execute q;
    get diagnostics n = row_count;
    return json_build_array(json_build_object('rowcount', n));
  end if;
end;
$$;

revoke all on function exec_sql(text) from public;
revoke all on function exec_sql(text) from anon;
revoke all on function exec_sql(text) from authenticated;
grant execute on function exec_sql(text) to service_role;
