# tech-brief

Basic Python MCP server template (stdio) exposing Hacker News tools: `get_headlines` and `get_article` (official HN API), `search_stories` (Algolia).

## Run
    uv sync
    uv run mcp dev src/tech_brief/server.py   # MCP Inspector
    uv run tech-brief                          # stdio server

## Use in Claude Code
    claude mcp add tech-brief -- uv run --directory $PWD tech-brief
(or use the included `.mcp.json`)

Add more tools in `src/tech_brief/server.py` with `@mcp.tool()`.

## Development
    uv sync                          # installs dev tools (ruff, mypy, pre-commit)
    uv run pre-commit install        # run checks automatically on every commit
    uv run pre-commit run --all-files

Checks: whitespace/EOF/YAML/TOML hygiene, `ruff` (lint + isort + format, line length 100),
and `mypy --strict`. Config lives in `pyproject.toml` and `.pre-commit-config.yaml`.
