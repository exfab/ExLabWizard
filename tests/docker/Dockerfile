FROM debian:bookworm-slim

# Password-based SFTP NAS emulator for the rclone-only sync path
# (rclone-only migration, 2026-05-26). IT policy forbids SSH keys, so the
# wizard authenticates the SFTP backend with an operator-typed password;
# this container mirrors that by enabling PasswordAuthentication and
# setting a password on the test user. No keys, no PAM challenge flow.
#
# rsync-over-ssh leg (2026-06-10): a second user (RSYNC_USER) is
# provisioned with key-only auth, no SFTP subsystem. The rsync binary
# is installed so ``rsync --server`` over ssh works as on a Synology.

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server \
        rsync \
        coreutils \
        iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

ARG NAS_USER=testuser
ARG NAS_UID=1000
ARG NAS_GID=1000
ENV NAS_USER=${NAS_USER}

ARG RSYNC_USER=rsyncuser
ARG RSYNC_UID=1001
ARG RSYNC_GID=1001
ENV RSYNC_USER=${RSYNC_USER}

RUN groupadd -g ${NAS_GID} ${NAS_USER} \
    && useradd -m -u ${NAS_UID} -g ${NAS_GID} -s /bin/bash ${NAS_USER} \
    && mkdir -p /home/${NAS_USER}/data \
    && chown -R ${NAS_USER}:${NAS_USER} /home/${NAS_USER}

RUN groupadd -g ${RSYNC_GID} ${RSYNC_USER} \
    && useradd -m -u ${RSYNC_UID} -g ${RSYNC_GID} -s /bin/sh ${RSYNC_USER} \
    && mkdir -p /home/${RSYNC_USER}/data \
    && chown -R ${RSYNC_USER}:${RSYNC_USER} /home/${RSYNC_USER}

# Password auth only for the SFTP user; no root login, no pubkey.
# The rsync user gets pubkey-only auth (configured by entrypoint).
RUN sed -ri 's/^#?PermitRootLogin.*/PermitRootLogin no/'                          /etc/ssh/sshd_config \
    && sed -ri 's/^#?PasswordAuthentication.*/PasswordAuthentication yes/'        /etc/ssh/sshd_config \
    && sed -ri 's/^#?PubkeyAuthentication.*/PubkeyAuthentication yes/'            /etc/ssh/sshd_config \
    && sed -ri 's/^#?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' /etc/ssh/sshd_config \
    && sed -ri 's/^#?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' /etc/ssh/sshd_config \
    && printf '\nAllowUsers %s %s\n' "${NAS_USER}" "${RSYNC_USER}" >> /etc/ssh/sshd_config

# Match block for the SFTP user: password auth, SFTP subsystem.
# Match block for the rsync user: pubkey only, no password, no SFTP
# (Synology posture: rsync --server over ssh, nothing else).
RUN printf '\nMatch User %s\n    PasswordAuthentication yes\n    PubkeyAuthentication no\n    ForceCommand internal-sftp\n\nMatch User %s\n    PasswordAuthentication no\n    PubkeyAuthentication yes\n' \
        "${NAS_USER}" "${RSYNC_USER}" >> /etc/ssh/sshd_config

# The keys/ directory is bind-mounted from the host at runtime (see
# docker-compose.yml). The entrypoint generates the keypair there on
# first boot and installs the pubkey as authorized_keys for RSYNC_USER.
RUN mkdir -p /keys && chown root:root /keys

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
