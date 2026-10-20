---
name: deployment
description: "Application deployment and DevOps practices"
domain:
  - devops
  - deployment
trigger: "When deploying applications or managing infrastructure"
always_on: false
---

# Deployment Skill

## Pre-deployment Checklist
1. All tests passing
2. No security vulnerabilities (check dependencies)
3. Environment variables configured
4. Database migrations ready
5. Backup plan in place

## Common Deployment Targets
- **GitHub Pages** — Static sites
- **Vercel / Netlify** — Frontend apps
- **Docker** — Containerized applications
- **Cloud VMs** — Custom deployments

## Best Practices
- Never deploy directly to production without testing
- Use environment variables for all secrets
- Set up CI/CD pipelines when possible
- Keep deployment scripts version-controlled
- Monitor after deployment
- Have a rollback plan

## Security
- No secrets in code or version control
- Use HTTPS everywhere
- Keep dependencies updated
- Set appropriate file permissions
- Use least-privilege access
