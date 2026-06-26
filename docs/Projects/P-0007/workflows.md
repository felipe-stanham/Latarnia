# P-0007: Workflows — Latarnia LiteLLM

## flow-01: App startup with `requires_litellm: true` (Linux/systemd)

Covers cap-002 (key provisioning), cap-003 (injection), cap-004 (model validation).

```mermaid
flowchart TD
    A[start_service called] --> B{requires_litellm?}
    B -- no --> C[existing start flow]
    B -- yes --> D{LiteLLM healthy?}
    D -- no --> E[set status=ERROR\n'LiteLLM not reachable'\nreturn False]
    D -- yes --> F[provision_key: GET /key/info]
    F --> G{key exists?}
    G -- yes --> H[read key from cache]
    G -- no --> I[POST /key/generate\nwith custom key string]
    I --> J[persist to litellm_keys.json]
    J --> H
    H --> K{requires_models declared?}
    K -- no --> M[inject LITELLM_BASE_URL\n+ LITELLM_API_KEY]
    K -- yes --> L[GET /models from LiteLLM]
    L --> N{all declared\nmodels available?}
    N -- no --> O[set status=ERROR\n'model not available: X'\nreturn False]
    N -- yes --> M
    M --> P[generate unit file\nwith Environment= lines]
    P --> Q[systemctl --user start]
    Q --> R[app running with\nLITELLM_BASE_URL + LITELLM_API_KEY]
```

## flow-02: Platform startup — LiteLLM bootstrap (cap-005)

Runs in `main.py` lifespan, after app discovery and before the auto-start loop.

```mermaid
flowchart TD
    A[lifespan: apps discovered] --> B{any app\nrequires_litellm?}
    B -- no --> Z[skip LiteLLM init]
    B -- yes --> C[LiteLLMProvisioner.health_check]
    C --> D{healthy?}
    D -- no --> E[mark all requires_litellm\napps: ERROR\n'LiteLLM not reachable']
    E --> F[log WARNING\nplatform continues]
    D -- yes --> G[bootstrap_all_keys:\nfor each app with requires_litellm\ncall provision_key idempotently]
    G --> H[auto-start loop\nbegins — ERROR apps skipped]
```

## flow-03: Operator adds a new model to LiteLLM

No platform changes required. App declares the new model alias in its manifest.

```mermaid
sequenceDiagram
    actor Op as Operator
    participant Config as litellm_config.yaml
    participant LLM as LiteLLM service
    participant App as App manifest

    Op->>Config: add new model_list entry\n(e.g. claude-opus)
    Op->>LLM: systemctl restart latarnia-litellm-{env}
    Note over LLM: new model now available at GET /models
    Op->>App: add "claude-opus" to requires_models
    Op->>Op: restart app via dashboard
    Note over App: model validation passes;\napp starts with LiteLLM access
```

## flow-04: Virtual key lifecycle

```mermaid
sequenceDiagram
    participant Prov as LiteLLMProvisioner
    participant FS as litellm_keys.json
    participant LLM as LiteLLM Admin API

    Note over Prov: First registration (app not in keys.json)
    Prov->>LLM: GET /key/info?key=sk-latarnia-{env}-my_app
    LLM-->>Prov: 404
    Prov->>LLM: POST /key/generate\n{key: "sk-latarnia-{env}-my_app",\n metadata: {app: "my_app", env: "{env}"}}
    LLM-->>Prov: 200 {key: "sk-latarnia-{env}-my_app"}
    Prov->>FS: write {"my_app": "sk-latarnia-{env}-my_app"}

    Note over Prov: Subsequent startups (idempotent)
    Prov->>LLM: GET /key/info?key=sk-latarnia-{env}-my_app
    LLM-->>Prov: 200 (key exists in LiteLLM)
    Note over Prov: no POST needed; key unchanged

    Note over Prov: LiteLLM restarted (in-memory key lost, no DATABASE_URL)
    Prov->>LLM: GET /key/info?key=sk-latarnia-{env}-my_app
    LLM-->>Prov: 404 (memory cleared)
    Prov->>LLM: POST /key/generate\n{key: "sk-latarnia-{env}-my_app", ...}
    LLM-->>Prov: 200 {key: "sk-latarnia-{env}-my_app"}
    Note over Prov: same deterministic key string re-issued;\napps already running are unaffected
```

## flow-05: Model validation gate (refuse-to-start)

Covers cap-004.

```mermaid
sequenceDiagram
    participant SM as ServiceManager
    participant Prov as LiteLLMProvisioner
    participant LLM as LiteLLM
    participant Reg as AppRegistry

    SM->>Prov: validate_models(app_entry)
    Prov->>LLM: GET /models
    LLM-->>Prov: {"data": [{"id": "claude-sonnet"}, ...]}
    Prov->>Prov: requires ["claude-opus"]\navailable ["claude-sonnet"]\nmissing = ["claude-opus"]
    Prov-->>SM: LiteLLMValidationResult(ok=False, missing=["claude-opus"])
    SM->>Reg: update_app(status=ERROR,\nerror_message="model not available in LiteLLM: claude-opus")
    SM-->>SM: return False (no unit file, no port)
```
