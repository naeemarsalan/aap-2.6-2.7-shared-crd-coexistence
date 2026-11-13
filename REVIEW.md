# Review record

The README at commit `e256a6f` was given to independent reviewer agents, one per model family, each with the same task: clone the repo, critique it as a Red Hat OLM or AAP engineer would, and propose ranked solutions. Every objection and proposal was then checked against the evidence files, the bundle CRDs, the OLM source (`openshift/operator-framework-olm`, `release-4.20`) or a new API-level test, and accepted or rejected on that basis.

## Reviewers

| Family | Model | Runner | Outcome |
|---|---|---|---|
| Anthropic Claude | `claude-opus-5-5` | Claude Code | Completed: 12 objections, 11 solution options |
| DeepSeek | `deepseek-v4-pro` | pi | Completed: 5 objections, 8 solution options |
| OpenAI | `gpt-5.6-sol` | Codex CLI | Did not run: the login on the review machine had expired |
| Xiaomi MiMo | `mimo-v2-pro` | opencode | Did not run: the provider rejected the API key |

Two families reviewed the repo, not the three that were intended. DeepSeek was not in the original plan; it was the only other family with a working credential. The model names are the ones the agents were configured with. The DeepSeek agent said it could not confirm its own version.

The reviewers worked from the published repo only. They had no cluster access and no access to the bundle images.

## What was used to check

- The evidence files `00` to `18` from the live run.
- The extracted 2.6 and 2.7 bundle manifests (not committed), through `scripts/crd_diff.py` and the new `scripts/bundle_checks.py`. Output: `crd-diff/bundle-checks.txt`.
- OLM v0 source, `openshift/operator-framework-olm` `release-4.20`, and OLM v1 source, `openshift/operator-framework-operator-controller` `release-4.20`.
- New API-level tests on a local Kubernetes v1.33.4 cluster with the bundle CRDs and no operators: `evidence/19` and `evidence/20`.
- The live OpenShift cluster no longer existed, so nothing that needs running AAP operators could be re-tested.

## Claude (`claude-opus-5-5`)

### Objections

| # | Objection | Verdict | Reason and evidence |
|---|---|---|---|
| 1 | Evidence `12` is never cited and shows the 2.6 loss persists after the 2.6 schema is restored | Accepted | `12`: `scheduler: null` and Deployment at defaults with the CRDs back at the 2.6 hashes (`08`). `19` T1 reproduces it: after one status write the field stays gone under either schema. Short answer, step 3 and section 4 changed. |
| 2 | "Immediately, with no write by anyone" is contradicted by the resourceVersion change | Accepted | `02` has resourceVersion 38359, `04` has 40621. Step 3 now describes both stages. `19` T1 shows the field does vanish from reads with no write, and that this stage is reversible. |
| 3 | "Every CR still reported `Successful=True`" misstates `04` and `05`; no events were captured | Accepted | `04` and `05` show `Successful=False` with `Running=True`. Reworded; the events claim is removed. |
| 4 | "Each channel head `replaces` the previous build" is wrong | Accepted | `spec.replaces` and `spec.skips` are unset in all four CSVs (`bundle-checks.txt`). |
| 5 | The "patch update reverts the schema" case is labelled Live but was not observed | Accepted | `11` shows no generation change because the CRDs were already at 2.6, and only 6 of 29 CRDs carry the new annotation. Step 4 and the section 4 table now say the update case rests on the shared code path in `step.go`. |
| 6 | Rolling 2.6 to 2.7 upgrades across namespaces hit the same failure; "same build" is misread as "same minor"; three failure modes do not depend on skew | Accepted | 2.7 `olm.skipRange` is `>=2.6.0-0 <2.7.1`. Added to the Short answer, section 4 and section 6. Not run live. |
| 7 | "No object failed validation naturally" generalises beyond the three kinds that existed; hub backup and restore diffs understated | Accepted | `manifests/02` and `04` disable hub and lightspeed. Full diff lines for `status.conditions[].message` and `status.secretKeySecret` confirmed. Sentence narrowed, table row corrected. |
| 8 | The CEL rule is not visible in the published diff; OLM's gate does not evaluate CEL | Accepted | The rule text was cut by a 200-character limit in `crd_diff.py`; fixed, and the rule is now in the full diff and `bundle-checks.txt`. `validateExistingCRs` builds a validator from the OpenAPI schema only. Section 2 says so. |
| 9 | Several diff-derived claims cannot be reproduced from the repo | Accepted in part | Fixed: `crd_diff.py` now prints the per-CRD counts (they match the summary); `bundle_checks.py` shows the closed `.spec` schemas and that namespace-scoped and cluster-scoped CRDs are identical, 29 of 29, for both releases. Not done: the bundle manifests are still not committed, because they are Red Hat registry content; spec hashes are published instead. |
| 10 | Short answer wording: "permanently", "every run", "OLM reports nothing", "only" in the doc paraphrase, "fails quietly" | Accepted, one part rejected | Four reworded. Rejected in part: "every run" had more support than one run, since `10` shows the failure condition from 23:21:10 and another failed run completing at 23:27:31. The text now states exactly that. The second supported scenario in the OLM doc was restored to the quote. Strict field validation rejecting the patch is confirmed in `19` T2. |
| 11 | "The other 22 were untouched" is imprecise | Accepted | `03`: all 29 CRDs carry the new CSV's annotation; 22 kept generation 1. |
| 12 | Minor: "about a minute" has no timestamps; "every default install" from one install; doc link and 2.7 wording unverified; CSV status cited to the wrong file | Accepted | All four reworded. The AAP quote was checked against the doc source on the `2.6` and `main` branches; there is no `2.7` branch, and the docs site link could not be fetched to confirm it. |

