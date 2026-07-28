# Security Policy

## Supported Versions

Security fixes are applied to the latest released version.

## Reporting a Vulnerability

Do not disclose vulnerabilities in public issues. Use GitHub's private
security advisory feature for this repository. Include affected versions,
reproduction steps, impact, and any proposed mitigation.

The maintainers will acknowledge a report as soon as practical, investigate
it, and coordinate disclosure. Never include production credentials,
personally identifiable information, tax records, or exchange exports in a
report.

## Security Principles

- Secrets are supplied through the environment and never committed.
- Imported financial records are treated as sensitive data.
- Dependencies and container images are pinned and reviewed.
- Public interfaces expose only the minimum required information.
- Automated recommendations never execute trades.
