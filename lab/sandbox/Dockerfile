# The FR-17 retest sandbox image (issue #105).
#
# The agent runs every approved command inside this image, on a per-session
# Docker `--internal` network whose only other member is the authorised lab
# target (ADR-0025). That egress lock is the reason the toolbox has to be baked
# in: the container has no route to the internet, so it can neither `apt install`
# nor `pip install` anything at runtime. What ships here is what the agent has.
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
      iputils-ping \
      jq \
      netcat-traditional \
      nikto \
      nmap \
      openssl \
      python3 \
      sqlmap \
      wget \
      whatweb \
 && rm -rf /var/lib/apt/lists/*

# The agent never runs a login shell; the container idles on `sleep infinity`
# and each approved command is a separate `exec` (see DockerSandbox.exec).
CMD ["sleep", "infinity"]
