# FantasyPros fixtures

**Provenance: mixed, and the difference matters.**

`malformed/no_api_key_403.json` is recorded. It is what the live API returns without a key,
captured 2026-09-12.

Everything else is **hand-built from the endpoint descriptions in docs/DATA_SOURCES.md, not
recorded.** FantasyPros costs $8.99/month, the free tier serves sample data only, and there
is no key on this machine, so the response shapes for `/projections` and
`/consensus-rankings` are unverified.

What that means in practice: the field names in these files are a good guess, not evidence.
The adapter is written so a wrong guess fails loudly -- `index_projections` and `index_ranks`
name every field they need and raise `SchemaDrift` identifying the missing one. Point a real
key at it and the first fetch says exactly what is wrong, if anything is. Re-record these
then, and delete this paragraph.

The rows cover every position the app models, plus one linebacker, because an
`position=ALL` pull includes IDP rows that are not an error and must not be treated as one.
