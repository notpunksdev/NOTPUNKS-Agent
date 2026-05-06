# NOTPUNKS Agent v0.11.2

> Curated default skills release.

This patch release tightens the default NOTPUNKS Agent install so new profiles
ship with a focused, useful skill set instead of the full upstream demo catalog.

## Highlights

- Default bundled skills are now curated to 42 practical skills for coding,
  GitHub work, research, documents, productivity, content, automation, and
  marketplace skill authoring.
- Old bundled demo/game/media/red-team/noise skills are removed from profile
  sync when they have not been user-modified.
- `/skills list` refreshes bundled skill state before rendering and hides stale
  manifest entries, so removed defaults no longer appear as enabled builtins.
- Website and README release links now point at the current NOTPUNKS Agent
  release archive set.

## Verification

- `tests/tools/test_skills_sync.py`
- `tests/hermes_cli/test_skills_hub.py`

