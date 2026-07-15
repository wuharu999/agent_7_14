# ECS server setup

See [FINAL_SETUP.md](FINAL_SETUP.md), sections **ECS upgrade** and **Security warning before deployment**.

The required new step is creating at least one login account:

```bash
source .venv-ecs/bin/activate
python3 scripts/create_user.py --username admin --role admin
```

The database migration is automatic at application startup.
