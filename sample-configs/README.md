# Sample Configs

This folder contains sanitized configuration examples that show integration shape without exposing private deployment details.

Guidelines:

- Use placeholders and environment variables for URLs, model names, and provider settings.
- Do not commit real `librechat.yaml` files, OAuth credentials, API keys, or service hostnames.
- Keep examples small and focused on how the services connect.
- Treat production configuration as private runtime state, not repository content.

Current examples:

- `librechat.rag-mcp.example.yaml` — minimal LibreChat wiring for the RAG MCP server.
