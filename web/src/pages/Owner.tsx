import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, Divider, Drawer,
  FormControlLabel, Grid, IconButton, MenuItem, Paper, Stack, Switch, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs,
  TextField, Tooltip, Typography,
} from "@mui/material";
import CloseOutlined from "@mui/icons-material/CloseOutlined";
import ContentCopyOutlined from "@mui/icons-material/ContentCopyOutlined";
import { billing, owner } from "../api";
import ReceiptLongOutlined from "@mui/icons-material/ReceiptLongOutlined";
import type { AuthUser, Payment, Plan, PurchaseRequest } from "../types";
import { C } from "../theme";
import { AppHeader } from "../components/AppHeader";
import { SectionTitle } from "../components/ui";
import { money } from "../site/Landing";

interface Props { me: AuthUser; mode: string; go: (r: string) => void; onLogout: () => void }
const fmt = (d?: string | null) => (d ? new Date(d).toLocaleString() : "—");
const day = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");

export function Owner({ me, mode, go, onLogout }: Props) {
  const [tab, setTab] = useState(0);
  const [msg, setMsg] = useState<{ sev: "success" | "error" | "info"; text: string } | null>(null);
  const isOwner = me.role === "owner";
  const say = (sev: "success" | "error" | "info", text: string) => setMsg({ sev, text });
  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppHeader me={me} mode={mode} go={go} onLogout={onLogout} active="owner" />
      <Box sx={{ bgcolor: "#fff", borderBottom: `1px solid ${C.line}` }}>
        <Container maxWidth="xl">
          <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable">
            {["Overview", "Requests", "Customers", "Plans & prices", "Payments", "Activity log", "Settings"].map((l) => <Tab key={l} label={l} />)}
          </Tabs>
        </Container>
      </Box>
      <Container maxWidth="xl" sx={{ py: 2.5 }}>
        {msg && <Alert severity={msg.sev} onClose={() => setMsg(null)} sx={{ mb: 2 }}>{msg.text}</Alert>}
        {mode !== "accounts" && <Alert severity="info" sx={{ mb: 2 }}>This server runs without accounts (GSB_AUTH_MODE={mode}). Set GSB_AUTH_MODE=accounts to sell access with credits and subscriptions.</Alert>}
        {tab === 0 && <Overview say={say} />}
        {tab === 1 && <Requests say={say} />}
        {tab === 2 && <Customers me={me} isOwner={isOwner} say={say} />}
        {tab === 3 && <Plans isOwner={isOwner} say={say} />}
        {tab === 4 && <Payments />}
        {tab === 5 && <Activity />}
        {tab === 6 && <Settings isOwner={isOwner} say={say} />}
      </Container>
    </Box>
  );
}

type Say = (sev: "success" | "error" | "info", text: string) => void;

