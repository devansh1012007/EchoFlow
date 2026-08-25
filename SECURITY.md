# Security Policy

## Supported Versions
We actively maintain and provide security updates for the latest release on the `main` branch. Older versions or experimental branches are not guaranteed to receive security patches.

## Reporting a Vulnerability
We take the security of EchoFlow seriously. If you believe you have found a security vulnerability, please report it to us as described below.

**Preferred Method: GitHub Private Vulnerability Reporting**
Please use the "Security" tab in this repository to submit a private vulnerability report. This ensures your report is encrypted, tied directly to the codebase, and managed under GitHub's embargo tools.

**Alternative Method: Email**
If you cannot use GitHub, please email us at [security@echoflow.dev](mailto:gadev2007@gamil.com) with the subject line: `[SECURITY] EchoFlow Vulnerability Report`.

Please include:
- A clear description of the vulnerability.
- Steps to reproduce (with proof-of-concept code or screenshots if applicable).
- The potential impact of the vulnerability.

## Disclosure Timeline
We are committed to a sustainable and transparent response process:
- **48 hours**: Acknowledgment of your report.
- **14 days**: Target time for a fix or mitigation for Critical/High severity issues.
- **30 days**: Coordinated public disclosure (after a patch is released), unless you request a delay.

## Scope
### In Scope
- The core Django backend and Django REST Framework API.
- Celery task queues and the AI/media processing pipeline.
- Docker Compose deployment configuration.
- Authentication, authorization, and data access controls.

### Out of Scope
- The `frontend` directory (provided for demonstration purposes only).
- Vulnerabilities in third-party dependencies (please report these directly to the respective maintainers or via GitHub Dependabot alerts).
- Denial of Service (DoS/DDoS) attacks.
- Social engineering or physical security attacks.
- Issues requiring physical access to a user's device.

## Reward Policy
As an open-source project, we currently **do not offer monetary bug bounties**. However, we deeply value responsible disclosure and will gladly provide public credit in our Security Hall of Fame (with your permission) once the vulnerability is patched.

## Known Security Mitigations
EchoFlow is designed with security and privacy in mind. Current architectural safeguards include:
- **Data Protection**: User emails are encrypted at rest using Fernet symmetric encryption.
- **Authentication**: JWT-based authentication with short-lived access tokens and secure refresh token rotation.
- **Isolation**: Services are containerized via Docker, separating the API, database, Redis, and Celery workers.
- **Scraping Safeguards**: The audio ingestion pipeline strictly respects `robots.txt`, enforces rate limits, and validates content types/licenses.

## Compliance & Legal Disclaimer
- **Privacy**: EchoFlow is designed to be GDPR-aware (data minimization, encryption). 
- **Certifications**: This project is **not** currently certified for HIPAA, PCI-DSS, or SOC2 compliance. Do not use this software to process regulated health or financial data without conducting your own audit.
- **Safe Harbor**: We consider security research and vulnerability disclosure activities conducted consistent with this policy to be "authorized" under the Computer Fraud and Abuse Act. We will not initiate legal action against researchers who act in good faith. 
- **Prohibited Actions**: Do not perform destructive testing, exfiltrate production user data, or degrade service for other users.

## Incident Response Process
In the event of a confirmed security incident, our process is:
1. **Triage**: Assess severity and scope of the report.
2. **Contain**: Isolate affected systems or revoke compromised credentials/tokens.
3. **Remediate**: Develop, test, and deploy a patch.
4. **Disclose**: Notify affected parties (if applicable) and publish a security advisory.

## Future Security Roadmap
We are actively working to improve our security posture. Planned improvements include:
- [ ] GPG signing of Docker images and release artifacts.
- [ ] Automated SAST/DAST pipelines (e.g., CodeQL, Bandit).
- [ ] Formalized rate-limiting at the API gateway layer.

---
*Last updated: August 2026*