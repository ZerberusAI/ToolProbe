# Live MCP Servers

MCP servers connecting to real APIs with attack scenario support.

## Servers

| Server | Port | API | Status |
|--------|------|-----|--------|
| GitHub | 8020 | github.com | Ready |
| Server-2 | 8021 | server.com | Planned |
| Server-3 | 8022 | server.com | Planned |

## Quick Start

```bash
cd datasets/tool-calling/live-mcp-servers

# Configure
cp .env.example .env
# Add API tokens to .env

# Run
docker-compose up -d

# Test
curl http://localhost:8020/health
```

## GitHub Server

See [github-mcp-server.md](github-mcp-server.md)

## Attack Scenarios

```bash
# List scenarios
curl http://localhost:8020/scenarios

# Switch scenario
curl -X POST http://localhost:8020/scenario/sc01

# Reset
curl -X POST http://localhost:8020/scenario/normal
```
