# 0041. Retest scope drives the sandbox target: allowlisting egress proxy for online targets

Date: 2026-07-24
Status: accepted (ratified 2026-07-25)

## Context

The agentic retest sandbox (ADR-0025) has always targeted the **lab**: its
`--internal` per-session network attaches exactly one container,
`revalid-juice-shop`, and nothing else — no gateway, no route off the bridge
(ADR-0033 made FR-06 *network membership* rather than an HTTP allowlist). That is
an excellent containment story, but it means the tool can **only** ever retest
the lab. A real revalidation has to hit the actual target the report is about — an
online host — which the current sandbox physically cannot reach by construction.

Álvaro's direction (issue #208): the whitelisted scope must come from the
**finding** (the target the operator writes before launch, already captured as
`target_set`), and it must be **parsed to its host** — a finding about
`https://domain.com/#/login` retests `domain.com` at any path, not just the one
page. And it must work against **online targets**, not only the lab.

The load-bearing decision is *how* egress is bounded once the sandbox is no longer
lab-only. `--internal` (physically no internet) cannot reach `domain.com`. Álvaro
chose the **allowlisting egress proxy** over a firewall IP-allowlist or open
operator-validated egress.

## Decision

**Scope → host.** `revalid.scope.scope_host` parses each scope endpoint to its
host (`host` or `host:port`), dropping scheme/path/query/fragment (incl. SPA hash
routes), preserving sub-domains and port, lower-cased. `scope_hosts` de-dupes a
scope's endpoints. The sandbox is provisioned against these hosts.

**Two provisioning modes, chosen from the scope.**

- **Lab mode (unchanged).** When the scope is empty or resolves to the lab
  (`localhost:3000` / the `revalid-juice-shop` container), provision exactly as
  today: an `internal=True` network with the lab container as its only member.
  Existing behaviour, tests and the egress-lock system test are untouched.
- **Online mode (new).** Otherwise the target is an online host. Provision:
  1. a per-session `internal=True` network — the sandbox still has **no direct
     route to the internet**;
  2. an **egress-proxy** container attached to *both* that internal network and
     an external (internet-capable) network, configured to **allow only the
     scoped host(s)** and deny everything else;
  3. the sandbox container on the internal network with `HTTP_PROXY`/`HTTPS_PROXY`
     pointed at the proxy — so its **only** route to the internet is HTTP(S)
     through the allowlist.

  The containment guarantee is preserved, just narrowed from "the lab" to "the
  operator-declared scope": the sandbox can reach the scoped host(s) over
  HTTP(S) and **nothing else**. Non-HTTP egress (raw TCP scans, etc.) has no
  route out in online mode — it fails closed rather than reaching an unlisted
  host. This is stated plainly, not papered over: online retests are HTTP(S)
  revalidation; a full network scan of an online target is out of scope for this
  containment model.

**Scope stays set-once.** The hosts are fixed when the sandbox is provisioned
(one `target_set`); changing scope still needs a fresh session — unchanged.

## Alternatives considered

- **Firewall IP-allowlist** (resolve the host, `iptables`-allow those IPs).
  Rejected: brittle against DNS changes, CDNs and IP rotation — it either breaks
  legitimate access or silently lets traffic leak to co-resolved IPs; a host
  allowlist at the proxy is what the operator actually declared.
- **Open egress, operator-validated scope** (drop `--internal`, advisory scope).
  Rejected: it discards the "physically cannot reach anything unlisted"
  guarantee entirely — the weakest containment and the biggest departure from the
  thesis's safety story.
- **Keep lab-only.** Rejected: it makes real online revalidation impossible,
  which is the whole point of #208.

## Consequences

- **Easier:** real online-target retests, with the scope taken from the finding
  and enforced as an egress allowlist; the lab path is byte-for-byte unchanged.
- **Security posture:** the containment claim shifts from "lab only, no internet"
  to "the operator-declared scope only, over HTTP(S)". It is still an *enforced*
  boundary (the proxy is the sole route out), not advisory — a weaker guarantee
  than lab-only, honestly narrower, but not open internet.
- **New surface:** a proxy container image + per-session config, a second
  (external) network in online mode, `scope.py`, and scope threaded from the
  finding through the sandbox factory. FR-06 is re-broadened from "network
  membership" to "network membership (lab) **or** a host-allowlisting egress
  proxy (online)".
- **Accepted limits:** non-HTTP egress is unavailable online (fails closed);
  proxy allowlisting is host-based (a host that fronts many origins via one name
  is trusted as a unit). The live online path is validated by a system test
  (Docker + the `sandbox` extra), like the existing egress lock.
- Supersedes the "the only reachable target is `revalid-juice-shop`" absolute in
  ADR-0025/0033 **for online scope**; the lab absolute still holds for lab scope.
