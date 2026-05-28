FROM debian:bookworm-slim

# Password-based SFTP NAS emulator for the rclone-only sync path
# (rclone-only migration, 2026-05-26). IT policy forbids SSH keys, so the
# wizard authenticates the SFTP backend with an operator-typed password;
# this container mirrors that by enabling PasswordAuthentication and
# setting a password on the test user. No keys, no PAM challenge flow.

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server \
        coreutils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

ARG NAS_USER=testuser
ARG NAS_UID=1000
ARG NAS_GID=1000
ENV NAS_USER=${NAS_USER}

RUN groupadd -g ${NAS_GID} ${NAS_USER} \
    && useradd -m -u ${NAS_UID} -g ${NAS_GID} -s /bin/bash ${NAS_USER} \
    && mkdir -p /home/${NAS_USER}/data \
    && chown -R ${NAS_USER}:${NAS_USER} /home/${NAS_USER}

# Password auth only: no root login, no pubkey. The test user's password
# is set at boot by the entrypoint from ${NAS_PASSWORD} so it never has
# to be baked into the image.
RUN sed -ri 's/^#?PermitRootLogin.*/PermitRootLogin no/'                          /etc/ssh/sshd_config \
    && sed -ri 's/^#?PasswordAuthentication.*/PasswordAuthentication yes/'        /etc/ssh/sshd_config \
    && sed -ri 's/^#?PubkeyAuthentication.*/PubkeyAuthentication no/'             /etc/ssh/sshd_config \
    && sed -ri 's/^#?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' /etc/ssh/sshd_config \
    && sed -ri 's/^#?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' /etc/ssh/sshd_config \
    && printf '\nAllowUsers %s\n' "${NAS_USER}" >> /etc/ssh/sshd_config

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
