# P-0007: Data Model — Latarnia LiteLLM

## Manifest Extensions

New fields added to `AppConfig` (in `AppManifest.config`):

```mermaid
classDiagram
    class AppConfig {
        bool requires_litellm = False
        list~str~ requires_models = []
    }
```

`requires_litellm: true` activates virtual key provisioning and environment injection.
`requires_models` declares the LiteLLM model aliases the app expects to be available. Validated at startup (refuse-to-start if any are missing from LiteLLM). Empty list = no model pre-flight check.
Declaring `requires_models` without `requires_litellm: true` is a `ValidationError`.

---

## Config Schema Extension

New `LiteLLMConfig` section in `config.json`:

```mermaid
classDiagram
    class LiteLLMConfig {
        bool enabled = false
        int port = 4000
        string host = "localhost"
        string master_key_env_var = "LITELLM_MASTER_KEY"
    }
```

`master_key_env_var`: the key name in `/opt/latarnia/{env}/litellm.env` that holds the LiteLLM admin master key. Read by `LiteLLMProvisioner` via `SecretManager` to authenticate admin API calls.

---

## LiteLLMProvisioner In-Memory + Persistent Model

```mermaid
classDiagram
    class LiteLLMProvisioner {
        +str base_url
        +str master_key
        +str env
        +Path keys_file
        +Dict~str_str~ _key_cache
        +health_check() bool
        +get_available_models() list~str~
        +provision_key(app_id) str
        +get_key(app_id) Optional~str~
        +validate_models(app_entry) LiteLLMValidationResult
        +bootstrap_all_keys(registry) None
        +get_status() LiteLLMStatus
    }

    class LiteLLMValidationResult {
        +bool ok
        +list~str~ missing_models
        +str detail
    }

    class LiteLLMStatus {
        +bool healthy
        +int port
        +list~str~ provisioned_apps
        +list~str~ available_models
    }

    LiteLLMProvisioner --> LiteLLMValidationResult : validate_models() returns
    LiteLLMProvisioner --> LiteLLMStatus : get_status() returns
```

---

## Registry Extension

`AppRegistryEntry` gains a new optional info block for LiteLLM, consistent with `DatabaseInfo` and `MCPInfo`:

```mermaid
classDiagram
    class AppRegistryEntry {
        ...
    }
    class LiteLLMInfo {
        +bool provisioned
        +bool key_injected
        +list~str~ requires_models
    }
    AppRegistryEntry "1" --> "0..1" LiteLLMInfo : litellm_info
```

`provisioned`: a virtual key was successfully created or verified via admin API.
`key_injected`: `LITELLM_BASE_URL` and `LITELLM_API_KEY` will be present in the app's environment at start.
`requires_models`: copied from the manifest (for inspection via `/api/apps`).

---

## File System Layout

New platform-managed files (per env):

```
/opt/latarnia/{env}/
├── litellm_config.yaml        # Model routing config (operator-managed).
│                              # Defines model_list aliases (e.g. claude-sonnet → anthropic/claude-sonnet-4-6).
│                              # LiteLLM reads this at startup.
├── litellm.env                # LiteLLM secrets: LITELLM_MASTER_KEY + provider API keys.
│                              # Mode 600. Operator-managed.
│                              # Referenced by latarnia-litellm-{env}.service EnvironmentFile=.
│                              # Also read by LiteLLMProvisioner via SecretManager for LITELLM_MASTER_KEY.
└── litellm_keys.json          # Latarnia's record of per-app virtual keys.
                               # Mode 600. Platform-managed (written by LiteLLMProvisioner).
                               # {"app_id": "sk-latarnia-{env}-app_id", ...}
```

### `litellm_keys.json` format

```json
{
  "example_full_app": "sk-latarnia-tst-example_full_app",
  "latarnik": "sk-latarnia-tst-latarnik"
}
```

---

## Port Additions to System Port Map

| Resource | TST | PRD |
|----------|-----|-----|
| LiteLLM AI Gateway | 4000 | 4001 |