/** Purchase requests (invoice / bank transfer): approve adds the credits and the plan, reject closes it. */
function Requests({ say }: { say: Say }) {
  const [status, setStatus] = useState("pending");
  const [rows, setRows] = useState<PurchaseRequest[]>([]);
  const load = () => owner.requests(status).then(setRows).catch((e) => say("error", e.message));
  useEffect(() => { load(); }, [status]);
  const decide = async (r: PurchaseRequest, approve: boolean) => {
    try { await owner.decide(r.id, approve); say("success", `${r.reference} ${approve ? "approved — credits added" : "rejected"}`); load(); }
    catch (e: any) { say("error", e.message); }
  };
  return (
    <Paper sx={{ overflow: "hidden" }}>
      <Stack direction="row" spacing={1} sx={{ p: 1.5, borderBottom: `1px solid ${C.line}` }}>
        {["pending", "approved", "rejected", ""].map((s) => (
          <Chip key={s || "all"} label={s || "all"} onClick={() => setStatus(s)} color={status === s ? "primary" : "default"} variant={status === s ? "filled" : "outlined"} />
        ))}
      </Stack>
      <Table size="small">
        <TableHead><TableRow><TableCell>Reference</TableCell><TableCell>Customer</TableCell><TableCell>Plan</TableCell><TableCell align="right">Amount</TableCell>
          <TableCell>Requested</TableCell><TableCell>Status</TableCell><TableCell align="right" /></TableRow></TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id}>
              <TableCell sx={{ fontFamily: "ui-monospace, Consolas, monospace", fontWeight: 700 }}>{r.reference}</TableCell>
              <TableCell>{r.name || "—"}<Typography variant="caption" component="div">{r.email}</Typography></TableCell>
              <TableCell>{r.plan_id}</TableCell>
              <TableCell align="right">{money(r.amount, r.currency)}</TableCell>
              <TableCell>{fmt(r.created_at)}</TableCell>
              <TableCell><Chip size="small" label={r.status} color={r.status === "approved" ? "success" : r.status === "pending" ? "warning" : "default"} /></TableCell>
              <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                {r.status === "pending" && <>
                  <Button size="small" variant="contained" onClick={() => decide(r, true)}>Approve</Button>
                  <Button size="small" sx={{ ml: 1, color: C.muted }} onClick={() => decide(r, false)}>Reject</Button>
                </>}
                {r.status === "approved" && <Button size="small" href={billing.receiptUrl(r.id)} startIcon={<ReceiptLongOutlined />}>Receipt</Button>}
              </TableCell>
            </TableRow>
          ))}
          {!rows.length && <TableRow><TableCell colSpan={7} sx={{ color: C.muted, py: 3, textAlign: "center" }}>No requests</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Paper>
  );
}

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <Paper sx={{ p: 1.75, height: "100%" }}>
      <Typography variant="caption" component="div">{label}</Typography>
      <Typography sx={{ fontSize: 26, fontWeight: 700, color: C.ink, lineHeight: 1.25 }}>{value}</Typography>
      {hint && <Typography variant="caption">{hint}</Typography>}
    </Paper>
  );
}

function Bars({ data, label, k }: { data: { day: string; [k: string]: any }[]; label: string; k: string }) {
  const days = useMemo(() => {
    const out: { day: string; v: number }[] = [];
    const m = new Map(data.map((d) => [d.day, Number(d[k]) || 0]));
    for (let i = 29; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
      out.push({ day: d, v: m.get(d) || 0 });
    }
    return out;
  }, [data, k]);
  const max = Math.max(1, ...days.map((d) => d.v));
  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="body2" fontWeight={650}>{label}</Typography>
      <Typography variant="caption">last 30 days · total {days.reduce((a, d) => a + d.v, 0)}</Typography>
      <svg viewBox="0 0 300 80" width="100%" height="110" role="img" aria-label={label} style={{ display: "block", marginTop: 8 }}>
        {days.map((d, i) => (
          <rect key={d.day} x={i * 10 + 1} width="8" y={78 - (d.v / max) * 74} height={Math.max(d.v ? 2 : 0.5, (d.v / max) * 74)} fill={d.v ? C.navy : C.line} rx="1">
            <title>{`${d.day}: ${d.v}`}</title>
          </rect>))}
      </svg>
    </Paper>
  );
}

