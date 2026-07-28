# KNX/IP Implementation Guide

This document records the lightweight working agreements for the native KNX/IP
integration. It guides the WIP branch without replacing project-wide guidance in
`CLAUDE.md` or the public design and security documents.

## Scope

- Add a vendor-neutral `knx_ip` HAL platform backed by XKNX.
- Keep `knx_devices.yaml` as the authoritative semantic device mapping.
- Treat an ETS `.knxproj` file as optional validation and DPT enrichment only.
- Preserve the existing Gira X1 platform as an alternative.
- Use simulated transports and real XKNX data types for automated validation;
  physical gateway validation remains an explicit follow-up.

## Source-of-truth contract

The device map alone defines stable device IDs, names, rooms, floors, device
classes, capabilities, command addresses, status addresses, and explicit DPT
overrides. KNX Agent exposes no device or writable address that is absent from
that map.

An optional ETS project may verify address existence, communication-object
links, and DPT consistency. It may supply a missing DPT only when the project
contains one unambiguous value. It must not create devices, infer command/status
roles, rename devices, assign rooms, add capabilities, or make an address
writable.

## Coding guidelines

1. Work in small vertical TDD slices: failing contract test, minimal behavior,
   green tests, then local cleanup.
2. Prefer existing `Platform`, HAL model, lifecycle, and state conventions over
   introducing parallel abstractions.
3. Keep protocol concerns in `src/integrations/` and HAL translation in
   `src/hal/platforms/`.
4. Keep modules below roughly 400 lines when a split has a clear responsibility;
   do not create tiny files solely to satisfy a line count.
5. Keep public configuration explicit and stable. Validate invalid or ambiguous
   mappings early and report actionable errors.
6. Fail closed for unknown sensor state and ambiguous DPTs. Never infer a state
   from an outbound command.
7. Do not expose a raw group-address write tool. Agents continue to act only
   through named HAL devices and existing guardrails.
8. Keep credentials in environment variables and private key/project/mapping
   files out of Git and normal logs.
9. Mock network I/O, not business behavior. Exercise real XKNX configuration and
   DPT objects where this does not open sockets.
10. Preserve Gira X1 behavior and avoid unrelated refactors while the feature is
    under development.
11. Use clear names and table-driven mappings where they reduce repetition, but
    prefer straightforward code over speculative frameworks.
12. After feature completion, perform separate simplification passes for
    duplication, unnecessary abstractions, naming, error handling, and file size.

## Validation gates

Each slice runs its focused tests. Before completion, run Ruff, formatting,
Mypy, the full pytest suite with branch coverage, dependency audit, Gitleaks,
and repository diagnostics. Hardware compatibility must not be claimed without
external deployment evidence.
