---
name: cron
description: Schedule reminders, recurring tasks, and one-time jobs using system crontab.
---

# Cron

Schedule tasks using the system crontab via `shell_exec`.

## List current cron jobs

```bash
crontab -l
```

## Add a recurring job

```bash
(crontab -l 2>/dev/null; echo "*/20 * * * * echo 'Time to take a break!' >> /tmp/opc-reminders.log") | crontab -
```

## Common schedules

| Schedule | Cron expression |
|----------|----------------|
| Every 20 minutes | `*/20 * * * *` |
| Every hour | `0 * * * *` |
| Every day at 8am | `0 8 * * *` |
| Weekdays at 5pm | `0 17 * * 1-5` |
| Every Monday at 9am | `0 9 * * 1` |

## One-time scheduled task

Use `at` for one-time jobs:
```bash
echo "echo 'Meeting reminder' >> /tmp/opc-reminders.log" | at 14:30
```

Or schedule with a specific date:
```bash
echo "echo 'Deadline reminder' >> /tmp/opc-reminders.log" | at 10:00 2026-03-20
```

## Remove a job

Edit the crontab directly:
```bash
crontab -e
```

Or filter out a specific job:
```bash
crontab -l | grep -v 'pattern-to-remove' | crontab -
```

## Notes

- Use full paths in cron commands (cron has a minimal `$PATH`).
- Redirect output to a log file or `/dev/null` to avoid mail noise.
- Check `at` availability: `which at` (install with `apt install at` if missing).
