# Public alpha scope

This repository starts with a sanitized, fresh-history snapshot of SPAM's source following private development. It does not expose the original repository's history or change that repository's visibility.

## Included

- Application source, local UI templates, shared fact-name/question rules, and example configuration with unknown values.
- Synthetic or redacted regression fixtures and development documentation.
- MIT license, contribution guidance, and security/data-flow notes.

## Excluded

- Applicant profiles and resumes, accounts and passwords, mailbox records, private extensions, and local personal notes.
- Application queues, trackers, results, run reports, screenshots, raw page captures, and browser profiles/cookies.
- Environment files, credential stores, virtual environments, model weights, build outputs, and the private Git history.

The snapshot was exported through an explicit source/documentation/test allowlist rather than by copying the entire directory. Release checks include secret-pattern scanning, local identifier scanning using private rules (not distributed), manual review, and automated tests. These checks are not a proof of anonymity or a full security audit.

This alpha is distributed as a source checkout. Some resources are checkout-relative; do not assume that building a wheel produces a complete standalone installation. Hosted AI, Gmail, optional MLX, and live employer integrations require independent setup. Live-site reliability and any user/adoption counts are not certified by the release.

Maintenance priorities: improve contributor setup and packaging, expand isolated regression coverage, clarify integration data flows, and keep private application data out of issues and fixtures.
