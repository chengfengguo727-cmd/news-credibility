import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

if (!url || !publishableKey) {
  throw new Error(
    "Missing NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY — see .env.local.example",
  );
}

// The publishable key (formerly "anon key") is safe to expose in the browser —
// it only grants what RLS allows.
export const supabase = createClient(url, publishableKey, {
  auth: { persistSession: false },
});
