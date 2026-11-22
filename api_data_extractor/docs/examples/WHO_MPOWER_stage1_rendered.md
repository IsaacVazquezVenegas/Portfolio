# Extraction plan — WHO_MPOWER_policies

* **Status:** scoping
* **Created:** 2026-11-19
* **Prepared by:** LLM (Claude, following LLM_DATA_EXTRACTION_PROTOCOL.md) with the researcher

## Stage 1 — Scoping

**Starting point:** B (the researcher knows the source or dataset only by name or description).

**Inputs supplied:**

* Pasted table with three columns: Component (P/O/W/E), Policy and Interventions (table)

**Scope:**

| Item | Value | Origin |
|---|---|---|
| Countries | All countries | default |
| Period | All report years (2007-2024) | default |
| Breakdown | National level | default |
| Outputs | Long CSV + Excel + country-year panel | default |

**Retrieval strategy:** Retrieve wide, select later: the overview table TOBACCO_MPOWER_OVERVIEW (all seven composite scores) plus every component bundle the list touches, about 15 requests for all countries. Composite scores are retrieved as a reference and never recomputed.

### Catalogue look-ups (unverified until Stage 4)

* `https://ghoapi.azureedge.net/api/Indicator` (2026-11-19): Full GHO indicator list
* `https://ghoapi.azureedge.net/api/DIMENSION/TOBACCO_INDICATOR/DimensionValues` (2026-11-19): The 264 MPOWER component questions
* `https://ghoapi.azureedge.net/api/TOBACCO_MPOWER_<bundle>?$filter=SpatialDim eq 'FRA'` (2026-11-19) (rows R1, R5, R12, R15, R17, R18): One row per country x report year x question (Dim1 = TOBACCO_INDICATOR_<question>); report years 2007-2024

### Requirements Register

All candidate retrieval units come from WHO GHO.

| ID | Researcher's wording | Group | Interpreted concept | Kind of data | Origin | Candidate retrieval unit | Status | Question |
|---|---|---|---|---|---|---|---|---|
| R1 | Smoke and vaping free air laws / Comprehensive smoke-free law | P | National smoke-free legislation by type of place | policy | stated | `P_Group` (composite); `TOBACCO_MPOWER_P1_SMOKEFREEPLACES` (bundle): 8 place types; `TOBACCO_MPOWER_P2_SMOKEFREEPLACEDETAILS` (bundle): number of places, fines, enforcement | ambiguous | Q2 |
| R2 | Bans in public places / "Model P together?" | P | Same legislation, by place | policy | stated | `TOBACCO_MPOWER_P1_SMOKEFREEPLACES` (bundle): same bundles as R1, no extra request; `P_compl_all_sfe` (single): compliance scores, with P_compl_p1 to P_compl_p8 | clear | — |
| R3 | Eliminate second-hand exposure | P | Population exposure to second-hand smoke (an outcome, not a policy) | outcome | stated | `SHS_2` (single): women 15+; `SHS_3` (single): men 15+; `SHS_1` (single): children under 15; burden in SHS_4 to SHS_11 | clear | — |
| R4 | Smoke-free home with kids | P | Children's exposure at home (no MPOWER law covers private homes) | outcome | stated | `SHS_1` (single); `TOBACCO_0000000346` (single): youth survey: exposure at home | clear | — |
| R5 | Pharmaceutical cessation aid: Varenicline | O | Legal sale, where available, cost coverage | policy / programme or service | stated | `TOBACCO_MPOWER_O2_MEDICATIONAVAILABILITY` (bundle): O_Varen_sold/_where/_ins | clear | — |
| R6 | Pharmaceutical cessation aid: Cytisine | O | Legal sale, where available, cost coverage | policy / programme or service | stated | `TOBACCO_MPOWER_O2_MEDICATIONAVAILABILITY` (bundle): O_Cytisine_sold/_where/_ins | clear | — |
| R7 | Pharmaceutical cessation aid: NRT | O | Legal sale, where available, cost coverage, Essential Medicines List | policy / programme or service | stated | `TOBACCO_MPOWER_O2_MEDICATIONAVAILABILITY` (bundle): O_NRT_sold/_where/_ins/_EML | clear | — |
| R8 | Pharmaceutical cessation aid: Combination NRT | O | Efficacy of an NRT regimen | **intervention effect** | stated | — | needs another source: An effect size from trials and reviews (e.g. Cochrane); GHO does not distinguish NRT forms. | Q1 |
| R9 | Pharmaceutical cessation aid: Nicotine patch alone | O | Efficacy of an NRT regimen | **intervention effect** | stated | — | needs another source: An effect size from trials and reviews (e.g. Cochrane); not in GHO. | Q1 |
| R10 | Pharmaceutical cessation aid: Fast-acting NRT alone | O | Efficacy of an NRT regimen | **intervention effect** | stated | — | needs another source: An effect size from trials and reviews (e.g. Cochrane); not in GHO. | Q1 |
| R11 | Pharmaceutical cessation aid: Bupropion | O | Legal sale, where available, cost coverage | policy / programme or service | stated | `TOBACCO_MPOWER_O2_MEDICATIONAVAILABILITY` (bundle): O_Bup_sold/_where/_ins | clear | — |
| R12 | National cessation support integrated into primary care / Counselling | O | Availability and cost coverage of tobacco-dependence treatment by setting | programme or service | stated | `TOBACCO_MPOWER_O1_TREATMENTSUPPORT` (bundle): O_Avail_hlth_clin, O_Ins_hlth_clin, hospitals, community... | clear | — |
| R13 | National free quit lines | O | Toll-free quit line | programme or service | stated | `O_Quitline` (single): in the O1 bundle; `O_mCessation` (single): related; `W25_quitline_number_A` (single): quit-line number on packs | clear | — |
| R14 | Workplace cessation | O | Cessation programmes at workplaces | programme or service | stated | — | needs another source: No GHO indicator found (only smoke-free workplaces, P5_indoor_off). | Q3 |
| R15 | Health warnings on tobacco packaging | W | Warning law, size, graphics, rotation... | policy | stated | `W_Group` (composite); `TOBACCO_MPOWER_W1A_CIGARETTES` (bundle): with W1B (other smoked) and W1C (smokeless) | clear | — |
| R16 | Plain packaging | W | Mandatory plain packaging | policy | stated | `W26_plain_packaging_A` (single): with _B and _C, inside bundles TOBACCO_MPOWER_W2A/W2B/W2C | clear | — |
| R17 | Anti-tobacco mass media campaigns | W | National campaigns and their characteristics | programme or service | stated | `W-MM_Group` (composite); `TOBACCO_MPOWER_W3_MASSMEDIA` (bundle): Camp_* questions, from 2010 | clear | — |
| R18 | Comprehensive bans on advertising, promotion and sponsorship (TAPS, partial, full) | E | Direct and indirect advertising bans, compliance | policy | stated | `E_Group` (composite): 'partial/full' is encoded in the E_Group levels; never recomputed; `TOBACCO_MPOWER_E1_DIRECTADBANS` (bundle); `TOBACCO_MPOWER_E2_INDIRECTADBANS` (bundle); `TOBACCO_MPOWER_E3_INDIRECTADBANSPLUS` (bundle) | clear | — |
| R19 | School tobacco | — | Unclear: smoke-free schools, youth prevalence, or school prevention programmes | ? | stated | `P2_education` (single): (a) smoke-free schools, with P_compl_p2; `TOBACCO_0000000344` (single): (b) youth prevalence, with Yth_curr_e-cig | ambiguous | Q4 |

