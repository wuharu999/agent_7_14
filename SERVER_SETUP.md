# ECS server setup

See [FINAL_SETUP.md](FINAL_SETUP.md), sections **ECS upgrade** and **Security warning before deployment**.

The required new step is creating at least one login account:

```bash
source .venv-ecs/bin/activate
python3 scripts/create_user.py --username admin --role admin
```

The database migration is automatic at application startup.

Keep the ECS authoring timeout longer than the Worker Claude timeout:

```env
WORKER_TIMEOUT=240
FILE_COMMAND_TIMEOUT=60
AUTHORING_COMMAND_TIMEOUT=270
```