function Overview({ say }: { say: Say }) {
  const [o, setO] = useState<any>(null);
  useEffect(() => { owner.overview().then(setO).catch((e) => say("error", e.message)); }, []);
  if (!o) return null;
  const rev = (o.revenue_30d || []).map((r: any) => money(r.amount, r.currency)).join(" + ") || money(0, o.currency);
  const it = o.integrations;
  return (
    <Stack spacing={2}>
      <Grid container spacing={1.5}>
        {[["Requests waiting", o.pending_requests ?? 0, "invoice / bank transfer"], ["Customers", o.users, `${o.suspended} suspended`], ["Active, last 7 days", o.active_7d], ["New, last 30 days", o.new_30d],
          ["Subscribers", o.subscribers, Object.entries(o.subscribers_by_plan).map(([p, n]) => `${p} ${n}`).join(", ") || "none yet"],
          ["Monthly recurring revenue", money(o.mrr, o.currency), "from active subscriptions"], ["Revenue, last 30 days", rev],
          ["Geodatabases, last 30 days", o.geodatabases_30d, `${o.credits_used_30d} credits used`], ["Credits held by customers", o.credits_outstanding]]
          .map(([l, v, h]) => <Grid item xs={6} sm={4} md={3} key={l as string}><Stat label={l as string} value={v as any} hint={h as string} /></Grid>)}
      </Grid>
      <Grid container spacing={1.5}>
        <Grid item xs={12} md={6}><Bars data={o.signups_series} k="n" label="Sign-ups per day" /></Grid>
        <Grid item xs={12} md={6}><Bars data={o.usage_series} k="jobs" label="Geodatabases generated per day" /></Grid>
      </Grid>
      <Paper sx={{ p: 2 }}>
        <SectionTitle>Connections</SectionTitle>
        <Table size="small">
          <TableBody>
            <Row k="Accounts" ok={it.auth_mode === "accounts"} v={it.auth_mode === "accounts" ? "on" : `off — GSB_AUTH_MODE=${it.auth_mode}`} />
            <Row k="Public address" ok={it.public_url_set} v={it.public_url} hint={it.public_url_set ? "" : "set GSB_PUBLIC_URL to the address customers use"} />
            <Row k="Stripe" ok={it.stripe !== "not configured"} v={it.stripe} hint={it.stripe === "not configured" ? "set GSB_STRIPE_SECRET_KEY" : ""} />
            <Row k="Stripe webhook" ok={it.stripe_webhook_secret} v={it.stripe_webhook_url}
              hint={`Stripe Dashboard → Developers → Webhooks → add this URL with events ${it.stripe_events.join(", ")}; put the signing secret in GSB_STRIPE_WEBHOOK_SECRET`} copy={it.stripe_webhook_url} />
            <Row k="Google sign-in" ok={it.google} v={it.google ? "on" : "off"} hint={`Google Cloud → OAuth client → redirect URI ${it.oauth_redirects.google}; GSB_GOOGLE_CLIENT_ID / _SECRET`} copy={it.oauth_redirects.google} />
            <Row k="GitHub sign-in" ok={it.github} v={it.github ? "on" : "off"} hint={`GitHub → Settings → Developer settings → OAuth App → callback ${it.oauth_redirects.github}; GSB_GITHUB_CLIENT_ID / _SECRET`} copy={it.oauth_redirects.github} />
            <Row k="E-mail (SMTP)" ok={it.smtp} v={it.smtp ? "on" : "off — messages kept in data/mail_outbox"} hint="GSB_SMTP_HOST, _PORT, _USER, _PASSWORD, _FROM" />
            <Row k="Owners from environment" ok v={it.owners_from_env.join(", ") || "—"} hint="GSB_OWNER_EMAILS" />
          </TableBody>
        </Table>
      </Paper>
    </Stack>
  );
}

function Row({ k, v, ok, hint, copy }: { k: string; v: string; ok: boolean; hint?: string; copy?: string }) {
  return (
    <TableRow>
      <TableCell sx={{ width: 190, fontWeight: 600 }}>{k}</TableCell>
      <TableCell sx={{ width: 30 }}><Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: ok ? C.green : C.amber }} /></TableCell>
      <TableCell>
        <Typography variant="body2" sx={{ wordBreak: "break-all" }}>{v}
          {copy && <IconButton size="small" onClick={() => navigator.clipboard?.writeText(copy)} aria-label="Copy"><ContentCopyOutlined sx={{ fontSize: 14 }} /></IconButton>}</Typography>
        {hint && <Typography variant="caption">{hint}</Typography>}
      </TableCell>
    </TableRow>
  );
}

