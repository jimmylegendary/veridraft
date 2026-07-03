# Portable PaperOrchestra runner

Run the PaperOrchestra 5-agent pipeline against **any** backend from one config, so it
works wherever you install the skills and whatever model you use. This is what Veridraft's
`paperorchestra` WritingEngine adapter invokes headlessly (`po_command`).

## Why this exists

PaperOrchestra skills are **host-agnostic**: the LLM + tools come from a host agent, not
the skill. That's why "it behaved weirdly" across environments — a different host = a
different model + different capabilities. This runner pins the backend explicitly.

## Backends (pick one in the config)

| `backend` | How it runs | Model source | Tested here |
|---|---|---|---|
| `api` | self-hosted OpenAI-compatible endpoint via an OSS agent CLI (`opencode`/`aider`) | `base_url` + `api_key` + `model` (e.g. vLLM/Ollama serving Qwen/Llama) | impl'd; needs your endpoint |
| `openclaw` | `openclaw agent --agent <id> --json` | openclaw's provider (e.g. gpt-5.5) | ✅ dispatch verified |
| `claude-code` | `claude -p` headless | Claude CLI's model | ✅ dispatch verified |
| `codex` | `codex exec` | codex/OpenAI | impl'd |

A raw LLM endpoint has no tools, so `api` routes through **opencode** (or aider), which
wraps the OpenAI-compatible model with the file/bash/web tools a step needs.

## Setup (self-host, one time)

```bash
bash setup.sh paperorchestra.config.json
```
Checks/《best-effort installs》 every LOCAL dependency and probes the backend:
- **LaTeX** (latexmk+pdflatex+bibtex + `cleveref/nicefrac/microtype/booktabs` — full TeX Live)
- **matplotlib venv** (plotting; PEP-668-safe venv)
- **poppler** `pdftotext` (Veridraft egress scan)
- **Semantic Scholar** reachability (literature verification; 1 QPS public)
- the chosen **backend CLI**

Copy `config.example.json` → `paperorchestra.config.json` and set your backend. Secrets
can use `env:VARNAME`. `vision_model` (a VLM) enables the figure-critique loop + multimodal
section writing — without it those steps degrade to text-only. `image_model` is optional
(generated diagrams); figures are otherwise matplotlib-rendered data plots. `web_search`
drives literature discovery — `none` runs degraded (user-supplied `refs.bib` only, no
invented citations).

## Run

```bash
python3 run.py --config paperorchestra.config.json --workspace <ws>   # ws has inputs/
python3 run.py --config paperorchestra.config.json --workspace <ws> --probe   # backend check only
python3 run.py --config paperorchestra.config.json --workspace <ws> --from 4 --to 5  # resume steps
```
The workspace must contain `inputs/{idea.md, experimental_log.md, template.tex,
conference_guidelines.md, figures/}` — **Veridraft's `assemble` step produces exactly this**,
so the normal path is: `veridraft assemble …` → this runner over the assembled workspace.

## Wire into Veridraft

Point the `paperorchestra` engine adapter at this runner (absolute paths):

```json
{ "adapters": { "writing_engine": { "id": "paperorchestra", "enabled": true, "config": {
  "po_command": ["python3", "/abs/impl/paperorchestra/run.py",
    "--config", "/abs/impl/paperorchestra/paperorchestra.config.json",
    "--workspace", "{workspace}"] } } } }
```
Then `veridraft --config that.json draft <bundle>` runs the governed pipeline with your backend.

## Honest status

Step-dispatch is verified for `openclaw` and `claude-code` here; `api`/`codex` are
implemented and need your endpoint/CLI to validate. Full-paper QUALITY depends on the
backend model + host capabilities (web/vision) — a weaker OSS model yields a weaker paper,
which Veridraft's deterministic gates (evidence, citations, anti-leakage, egress) still keep
honest. Steps 2‖3 run concurrently when the backend allows (`parallel_2_3`).
