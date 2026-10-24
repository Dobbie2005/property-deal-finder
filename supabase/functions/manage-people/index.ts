// Deal Finder: manage who can sign in (admin only).
// The map calls this with the signed-in user's session. It checks that user is an admin, then uses the
// project's secret key (which never leaves Supabase) to create accounts, set passwords or remove access.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });

const url = Deno.env.get("SUPABASE_URL")!;
const secret = (() => {
  try { return JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") ?? "{}").default; } catch { return undefined; }
})() ?? Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const adminDb = createClient(url, secret, { auth: { persistSession: false, autoRefreshToken: false } });

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return reply({ error: "POST only" }, 405);

  // Who is calling?
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  const { data: who, error: whoErr } = await adminDb.auth.getUser(token);
  if (whoErr || !who?.user?.email) return reply({ error: "Please sign in again." }, 401);
  const me = who.user.email.toLowerCase();
  const { data: people, error: pErr } = await adminDb.rpc("svc_people");
  if (pErr) return reply({ error: pErr.message }, 500);
  const admins = new Set((people ?? []).filter((p: any) => p.is_admin).map((p: any) => p.email));
  if (!admins.has(me)) return reply({ error: "Only an admin can manage people." }, 403);

  let body: any;
  try { body = await req.json(); } catch { return reply({ error: "Bad request" }, 400); }
  const email = String(body.email ?? "").trim().toLowerCase();
  const password = String(body.password ?? "");

  async function listAll() {
    const { data, error } = await adminDb.auth.admin.listUsers({ perPage: 200 });
    if (error) throw error;
    const accounts = new Map(data.users.map((u) => [u.email?.toLowerCase(), u]));
    const { data: ppl } = await adminDb.rpc("svc_people");
    const emails = new Set<string>([...(ppl ?? []).map((p: any) => p.email), ...accounts.keys()].filter(Boolean) as string[]);
    return [...emails].sort().map((e) => {
      const u = accounts.get(e);
      const p = (ppl ?? []).find((x: any) => x.email === e);
      return { email: e, id: u?.id ?? null, has_account: !!u, invited: !!p, is_admin: !!p?.is_admin,
               last_sign_in_at: u?.last_sign_in_at ?? null, created_at: u?.created_at ?? null, me: e === me };
    });
  }
  const findUser = async () => (await listAll()).find((p) => p.email === email);

  try {
    switch (body.action) {
      case "list":
        return reply({ people: await listAll() });

      case "create": {
        if (!EMAIL.test(email)) return reply({ error: "That doesn't look like an email address." }, 400);
        if (password.length < 8) return reply({ error: "The password needs at least 8 characters." }, 400);
        const existing = await findUser();
        if (existing?.has_account) return reply({ error: "That email already has an account. Use “Set password” instead." }, 409);
        const { error: gErr } = await adminDb.rpc("svc_grant", { p_email: email, p_admin: !!body.admin });
        if (gErr) throw gErr;  // invite first: sign-ups are invite-only
        const { error } = await adminDb.auth.admin.createUser({ email, password, email_confirm: true });
        if (error) throw error;
        return reply({ ok: true, people: await listAll() });
      }

      case "set_password": {
        if (password.length < 8) return reply({ error: "The password needs at least 8 characters." }, 400);
        const u = await findUser();
        if (!u?.id) return reply({ error: "No account for that email." }, 404);
        const { error } = await adminDb.auth.admin.updateUserById(u.id, { password });
        if (error) throw error;
        return reply({ ok: true });
      }

      case "set_admin": {
        if (email === me && !body.admin) return reply({ error: "You can't remove your own admin rights." }, 400);
        const { error } = await adminDb.rpc("svc_grant", { p_email: email, p_admin: !!body.admin });
        if (error) throw error;
        return reply({ ok: true, people: await listAll() });
      }

      case "remove": {
        if (email === me) return reply({ error: "You can't remove yourself." }, 400);
        const u = await findUser();
        if (u?.id) {
          const { error } = await adminDb.auth.admin.deleteUser(u.id);
          if (error) throw error;
        }
        const { error } = await adminDb.rpc("svc_revoke", { p_email: email });
        if (error) throw error;
        return reply({ ok: true, people: await listAll() });
      }

      default:
        return reply({ error: "Unknown action" }, 400);
    }
  } catch (e) {
    return reply({ error: (e as Error).message ?? String(e) }, 500);
  }
});
