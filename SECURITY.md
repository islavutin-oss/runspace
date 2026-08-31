# Security Policy

## Supported versions

Runspace is pre-1.0. Security fixes land on `main` and in the next release;
older versions are not patched.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: go to the
[Security tab](https://github.com/islavutin-oss/runspace/security/advisories/new)
and open a draft advisory. That thread is visible only to you and the
maintainer.

Please include what you can:

- what an attacker can do, and what access they need to start
- a minimal reproduction
- the version or commit you tested

You should get a first reply within a week. If a report is valid you will be
credited in the advisory unless you would rather not be.

## Scope

In scope: anything that lets untrusted input escape its boundary — tool
arguments reaching the shell or filesystem outside the configured root, one
tenant reading another's data, secrets reaching logs or model prompts,
sandbox and gate bypasses.

Out of scope: what an LLM chooses to say. Runspace gives you gates, hooks and
sanitizers to constrain tool use; it cannot make a model's output safe, and
prompt injection that only produces bad text is not treated as a
vulnerability here.
