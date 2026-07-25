# 0045. Online-scope egress: a per-session L3 gateway, not an HTTP proxy

Date: 2026-07-25
Status: proposed

Supersedes [ADR-0041](0041-scope-egress-proxy-online-targets.md).

## Context

ADR-0041 gave the retest sandbox a way to reach an **online** target (not just
the lab): a per-session Squid proxy that the sandbox's HTTP(S) traffic passed
through, allowlisting only the scoped host. Two things were wrong with it.

First, it never worked — the provisioning code shipped `# pragma: no cover` with
two fatal bugs (see the ADR-0041 update note). That was fixed, and then the real
limitation surfaced.

Second, and fundamentally: **a proxy is L7 — HTTP(S) only.** The whole value of
the agentic console is that the agent has a full toolbox (nmap, sqlmap, hydra,
raw-socket probes). None of those speak to an HTTP proxy, so against an online
target they had no route out. An egress mechanism that only carries HTTP defeats
the tool it is meant to enable.

Álvaro's direction: move egress control to **L3 (an IP allowlist)** so *every*
tool works, and enforce it **outside the sandbox** so the model cannot change it
— the same "reachable by construction, not by a check" property the lab path has.

## Decision

**Per-session egress gateway; the sandbox runs inside its network namespace.**

For an online scope, provision two containers on a per-session bridge network:

- a **gateway** (the sandbox image) with `cap_add=[NET_ADMIN]`, whose entrypoint
  installs an `iptables` **OUTPUT allowlist** — default-drop, then permit only
  loopback, established/related return traffic, the one allowed DNS resolver on
  port 53, and each resolved scope IP (any protocol) — then blocks on
  `sleep infinity` to keep the namespace alive;
- the **sandbox** itself, run with `network_mode=container:<gateway>` so it
  *shares the gateway's network namespace*, with `cap_add=[NET_RAW]` (SYN scans,
  raw sockets) but **not** `NET_ADMIN`.

Because the sandbox's packets originate in the gateway's namespace, the gateway's
allowlist is the sandbox's only route out — and because the capability to edit
`iptables` lives in a *different* container the sandbox cannot reach, and the
sandbox's own bounding set excludes `NET_ADMIN`, no command the agent runs can
widen scope. `iptables -F` from inside the sandbox fails with `Operation not
permitted`, verified live. This is the L3 analogue of the lab's "network
membership" guarantee: enforced by topology, not by inspecting commands.

