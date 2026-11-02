-- Run this once in Supabase's SQL editor (Project > SQL Editor > New query > paste > Run).
-- It only CREATES a function; it does not touch any existing table, row, or data.
--
-- Why: every write in this app currently does "read the whole invoice row, change one
-- field in memory, write the whole row back." If anything else touches that same row
-- between the read and the write (e.g. someone has the invoice open in board.html and
-- saves an unrelated edit using a copy of the data they loaded earlier), the later write
-- silently overwrites the earlier one. Confirmed live Nov 1 2026: two real Stripe webhook
-- events for extension payments (#202092-EXT1, -EXT2) fired and sent receipts, but the
-- `paid` flag never stuck in the stored data.
--
-- This function makes marking one extension paid a single atomic database statement --
-- the read, the change, and the write happen as one step the database guarantees can't be
-- interleaved with another transaction, closing the race for this specific operation.
--
-- api/webhook.py calls this via Supabase's RPC endpoint (POST .../rest/v1/rpc/mark_extension_paid)
-- and falls back to the old read-modify-write automatically if this function doesn't exist
-- yet, so deploying the Python change is safe whether or not this has been run.
--
-- Nov 2, 2026 update: added p_is_test (fix spec Batch A #6 — tag test-mode Stripe charges
-- at creation so they can be excluded from the Tax Report/Finance/Cleanup Review). The old
-- 6-argument version is dropped first so this cleanly replaces it instead of leaving two
-- overloads behind — safe to re-run; it only ever affects this one function definition.
drop function if exists mark_extension_paid(text, text, text, text, numeric, text);

create or replace function mark_extension_paid(
  p_invnum text,
  p_ext_invnum text,
  p_paid_date text,
  p_payment_id text default null,
  p_amount numeric default null,
  p_manual_method text default null,
  p_is_test boolean default null
) returns jsonb
language plpgsql
as $$
declare
  result_data jsonb;
  found_entry boolean;
begin
  select exists(
    select 1
    from invoices, jsonb_array_elements(coalesce(data->'extensions', '[]'::jsonb)) as ext
    where invnum = p_invnum and ext->>'invnum' = p_ext_invnum
  ) into found_entry;

  if not found_entry then
    raise exception 'Extension % not found on invoice %', p_ext_invnum, p_invnum;
  end if;

  update invoices
  set data = jsonb_set(
    data,
    '{extensions}',
    (
      select jsonb_agg(
        case
          when ext->>'invnum' = p_ext_invnum then
            ext
            || jsonb_build_object('paid', true, 'paid_date', p_paid_date)
            || case when p_payment_id is not null then jsonb_build_object('payment_id', p_payment_id) else '{}'::jsonb end
            || case when p_amount is not null then jsonb_build_object('amount', p_amount) else '{}'::jsonb end
            || case when p_manual_method is not null then jsonb_build_object('manual_method', p_manual_method) else '{}'::jsonb end
            || case when p_is_test is not null then jsonb_build_object('is_test', p_is_test) else '{}'::jsonb end
          else ext
        end
      )
      from jsonb_array_elements(data->'extensions') as ext
    )
  )
  where invnum = p_invnum
  returning data into result_data;

  return result_data;
end;
$$;

-- Lets the app's anon API key call this function via PostgREST's RPC endpoint (same
-- access level the app already has to read/write the invoices table directly).
grant execute on function mark_extension_paid(text, text, text, text, numeric, text, boolean) to anon;