function Customers({ me, isOwner, say }: { me: AuthUser; isOwner: boolean; say: Say }) {
  const [q, setQ] = useState(""); const [status, setStatus] = useState(""); const [plan, setPlan] = useState("");
  const [data, setData] = useState<{ total: number; users: AuthUser[] } | null>(null);
  const [sel, setSel] = useState<number | null>(null);
  const [invite, setInvite] = useState(false);
  const load = () => owner.users({ q, status, plan }).then(setData).catch((e) => say("error", e.message));
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [q, status, plan]);
  return (
    <Paper sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25} sx={{ mb: 1.5 }} alignItems={{ sm: "center" }}>
        <TextField placeholder="Search e-mail, name or company" value={q} onChange={(e) => setQ(e.target.value)} sx={{ minWidth: 280 }} />
        <TextField select label="Status" value={status} onChange={(e) => setStatus(e.target.value)} sx={{ width: 150 }}>
          <MenuItem value="">All</MenuItem><MenuItem value="active">Active</MenuItem><MenuItem value="suspended">Suspended</MenuItem>
        </TextField>
        <TextField select label="Plan" value={plan} onChange={(e) => setPlan(e.target.value)} sx={{ width: 170 }}>
          <MenuItem value="">All</MenuItem><MenuItem value="none">No subscription</MenuItem>
          <MenuItem value="starter">Starter</MenuItem><MenuItem value="professional">Professional</MenuItem><MenuItem value="enterprise">Enterprise</MenuItem>
        </TextField>
        <Box sx={{ flex: 1 }} />
        <Typography variant="caption">{data?.total ?? 0} customers</Typography>
        <Button variant="contained" onClick={() => setInvite(true)}>Create account</Button>
      </Stack>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small">
          <TableHead><TableRow>
            <TableCell>Customer</TableCell><TableCell>Role</TableCell><TableCell>Status</TableCell><TableCell>Plan</TableCell>
            <TableCell align="right">Credits</TableCell><TableCell align="right">Geodatabases</TableCell><TableCell>Sign-in</TableCell>
            <TableCell>Last seen</TableCell><TableCell>Joined</TableCell>
          </TableRow></TableHead>
          <TableBody>
            {(data?.users || []).map((u) => (
              <TableRow key={u.id} hover sx={{ cursor: "pointer" }} onClick={() => setSel(u.id!)}>
                <TableCell><Typography variant="body2" fontWeight={600}>{u.name || u.email}</Typography>
                  <Typography variant="caption">{u.email}{u.company ? ` · ${u.company}` : ""}</Typography></TableCell>
                <TableCell>{u.role !== "user" ? <Chip size="small" label={u.role} color={u.role === "owner" ? "primary" : "default"} /> : "customer"}</TableCell>
                <TableCell><Chip size="small" label={u.status} color={u.status === "active" ? "success" : "warning"} variant="outlined" /></TableCell>
                <TableCell>{u.plan_id && u.subscription_status !== "canceled" ? `${u.plan_id} (${u.subscription_status})` : "—"}</TableCell>
                <TableCell align="right">{u.credits}</TableCell>
                <TableCell align="right">{u.geodatabases}</TableCell>
                <TableCell>{u.signup_method}</TableCell>
                <TableCell>{fmt(u.last_login_at)}</TableCell>
                <TableCell>{day(u.created_at)}</TableCell>
              </TableRow>))}
            {data && !data.users.length && <TableRow><TableCell colSpan={9}>No customers match.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </Box>
      {sel !== null && <CustomerDrawer id={sel} me={me} isOwner={isOwner} say={say} onClose={() => { setSel(null); load(); }} />}
      {invite && <InviteDialog say={say} onClose={() => { setInvite(false); load(); }} />}
    </Paper>
  );
}