**Scope → IPv4, pinned at launch.** `resolve_scope_ips` resolves each scope host
to its **IPv4** addresses (`getaddrinfo(..., AF_INET)`); those are allowlisted and
written into the shared `/etc/hosts` (via the gateway's `extra_hosts`) so name
resolution needs no DNS. IPv6 is resolved for nothing and blanket-dropped
(`ip6tables -P OUTPUT DROP`, best-effort): Docker's bridge is v4-only, and feeding
a v6 address to the v4-only `iptables` would break the firewall. A host that does
not resolve to any IPv4 address fails closed.

**One DNS resolver is allowed.** Tools that run their own resolver instead of
`getaddrinfo` — nmap most notably — need to resolve the scope name over the wire,
so one resolver (default `1.1.1.1`, `$REVALID_DNS_RESOLVER`) is permitted on port
53. The allowlist still admits only the scoped IPs, so a resolved-but-off-scope
address is dropped regardless. The residual is a small DNS side-channel, accepted
under the single-user threat model (ADR-0008).

**The sandbox image carries `iptables`/`iproute2`, and its file capabilities are
stripped.** The gateway reuses the sandbox image (built locally, never pulled), so
that image now installs `iptables`. It also strips file capabilities from its
binaries at build time: Kali sets `cap_net_admin` on nmap, and the kernel refuses
to `execve` a file whose *effective* file-caps exceed the container's shrunk
bounding set — so nmap failed with `Operation not permitted` until stripped. The
sandbox runs as root with `NET_RAW`, so scans still work; the file caps (only for
a non-root nmap) are dead weight here.

**Lab scope is unchanged.** An empty scope, or one that resolves to the lab, keeps
the ADR-0025 `--internal` network with the lab container attached — it never needs
DNS or egress.

**Lifecycle: per-session, reaped by name.** Every per-session resource (network,
sandbox container, gateway container) is named by session id. `stop()` and the
stale-reap both tear down **by name**, so a freshly constructed `DockerSandbox`
can reap resources it never created. Deleting a report therefore takes down the
sandbox of *every* session under it — the live ones ended cleanly, the rest reaped
by id — closing a real leak: a session orphaned by a backend restart (containers
still up, but dropped from the in-memory registry) used to survive its report.

## Consequences

- **All tools reach the scoped host.** Validated live against `www.hackthissite.org`
  and `example.com`: curl → 200 at any path, `nmap` → open ports, an off-scope host
  refused, `iptables -F` denied.
- **FR-06 now reads:** egress is bounded by *network membership* (lab) **or** a
  *per-session L3 egress gateway* (online) — never an HTTP-layer check.
- **The IP allowlist cannot follow a hostname across rotating CDN IPs.** IPs are
  pinned at launch; a target behind a large, rotating CDN may present addresses not
  seen at resolution and those connections drop. This is the mirror of the proxy's
  HTTP-only limit, and the honest cost of L3 — stated, not hidden. For a scoped
  retest of a specific host it is a non-issue.
- **IPv6-only targets are unreachable** (v4-only by design); they fail closed.
- **The gateway holds `NET_ADMIN`.** That capability lives in a helper container the
  sandbox cannot reach, not in the sandbox — strictly better than the alternative
  of giving the sandbox itself `NET_ADMIN` and trusting it not to flush the rules.
  True host-kernel enforcement was rejected: it needs host root the app has in
  neither run mode (no `sudo` from a checkout; only the Docker socket, not host
  netfilter, in the container deployment).
- **The live gateway path stays `# pragma: no cover`** (it drives a real daemon),
  but its seams are now unit-tested against a fake client — the firewall script,
  the entrypoint/caps/`network_mode` wiring, name-based teardown — and a `system`
  test exercises the whole path against a real host nightly. Both classes of bug
  that sank ADR-0041 (a broken config, a swallowed command) would now be caught.

## Alternatives considered

- **In-sandbox firewall** (the sandbox holds `NET_ADMIN` and installs its own
  allowlist) — rejected: an approved command could `iptables -F` and widen egress.
  The separate namespace-owning gateway removes the capability from the model's
  reach entirely.
- **Privileged host-level iptables** (a `--privileged`/host-network helper editing
  host netfilter) — rejected: heavier, fragile host-state cleanup, and only viable
  under the socket-mount deployment. The gateway gives the same "enforced outside
  the sandbox" property with no host privilege, portable across both run modes.
- **Keeping the L7 proxy** — rejected: it is the thing that made every non-HTTP tool
  useless against an online target.
- **Allowlisting hostnames instead of IPs** — impossible at L3; that is precisely
  what a proxy does, and why it was tried first. The IP-pinning caveat is the price
  of letting all tools through.
- **A single shared gateway for all sessions instead of one per session** —
  rejected. The clean netns-sharing design is inherently 1:1: N sandboxes sharing
  one gateway's namespace would share one IP and one stack, so the firewall could
  not tell their traffic apart and every sandbox would get the *union* of all
  scopes — a scope leak. A shared gateway therefore forces a heavier **router**
  model (sandboxes on a common network, per-source-IP `FORWARD` rules), which is
  worse here on two counts. First, correctness: scope becomes per-IP rule keying
  vulnerable to an IP-reuse race, where a per-session gateway makes scope a property
  of a dedicated namespace. Second, isolation: a shared network lets the sandboxes
  **see each other** by default (Docker bridges permit intra-network traffic), so
  one session could scan or tamper with another's sandbox unless extra
  cross-sandbox `DROP` rules are added and maintained — where per-session networks
  make the sandboxes mutually invisible by construction. The only thing the shared
  model saves is container count, which is irrelevant for a single-user tool that
  runs a retest at a time. It also ties lifecycles together (deleting one session
  must not tear down a gateway others use), defeating the per-session cleanup this
  ADR requires.
