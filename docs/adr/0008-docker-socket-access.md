# ADR-0008: Host Docker Socket Access from the Backend

- **Status:** Accepted (2026-08-25)
- **Deciders:** @Narendarcodes
- **Supersedes:** CRITICAL_BUGS.md finding "Docker socket exposed to backend"
- **Related:** issue #14

## Context

The `backend` container mounts the host Docker socket
(`/var/run/docker.sock`) so it can manage the `whatsapp_hermes` container at
runtime. Two distinct capabilities depend on it:

1. **Pairing** — `start_pairing_session` defaults its `runner` to
   `docker_manager.exec_detached` and execs the Baileys bridge-coupling command
   (`build_pairing_command`) *inside* the hermes container. Without a working
   socket the call returns `{"started": False, "reason": "exec_failed"}` and
   WhatsApp cannot be paired at all.
2. **Auto-restart** — `docker_manager.restart_hermes_agent()` restarts hermes
   after credentials land (pairing watchdog) and after OAuth re-sync.

Exposing the raw socket grants the backend **root-equivalent host control**:
any code-execution bug in the backend (or a poisoned dependency) becomes full
host compromise — create/delete any container, read any volume, pull images,
pivot to the network. This is the single largest trust-boundary gap in the
stack, which is otherwise tight: secrets are fail-fast, all inbound webhook
traffic is signature-gated, and the only other exposed surface is the
authenticated admin dashboard.

## Decision

**Keep the host Docker socket mounted into `backend`, and formally accept the
residual risk.** It is not dropped because doing so silently breaks the core
WhatsApp pairing flow (not merely a convenience), and the user explicitly
deferred this item. The risk is mitigated by the compensating controls below
rather than by removal.

### Compensating controls (must hold)

1. **No unauthenticated ingress.** The backend is reachable only via
   HMAC-signed Evolution webhooks (verified at the router, not the handler) and
   the authenticated dashboard. There is no anonymous API surface that could
   reach `docker_manager`.
2. **Graceful degradation already in place.** `DockerManager` catches socket
   errors and returns `False`; call sites (`oauth.py`, `setup.py`) already
   no-op or log. Removing the socket would degrade cleanly — pairing is the only
   hard dependency.
3. **Least-privilege is *not* currently enforced** (the full socket is mounted).
   The recommended remediation below should be scheduled.

## Consequences

- **Positive:** pairing and auto-restart keep working with zero behaviour change
  for the owner; no migration needed right now.
- **Negative:** the host remains one backend-RCE away from full compromise. This
  is documented, not hidden.
- **Operational:** if the socket is ever removed, the owner must run
  `docker restart whatsapp_hermes` manually after scanning the QR and the
  dashboard pairing button will report `exec_failed`.

## Future remediation (recommended, not blocking)

Decouple pairing from raw Docker exec, in priority order:

1. **Socket proxy.** Mount `tecnativa/docker-socket-proxy` (or equivalent)
   instead of the raw socket and allow **only**
   `POST /containers/whatsapp_hermes/exec`,
   `POST /containers/whatsapp_hermes/restart`,
   `POST /containers/whatsapp_hermes/start`,
   `POST /containers/whatsapp_hermes/stop`, and read-only container inspect.
   This removes image-pull / volume / other-container control while keeping
   pairing working.
2. **Hermes-native pairing.** Expose a pairing trigger over hermes's own
   authenticated API so the backend never needs exec at all, then drop the
   socket entirely (the original CRITICAL_BUGS goal).

Either path lets us delete the socket; until then this ADR records why it stays.
