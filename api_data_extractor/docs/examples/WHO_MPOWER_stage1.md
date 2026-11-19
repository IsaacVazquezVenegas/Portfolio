# Worked example — Stage 1 for a WHO MPOWER request

**Input:** the researcher chose starting point **B** ("I know the source, WHO MPOWER, but only by description") and pasted a table with three columns: *Component* (P/O/W/E), *Policy* and *Interventions*.

**Catalogue look-ups used (§1.4, 2026-11-19, all unverified until Stage 4):**

* `https://ghoapi.azureedge.net/api/Indicator` gives the full GHO indicator list.
* `https://ghoapi.azureedge.net/api/DIMENSION/TOBACCO_INDICATOR/DimensionValues` gives the 264 MPOWER component questions.
* Sample queries `…/api/TOBACCO_MPOWER_<bundle>?$filter=SpatialDim eq 'FRA'` showed the bundle structure.

The text below is the reply the LLM sends after those look-ups.

---

## How WHO stores MPOWER (what matters for your list)

GHO holds MPOWER at three levels:

| Level | Example | Notes |
|---|---|---|
| **Composite score** (one per component) | `P_Group`, `O_Group`, `W_Group`, `W-MM_Group`, `E_Group`, `R_Group`, `M_Group` | Ordinal group 1–5, computed by WHO from the components. All seven are also in one table, `TOBACCO_MPOWER_OVERVIEW`. |
| **Component bundle** (one table per sub-component) | `TOBACCO_MPOWER_P1_SMOKEFREEPLACES`, `…_O2_MEDICATIONAVAILABILITY`, `…_W1A_CIGARETTES`, `…_E1_DIRECTADBANS`… | Each row is one country × report year × question (`Dim1 = TOBACCO_INDICATOR_<question>`); values are mostly Yes/No/categories. Report years run **2007–2024**. |
| **Single question** | `O_Varen_sold`, `P6_restaurants`, `W26_plain_packaging_A`… | The same questions published as separate indicators. Some single codes stop earlier (the `_Group` codes end in 2022, while the bundles include 2024). |

**Recommended strategy (§1.3): retrieve wide, select later.** Retrieve the overview table plus every bundle your list touches: about 15 requests for all countries. You then pick the questions you need in analysis, with no new extraction.

## Requirements Register

