# Day1 repro trials (2026-07-13)

Goal: reproduce first-day success path then optimize.

## Day1 success recipe (2026-07-11)

```
OTP-only passwordless
sentinel: pure Python pow, t=""
so: none (no browser, no HAR so)
body: {name, birthdate}
proxy: 辣椒 US
```

Evidence accounts: Roelfs×2, ConderGord (no sentinel_obs field; later JohnOwens/Eric/Embree confirm has_so=false create 200).

## Repro config (this session)

| key | value |
|-----|--------|
| sentinel_source | pow |
| pow_so_source | **none** (Day1-faithful; not xiaopp) |
| create_browser_fallback | false |
| create_retries | 3 |

## Results

| # | root | to create | has_so | create | bucket | note |
|---|------|-----------|--------|--------|--------|------|
| 1 | KathrynEverett6196 | yes | false | 400×3 disallow | create_disallow | full path |
| 2 | DebbieRodriguez7023 | yes | false | 400×3 disallow | create_disallow | full path |
| 3 | NicoleBurch5569 | no | — | — | other | providers **403** (infra) |

**到 create 2/2 → 0 success；协议步进与 Day1 同形，create 业务拒建。**

Artifacts:
- `TRIAL_day1_repro_20260713.json`
- `TRIAL_day1_repro_n2_20260713.json`

## Interpretation (for optimize)

1. **Reproduced the Day1 *protocol path*** (OTP→about_you→create pow no so) — not the Day1 *success rate*.
2. Same shape that historically created Eric/Embree/Roelfs still hits `registration_disallowed` on current outlook roots.
3. **Do not optimize by default browser or fake so** — path already matches Day1; failure is post-path (identity/root/era).
4. xiaopp HAR so earlier (Adam) also disallow with has_so=true — so not the missing Day1 magic.

## Optimize candidates (ordered, pure protocol)

| priority | action | why |
|----------|--------|-----|
| P0 | **new mailbox source** (non-burned outlook batch) | H1 root |
| P1 | stop burning on consecutive create_disallow | pool hygiene |
| P2 | optional: name style A/B (Z4-like vs real) n small | weak; Day1 random names worked |
| P3 | keep Datadog/sv/~S already on | already aligned |
| skip | default browser / HAR so as “fix” | user pure-protocol; evidence weak |

## Pool after

unused decreased by ~3 this repro block; avoid more burns without new source.