function CustomerDrawer({ id, me, isOwner, say, onClose }: { id: number; me: AuthUser; isOwner: boolean; say: Say; onClose: () => void }) {
  const [d, setD] = useState<any>(null);
  const [delta, setDelta] = useState(""); const [note, setNote] = useState("");
  const [link, setLink] = useState("");
  const load = () => owner.user(id).then(setD).catch((e) => say("error", e.message));
  useEffect(() => { load(); }, [id]);
  const act = async (f: () => Promise<any>, ok: string) => { try { await f(); say("success", ok); load(); } catch (e: any) { say("error", e.message); } };
  const u: AuthUser | undefined = d?.user;
  return (
    <Drawer anchor="right" open onClose={onClose} PaperProps={{ sx: { width: { xs: "100%", md: 640 } } }}>
      {u && (
        <Box sx={{ p: 2.5 }}>
          <Stack direction="row" alignItems="center">
            <Box sx={{ flex: 1 }}>
              <Typography sx={{ fontSize: 18, fontWeight: 700 }}>{u.name || u.email}</Typography>
              <Typography variant="caption">{u.email} · {u.company || "no company"} · joined {day(u.created_at)} · {u.signup_method}</Typography>
            </Box>
            <IconButton onClick={onClose} aria-label="Close"><CloseOutlined /></IconButton>
          </Stack>
          <Grid container spacing={1.5} sx={{ mt: 1 }}>
            <Grid item xs={4}><Stat label="Credits" value={u.credits} /></Grid>
            <Grid item xs={4}><Stat label="Plan" value={<span style={{ fontSize: 18 }}>{u.plan_id && u.subscription_status !== "canceled" ? u.plan_id : "—"}</span>} hint={u.subscription_status || ""} /></Grid>
            <Grid item xs={4}><Stat label="Status" value={<span style={{ fontSize: 18 }}>{u.status}</span>} hint={u.email_verified ? "e-mail confirmed" : "e-mail not confirmed"} /></Grid>
          </Grid>
          <Paper sx={{ p: 1.75, mt: 2 }}>
            <Typography variant="body2" fontWeight={650}>Adjust credits</Typography>
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <TextField type="number" label="+ / − credits" value={delta} onChange={(e) => setDelta(e.target.value)} sx={{ width: 140 }} />
              <TextField label="Reason (shown to the customer)" value={note} onChange={(e) => setNote(e.target.value)} sx={{ flex: 1 }} />
              <Button variant="contained" disabled={!Number(delta)} onClick={() => act(() => owner.credits(id, Number(delta), note), "Credits updated.").then(() => { setDelta(""); setNote(""); })}>Apply</Button>
            </Stack>
          </Paper>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 2 }}>
            {u.status === "active"
              ? <Button color="warning" variant="outlined" disabled={u.id === me.id} onClick={() => act(() => owner.updateUser(id, { status: "suspended" }), "Account suspended and signed out.")}>Suspend</Button>
              : <Button color="success" variant="outlined" onClick={() => act(() => owner.updateUser(id, { status: "active" }), "Account reactivated.")}>Reactivate</Button>}
            {!u.email_verified && <Button variant="outlined" onClick={() => act(() => owner.updateUser(id, { email_verified: true }), "E-mail marked as confirmed.")}>Mark e-mail confirmed</Button>}
            <Button variant="outlined" onClick={async () => { try { setLink((await owner.resetLink(id)).url); } catch (e: any) { say("error", e.message); } }}>Password reset link</Button>
            <Button variant="outlined" onClick={() => act(() => owner.logoutUser(id), "Signed out on every device.")}>Sign out everywhere</Button>
            {isOwner && (
              <TextField select size="small" label="Role" value={u.role} sx={{ width: 140 }} disabled={u.id === me.id}
                onChange={(e) => act(() => owner.updateUser(id, { role: e.target.value }), "Role changed.")}>
                <MenuItem value="user">Customer</MenuItem><MenuItem value="admin">Administrator</MenuItem><MenuItem value="owner">Owner</MenuItem>
              </TextField>)}
            {isOwner && u.id !== me.id && <Button color="error" onClick={() => { if (window.confirm(`Delete ${u.email}? The e-mail is anonymised and the account cannot sign in again.`)) act(() => owner.deleteUser(id), "Account deleted.").then(onClose); }}>Delete account</Button>}
          </Stack>
          {link && <Alert severity="info" sx={{ mt: 1.5, wordBreak: "break-all" }} action={<IconButton size="small" onClick={() => navigator.clipboard?.writeText(link)}><ContentCopyOutlined fontSize="small" /></IconButton>}>
            Valid 24 hours — send it to the customer: {link}</Alert>}
          <Divider sx={{ my: 2 }} />
          <Typography variant="body2" fontWeight={650}>Credit history</Typography>
          <Table size="small"><TableBody>
            {d.ledger.slice(0, 60).map((l: any) => (
              <TableRow key={l.id}><TableCell sx={{ whiteSpace: "nowrap" }}>{fmt(l.created_at)}</TableCell><TableCell>{l.kind}{l.note ? ` — ${l.note}` : ""}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600, color: l.delta > 0 ? C.green : C.ink }}>{l.delta > 0 ? `+${l.delta}` : l.delta}</TableCell><TableCell align="right">{l.balance_after}</TableCell></TableRow>))}
          </TableBody></Table>
          {d.jobs.length > 0 && <>
            <Typography variant="body2" fontWeight={650} sx={{ mt: 2 }}>Recent conversions</Typography>
            <Table size="small"><TableBody>{d.jobs.slice(0, 20).map((j: any) => (
              <TableRow key={j.job_id} hover sx={{ cursor: "pointer" }} onClick={() => { window.location.hash = `job=${j.job_id}`; }}>
                <TableCell>{j.file?.name}</TableCell><TableCell>{j.schema_name}</TableCell><TableCell>{j.status}</TableCell><TableCell>{fmt(j.created_at)}</TableCell></TableRow>))}
            </TableBody></Table></>}
          {d.payments.length > 0 && <>
            <Typography variant="body2" fontWeight={650} sx={{ mt: 2 }}>Payments</Typography>
            <Table size="small"><TableBody>{d.payments.map((p: Payment) => (
              <TableRow key={p.id}><TableCell>{day(p.created_at)}</TableCell><TableCell>{p.description}</TableCell><TableCell align="right">{money(p.amount, p.currency)}</TableCell></TableRow>))}
            </TableBody></Table></>}
          <Typography variant="body2" fontWeight={650} sx={{ mt: 2 }}>Sign-in</Typography>
          <Typography variant="caption" component="div">Linked: {d.identities.map((i: any) => i.provider).join(", ") || "password only"}{d.stripe_customer_id ? ` · Stripe ${d.stripe_customer_id}` : ""}</Typography>
          {d.sessions.slice(0, 5).map((s: any, i: number) => <Typography key={i} variant="caption" component="div">{fmt(s.last_seen)} · {s.ip} · {(s.user_agent || "").slice(0, 70)}</Typography>)}
        </Box>
      )}
    </Drawer>
  );
}