### Solutions proposed

| Proposal | Verdict | Reason and evidence |
|---|---|---|
| Separate clusters | Accepted | CRDs are per API server. |
| Hosted control planes, one hosted cluster per AAP version | Accepted as untested | Sound in principle. Not run, and AAP support on hosted clusters is unconfirmed. Listed with that caveat. |
| Upgrade every namespace in one window instead of coexisting | Accepted as untested | Upgrade edge confirmed from the bundle. Not run. |
| Mutating admission webhook that sets `x-kubernetes-preserve-unknown-fields` on 19 nodes | Accepted in part | The node list is correct: `scripts/preserve_patch.py` derives the same 19 nodes independently, and `20` shows no pruning under either patched CRD. The webhook itself was not built or tested, and operator tolerance of the extra fields is unknown, so the README does not call it the "best" workaround. |
| Superset CRD: take the looser rule everywhere, drop the `ingress_type` default, freeze writes or mutate in admission | Accepted in part | Pruning result confirmed in `19` T3 and T5; the loss when a bundle CRD lands first confirmed in T4. The advice to drop the default depends on the 2.6 role defaults, which could not be checked without the operator images; `scripts/superset_crd.py` keeps the 2.6 default and the README states the choice is open. The write-freeze policy was not tested and is not in the README. |
| Manual approval, 2.7 schema wins, pinned catalog | Accepted | Consistent with `05` and `10`. |
| RFE: CRDs in a separate bundle, additive-only changes | Accepted | Matches the second supported scenario in the OLM documentation. |
| Conversion webhooks cannot work for namespace-scoped bundles in OLM v0 | Accepted | `pkg/controller/install/webhook.go`: `CSVs with conversion webhooks must support only AllNamespaces`. |
| OLM v1 refuses a second owner of a CRD, has single-namespace installs behind an alpha gate, and runs a CRD upgrade safety preflight | Accepted, one inference not adopted | Feature gate, install-conflict handling and preflight enforcement setting confirmed in the operator-controller source. The claim that the preflight would block the 2.6 to 2.7 change was not verified and is not in the README. |
| vcluster and a single cluster-scoped operator do not solve it | Accepted | `16` for the cluster-scoped case. vcluster was not tested. |

## DeepSeek (`deepseek-v4-pro`)

### Objections

