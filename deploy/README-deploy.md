# Server notes — recipes.dustincremascoli.com

Runs on the same EC2 box as the main site, as a separate systemd
service behind the same nginx.

| | |
|---|---|
| Code | `/home/ec2-user/recipes_flask` |
| Secrets | `/etc/recipes/recipes.env` (root:ec2-user, 0640) |
| Service | `recipes-web.service` |
| Socket | `/run/recipes/recipes.sock` |
| nginx | `/etc/nginx/conf.d/recipes.conf` |
| Database | `recipes` on the local Postgres, as role `recipes_app` |
| Logs | `journalctl -u recipes-web`, `/var/log/nginx/recipes.*.log` |

## The socket group, which is easy to get wrong

The unit sets `Group=nginx` while running as `User=ec2-user`. `RuntimeDirectory`
and the gunicorn socket inherit the **group**, and that is the only reason nginx
can open the socket.

With `Group=ec2-user` (the obvious choice) the directory comes out as
`drwxr-x--- ec2-user:ec2-user` and every proxied request fails with:

```
connect() to unix:/run/recipes/recipes.sock failed (13: Permission denied)
```

The app itself stays healthy, so this looks like an app outage and isn't one.
Check `sudo ls -ld /run/recipes` first — it should be group `nginx`.

## Database role

`recipes_app` holds `SELECT, INSERT, UPDATE, DELETE` on `authors` and `recipes`
and `USAGE, SELECT` on their sequences. Nothing else: no `CREATE` on the schema,
no rights on `recipes_backup`. Verify with:

```sql
select table_name, string_agg(privilege_type, ',' order by privilege_type)
from information_schema.table_privileges
where grantee = 'recipes_app' group by table_name;
```

A quick negative check — this must fail:

```bash
psql -h 127.0.0.1 -U recipes_app -d recipes -c 'drop table recipes;'
-- ERROR: must be owner of table recipes
```

## TLS

`provision.sh` installs an **HTTP-only** vhost when no certificate exists, because
nginx refuses to start with an `ssl_certificate` path that isn't there yet. Once
DNS resolves:

```bash
sudo certbot --nginx -d recipes.dustincremascoli.com
sudo bash /home/ec2-user/recipes_flask/deploy/provision.sh   # installs the hardened vhost
```

## Smoke-testing before DNS exists

Use a Host header against localhost:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: recipes.dustincremascoli.com' http://127.0.0.1/recipes
```

Note that **login and any form POST will appear broken over plain HTTP.** The
session cookie is `Secure`, so it is not sent back on an HTTP request, the CSRF
token cannot validate, and the form silently re-renders with a 200. That is
correct production behavior, not a bug. To exercise a form end-to-end before TLS
is live, temporarily set `SESSION_COOKIE_SECURE=0` in the env file, restart, test,
then set it back.

## Backups

The database is covered by the existing nightly `pg-backup.sh` → S3 timer, which
dumps all databases on the instance. A pre-migration dump was taken to
`/tmp/recipes_pre_migrate_<date>.dump`; move it somewhere durable if you want to
keep it, as `/tmp` does not survive a reboot.