function InviteDialog({ say, onClose }: { say: Say; onClose: () => void }) {
  const [f, setF] = useState({ email: "", name: "", company: "", credits: "0" });
  const [url, setUrl] = useState("");
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Create a customer account</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1.5 }}>For contracts arranged directly (e.g. Enterprise). The customer receives a link to set a password.</Typography>
        <Stack spacing={1.5}>
          <TextField label="E-mail" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
          <TextField label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <TextField label="Company" value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} />
          <TextField type="number" label="Extra credits (on top of the trial)" value={f.credits} onChange={(e) => setF({ ...f, credits: e.target.value })} />
          {url && <Alert severity="success" sx={{ wordBreak: "break-all" }}>Account created. Set-password link (valid 7 days): {url}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{url ? "Close" : "Cancel"}</Button>
        {!url && <Button variant="contained" onClick={async () => {
          try { setUrl((await owner.invite({ ...f, credits: Number(f.credits) || 0 })).url); } catch (e: any) { say("error", e.message); }
        }}>Create account</Button>}
      </DialogActions>
    </Dialog>
  );
}

function Plans({ isOwner, say }: { isOwner: boolean; say: Say }) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [edit, setEdit] = useState<Partial<Plan> | null>(null);
  const load = () => owner.plans().then(setPlans).catch((e) => say("error", e.message));
  useEffect(() => { load(); }, []);
  return (
    <Paper sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" sx={{ mb: 1.5 }}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="body2">Prices are charged by Stripe: create a product and price in the Stripe Dashboard and paste its <b>price_…</b> id here.
            Subscriptions add their credits on every paid invoice; packs add them once.</Typography>
        </Box>
        {isOwner && <Button variant="contained" onClick={() => setEdit({ id: "", name: "", kind: "pack", price_amount: 0, currency: "aed", credits: 100, active: true, features: [], description: "" })}>New plan</Button>}
      </Stack>
      <Table size="small">
        <TableHead><TableRow><TableCell>Plan</TableCell><TableCell>Type</TableCell><TableCell align="right">Price</TableCell><TableCell align="right">Credits</TableCell>
          <TableCell>Stripe price</TableCell><TableCell>Shown</TableCell><TableCell /></TableRow></TableHead>
        <TableBody>{plans.map((p) => (
          <TableRow key={p.id}>
            <TableCell><b>{p.name}</b>{p.highlight && <Chip size="small" label="highlighted" sx={{ ml: 1 }} />}<Typography variant="caption" component="div">{p.id}</Typography></TableCell>
            <TableCell>{p.kind}</TableCell>
            <TableCell align="right">{p.kind === "enterprise" ? "custom" : money(p.price_amount, p.currency) + (p.kind === "subscription" ? " / month" : "")}</TableCell>
            <TableCell align="right">{p.credits}</TableCell>
            <TableCell>{p.kind === "enterprise" ? "—" : p.stripe_price_id || <Typography variant="caption" sx={{ color: C.amber }}>missing — cannot be bought</Typography>}</TableCell>
            <TableCell>{p.active ? "yes" : "hidden"}</TableCell>
            <TableCell>{isOwner && <Button size="small" onClick={() => setEdit(p)}>Edit</Button>}</TableCell>
          </TableRow>))}
        </TableBody>
      </Table>
      {edit && <PlanDialog plan={edit} isNew={!plans.some((p) => p.id === edit.id)} say={say} onClose={() => { setEdit(null); load(); }} />}
    </Paper>
  );
}

