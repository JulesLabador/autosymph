# Autosymph Agent Notes

## Prompt ownership

- Canonical autosymph agent prompts live in this repository under `prompts/`.
- The external autosymph config directory owns workflow config only: project YAMLs,
  device YAMLs, state routing, and `prompts.root` pointers.
- Do **not** recreate or edit a second prompt copy under `autosymph-config`.
  That drift previously caused live runs to use stale verify instructions even
  after the repo prompt had been fixed.
- If you need to change autosymph verify / implement / merge / global prompt
  behavior, edit `prompts/*.md`.
- If you need a config to consume those prompts, point it at the canonical root
  with `prompts.root`, not a duplicated prompt directory.
