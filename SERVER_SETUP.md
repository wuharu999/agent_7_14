# ECS server setup

See [FINAL_SETUP.md](FINAL_SETUP.md), sections **ECS upgrade** and **Security warning before deployment**.

The database migration is automatic at application startup. A fresh database
also receives the default `admin` account. Set `DEFAULT_ADMIN_PASSWORD` before
the first startup, or use `Admin#2026!Secured89` once and change it immediately
at `/admin/users`.

```env
WORKER_TIMEOUT=240
FILE_COMMAND_TIMEOUT=60
```
