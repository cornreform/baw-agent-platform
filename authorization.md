# Authorization

### Allowed
- All API calls, file ops (read/write), git, cron, delegation
- HTML/PDF generation, image generation via API
- System commands via bash (sudo available)

### Forbidden
- `rm -rf` on system directories
- `/etc/*` modification without explicit approval
- systemd config changes without user OK
