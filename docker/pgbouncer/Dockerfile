FROM edoburu/pgbouncer:latest

# DECISION: Use edoburu/pgbouncer instead of upstream pgbouncer/pgbouncer
# because the upstream 1.x images are built on Alpine 3.9, which is past
# EOL and whose package mirrors (dl-cdn.alpinelinux.org/alpine/v3.9) are
# no longer reachable — apk add fails. edoburu ships current pgbouncer
# (1.25.x) with psql preinstalled and a working entrypoint.
#
# The image's entrypoint supports AUTH_TYPE=scram-sha-256 directly; we
# set that and the rest of the pool/timeout config in docker-compose.yml
# as environment variables (which the entrypoint templates into
# /etc/pgbouncer/pgbouncer.ini on startup).
#
# SECURITY: with this image, the plaintext DB_PASSWORD lands in
# /etc/pgbouncer/userlist.txt inside the pgbouncer container. The file
# is chmod 600 owned by pgbouncer; it never leaves the container. Wire
# auth pgbouncer <-> db is still SCRAM-SHA-256. The alternative
# (fetching rolpassword from pg_authid) needs a custom entrypoint, but
# the EOL Alpine 3.9 base makes that path fail to build. Documented and
# accepted; revisit when upstream ships a current base or we move to a
# distroless pgbouncer image.