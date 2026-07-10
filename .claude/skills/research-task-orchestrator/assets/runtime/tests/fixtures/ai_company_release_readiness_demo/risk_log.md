# Risk log

- Token overflow risk: previous large-folder tasks exceeded context budget when raw logs were replayed into the main agent.
- Mitigation: Main Agent Memory Guard creates checkpoints and instructs later phases to use condensed state instead of full logs.
- Router risk: one live child worker response was partial during smoke testing.
- Mitigation: watchdog classifies partial or empty router output as recoverable failure and uses bounded retry/replan.
- False success risk: a worker can return a good-looking summary without evidence refs.
- Mitigation: claim ledger requires each accepted claim to have evidence refs, and reviewer can block as `FALSE_SUCCESS_BLOCKED`.
- Operational risk: dashboard may show zero runs if no compatible run artifacts exist.
- Mitigation: dashboard now separates All runs from Current run and does not fake zero agents while detail is loading.

