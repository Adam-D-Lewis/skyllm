# Gap: Edge-level authentication

**Not a phase — an open gap flagged 2026-04-24 after seeing unexplained 401s on a live deployment.**

## Current state

The CF Tunnel that ships with v1 exposes the origin (`llama-server --api-key`) on a public hostname. Cloudflare Tunnel itself does *not* authenticate clients — it just creates a secure outbound channel from the pod to CF's edge. Anyone on the internet can hit `https://llm.yourdomain.com/...` and reach the origin.

What the bearer token (`--api-key $LLM_API_KEY`) actually covers on llama-server (verified 2026-04-24):

| Endpoint | Auth required? | Leak if unauthed |
|---|---|---|
| `/v1/*` (chat, completions, models) | ✅ yes | — |
| `/metrics` (Prometheus) | ✅ yes | — |
| `/health` | ❌ no | `{"status":"ok"}` only — no information leak |

So with a strong 256-bit `LLM_API_KEY`, the attack surface really is just the bearer token's entropy. This is stronger than I initially thought — `/metrics` is *not* publicly scrapeable, which was a concern earlier.

Observed symptom (2026-04-24 on a live Qwen3.6-27B deployment): intermittent `Unauthorized: Invalid API Key` entries in `llama-server.log` concurrent with legitimate chats. Almost certainly internet scanners probing `/v1/chat/completions` (a well-known shape bots scan for). Harmless per-request — 256 bits of entropy makes brute-force infeasible — but:

- Clogs logs (hard to distinguish real auth mistakes from scanner noise).
- Wastes a small amount of origin CPU on every auth-reject.
- No defense-in-depth: if the bearer token ever leaks (accidentally committed to a repo, shared in a screenshot, exfiltrated by a compromised client), there's nothing else in the way.

## Why this is a separate doc

The existing "v2: migrate to FRP" plan in the README is about **where plaintext terminates** (CF edge vs. your own VPS), not about **who is allowed to connect**. Those are orthogonal. You can have CF Tunnel + Access (TLS at CF, authed at CF). You can have FRP + WireGuard (TLS at VPS, authed at network layer). Or CF Tunnel with no Access (today). Picking the FRP migration doesn't automatically solve auth, and picking auth doesn't require the FRP migration.

## Options

### 1. Cloudflare Access (v1-compatible, low-friction)

Free for ≤50 seats. Layer an Access application on top of the existing tunnel:
- Browser clients do a one-time email OTP / SSO / GitHub login → CF issues a session cookie.
- API clients (curl, OpenWebUI, OpenAI SDK) send `CF-Access-Client-Id` + `CF-Access-Client-Secret` headers alongside the bearer. Service tokens are generated per-client in the CF dashboard.
- Scanners see CF's login page at the edge and never reach the pod.
- Zero code changes in this repo; ~10 min of dashboard config + documenting the extra headers for clients.

**Best if**: you want edge auth today without touching the transport.

### 2. FRP + WireGuard (aligns with planned v2)

Already in-scope for the "v2: CF Tunnel → FRP" migration. Instead of exposing FRP's tunneled port publicly, put WireGuard in front of it — only devices with a WG key can reach the VPS's FRP port. Network-layer auth; no HTTP shenanigans.

**Trade-off**: breaks casual browser-from-any-device usage — every client has to run the WG daemon. Better for trusted-devices-only topology (your laptop + phone) than for "share a URL with a teammate."

**Best if**: you're already migrating to FRP for the plaintext-at-CF reason, and your clients are your own devices.

### 3. CF WAF rule (coarse, quick)

Country allowlist or challenge-suspicious-bots rule at the CF edge. Not airtight (sophisticated scanners bypass it), but cuts opportunistic noise 90 %+ with one dashboard click.

**Best if**: you want less log noise and don't want to set up Access yet.

### 4. Do nothing (current state)

`LLM_API_KEY` at 256 bits is cryptographically secure against brute force. The 401s are noise, not a breach. Cost of inaction is log clutter and a small amount of wasted origin CPU.

**Best if**: you trust the bearer token and the noise isn't bothering you.

## Recommendation

**Option 1 (CF Access) now**, **Option 2 (FRP + WG) when v2 migration happens.** They're complementary — Access gates HTTP clients even after you move to FRP, and WireGuard gates network access even for clients bypassing Access. No mutual exclusion.

## Defer until

- Log noise becomes actively annoying, OR
- The v2 FRP migration is kicked off (at which point wire WG in from day one), OR
- Evidence of a targeted attack (not just opportunistic scanning).

The security risk right now is low: bearer-only defense has held on every LLM-endpoint deployment I've seen on this scaffold shape. This is an ops/hygiene issue, not an incident.
