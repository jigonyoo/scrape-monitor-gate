# Monitoring report

| day | rows | status | selector | note |
|---|---:|---|---|---|
| 2026-03-01 | 4 | `OK` | gen1 |  |
| 2026-03-02 | 4 | `OK` | gen1 |  |
| 2026-03-03 | 4 | `SCHEMA_DRIFT` | gen2 | markup changed: selector moved gen1 -> gen2 |
| 2026-03-04 | 4 | `OK` | gen2 |  |
| 2026-03-05 | 0 | `SOFT_BLOCK` | - | page returned content but it is an interstitial, not the listing |
| 2026-03-06 | 1 | `PARTIAL` | gen2 | 1 rows against a baseline of 4 |
| 2026-03-07 | 4 | `OK` | gen2 |  |

## Alerts

- **2026-03-03 — SCHEMA_DRIFT**: markup changed: selector moved gen1 -> gen2
- **2026-03-05 — SOFT_BLOCK**: page returned content but it is an interstitial, not the listing
- **2026-03-06 — PARTIAL**: 1 rows against a baseline of 4
- **2026-03-07 — value move**: Cyan 75.50 to 755.00 (10.0x), page structure was sound