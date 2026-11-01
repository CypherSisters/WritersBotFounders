# A BuildBot inside one container, for a computer that already runs other things (D132). Everything it keeps is on the
# mount at /data/bot: the image holds only what can't be downloaded without root. See run.sh next to this file.
# Chromium: how a try reads an app's page, since most apps draw theirs with JavaScript.
# Node.js: the same pinned copy and hashes as scripts/install_tools.sh.
FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends curl git procps socat ca-certificates xz-utils chromium \
    && rm -rf /var/lib/apt/lists/* && useradd -u 1000 -m owner \
    && case "$(uname -m)" in \
         aarch64) N=linux-arm64; S=724282c3b43aec998aa9527380465b45d229e021b58035f5f4f63095eabfe5d5 ;; \
         x86_64)  N=linux-x64;   S=6e1db87ef58b8819e5d5402eff1536491b18edd8eb7bee5ef7897876e88dc5ff ;; \
         *) echo "No Node.js is pinned for this computer."; exit 1 ;; \
       esac \
    && curl --proto '=https' -fsSL -o /tmp/node.tgz "https://nodejs.org/dist/v24.21.0/node-v24.21.0-$N.tar.gz" \
    && echo "$S  /tmp/node.tgz" | sha256sum -c - \
    && mkdir -p /opt/node && tar -xzf /tmp/node.tgz -C /opt/node --strip-components=1 && rm /tmp/node.tgz
ENV PATH=/opt/node/bin:$PATH
USER 1000
WORKDIR /home/owner
