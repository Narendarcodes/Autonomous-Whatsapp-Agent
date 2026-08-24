# 7. Deep Message Intake Module over Durable Streams

Date: 2026-08-24

## Status

Accepted

## Context

The inbound message pipeline lives inline in `api/webhooks.py` (~550 lines): HMAC
verification, idempotency, rate limiting, loop guards, in-memory per-chat asyncio
queues, DPDP group filtering, owner/ACL evaluation, command intercepts, quoted-reply
parsing, and Hermes dispatch all share one router plus two module-level mutable dicts.
ADR-0004 requires the webhook router to be "extremely thin"; today it is the fattest
module in the codebase. The architecture review (2026-08-24, candidate 1) identified
this as the highest-risk shallowness in the system, and is the direct root cause of
tracker issues #6 (messages lost on restart), #11 (weak loop prevention) and
#12 (user-creation race).

## Decision

Consolidate message intake behind one deep module — the **Inbox** — with the interface:

```
accept(msg: InboundMessage) -> Ack
```

Design points locked during review:

1. **Seam placement** — HMAC signature verification and Evolution payload parsing stay
   at the HTTP edge as an *adapter*. The Inbox only ever sees a trusted, normalized
   `InboundMessage`. If Evolution API changes shape, only the adapter changes.
2. **Durable queue** — per-chat asyncio queues are replaced by a Redis Streams
   consumer group (`omniwa:inbound` / `agent_workers`) as the Inbox's internal
   implementation. Queued-but-unprocessed messages survive restarts via PENDING
   re-claim on boot. The orphaned stream helpers in `db/redis_client.py`
   (`enqueue_message`, `schedule_job`, `fetch_due_jobs`, `cancel_jobs_for_event`)
   are absorbed here or deleted (see ADR-0001/0002 for scheduler deletion).
3. **Ack semantics** — `Ack` is an opaque admission enum (`accepted · duplicate ·
   rate_limited · rejected_queue_full · ignored`). It describes **admission only**, never
   delivery. Post-admission gates run asynchronously
   inside the module; their outcomes never appear in `Ack`.
4. **Gate order is load-bearing and frozen** inside the module:
   idempotency → rate limit → loop guard → queue cap ‖ (async)
   DPDP filter → owner resolution → ACL / quiet hours → command / approval
   intercepts → prompt assembly → dispatch.
5. **Consumption model** — one consumer, sequential per chat. The consumer group is
   retained so horizontal partitioning (chat-hash across N consumers) remains a
   config-level change if scale ever demands it. Documented constraint until then:
   single backend replica.
6. **Command/approval/setup intercepts, quoted-reply prefixing, and group sender-info
   prefixing are private stages** of the implementation. Adding a stage is an
   implementation change with zero interface churn.
7. **Testing** — tests target the interface against in-memory fakes
   (`FakeStream`, `FakeOutbound`, `FakeHermes`). The Redis rate-limit helper becomes
   private implementation; its test is rewritten at the `accept()` level
   (flood → `Ack.rate_limited`). Dashboard/auth endpoint tests are unaffected.

## Consequences

- `webhooks.py` shrinks to: verify signature → parse → `inbox.accept()` → map Ack
  to HTTP response. ADR-0004's thin-router requirement is finally met.
- Issues **#6, #11, #12** are resolved by this redesign rather than patched in place;
  they close when the module lands.
- Messages survive deploys/restarts; per-chat ordering holds under the documented
  single-replica constraint.
- New failure surface to own: Streams consumer lifecycle (PENDING reclaim,
  poison-message dead-lettering).
- Future deepening candidates (outbound seam, RuntimeConfig) plug in at the edges of
  this module without reopening its interface.
