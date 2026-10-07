# Firecrawl fixtures

**Provenance: recorded**, from https://www.firecrawl.dev/alexandria/fantasy on 2026-10-06
(week 5), then trimmed. The real page is ~600 KB; each fixture keeps the real `<title>` and a
dozen or so real board rows, re-wrapped in one `self.__next_f.push` chunk the way the page
carries them. A decoy row with no points sits first, because the live page also carries a
player index without points and the parser has to skip it.

| File | What it is |
|---|---|
| `wr_ppr_week5.html` | `?position=WR&scoring=ppr` |
| `wr_std_week5.html` | `?position=WR&scoring=std`, the same players in standard scoring |
| `wr_halfppr_param_week5.html` | `?position=WR&scoring=half_ppr`: the parameter is ignored and PPR is served |
| `dst_week5.html` | `?position=DST` |
| `def_param_serves_qbs_week5.html` | `?position=DEF`: the parameter is ignored and quarterbacks are served |
