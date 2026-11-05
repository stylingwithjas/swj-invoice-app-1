-- Prepared only: install after owner approval, before enabling this application.
-- Does not alter existing invoice values or invoice numbering strings.
BEGIN;
CREATE TABLE IF NOT EXISTS public.invoice_number_state (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  last_number bigint NOT NULL CHECK (last_number > 0)
);
CREATE TABLE IF NOT EXISTS public.invoice_number_reservations (
  request_id uuid PRIMARY KEY,
  invnum text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.invoice_number_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoice_number_reservations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.invoice_number_state, public.invoice_number_reservations FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.invoice_number_state TO service_role;
GRANT SELECT, INSERT ON public.invoice_number_reservations TO service_role;
INSERT INTO public.invoice_number_state(singleton,last_number) VALUES(true,202092) ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION public.reserve_invoice_number(p_request_id uuid, p_requested_invnum text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE
  prior text;
  requested text := nullif(btrim(p_requested_invnum),'');
  last_value bigint;
  existing_max bigint;
  chosen text;
  chosen_base bigint;
BEGIN
  IF p_request_id IS NULL THEN RAISE EXCEPTION 'Reservation request required' USING ERRCODE='22023'; END IF;
  -- Shared row lock serializes all server instances, regular and luxury editors.
  SELECT last_number INTO last_value FROM public.invoice_number_state WHERE singleton=true FOR UPDATE;
  IF last_value IS NULL THEN RAISE EXCEPTION 'Number allocator unavailable'; END IF;
  SELECT invnum INTO prior FROM public.invoice_number_reservations WHERE request_id=p_request_id;
  IF prior IS NOT NULL THEN
    IF requested IS NOT NULL AND requested<>prior THEN RAISE EXCEPTION 'Reservation request changed' USING ERRCODE='22023'; END IF;
    RETURN jsonb_build_object('request_id',p_request_id,'invnum',prior);
  END IF;
  IF requested IS NOT NULL AND requested !~ '^[0-9]{1,15}([vV][0-9]{1,6})?$' THEN
    RAISE EXCEPTION 'Use a numeric staging invoice number with an optional vN revision' USING ERRCODE='22023';
  END IF;
  SELECT coalesce(max(regexp_replace(invnum,'[vV][0-9]+$','')::bigint),0) INTO existing_max
    FROM public.invoices WHERE invnum ~ '^[0-9]{1,15}([vV][0-9]{1,6})?$';
  IF requested IS NULL THEN
    chosen_base:=greatest(last_value,existing_max)+1;
    chosen:=chosen_base::text;
    WHILE EXISTS(SELECT 1 FROM public.invoices WHERE invnum=chosen)
       OR EXISTS(SELECT 1 FROM public.invoice_number_reservations WHERE invnum=chosen) LOOP
      chosen_base:=chosen_base+1; chosen:=chosen_base::text;
    END LOOP;
  ELSE
    chosen:=requested; chosen_base:=regexp_replace(chosen,'[vV][0-9]+$','')::bigint;
    IF chosen_base<=0 THEN RAISE EXCEPTION 'Invoice number must be positive' USING ERRCODE='22023'; END IF;
    IF EXISTS(SELECT 1 FROM public.invoices WHERE invnum=chosen)
      OR EXISTS(SELECT 1 FROM public.invoice_number_reservations WHERE invnum=chosen) THEN
      RAISE EXCEPTION 'Invoice number is already in use; reserve a new number' USING ERRCODE='23505';
    END IF;
  END IF;
  IF chosen_base>999999999999999 THEN RAISE EXCEPTION 'Invoice number out of range' USING ERRCODE='22023'; END IF;
  INSERT INTO public.invoice_number_reservations(request_id,invnum) VALUES(p_request_id,chosen);
  UPDATE public.invoice_number_state SET last_number=greatest(last_value,existing_max,chosen_base) WHERE singleton=true;
  RETURN jsonb_build_object('request_id',p_request_id,'invnum',chosen);
END;
$function$;
REVOKE ALL ON FUNCTION public.reserve_invoice_number(uuid,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_invoice_number(uuid,text) TO service_role;
COMMIT;