### Questions

* **Q1** (round 1, rows R8, R9, R10): Combination NRT, patch alone and fast-acting NRT look like inputs for an effect model. Do you need intervention effect sizes? Those come from trials and reviews (e.g. Cochrane), not from WHO monitoring data. *[Default: Retrieve country policy data only; R8-R10 parked as 'effect sizes, separate literature task'.]*
  * Open.
* **Q2** (round 1, rows R1): MPOWER P covers smoking only. Should we look for a separate WHO source on e-cigarette regulation in Stage 2? *[Default: Smoking only for now; vaping regulation listed for Stage 2.]*
  * Open.
* **Q3** (round 1, rows R14): No GHO indicator exists for workplace cessation. Drop it, or look for another source? *[Default: Park.]*
  * Open.
* **Q4** (round 1, rows R19): By 'school tobacco', do you mean (a) smoke-free school law, (b) youth tobacco use, or (c) school prevention programmes? *[Default: (a) + (b).]*
  * Open.
* **Q5** (round 1): M and R are not in your list. Retrieve only their composite scores (they come free with the overview table), or also their bundles? *[Default: Composites only.]*
  * Open.

### Analytical notes (not part of the extraction)

* A1 (rows R2): Whether to model the P provisions jointly, or use P_Group, is an analysis decision. Retrieving the full P bundles keeps both options open.

**Settled:** R2, R3, R4, R5, R6, R7, R8, R9, R10, R11, R12, R13, R14, R15, R16, R17, R18.
**Open:** R1, R19, Q1, Q2, Q3, Q4, Q5.

## Not retrieved by this plan

* **R8** Pharmaceutical cessation aid: Combination NRT: needs another source. An effect size from trials and reviews (e.g. Cochrane); GHO does not distinguish NRT forms.
* **R9** Pharmaceutical cessation aid: Nicotine patch alone: needs another source. An effect size from trials and reviews (e.g. Cochrane); not in GHO.
* **R10** Pharmaceutical cessation aid: Fast-acting NRT alone: needs another source. An effect size from trials and reviews (e.g. Cochrane); not in GHO.
* **R14** Workplace cessation: needs another source. No GHO indicator found (only smoke-free workplaces, P5_indoor_off).

## Validation warnings

* register row R1 ('Smoke and vaping free air laws / Comprehensive smoke-free law') is still ambiguous
* register row R19 ('School tobacco') is still ambiguous
* question Q1 is open (default if unanswered: Retrieve country policy data only; R8-R10 parked as 'effect sizes, separate literature task'.)
* question Q2 is open (default if unanswered: Smoking only for now; vaping regulation listed for Stage 2.)
* question Q3 is open (default if unanswered: Park.)
* question Q4 is open (default if unanswered: (a) + (b).)
* question Q5 is open (default if unanswered: Composites only.)
