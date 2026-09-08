# Security notes

- Never commit `.env` (contains the DeepSeek API key), `runtime/`, browser sessions, passwords, or production logs.
- Keep the repository private and restrict production deployment access to maintainers.
- Do not expose Open WebUI's port `8080`, PostgreSQL, or Docker's socket to the public network.
- If `.env` leaks, rotate `DEEPSEEK_API_KEY` (revoke the key on the DeepSeek platform), `WEBUI_SECRET_KEY`, the database password, and the administrator password.
- Review upstream image release notes before changing pinned image tags. Do not enable automatic updates in production.
- Share sanitized logs only. Remove prompts, email addresses, authorization headers, and tokens first.