function PlanDialog({ plan, isNew, say, onClose }: { plan: Partial<Plan>; isNew: boolean; say: Say; onClose: () => void }) {
  const [p, setP] = useState<any>({ ...plan, price: ((plan.price_amount || 0) / 100).toString(), feats: (plan.features || []).join("\n") });
  const save = async () => {
    try {
      await owner.savePlan(p.id, { name: p.name, kind: p.kind, price_amount: Math.round(Number(p.price) * 100), currency: p.currency,
        credits: Number(p.credits), stripe_price_id: p.stripe_price_id || null, active: !!p.active, highlight: !!p.highlight,
        sort: Number(p.sort || 0), description: p.description, features: p.feats.split("\n").map((x: string) => x.trim()).filter(Boolean) });
      say("success", `Plan ${p.name} saved.`); onClose();
    } catch (e: any) { say("error", e.message); }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isNew ? "New plan" : `Edit ${plan.name}`}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <Stack direction="row" spacing={1}>
            <TextField label="Id" value={p.id} disabled={!isNew} onChange={(e) => setP({ ...p, id: e.target.value })} helperText="letters, digits, _" />
            <TextField label="Name" value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} sx={{ flex: 1 }} />
          </Stack>
          <Stack direction="row" spacing={1}>
            <TextField select label="Type" value={p.kind} onChange={(e) => setP({ ...p, kind: e.target.value })} sx={{ width: 170 }}>
              <MenuItem value="subscription">Monthly subscription</MenuItem><MenuItem value="pack">Credit pack</MenuItem><MenuItem value="enterprise">Enterprise (contact)</MenuItem>
            </TextField>
            <TextField label="Price (display)" type="number" value={p.price} onChange={(e) => setP({ ...p, price: e.target.value })} sx={{ width: 140 }} />
            <TextField label="Currency" value={p.currency} onChange={(e) => setP({ ...p, currency: e.target.value.toLowerCase() })} sx={{ width: 100 }} />
            <TextField label="Credits" type="number" value={p.credits} onChange={(e) => setP({ ...p, credits: e.target.value })} sx={{ width: 110 }} />
          </Stack>
          {p.kind !== "enterprise" && <TextField label="Stripe price id" placeholder="price_1Q…" value={p.stripe_price_id || ""} onChange={(e) => setP({ ...p, stripe_price_id: e.target.value.trim() })}
            helperText="Must match the Stripe price; the amount charged is the Stripe price, the price above is what the website shows." />}
          <TextField label="Description" value={p.description} onChange={(e) => setP({ ...p, description: e.target.value })} />
          <TextField label="Features (one per line)" multiline minRows={3} value={p.feats} onChange={(e) => setP({ ...p, feats: e.target.value })} />
          <Stack direction="row" spacing={2}>
            <FormControlLabel control={<Switch checked={!!p.active} onChange={(e) => setP({ ...p, active: e.target.checked })} />} label="Shown to customers" />
            <FormControlLabel control={<Checkbox checked={!!p.highlight} onChange={(e) => setP({ ...p, highlight: e.target.checked })} />} label="Highlight" />
            <TextField label="Order" type="number" value={p.sort ?? 0} onChange={(e) => setP({ ...p, sort: e.target.value })} sx={{ width: 90 }} />
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Cancel</Button><Button variant="contained" onClick={save}>Save plan</Button></DialogActions>
    </Dialog>
  );
}

