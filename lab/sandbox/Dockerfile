# The FR-17 retest sandbox image (issue #105).
#
# The agent runs every approved command inside this image. For a lab retest that
# is a per-session Docker `--internal` network whose only other member is the
# authorised target (ADR-0025); for an online retest the same image *also* backs
# the per-session egress gateway (ADR-0045), which is why `iptables`/`iproute2`
# are installed — the gateway installs the L3 allowlist and the sandbox, sharing
# its namespace but holding no NET_ADMIN, cannot touch it. That egress lock is
# the reason the toolbox has to be baked in: the container has no general route
# out, so it can neither `apt install` nor `pip install` anything at runtime.
# What ships here is what the agent has.
#
# Kali is the base because its repositories carry the whole arsenal, so adding a
# tool later is one line rather than a hunt for a third-party build.
#
# Deliberately NOT installed: `kali-linux-headless` (a multi-gigabyte
# metapackage, most of it irrelevant to web-application revalidation) and
# anything needing a live network to be useful (wordlist downloaders, updaters).
# The list below is curated for the retest job and is meant to be edited.
FROM kalilinux/kali-rolling:latest

# One layer, no recommends, apt lists dropped — this image is rebuilt rarely and
# pulled into every session, so size is worth the ceremony.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      coreutils \
      curl \
      dnsutils \
      hydra \
      iproute2 \
      iptables \
      iputils-ping \
      jq \
      libcap2-bin \
      netcat-traditional \
      nikto \
      nmap \
      openssl \
      python3 \
      sqlmap \
      wget \
      whatweb \
 && rm -rf /var/lib/apt/lists/* \
 # Strip file capabilities from every binary that ships with them (Kali sets
 # cap_net_admin+cap_net_raw on nmap). The online egress gateway (ADR-0045) runs
 # the sandbox with a bounding set that deliberately excludes NET_ADMIN, and the
 # kernel refuses to execve a file whose *effective* file-caps exceed that set —
 # so nmap failed with "Operation not permitted" until stripped. The sandbox runs
 # as root with NET_RAW in its bounding set, so raw-socket scans still work; the
 # file caps (needed only for a non-root nmap) are dead weight here.
 && getcap -r / 2>/dev/null | awk '{print $1}' | xargs -r setcap -r

# The agent never runs a login shell; the container idles on `sleep infinity`
# and each approved command is a separate `exec` (see DockerSandbox.exec).
CMD ["sleep", "infinity"]
