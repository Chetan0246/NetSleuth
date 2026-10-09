# References

Only sources that were actually consulted while implementing this project are listed.
The RFCs were checked against the RFC Editor / IETF Datatracker pages for their
identifiers, titles, obsoletion relationships and the specific protocol details cited
below. Where an implementation choice corresponds to a standard, the correspondence is
stated explicitly — and where the simulator *simplifies* a standard, that is stated
too (see `networking-concepts.md` and `limitations.md`).

---

## Standards (IETF RFCs)

### [RFC 791] Internet Protocol — IPv4
Postel, J. (ed.), *Internet Protocol — DARPA Internet Program Protocol Specification*,
RFC 791, September 1981.
<https://www.rfc-editor.org/info/rfc791>

Used for: the IP datagram model behind the MTU ladder, and the **minimum reassembly
buffer of 576 bytes**. `app/lab/graph.py` validates `link.mtu_bytes` against
`MTU_MIN = 68` (the smallest legal IPv4 MTU) and `MTU_MAX = 65535`; the default
fault parameter for `MTU_BLACK_HOLE` is 576 bytes precisely because it is the
standard's minimum reassembly buffer size, so the fault is realistic rather than
arbitrary.

### [RFC 792] Internet Control Message Protocol
Postel, J., *Internet Control Message Protocol — DARPA Internet Program Protocol
Specification*, RFC 792, September 1981.
<https://www.rfc-editor.org/info/rfc792>

Used for: the ICMP error semantics the simulator distinguishes.
`ICMP_UNREACHABLE_NETWORK` (type 3, code 0) and `ICMP_UNREACHABLE_HOST`
(type 3, code 1) are the two error outcomes the reachability probe reports separately,
because where the error is generated localizes the fault: a network-unreachable error
comes from a router partway along the path, while host-unreachable is generated at the
final hop. The diagnostic engine uses exactly this distinction to favour the
link-failure hypothesis over the routing hypothesis (see `diagnostic-algorithm.md`
§3.3 and the `UNREACHABLE_NETWORK` / `UNREACHABLE_HOST` rows in
`app/diagnosis/likelihoods.py`).

### [RFC 1035] Domain Names — Implementation and Specification
Mockapetris, P., *Domain Names — Implementation and Specification*, RFC 1035,
November 1987.
<https://www.rfc-editor.org/info/rfc1035>

