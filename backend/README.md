# citi-project backend

The Python services, agents and CLIs. See the repository [README](../README.md) and
[`instructions/IMPLEMENTATION_PLAN.md`](../instructions/IMPLEMENTATION_PLAN.md).

Run everything from this directory:

```powershell
uv sync --extra agents
uv run pytest
uv run citi-agent ask "What do we know about V-003?"   # after loading .env into the shell
uv run langgraph dev --allow-blocking                  # LangGraph Studio; loads .env itself
```

The shared data pack stays in `../initial_plan/`.