| ID | Your wording | Group | Interpreted concept | Kind of data | Candidate retrieval unit | Status | Question |
|---|---|---|---|---|---|---|---|
| R1 | Smoke and vaping free air laws / Comprehensive smoke-free law | P | National smoke-free legislation by type of place | policy | `P_Group` (composite) + bundles `TOBACCO_MPOWER_P1_SMOKEFREEPLACES` (8 place types) and `…_P2_SMOKEFREEPLACEDETAILS` (number of places, fines, enforcement) | ambiguous | Q2 (vaping) |
| R2 | Bans in public places / "Model P together?" | P | Same legislation, by place | policy | Same bundles as R1 (no extra request); compliance scores `P_compl_p1…p8`, `P_compl_all_sfe` | clear | "Model together" → analytical note A1 |
| R3 | Eliminate second-hand exposure | P | Population exposure to second-hand smoke (outcome, not policy) | outcome | `SHS_2` (women 15+), `SHS_3` (men 15+), `SHS_1` (children <15); burden `SHS_4`–`SHS_11` | clear | — |
| R4 | Smoke-free home with kids | P | Children's exposure at home (no MPOWER law covers private homes) | outcome | `SHS_1`; youth survey `TOBACCO_0000000346` (exposure at home) | clear | — |
| R5 | Pharmaceutical cessation aid: Varenicline | O | Legal sale, where available, cost coverage | policy / availability | Bundle `TOBACCO_MPOWER_O2_MEDICATIONAVAILABILITY` (`O_Varen_sold/_where/_ins`) | clear | — |
| R6 | … Cytisine | O | Same | policy / availability | Same bundle (`O_Cytisine_sold/_where/_ins`) | clear | — |
| R7 | … NRT | O | Same, plus Essential Medicines List | policy / availability | Same bundle (`O_NRT_sold/_where/_ins/_EML`) | clear | — |
| R8 | … Combination NRT | O | Efficacy of an NRT regimen | **intervention effect** | Not in GHO; GHO does not distinguish NRT forms | needs another source | Q1 |
| R9 | … Nicotine patch alone | O | Same | **intervention effect** | Not in GHO | needs another source | Q1 |
| R10 | … Fast-acting NRT alone | O | Same | **intervention effect** | Not in GHO | needs another source | Q1 |
| R11 | … Bupropion | O | Legal sale, where available, cost coverage | policy / availability | Same bundle (`O_Bup_sold/_where/_ins`) | clear | — |
| R12 | National cessation support integrated into primary care / Counselling | O | Availability and cost coverage of tobacco-dependence treatment by setting | programme / service | Bundle `TOBACCO_MPOWER_O1_TREATMENTSUPPORT` (`O_Avail_hlth_clin`, `O_Ins_hlth_clin`, hospitals, community…) | clear | — |
| R13 | National free quit lines | O | Toll-free quit line | programme / service | `O_Quitline` (in O1 bundle); related: `O_mCessation`, quit-line number on packs `W25_quitline_number_A` | clear | — |
| R14 | Workplace cessation | O | Cessation programmes at workplaces | programme / service | No GHO indicator found (only smoke-free *workplaces*, `P5_indoor_off`) | needs another source | Q3 |
| R15 | Health warnings on tobacco packaging | W | Warning law, size, graphics, rotation… | policy | `W_Group` + bundles `TOBACCO_MPOWER_W1A/W1B/W1C` (cigarettes / other smoked / smokeless) | clear | — |
| R16 | Plain packaging | W | Mandatory plain packaging | policy | `W26_plain_packaging_A/B/C`, inside bundles `TOBACCO_MPOWER_W2A/W2B/W2C` | clear | — |
| R17 | Anti-tobacco mass media campaigns | W | National campaigns and their characteristics | programme | `W-MM_Group` + bundle `TOBACCO_MPOWER_W3_MASSMEDIA` (`Camp_*`, from 2010) | clear | — |
| R18 | Comprehensive bans on advertising, promotion and sponsorship (TAPS, partial, full) | E | Direct and indirect advertising bans, compliance | policy | `E_Group` + bundles `TOBACCO_MPOWER_E1_DIRECTADBANS`, `…_E2_INDIRECTADBANS`, `…_E3_INDIRECTADBANSPLUS` | clear | "Partial/full" is encoded in `E_Group` levels; never recomputed (§1.3) |
| R19 | School tobacco | — | Unclear: smoke-free schools, youth prevalence, or school prevention programmes | ? | (a) `P2_education` / `P_compl_p2`; (b) youth prevalence `TOBACCO_0000000344`, `Yth_curr_e-cig`; (c) school programmes: not in GHO | ambiguous | Q4 |

**Scope (assumed defaults):** all countries · all report years 2007–2024 · national level · long CSV + Excel + country-year panel.

**Analytical notes (parked, not part of the extraction):**

* A1: whether to model the P provisions jointly, or use `P_Group`, is an analysis decision. Retrieving the full P bundles keeps both options open.

## Questions (one round, defaults in brackets)

1. **Purpose of the "Interventions" column.** Combination NRT, patch alone and fast-acting NRT look like inputs for an effect model. Do you need intervention *effect sizes*? Those come from trials and reviews (e.g. Cochrane), not from WHO monitoring data. *[Default: retrieve country policy data only; R8–R10 parked as "effect sizes, separate literature task".]*
2. **Vaping in smoke-free laws.** MPOWER P covers smoking only. Should we look for a separate WHO source on e-cigarette regulation in Stage 2? *[Default: smoking-only for now; vaping regulation listed for Stage 2.]*
3. **Workplace cessation.** No GHO indicator exists. Drop it, or look for another source? *[Default: park.]*
4. **"School tobacco".** Do you mean (a) smoke-free school law, (b) youth tobacco use, or (c) school prevention programmes? *[Default: (a) + (b).]*
5. **M and R components.** They are not in your list. Retrieve only their composite scores (they come free with the overview table), or also their bundles? *[Default: composites only.]*

**Settled:** P, O-medications, O-services, W and E items (R1–R7, R11–R13, R15–R18).
**Open:** Q1–Q5 (R8–R10, R14, R19, vaping, scope of M/R).

---

## Notes on this example

* The engine can already retrieve the bundles as they are: the `who_gho` connector treats `TOBACCO_INDICATOR` as an ordinary dimension, and the values stay text in `value_text`.
* This example showed that the existing `projects/WHO_MPOWER` configuration, built on the `_Group` codes, misses the 2024 report year. That will be revisited when this request reaches Stage 5.