Used for: the DNS outcome vocabulary. `NXDOMAIN` (RCODE 3) means the name does not
exist in the zone *and the server responded*; a failure to receive any response is a
separate condition (the simulator's `TIMEOUT_RESOLVER`). Keeping these outcomes
distinct is what lets the engine treat "the resolver answered and the name is missing"
as stronger DNS evidence than "the resolver never answered" — the latter is also
consistent with an unreachable resolver host, which is why the resolver-host ping
exists. See `docs/networking-concepts.md` §4.

### [RFC 1191] Path MTU Discovery
Mogul, J. and Deering, S., *Path MTU Discovery*, RFC 1191, November 1990.
(Obsoletes RFC 1063.)
<https://www.rfc-editor.org/info/rfc1191>

Used for: the path-MTU black-hole model. Under PMTUD a sender sets the DF bit and
learns a smaller path MTU from an ICMP "fragmentation needed and DF set" message
(type 3, code 4). If that message is lost or filtered, the sender never learns to
reduce its payload size and large datagrams are silently discarded — the textbook
path-MTU black hole. This is modelled directly by
`Link.suppress_frag_needed`: when set, oversized datagrams produce a timeout with no
ICMP error (`MtuOutcome.LIMITED_DROP`); when clear, the sender is told
(`MtuOutcome.LIMITED_REPORTED`). The distinction is load-bearing in the likelihood
table, because only the former is a black hole.

### [RFC 9293] Transmission Control Protocol (TCP)
Eddy, W. (ed.), *Transmission Control Protocol (TCP)*, RFC 9293, August 2022.
(Obsoletes RFC 793; updates RFC 1011 and RFC 1122.) STD 7.
<https://www.rfc-editor.org/info/rfc9293>

Used for: the handshake model and, more importantly, the three distinguishable
transport failures the diagnostic engine relies on. A SYN that receives no answer is a
timeout (silent drop); an immediate refusal means something actively rejected the
connection; a reset from the destination host itself means the host is up and the port
has no listener. The simulator models these as `TcpOutcome.TIMEOUT_DROP`,
`REFUSED_NETWORK_POLICY` and `REFUSED_NO_LISTENER`, and a test asserts that timeout and
refusal are different outcomes
(`test_lab_simulator.py::TestTcpLayer::test_timeout_and_refusal_are_different_outcomes`).

### [RFC 1812] Requirements for IP Version 4 Routers
Baker, F. (ed.), *Requirements for IP Version 4 Routers*, RFC 1812, June 1995.
<https://www.rfc-editor.org/info/rfc1812>

Consulted for: router forwarding-table expectations. The simulator's
`RoutingTable.forwarding_table` presents a derived destination → next-hop view, and
`docs/networking-concepts.md` states plainly that the model uses **lowest-hop-count
shortest path with no dynamic routing protocol**, i.e. it does not meet the dynamic
routing requirements of this specification. That gap is a documented limitation, not
an oversight.

---

## Tool and library documentation

### FastAPI
<https://fastapi.tiangolo.com/> — used for the HTTP layer, request/response validation
via Pydantic models, dependency injection (`app/api/deps.py`), the exception-handler
model, and the auto-generated OpenAPI schema served at `/docs` and `/openapi.json`.

### Starlette
<https://www.starlette.io/> — the ASGI foundation FastAPI is built on: the CORS
middleware configuration and the `TestClient` (with
`raise_server_exceptions=False`, used by the API test that asserts internal errors do
not leak stack traces).

### Pydantic
<https://docs.pydantic.dev/> — typed models for topology, faults, probe
requests/observations, API schemas and stored records. Field constraints
(`ge`, `le`, `min_length`) and `field_validator`/`model_validator` implement much of
the validation the API tests exercise.

### Uvicorn
<https://www.uvicorn.org/> — the ASGI server used by `scripts/run_dev.sh` and
`scripts/smoke_test.py`.

### NetworkX
<https://networkx.org/documentation/stable/> — listed as the plan's intended graph
library and declared in `requirements.txt`. The routing implementation ended up as a
hand-written Dijkstra in `app/lab/routing.py` **instead**: the project needs a specific
deterministic tie-break on the *link-id sequence* and a "nominal path as if all links
were up" computation for localization, both of which were clearer as explicit code
than as wrappers around a general-purpose library. This is a deliberate deviation from
the plan's suggested tool, and the reason is recorded here rather than left implicit.

### pytest
<https://docs.pytest.org/> — the test runner. Configuration in `backend/pytest.ini`
(`testpaths = tests`, `pythonpath = .`).

### HTTPX / Starlette TestClient
<https://www.python-httpx.org/> — the transport behind FastAPI's `TestClient`, used by
every test in `backend/tests/api/`.

### React
<https://react.dev/> — component model and hooks for the dashboard.

### Vite
<https://vitejs.dev/> — dev server and production build, including the `/api` proxy
configuration that lets the frontend use same-origin relative URLs in every
environment.

### React Router
<https://reactrouter.com/> — the five-page application shell.

### Recharts
<https://recharts.org/> — the experiment charts. Charts are rendered only from API
payloads; the frontend tests assert the metric *tables* (and therefore the numbers)
rather than chart geometry.

### Tailwind CSS
<https://tailwindcss.com/> — styling. Status information is always carried by text
and symbols in addition to colour (see `StatusBadge`, `ModeBadge`, `StrengthTag` in
`frontend/src/components/ui.tsx`).

### Vitest and React Testing Library
<https://vitest.dev/>, <https://testing-library.com/docs/react-testing-library/intro/>
— the frontend test stack. The API is mocked at the `fetch` boundary.

### Lucide
<https://lucide.dev/> — interface icons, always paired with a visible text label or an
`aria-hidden` attribute when decorative.

---

## Concepts referenced in the design

### Bayesian inference and entropy
The belief updater implements the standard recursive Bayes filter with normalisation,
and the planner uses Shannon entropy in bits:

```
H(B) = -Σ_h p(h) log2 p(h)
EIG(q) = H(B) - Σ_o P(o | q, B) · H(B | o, q)
```

Shannon, C. E., *A Mathematical Theory of Communication*, Bell System Technical
Journal, 27(3):379–423, 1948. The expected-information-gain criterion is the standard
myopic (one-step-lookahead) value-of-information rule used in sequential experiment
design and active diagnosis; this project's contribution is its application to a
reproducible multi-layer network lab with an explicit, inspectable likelihood
catalogue, not the mathematics itself.

### Path-MTU black holes in practice
The failure mode being modelled — small packets succeed, full-size packets are
silently dropped, and the connection appears to hang — is the operational problem
described in RFC 1191 and its follow-on discussions of PMTUD deployment. The project
treats it as a distinct diagnosable cause rather than as a sub-case of "packet loss",
which is why it has its own hypothesis, its own likelihood signature
(`MtuOutcome.LIMITED_DROP`) and its own inconclusive guard
(`INCONCLUSIVE_NON_MONOTONE`).

---

## Citation note

`plan.md` §23 lists candidate references and instructs that they be verified before
submission and not treated as a substitute for reading the relevant portions. In this
implementation:

- the identifiers, titles and obsoletion relationships above were checked against the
  RFC Editor and IETF Datatracker pages;
- the specific protocol details attributed to each RFC (the 576-byte minimum
  reassembly buffer, ICMP type 3 codes 0/1/4, DNS RCODE 3, the DF bit and PMTUD
  procedure, and the distinction between a connection timeout and an active refusal)
  are the details actually implemented in the simulator;
- **no** reference is cited for a claim this project does not implement. Where the
  simulator simplifies a standard — no dynamic routing, no fragmentation, no
  congestion control — the discrepancy is stated in `networking-concepts.md` and
  `limitations.md` rather than papered over by a citation.
