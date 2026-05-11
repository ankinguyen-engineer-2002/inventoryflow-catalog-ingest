# LLM prompt templates

Versioned prompt templates. **Version bumps invalidate the cache** (intentional — see ADR-007).

## Convention

```
prompts/
└── <use-case>/
    ├── v1.txt
    ├── v2.txt        ← later version, supersedes v1
    └── _README.md    ← change history for this use case
```

## Use cases planned

| Use case          | Path                       | Triggered when                                                |
| ----------------- | -------------------------- | ------------------------------------------------------------- |
| Translate CN → EN | `translate-cn-en/v1.txt`   | `products.name_en` is null/empty and `name_cn` exists         |
| Extract callouts  | `extract-callouts/v1.txt`  | Audit job comparing schematic image to table `No.` values     |
| Infer make        | `infer-make/v1.txt`        | First file from a new dealer where make isn't in `rules.yaml` |
| Parse exception sheet | `parse-exception/v1.txt` | One of the ~12 reference sheets (Carburetor Jets, etc.)      |

## Template variables

Templates use `{{var_name}}` placeholders. The runtime substitutes them before sending. The variable set per use-case is documented in the template's `_README.md`.

## Why versioning

Treating prompts as code:

- Reproducibility: same prompt + same input = same response (modulo provider non-determinism).
- Cache validity: a prompt change is a logical schema change for the model's output.
- Auditability: `ingest_audit.prompt_template_ver` records which version was used per call.

## Anti-patterns to avoid

- ❌ Embedding prompts as string literals in code.
- ❌ Changing prompts in-place without bumping the version (silent cache corruption).
- ❌ Including user-data or PII in templates (templates are public; data is interpolated).
- ❌ Long, vague prompts. Keep them ≤200 tokens; precision > word count.
