import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, Collapse, Container, IconButton, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import ScheduleOutlined from "@mui/icons-material/ScheduleOutlined";
import ReceiptLongOutlined from "@mui/icons-material/ReceiptLongOutlined";
import { auth, billing } from "../api";
import type { AuthUser, BillingStatus, Plan, PurchaseRequest } from "../types";
import { C } from "../theme";
import { AppHeader } from "../components/AppHeader";
import { money } from "../site/Landing";

interface Props { me: AuthUser; mode: string; go: (r: string) => void; onLogout: () => void; refreshMe: () => void; params: URLSearchParams }

const KIND: Record<string, string> = {
  trial: "Free trial", subscription: "Subscription", purchase: "Credits bought", grant: "Added by GeoSyntra",
  revoke: "Removed by GeoSyntra", charge: "Geodatabase", refund: "Refund",
};

/** Account: balance, plan, buying (online with Stripe, or by invoice approved by the owner), history, settings. */
export function Account({ me, mode, go, onLogout, refreshMe, params }: Props) {
  const [b, setB] = useState<BillingStatus | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [reqs, setReqs] = useState<PurchaseRequest[]>([]);
  const [msg, setMsg] = useState<{ sev: "success" | "error" | "info"; text: string } | null>(null);
  const [prof, setProf] = useState({ name: me.name || "", company: me.company || "" });
  const [pw, setPw] = useState({ current: "", password: "" });
  const [busy, setBusy] = useState("");
  const [settings, setSettings] = useState(false);

  const load = () => {
    billing.status().then(setB).catch((e) => setMsg({ sev: "error", text: e.message }));
    billing.requests().then(setReqs).catch(() => setReqs([]));
    refreshMe();
  };
  useEffect(() => {
    load();
    billing.plans().then(setPlans).catch(() => undefined);
    const c = params.get("checkout");
    if (c === "success") setMsg({ sev: "success", text: "Payment received — credits appear in a few seconds." });
    if (c === "cancelled") setMsg({ sev: "info", text: "Checkout cancelled — nothing was charged." });
    if (params.get("verified") === "1") setMsg({ sev: "success", text: "E-mail confirmed." });
    if (c === "success") { const t = setTimeout(load, 4000); return () => clearTimeout(t); }
  }, []);

  const buy = async (p: Plan) => {
    if (p.kind === "enterprise") { window.location.href = "mailto:?subject=GeoSyntra%20Bridge%20Enterprise"; return; }
    setBusy(p.id);
    try {
      if (b?.stripe && p.purchasable) { window.location.href = (await billing.checkout(p.id)).url; return; }
      const r = await billing.request(p.id);
      setMsg({ sev: "success", text: `Request ${r.reference} sent — credits are added as soon as payment is confirmed.` });
      load();
    } catch (e: any) { setMsg({ sev: "error", text: e.message }); }
    setBusy("");
  };
  const portal = async () => {
    setBusy("portal");
    try { window.location.href = (await billing.portal()).url; }
    catch (e: any) { setMsg({ sev: "error", text: e.message }); setBusy(""); }
  };
  const wanted = params.get("plan");
  const subscribed = !!b?.plan && ["active", "trialing", "past_due"].includes(b?.subscription_status || "");
  const pending = reqs.filter((r) => r.status === "pending");
  const subs = plans.filter((p) => p.kind !== "pack");
  const packs = plans.filter((p) => p.kind === "pack");
  const planName = (id: string) => plans.find((p) => p.id === id)?.name || id;

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppHeader me={me} mode={mode} go={go} onLogout={onLogout} active="account" />
      <Container maxWidth="lg" sx={{ py: 3 }}>
        {msg && <Alert severity={msg.sev} onClose={() => setMsg(null)} sx={{ mb: 2 }}>{msg.text}</Alert>}
        {!me.email_verified && (
          <Alert severity="info" sx={{ mb: 2 }} action={<Button size="small" onClick={async () => {
            const r = await auth.resendVerification(); setMsg({ sev: "info", text: r.smtp ? `Sent to ${me.email}.` : "E-mail is not set up on this server — the owner can confirm your address." });
          }}>Resend</Button>}>Confirm your e-mail — link sent to {me.email}.</Alert>
        )}

        {/* ---------------- balance + plan */}
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1.2fr 1fr" } }}>
          <Paper sx={{ p: 3, display: "flex", alignItems: "center", gap: 3 }}>
            <Box sx={{ flex: 1 }}>
              <Typography sx={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase", color: C.muted }}>Credits</Typography>
              <Typography sx={{ fontSize: 52, fontWeight: 800, lineHeight: 1.05, color: C.ink }}>{b?.unlimited ? "Unlimited" : (b?.credits ?? me.credits ?? 0).toLocaleString()}</Typography>
              <Typography sx={{ fontSize: 13, color: C.muted, mt: 0.5 }}>
                {b?.unlimited ? "Owner account — geodatabases are not charged"
                  : <>1 geodatabase = {b?.pricing.generate_base_credits ?? 10} + {b?.pricing.generate_credits_per_1000_features ?? 5} per 1,000 features</>}
              </Typography>
            </Box>
            {!b?.unlimited && <Button variant="contained" size="large" onClick={() => document.getElementById("buy")?.scrollIntoView({ behavior: "smooth" })}>Add credits</Button>}
          </Paper>
          <Paper sx={{ p: 3 }}>
            <Typography sx={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase", color: C.muted }}>Plan</Typography>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 0.5 }}>
              <Typography sx={{ fontSize: 24, fontWeight: 700 }}>{b?.plan ? b.plan.name : "Pay as you go"}</Typography>
              {b?.subscription_status && <Chip size="small" label={b.subscription_status} color={b.subscription_status === "active" ? "success" : "default"} />}
            </Stack>
            {b?.current_period_end && subscribed && (
              <Typography sx={{ fontSize: 13, color: C.muted, mt: 0.5 }}>{b.cancel_at_period_end ? "Ends" : "Renews"} {new Date(b.current_period_end).toLocaleDateString()}
                {b.plan ? ` · ${b.plan.credits.toLocaleString()} credits` : ""}</Typography>)}
            {b?.has_customer && <Button sx={{ mt: 1.5 }} variant="outlined" size="small" onClick={portal} disabled={busy === "portal"}>Billing & invoices</Button>}
          </Paper>
        </Box>

        {/* ---------------- pending requests */}
        {pending.length > 0 && (
          <Paper sx={{ mt: 2, p: 2.5, border: "1px solid #FDE68A", bgcolor: "#FFFBEB" }}>
            {pending.map((r) => (
              <Stack key={r.id} direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
                <ScheduleOutlined sx={{ color: C.amber }} />
                <Box sx={{ flex: 1 }}>
                  <Typography sx={{ fontWeight: 700 }}>{r.note === "renewal" ? "Renewal · " : ""}{planName(r.plan_id)} · {money(r.amount, r.currency)} — waiting for payment</Typography>
                  <Typography sx={{ fontSize: 13, color: C.muted }}>Pay by bank transfer with the reference <b style={{ color: C.ink, fontFamily: "ui-monospace, Consolas, monospace" }}>{r.reference}</b>. Credits are added once it is confirmed.</Typography>
                  {b?.payment_instructions && <Typography component="pre" sx={{ mt: 1, mb: 0, p: 1.25, bgcolor: "#fff", border: "1px solid #FDE68A", borderRadius: 1.5,
                    fontSize: 13, fontFamily: "inherit", whiteSpace: "pre-wrap" }}>{b.payment_instructions}</Typography>}
                  {!b?.payment_instructions && b?.support_email && <Typography sx={{ fontSize: 13, mt: 0.5 }}>Payment details: <a href={`mailto:${b.support_email}?subject=${r.reference}`}>{b.support_email}</a></Typography>}
                </Box>
                <Button size="small" sx={{ color: C.muted }} onClick={async () => { await billing.cancelRequest(r.id); load(); }}>Cancel</Button>
              </Stack>
            ))}
          </Paper>
        )}

        {/* ---------------- plans */}
        <Box id="buy" sx={{ mt: 3 }}>
          <Typography sx={{ fontSize: 18, fontWeight: 700, mb: 1.5 }}>{subscribed ? "Change plan or add credits" : "Choose a plan"}</Typography>
          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: `repeat(${Math.max(1, subs.length)}, minmax(0,1fr))` } }}>
            {subs.map((p) => {
              const current = subscribed && b?.plan?.id === p.id;
              const pend = pending.some((r) => r.plan_id === p.id);
              return (
                <Paper key={p.id} sx={{ p: 2.5, display: "flex", flexDirection: "column", gap: 1.25, position: "relative",
                                         border: `1.5px solid ${wanted === p.id || p.highlight ? C.ink : C.line}` }}>
                  {p.highlight && <Box sx={{ position: "absolute", top: -11, left: 20, px: 1, bgcolor: C.ink, color: "#fff", fontSize: 11, fontWeight: 700, borderRadius: 1 }}>Most chosen</Box>}
                  <Typography sx={{ fontWeight: 700, fontSize: 16 }}>{p.name}</Typography>
                  <Typography sx={{ fontSize: 28, fontWeight: 800, lineHeight: 1 }}>
                    {p.kind === "enterprise" ? "Custom" : money(p.price_amount, p.currency)}
                    {p.kind === "subscription" && <Typography component="span" sx={{ fontSize: 13, color: C.muted, fontWeight: 500 }}> / month</Typography>}
                  </Typography>
                  {p.credits > 0 && <Typography sx={{ fontSize: 13, fontWeight: 650, color: C.green }}>{p.credits.toLocaleString()} credits every month</Typography>}
                  <Typography sx={{ fontSize: 13, color: C.muted }}>{p.description}</Typography>
                  <Stack spacing={0.5} sx={{ flex: 1 }}>
                    {(p.features || []).slice(0, 5).map((f) => (
                      <Stack key={f} direction="row" spacing={0.75} alignItems="flex-start"><CheckRounded sx={{ fontSize: 16, color: C.green, mt: 0.2 }} />
                        <Typography sx={{ fontSize: 13 }}>{f}</Typography></Stack>))}
                  </Stack>
                  <Button variant={p.highlight || wanted === p.id ? "contained" : "outlined"} disabled={!!busy || current || pend} onClick={() => buy(p)}>
                    {current ? "Current plan" : pend ? "Requested" : p.kind === "enterprise" ? "Contact us" : b?.stripe && p.purchasable ? "Subscribe" : "Request"}
                  </Button>
                </Paper>
              );
            })}
          </Box>
          {packs.length > 0 && (
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 2 }} alignItems={{ sm: "center" }}>
              <Typography sx={{ fontSize: 13.5, fontWeight: 650, mr: 1 }}>Top-up · no expiry</Typography>
              {packs.map((p) => {
                const pend = pending.some((r) => r.plan_id === p.id);
                return (
                  <Paper key={p.id} sx={{ px: 2, py: 1, display: "flex", alignItems: "center", gap: 2 }}>
                    <Typography sx={{ fontSize: 14 }}><b>{p.credits.toLocaleString()}</b> credits</Typography>
                    <Typography sx={{ fontSize: 14, fontWeight: 700 }}>{money(p.price_amount, p.currency)}</Typography>
                    <Button size="small" variant="outlined" disabled={!!busy || pend} onClick={() => buy(p)}>{pend ? "Requested" : b?.stripe && p.purchasable ? "Buy" : "Request"}</Button>
                  </Paper>
                );
              })}
            </Stack>
          )}
          <Typography sx={{ fontSize: 12, color: C.muted, mt: 1.25 }}>
            {b?.stripe ? "Card payments by Stripe." : "Pay by invoice / bank transfer — credits are added when payment is confirmed."} Unused credits carry over.
          </Typography>
        </Box>

        {/* ---------------- history */}
        <Paper sx={{ mt: 3, overflow: "hidden" }}>
          <Typography sx={{ px: 2.5, pt: 2, pb: 1, fontSize: 16, fontWeight: 700 }}>Activity</Typography>
          <Box sx={{ maxHeight: 360, overflow: "auto" }}>
            <Table size="small" stickyHeader>
              <TableHead><TableRow><TableCell>Date</TableCell><TableCell>What</TableCell><TableCell align="right">Credits</TableCell><TableCell align="right">Balance</TableCell></TableRow></TableHead>
              <TableBody>
                {(b?.ledger || []).map((l) => (
                  <TableRow key={l.id}>
                    <TableCell sx={{ whiteSpace: "nowrap", color: C.muted }}>{new Date(l.created_at).toLocaleString()}</TableCell>
                    <TableCell>{KIND[l.kind] || l.kind}{l.note ? <Box component="span" sx={{ color: C.muted }}> · {l.note}</Box> : null}</TableCell>
                    <TableCell align="right" sx={{ color: l.delta < 0 ? C.ink : C.green, fontWeight: 700 }}>{l.delta > 0 ? `+${l.delta}` : l.delta}</TableCell>
                    <TableCell align="right">{l.balance_after}</TableCell>
                  </TableRow>))}
                {reqs.filter((r) => r.status !== "pending").map((r) => (
                  <TableRow key={"r" + r.id}>
                    <TableCell sx={{ whiteSpace: "nowrap", color: C.muted }}>{new Date(r.decided_at || r.created_at).toLocaleString()}</TableCell>
                    <TableCell>Request {r.reference} · {planName(r.plan_id)}</TableCell>
                    <TableCell align="right" colSpan={2} sx={{ whiteSpace: "nowrap" }}>
                      {r.status === "approved" && <Tooltip title="Receipt (PDF)"><IconButton size="small" aria-label={`Receipt ${r.reference}`} href={billing.receiptUrl(r.id)} sx={{ mr: 0.5 }}><ReceiptLongOutlined fontSize="small" /></IconButton></Tooltip>}
                      <Chip size="small" label={r.status} color={r.status === "approved" ? "success" : "default"} /></TableCell>
                  </TableRow>))}
                {b && !b.ledger.length && <TableRow><TableCell colSpan={4}>No activity yet.</TableCell></TableRow>}
              </TableBody>
            </Table>
          </Box>
        </Paper>

        {/* ---------------- settings (folded) */}
        <Button sx={{ mt: 2, color: C.muted }} onClick={() => setSettings(!settings)}>{settings ? "Hide settings" : "Profile & password"}</Button>
        <Collapse in={settings} unmountOnExit>
          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, mt: 1 }}>
            <Paper sx={{ p: 2.5 }}>
              <Stack spacing={1.5}>
                <TextField size="small" label="E-mail" value={me.email} disabled />
                <TextField size="small" label="Full name" value={prof.name} onChange={(e) => setProf({ ...prof, name: e.target.value })} />
                <TextField size="small" label="Company" value={prof.company} onChange={(e) => setProf({ ...prof, company: e.target.value })} />
                <Box><Button variant="contained" onClick={async () => {
                  try { await auth.updateAccount(prof); refreshMe(); setMsg({ sev: "success", text: "Saved." }); }
                  catch (e: any) { setMsg({ sev: "error", text: e.message }); }
                }}>Save</Button></Box>
              </Stack>
            </Paper>
            <Paper sx={{ p: 2.5 }}>
              <Stack spacing={1.5}>
                {me.has_password && <TextField size="small" type="password" label="Current password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" />}
                <TextField size="small" type="password" label="New password" helperText="At least 8 characters" value={pw.password} onChange={(e) => setPw({ ...pw, password: e.target.value })} autoComplete="new-password" />
                <Box><Button variant="outlined" onClick={async () => {
                  try { await auth.changePassword(pw.current, pw.password); setPw({ current: "", password: "" }); refreshMe(); setMsg({ sev: "success", text: "Password saved." }); }
                  catch (e: any) { setMsg({ sev: "error", text: e.message }); }
                }}>Save password</Button></Box>
              </Stack>
            </Paper>
          </Box>
        </Collapse>
      </Container>
    </Box>
  );
}
