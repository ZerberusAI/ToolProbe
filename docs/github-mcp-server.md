# GitHub MCP Server

Real GitHub API with attack scenario support.

## Quick Start

```bash
cd datasets/tool-calling/live-mcp-servers

# Configure
cp .env.example .env
# Add: GITHUB_TOKEN=ghp_xxx

# Run
docker-compose up -d

# Test
curl http://localhost:8020/health
```

## Get Token

1. https://github.com/settings/tokens
2. Generate new token (classic)
3. Scope: `repo` or `public_repo`

## Tools (10)

| Tool | Description |
|------|-------------|
| `list_repositories` | List repos |
| `get_repository` | Get repo details |
| `search_code` | Search code |
| `list_issues` | List issues |
| `create_issue` | Create issue |
| `get_issue` | Get issue |
| `list_pull_requests` | List PRs |
| `get_pull_request` | Get PR |
| `get_file_contents` | Read file |
| `list_branches` | List branches |

## Attack Scenarios

| Scenario | Type | Target |
|----------|------|--------|
| `normal` | - | No attack |
| `sc01` | S3 | get_repository exfiltration |

## Example

```bash
curl -X POST http://localhost:8020/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_repository","arguments":{"owner":"anthropics","repo":"claude-code"}},"id":"1"}'
```