function Payments() {
  const [rows, setRows] = useState<Payment[]>([]);
  useEffect(() => { owner.payments().then(setRows).catch(() => undefined); }, []);
  return (
    <Paper sx={{ p: 2 }}>
      <Table size="small">
        <TableHead><TableRow><TableCell>Date</TableCell><TableCell>Customer</TableCell><TableCell>Description</TableCell><TableCell>Type</TableCell>
          <TableCell align="right">Amount</TableCell><TableCell>Stripe</TableCell></TableRow></TableHead>
        <TableBody>
          {rows.map((p) => <TableRow key={p.id}><TableCell>{fmt(p.created_at)}</TableCell><TableCell>{p.email}</TableCell><TableCell>{p.description}</TableCell>
            <TableCell>{p.kind}</TableCell><TableCell align="right">{money(p.amount, p.currency)}</TableCell><TableCell sx={{ fontSize: 11.5 }}>{p.stripe_id}</TableCell></TableRow>)}
          {!rows.length && <TableRow><TableCell colSpan={6}>No payments yet. They appear here when Stripe confirms them.</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Paper>
  );
}

function Activity() {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { owner.audit().then(setRows).catch(() => undefined); }, []);
  return (
    <Paper sx={{ p: 2 }}>
      <Table size="small">
        <TableHead><TableRow><TableCell>When</TableCell><TableCell>Who</TableCell><TableCell>Action</TableCell><TableCell>On</TableCell><TableCell>Detail</TableCell><TableCell>IP</TableCell></TableRow></TableHead>
        <TableBody>{rows.map((a) => (
          <TableRow key={a.id}><TableCell sx={{ whiteSpace: "nowrap" }}>{fmt(a.at)}</TableCell><TableCell>{a.actor_email || "system"}</TableCell>
            <TableCell>{a.action.replace(/_/g, " ")}</TableCell><TableCell>{a.target}</TableCell>
            <TableCell sx={{ fontSize: 11.5, maxWidth: 420, wordBreak: "break-word" }}>{a.detail}</TableCell><TableCell>{a.ip}</TableCell></TableRow>))}
        </TableBody>
      </Table>
    </Paper>
  );
}

function Settings({ isOwner, say }: { isOwner: boolean; say: Say }) {
  const [s, setS] = useState<Record<string, any> | null>(null);
  useEffect(() => { owner.settings().then((r) => setS(r.settings)).catch((e) => say("error", e.message)); }, []);
  if (!s) return null;
  const n = (k: string, label: string, help: string) => (
    <TextField type="number" label={label} helperText={help} value={s[k]} disabled={!isOwner} onChange={(e) => setS({ ...s, [k]: Number(e.target.value) })} />);
  return (
    <Paper sx={{ p: 2.5, maxWidth: 720 }}>
      <Stack spacing={2}>
        <SectionTitle>Free trial and pricing</SectionTitle>
        {n("trial_credits", "Credits for every new account", "Free trial — enough for a few real drawings")}
        {n("generate_base_credits", "Credits per geodatabase", "Charged once per job when generation starts")}
        {n("generate_credits_per_1000_features", "Extra credits per 1,000 features", "Larger drawings cost a little more")}
        <SectionTitle>Sign-up</SectionTitle>
        <FormControlLabel control={<Switch checked={!!s.signup_open} disabled={!isOwner} onChange={(e) => setS({ ...s, signup_open: e.target.checked })} />}
          label="Anyone can create an account (off: only accounts you create)" />
        <FormControlLabel control={<Switch checked={!!s.require_email_verification} disabled={!isOwner} onChange={(e) => setS({ ...s, require_email_verification: e.target.checked })} />}
          label="Confirmed e-mail required before generating a geodatabase (needs SMTP)" />
        <SectionTitle>Company</SectionTitle>
        <TextField label="Company name (footer, e-mails)" value={s.company_name} disabled={!isOwner} onChange={(e) => setS({ ...s, company_name: e.target.value })} />
        <TextField label="Support e-mail (website contact)" value={s.support_email} disabled={!isOwner} onChange={(e) => setS({ ...s, support_email: e.target.value })} />
        <TextField label="Payment instructions (bank / invoice — shown on a credit request)" value={s.payment_instructions || ""} disabled={!isOwner} multiline minRows={3}
          placeholder={"Bank: …\nIBAN: AE…\nAccount name: …"} onChange={(e) => setS({ ...s, payment_instructions: e.target.value })} />
        {isOwner ? <Box><Button variant="contained" onClick={async () => {
          try { setS(await owner.saveSettings(s)); say("success", "Settings saved."); } catch (e: any) { say("error", e.message); }
        }}>Save settings</Button></Box> : <Typography variant="caption">Only an owner can change settings.</Typography>}
      </Stack>
    </Paper>
  );
}

export const _unused = Tooltip;
