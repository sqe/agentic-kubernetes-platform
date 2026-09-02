# Security policy

Please report vulnerabilities privately to the maintainers rather than opening
a public issue. Deployments must provide secrets through an external secret
manager or pre-created Kubernetes Secrets. Images run as non-root where their
upstream GPU runtime permits it. Enable and tailor the optional network policy
for the deployment's namespaces; service accounts default to no token mount.

The examples are a starting point, not a substitute for threat modeling. Use
TLS/SASL for Kafka, private object-store endpoints, signed images, and restricted
egress in production.
