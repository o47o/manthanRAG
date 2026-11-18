# Standalone RAG Product — High-Level Plan

**Status:** Draft for discussion. Goal: a shared, simple mental model before writing the real build plan.

## 1. What we're actually doing

Today, RAG logic (RAVEN engine) lives *inside* our bigger analytics platform. Clients see it in demos and want it on its own — without the rest of the platform, and without us touching their data.

This is two separate problems, and it helps to keep them separate:

1. **Extraction** — pull the RAG engine out of the monolith so it can run alone, with no dependency on the SQL/agent-orchestration side of the platform.
2. **Productization** — package, license, and ship that extracted engine as something a customer runs *themselves*, on infrastructure *we never touch*.

Everything below is built around the ownership table you wrote — that table is the spec. The rest of this doc is "what has to be true for that table to hold."

## 2. What already exists vs. what's new

| Already exists (reuse) | Needs to be built |
|---|---|
| Multi-store ingestion pipeline (parse → route → chunk → embed → store) | License/activation system |
| Hybrid + agentic retrieval, reranking, GraphRAG | Self-contained installer / deployment package |
| Document → chunk → vector storage model | Admin UI for license status, version, support-session grant |
| Synthesis via LLM (pluggable endpoint) | Opt-in, content-free usage telemetry |
| Table-aware querying | "Bring your own LLM" config (local model or customer's own API key) |
| | Signed release/update mechanism |
| | Support-access model (time-boxed, customer-granted) |

The left column is **under a year of engineering risk already retired** — this is not a from-scratch RAG build. The right column is where the actual new work is, and it's mostly *product/packaging* work, not ML work.

## 3. The deployment model (this is the part your table dictates)

Your table says one thing, over and over: **we ship software, the customer runs it, we never see what's inside it.** That has one clean architectural consequence:

> **The product must run entirely inside the customer's own environment — their cloud account, their VPC, or their on-prem servers — with no required network path back to us.**

This is usually called **"self-hosted" / "on-prem" / "customer-managed"** deployment, as opposed to SaaS (where we run it and they send us data). Given your table, SaaS is off the table by design — every row that says "No access" for us only holds true if we never run the workload.

Concretely, that means:

- We build and **sign** container images (Docker). Signing lets the customer verify "this image really came from us and wasn't tampered with" without needing us present.
- The customer pulls the signed images into **their own** infrastructure (their cloud, their GPUs, their database) and runs them with `docker compose` / Kubernetes / whatever fits their environment.
- Their documents, their database, their embeddings, their LLM provider account — all of it stays inside their network boundary, because the software runs there and nowhere else.
- We never hold credentials to their systems. Full stop.

## 4. The pieces of the product

```
┌─────────────────────────────────────────────────────────┐
│                  CUSTOMER'S ENVIRONMENT                   │
│                                                             │
│   ┌──────────┐   ┌──────────────┐   ┌──────────────┐     │
│   │  RAG API  │──│  Vector/SQL   │   │  Local or     │     │
│   │  (our     │   │  database     │   │  customer's   │     │
│   │  image)   │   │  (customer's) │   │  LLM endpoint │     │
│   └──────────┘   └──────────────┘   └──────────────┘     │
│        │                                                   │
│   ┌──────────┐                                             │
│   │ Admin UI │  ← license status, version, support toggle  │
│   └──────────┘                                             │
└─────────────────────────────────────────────────────────┘
                         │
                         │  (only ever outbound, only when
                         │   the customer explicitly allows it)
                         ▼
        ┌───────────────────────────────┐
        │   OUR SIDE                     │
        │  - License server (issue/check)│
        │  - Image registry (signed)     │
        │  - Opt-in anonymous telemetry  │
        │  - Support access broker        │
        └───────────────────────────────┘
```

Three things on our side, deliberately thin:

1. **License server** — issues a license key, and answers "is this key valid / expired" when the customer's install checks in (we don't need to see their data to answer that — just the key).
2. **Release registry** — where signed images and version metadata live. Customer pulls from here; we never push into their environment.
3. **Telemetry/support backend** — only ever receives what the customer opted into (aggregated, content-free counters), and only gets live access when the customer flips a time-boxed switch in their own admin UI.

## 5. Licensing & updates, concretely

- **License key** — issued per customer, tied to seats/usage tier/expiry. The running software checks it locally (and optionally phones home periodically to confirm it's still valid — this is the *only* default outbound traffic, and it carries no customer data, just a key + maybe a version number).
- **Version reporting** — the table says we *can* see installed version "if reported." So: version check-in is opt-in or at minimum clearly disclosed, not silent.
- **Updates** — we publish new signed images; the customer decides when to pull and deploy them. No auto-update that we control remotely — that would break "infrastructure: full control" for the customer.

## 6. Support, concretely

Support only happens when the customer **grants** a session, and it's **time-boxed** (e.g., "you have remote access for the next 2 hours," then it auto-revokes). This is usually built as:

- A support tunnel the customer's admin UI opens on demand (e.g., a temporary SSH/VPN session or a screen-share-style remote session into their environment), not a standing backdoor.
- Logged, visible to the customer, revocable by them at any time.

## 7. What "licensed use only" means for the source code

The customer runs our container images — they don't need our source code to do that. So:
- We keep the source in our own private repo, as we do today.
- We ship **compiled/built artifacts** (container images), not source.
- The license agreement (legal, not technical) is what makes "licensed use only" stick — the technical side just makes it *practical* (they literally don't receive the source).

## 8. Suggested build order (phases, not dates yet)

1. **Extract** — pull the RAG engine's code out of the platform repo into its own standalone service with its own config, no calls back into the bigger platform's auth/orchestration. Prove it runs fully stand-alone end-to-end (ingest a doc, query it, get an answer) with nothing but its own database and an LLM endpoint.
2. **Containerize & harden config** — Docker image(s), all secrets/config via env vars or mounted files (no hardcoded endpoints), works with either a local LLM or a customer-supplied API key.
3. **License gate** — add the license-check call, admin UI showing license/version status.
4. **Packaging** — signed image publishing, a install/deploy guide (docker-compose to start, Kubernetes later if needed), a "pull and run" customer experience.
5. **Telemetry & support hooks** — opt-in aggregated usage reporting, time-boxed support-access toggle.
6. **Pilot** — one friendly customer, full install from scratch using only the docs, no engineer-in-the-room — this is the real test of whether the ownership table actually holds in practice.

## 9. Open questions to settle before the real plan

- Does the customer need **multi-tenant** isolation inside one install, or is it always one install = one customer/department?
- Which LLM story do we lead with: local open-weight model (fully air-gapped, no outbound calls at all) vs. "bring your own API key" (simpler for us, but then the customer's LLM provider sees their prompts — worth being explicit about in the ownership table)?
- Kubernetes vs. docker-compose as the primary supported deployment target — affects how much packaging work phase 4 actually is.
- What counts as a "seat" or usage unit for licensing (documents ingested? queries/month? users?).

## 10. One-line summary

**We ship signed, versioned containers; the customer runs them entirely inside their own walls; our only ongoing touch-points are a license check-in, opt-in anonymous telemetry, and support access they grant and revoke themselves.**