| # | Objection | Verdict | Reason and evidence |
|---|---|---|---|
| 1 | "With no write by anyone" is contradicted by the resourceVersion change; Kubernetes prunes only when an object is next written | Accepted in part | The resourceVersion point is correct (`02`, `04`). The stated mechanism is wrong: `19` T1 shows the field disappears from reads with the resourceVersion unchanged, and comes back if the old CRD is restored before any write. The write is what makes it permanent. |
| 2 | The hub `status.conditions` tightening is under-weighted, because hub was never deployed | Accepted | "Not covered" and section 4 now say a real 2.6 hub is the likeliest place for a natural validation failure. `19` T6 shows the 2.7 CRD rejects a status condition without `lastTransitionTime`. Whether the 2.6 hub operator ever writes one is unknown. |
| 3 | The `replaces` sentence is wrong | Accepted | Same as Claude 4. |
| 4 | "The only arrangement that isolates the CRDs is separate clusters" is too broad | Accepted | Section 6 rewritten; hosted control planes and vcluster are covered in section 7. |
| 5 | `manifests/06` does not contain the status that trips the gate | Accepted | The file only had `spec: {}`. The status patch command is now in the file; `19` T6 confirms it is accepted under the 2.6 CRD and rejected under 2.7. |

### Solutions proposed

| Proposal | Verdict | Reason and evidence |
|---|---|---|
| Separate clusters as the only supported option | Accepted | |
| Serial cutover: uninstall 2.6, install 2.7, recreate instances from backups | Rejected as written | The 2.7 bundle upgrades 2.6 in place (`olm.skipRange >=2.6.0-0 <2.7.1`), so uninstalling and recreating adds downtime and risk for no benefit. The README lists the in-place, all-namespaces-in-one-window version. |
| Manual approval with an agreed order | Accepted | |
| Superset CRD: defaults cannot be merged, validation must be chosen, re-apply races OLM | Accepted | Confirmed by `19` T4. The reviewer named the wrong CEL rule as the one 2.7 added; the added rule is the `event_stream.database_secret` one (`bundle-checks.txt`). |
| vcluster or hosted control planes isolate CRDs but are unsupported | Accepted in part | Isolation is correct. "Not supported" is established for neither; the README says to confirm hosted control planes with Red Hat. |
| A single cluster-scoped operator is not viable | Accepted | `16`. |
| RFE for versioned CRDs with conversion webhooks as the long-term fix | Rejected as the recommendation | OLM v0 does not allow conversion webhooks in CSVs that support namespaced install modes (see Claude's finding, confirmed in source). The separate-CRD-bundle RFE is listed first instead. |
| OLM v1 removes the namespace-scoped option | Accepted in part | Close: single-namespace installs exist behind an alpha feature gate that is off by default. |

## Which reviewer caught what

- Only Claude: the uncited evidence `12` and the permanent loss on the 2.6 side; conditions misquoted; the Live label on the update case; rolling upgrades; the hidden CEL rule and OLM not evaluating CEL; reproducibility gaps; the elided second scenario in the OLM doc quote; the 22 CRDs being rewritten; the conversion-webhook restriction; the 19-node preserve-unknown-fields approach.
- Only DeepSeek: the missing status write in `manifests/06`; the over-broad "only arrangement" sentence.
- Both: the resourceVersion contradiction, the `replaces` error, and the under-tested hub path.
- Neither reviewer disputed the central finding or the OLM mechanics in section 2.

## Unresolved

- **Does the 2.6 platform operator copy `spec.eda.scheduler` back to the EDA object once the 2.6 schema is restored?** `12` shows it had not, with the parent still holding the setting, but it is not known whether a full platform reconcile had run by then. The 2.7 platform operator did re-push its child (`09`, `14`). Needs a live cluster.
- **Automation hub.** No hub, hub backup or hub restore object existed in the live run. Whether a real 2.6 hub trips OLM's existing-object gate or has status writes rejected under the 2.7 schema is unknown.
- **A 2.6 update InstallPlan reverting a live 2.7 schema** was not run; it rests on the source.
- **Operator tolerance of foreign fields**, which options 5 and 6 depend on, was not tested. Nor was any mechanism that keeps a patched CRD in place across an InstallPlan.
- **Hosted control planes and vcluster** were not tested.
- **OpenAI and MiMo reviews** did not run.
