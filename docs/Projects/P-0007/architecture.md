# P-0007: Architecture — Latarnia LiteLLM

## High-Level Component Architecture

LiteLLM joins Redis and Postgres as a platform-level infrastructure service. It is not a Latarnia app — it has no `latarnia.json`, is not discovered, and is not managed by the app lifecycle system. The platform interacts with it solely through its HTTP admin API and OpenAI-compatible proxy API.

```mermaid
graph TB
    subgraph "Raspberry Pi 5"
        subgraph "Latarnia Main Application"
            FastAPI[FastAPI Web Server\nPort 8000]
            AppMgr[App Manager]
            Router[LaunchRouter]
            SvcMgr[Service Manager\nsystemd --user]
            SubLaunch[SubprocessLauncher\nmacOS fallback]
            MCPGateway[MCP Gateway]
            WebProxy[Web Proxy]
            SecMgr[Secret Manager]
            DbProv[DB Provisioner]
            LLMProv[LiteLLM Provisioner ★]
        end

        subgraph "Platform Services"
            Redis[(Redis\nPort 6379)]
            Postgres[(Postgres\nPort 5432)]
            LiteLLM[(LiteLLM Proxy\nTST: 4000 / PRD: 4001 ★)]
        end

        subgraph "systemd --user (Linux)"
            UA[latarnia-{env}-app_a.service]
            UB[latarnia-{env}-app_b.service]
        end

        subgraph "Applications"
            SvcApp1[Service App 1\nwith requires_litellm: true ★]
            SvcApp2[Service App 2]
        end
    end

    subgraph "AI Providers (External)"
        Anthropic[Anthropic API]
        OpenAI[OpenAI API]
    end

    Browser[Web Browser] --> FastAPI
    FastAPI --> LLMProv
    LLMProv -->|admin API: /key/generate\n/key/info /models /health| LiteLLM
    SvcMgr -->|Environment= LITELLM_BASE_URL\nLITELLM_API_KEY ★| UA
    SubLaunch -->|Popen env= ★| SvcApp1
    UA --> SvcApp1
    SvcApp1 -->|OpenAI-compatible client| LiteLLM
    LiteLLM --> Anthropic
    LiteLLM --> OpenAI
    SecMgr -->|read LITELLM_MASTER_KEY\nfrom litellm.env ★| LLMProv
```

★ = new in P-0007

---

## LiteLLM Service Deployment Topology

```mermaid
graph TB
    subgraph "systemd (system scope)"
        MainTST[latarnia-tst.service\nport 8000]
        MainPRD[latarnia-prd.service\nport 8001]
        LLMTst[latarnia-litellm-tst.service ★\nport 4000]
        LLMPrd[latarnia-litellm-prd.service ★\nport 4001]
    end

    subgraph "Config files (operator-managed)"
        LLMCfgTST[/opt/latarnia/tst/litellm_config.yaml]
        LLMEnvTST[/opt/latarnia/tst/litellm.env mode 600]
        LLMCfgPRD[/opt/latarnia/prd/litellm_config.yaml]
        LLMEnvPRD[/opt/latarnia/prd/litellm.env mode 600]
    end

    subgraph "Platform-managed files"
        KeysTST[/opt/latarnia/tst/litellm_keys.json mode 600]
        KeysPRD[/opt/latarnia/prd/litellm_keys.json mode 600]
    end

    LLMTst --> LLMCfgTST
    LLMTst --> LLMEnvTST
    LLMPrd --> LLMCfgPRD
    LLMPrd --> LLMEnvPRD
    MainTST -->|reads| KeysTST
    MainPRD -->|reads| KeysPRD
```

---

## LiteLLMProvisioner — Component Detail

```mermaid
graph LR
    subgraph "main.py lifespan"
        Init[instantiate\nLiteLLMProvisioner]
        Boot[bootstrap_all_keys]
    end

    subgraph "LiteLLMProvisioner"
        HC[health_check\nGET /health]
        GM[get_available_models\nGET /models]
        PK[provision_key\nGET /key/info → POST /key/generate]
        VM[validate_models]
        GS[get_status]
    end

    subgraph "Storage"
        KF[litellm_keys.json]
    end

    subgraph "LiteLLM Admin API"
        LLMAPI[http://localhost:{port}]
    end

    Init --> HC
    Boot --> PK
    PK --> LLMAPI
    PK --> KF
    VM --> GM
    GM --> LLMAPI
    GS --> HC
    GS --> GM
    GS --> KF
    HC --> LLMAPI
```

---

## Environment Injection — Diff from Existing Pattern

LiteLLM injection follows the exact same pattern as `DATABASE_URL` injection introduced in P-0002. The table below shows how each piece of injected infrastructure maps:

| Injected var | Source | Linux (ServiceManager) | macOS (SubprocessLauncher) |
|---|---|---|---|
| `DATABASE_URL` | DB Provisioner | `Environment=DATABASE_URL=...` in unit | merged into `Popen(env=...)` |
| `LITELLM_BASE_URL` | LiteLLM Provisioner ★ | `Environment=LITELLM_BASE_URL=...` in unit | merged into `Popen(env=...)` |
| `LITELLM_API_KEY` | LiteLLM Provisioner ★ | `Environment=LITELLM_API_KEY=...` in unit | merged into `Popen(env=...)` |

Both injections happen in `_build_env_vars(app_entry)` (new shared helper) or directly in `generate_service_template` / `start_service`, whichever pattern is cleaner given existing code structure. Implementation chooses; the contract is that both vars are present for any app with `requires_litellm: true`.

---

## Startup Sequence (updated with LiteLLM)

```mermaid
sequenceDiagram
    participant Main as main.py lifespan()
    participant Disc as App Discovery
    participant Prov as LiteLLMProvisioner
    participant LLM as LiteLLM
    participant Loop as Auto-start Loop

    Main->>Disc: discover_apps()
    Disc-->>Main: registry populated

    Main->>Prov: health_check()
    Prov->>LLM: GET /health
    alt healthy
        LLM-->>Prov: 200
        Main->>Prov: bootstrap_all_keys(registry)
        Note over Prov: provisions keys for all\nrequires_litellm apps idempotently
    else unreachable
        Note over Main: mark requires_litellm apps ERROR\nlog WARNING — platform continues
    end

    Main->>Loop: auto-start registered apps
    Note over Loop: ERROR apps skipped;\nall others start normally
```
