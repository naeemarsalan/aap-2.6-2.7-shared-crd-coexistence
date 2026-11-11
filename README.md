# AAP operator 2.6 and 2.7 side by side on one OpenShift cluster: what happens to the shared CRDs

Two namespace-scoped Ansible Automation Platform operators, 2.6 in one namespace and 2.7 in another, on the same OpenShift cluster. The operators are namespaced; their 29 CustomResourceDefinitions are not. This repo records what OLM does with those shared CRDs, tested live, and what it takes to break one side.

## Short answer

It installs and both operators report `Succeeded`, but it is not safe. The 2.6 and 2.7 bundles ship different schemas for 7 of the 29 CRDs under the same version names, with no conversion webhooks, and OLM overwrites the cluster-wide CRD with whichever bundle's InstallPlan ran last. There is no version comparison, so 2.7 replaces 2.6's schema and a later 2.6 InstallPlan puts the 2.6 schema back.

Each direction damages the other side, and OLM raises no warning or condition:

- **2.7 schema on the cluster:** the API server stops returning `spec.scheduler` on 2.6 EDA objects, and the first write to the object (the operator's own status update is enough) makes the loss permanent. On the next reconcile the 2.6 EDA scheduler Deployment reverted to defaults. Putting the 2.6 schema back did not bring the setting back.
- **2.6 schema on the cluster:** the API server strips fields the 2.7 operators depend on. The 2.7 EDA reconcile failed until the 2.7 schema was restored, and user-set 2.7 fields such as `spec.gateway_timeouts` are removed from the stored object and have to be re-applied by hand.

The same thing happens without anyone choosing coexistence: upgrading several namespace-scoped 2.6 installs to 2.7 one namespace at a time puts the 2.7 schema under the namespaces still on 2.6.

OpenShift's OLM documentation supports different versions of one operator on a cluster in two cases only: they ship the exact same CRD definitions, or they ship no CRDs and take them from a separate bundle. AAP 2.6 and 2.7 are neither. The clean fix is one AAP version per control plane (separate clusters or hosted control planes). Single-cluster workarounds exist but modify operator-owned CRDs and are unsupported; see [Solutions](#7-solutions).

## Long answer

### What was tested

| | |
|---|---|
| Cluster | OpenShift 4.20.27 (Kubernetes v1.33.12), 3 control plane + 3 workers, disposable |
| Catalog | `redhat-operators`, package `ansible-automation-platform-operator` |
| 2.6 | `aap-operator.v2.6.0-0.1793352877`, channel `stable-2.6` (and `v2.6.0-0.1793137017` for the patch-update test) |
| 2.7 | `aap-operator.v2.7.0-0.1793374941`, channel `stable-2.7` |
| Cluster-scoped | `aap-operator.v2.7.0-0.1793374989`, channel `stable-2.7-cluster-scoped` |
| Instances | One `AnsibleAutomationPlatform` per version with controller and EDA enabled, hub and lightspeed disabled |

Results apply to these builds. Other builds may differ in which fields changed, including two builds of the same minor release, but the OLM mechanism is the same.

Not covered:

- Automation hub and lightspeed were not deployed (no RWX storage on the test cluster), and no backup or restore objects were created. The hub findings come from the schema diff and synthetic API-level tests. Hub is where 2.7 tightens validation, so a real 2.6 hub is the most likely place for the validation failures in section 4 to occur naturally; this is untested.
- The removed `ingress_type` defaults were not exercised.
- Whether each operator tolerates the other release's fields being present on its objects was not tested. It matters for the workarounds in section 7.

The live cluster was destroyed after the run. Evidence `19` and `20` were produced later on a local Kubernetes 1.33 cluster with the bundle CRDs and no operators; they show API-server behaviour only.

### 1. What the bundles contain

From the bundle images ([`crd-diff/`](crd-diff/), produced by [`scripts/extract-bundles.sh`](scripts/extract-bundles.sh), [`scripts/crd_diff.py`](scripts/crd_diff.py) and [`scripts/bundle_checks.py`](scripts/bundle_checks.py)). The bundle manifests themselves are not committed; pulling them needs a registry.redhat.io pull secret.

- Both CSVs own the same 29 CRDs, with 0 required CRDs and no `webhookdefinitions`.
- Every CRD has a single version that is both served and stored, and the version names are identical in 2.6 and 2.7 (`v1alpha1` or `v1beta1` depending on the CRD). No CRD sets `spec.conversion`. There is nothing for the API server to convert between; a field either exists in the current schema or it does not.
- `installModes`: the `stable-2.x` bundles support OwnNamespace, SingleNamespace and MultiNamespace but not AllNamespaces. The `-cluster-scoped` bundles add AllNamespaces. The CRDs in the namespace-scoped and cluster-scoped bundle of the same release are identical, 29 of 29 for both 2.6 and 2.7 ([`crd-diff/bundle-checks.txt`](crd-diff/bundle-checks.txt)).
- Upgrade graph: 2.6 has `olm.skipRange: '>=2.4.0-0.1727257740 <2.6.1'`, 2.7 has `'>=2.6.0-0 <2.7.1'`. No CSV sets `spec.replaces` or `spec.skips`; the upgrade edges come from `olm.skipRange` alone. Nothing in the graph relates a 2.6 install in one namespace to a 2.7 install in another.
- 22 CRDs are schema-identical between 2.6 and 2.7. 7 differ:

| CRD | Difference 2.6 → 2.7 |
|---|---|
| `edas.eda.ansible.com` | `spec.scheduler` removed (43 schema nodes); `spec.event_persistence` and `spec.event_stream.database_secret` added; many `affinity` blocks added; one CEL rule added (`event_stream.database_secret` must differ from `database.database_secret`); `ingress_type` default removed |
| `ansibleautomationplatforms.aap.ansible.com` | `spec.gateway_timeouts` and two status fields added |
| `automationhubs.automationhub.ansible.com` | `spec.secret_key_secret` and `affinity` blocks added; `status.conditions[]` now requires `lastTransitionTime`, `status`, `type`; `ingress_type` default removed |
| `automationhubbackups`, `automationhubrestores` | same `status.conditions[]` tightening; `status.conditions[].message` added to both; `status.secretKeySecret` added to backups |
| `automationcontrollers.automationcontroller.ansible.com` | `ingress_type` default removed |
| `ansiblelightspeeds.lightspeed.ansible.com` | `spec.chatbot_extra_settings` added |

One detail decides how much this matters: the `spec` of the component CRDs (EDA, controller, hub) is a closed structural schema, so unknown fields are pruned by the API server (`x-kubernetes-preserve-unknown-fields` is unset on `.spec` in all three, listed in `bundle-checks.txt`). On the parent `AnsibleAutomationPlatform` CRD, `spec.eda`, `spec.controller` and similar are `x-kubernetes-preserve-unknown-fields`, so the parent object keeps a setting that the child object loses.

### 2. How OLM handles a CRD two CSVs both own

Read from the OLM source shipped in OpenShift 4.20 (`openshift/operator-framework-olm`, branch `release-4.20`, `staging/operator-lifecycle-manager`) and confirmed by the live behaviour below.

- **The InstallPlan CRD step is an overwrite.** If the CRD exists, OLM copies its `resourceVersion` onto the bundle's manifest and calls `Update` with the whole object (`pkg/controller/operators/catalog/step.go`). The same code runs for a first install and for an update. It runs for every CRD in the bundle, including the ones whose schema is unchanged.
- **Two checks gate it, and neither is a version check.**
  - Existing CRs, listed cluster-wide across every namespace, must validate against the incoming OpenAPI schema. Failure message: `error validating existing CRs against new CRD's schema`.
  - Every name in `status.storedVersions` must still exist in the new CRD. Failure message: `risk of data loss updating`.
  - Removed fields, removed defaults and pruning are not checked. OLM does not compare bundle versions, so an older bundle overwrites a newer one.
  - The first check uses the OpenAPI schema only. CEL rules (`x-kubernetes-validations`) are not evaluated, so an existing object that violates a CEL rule added by the incoming CRD passes OLM's gate and is rejected later by the API server when it is next written.
- **The resolver does not see the conflict.** Resolution is per namespace. The OperatorGroup API conflict check only fires when the groups' target namespaces intersect, which two OwnNamespace groups never do.
- **The CSV requirement check is shallow.** It needs the CRD to exist, be Established, and serve the version name the CSV lists. Since both releases use the same version names, both CSVs stay `Succeeded` whichever schema is installed.
- **OLM never deletes CRDs** when a CSV or Subscription is removed, and it never rolls a CRD back.
- OLM records each installing CSV on the CRD as an `operatorframework.io/installed-alongside-<hash>` annotation. OLM uses it in the CSV requirement check for `replaces` chains inside one namespace. It does not arbitrate between namespaces.

### 3. Live reproduction

Evidence files are in [`evidence/`](evidence/), numbered in the order things happened.

**Step 1. 2.6 in namespace A.** All 29 CRDs created at generation 1. The instance was deployed with EDA scheduler settings (`replicas: 1`, a `node_selector`, custom resource requests), which the running `aap26-eda-scheduler` Deployment reflected. (`01`, `02`)

**Step 2. 2.7 in namespace B.** OLM did not block or warn. The InstallPlan completed, both CSVs `Succeeded`. Exactly the 7 CRDs listed above moved to generation 2 with the 2.7 schema. The other 22 were rewritten too, with an identical spec: their generation did not change, but all 29 gained the new CSV's `installed-alongside` annotation. (`03`)

**Step 3. Effect on the 2.6 instance.** (`04`, `05`, `06`, `12`, `19`)

- In the first snapshot after the 2.7 install, `spec.scheduler` was gone from the 2.6 EDA object (`aap26-eda`). Nobody edited the object: its generation stayed at 1. Its `resourceVersion` had moved from 38359 to 40621, so something, most likely the operator's status update, had written it in between. The parent `AnsibleAutomationPlatform` object still showed the setting under `spec.eda.scheduler`.
- The mechanism has two stages, shown without operators in `19`. Once the CRD changes, the API server prunes the field from every read, with no write to the object, and at that point putting the old CRD back makes the field reappear. The first write of any kind, including a status-only update, stores the pruned object, and after that the field is gone under either schema. AAP operators update status on every reconcile, so the reversible stage is short.
- This is what `12` shows on the live cluster: with the CRDs back at the 2.6 schema (step 4), `spec.scheduler` was still absent from the 2.6 EDA object and the scheduler Deployment was still at defaults. The 2.6 parent still held `spec.eda.scheduler` at that point. Whether the 2.6 platform operator would eventually copy it back to the child was not established; see [REVIEW.md](REVIEW.md), unresolved items.
- The running Deployment kept its settings until the 2.6 EDA operator next reconciled. After its pod restarted, the Deployment went from `replicas: 1`, the custom node selector and `111m / 211Mi` requests to `replicas: 2`, no node selector and `50m / 256Mi`.
- No CR reported `Failure=True` at any point, and the CSV stayed `Succeeded`. The snapshots in `04` and `05` were taken mid-reconcile (`Running=True`, `Successful=False`); by `12` all three CRs were `Successful=True` with the configuration still missing. Events were not captured.
- Putting the field back does not work under the 2.7 schema: `oc patch` prints `Warning: unknown field "spec.scheduler"` and reports `patched (no change)`. A client that requests strict field validation gets a rejection instead (`19`).

**Step 4. A 2.6 InstallPlan runs after 2.7.** A second 2.6 operator was installed in a third namespace, one patch build behind the channel head, then updated to the head. (`08`, `09`, `10`, `11`)

- The first 2.6 InstallPlan, a new install, moved all 7 CRDs to generation 3 with spec hashes identical to step 1. The 2.7 schema was replaced by 2.6's. All three CSVs stayed `Succeeded` (listed in `16`).
- The update to the channel head then ran with the CRDs already at the 2.6 schema, so it changed no generation (`11`, captured part-way through that InstallPlan). It shows an update InstallPlan rewriting the CRDs, but not an update reverting a live 2.7 schema. That case rests on the source: install and update share the same CRD step.
- On the 2.7 parent object, `spec.gateway_timeouts` was gone.
- On the 2.7 EDA object, `spec.event_persistence` and `spec.event_stream.database_secret` were gone. The second one was set by the 2.7 platform operator in this install, not by the user (the 2.7 manifest in `manifests/04` does not contain it).
- The 2.7 EDA operator then failed its reconciles. `10` records the `Failure` condition from 23:21:10 and another failed run completing at 23:27:31:

  ```
  TASK [postgres : Validate event stream configuration for external database]
  fatal: [localhost]: FAILED! => {"changed": false, "msg": "When using an external database, you must
  provide event stream database credentials. Specify 'event_stream.database_secret' in your EDA CR ..."}
  ```

  The EDA object went to `Failure=True`, `Successful=False`. Existing pods kept running, but the operator could no longer apply any change to that EDA.

**Step 5. Putting the 2.7 CRDs back.** OLM only rewrites CRDs when an InstallPlan runs, so the 7 CRDs were re-applied by hand from the 2.7 bundle. (`13`, `14`)

- The 2.7 EDA recovered without intervention: the platform operator rewrote the child object from the parent (generation 3 to 4) and `Failure` cleared.
- `spec.gateway_timeouts` on the parent did not come back. The pruned object had been persisted, so a user-authored field on a top-level CR is lost until someone re-applies it from a copy kept outside the cluster.
- The restored 2.7 schema is the same one that stripped `spec.scheduler` from the 2.6 EDA object in step 3. With these two builds no single schema serves both operators.

### 4. What breaks, and what it takes

| Trigger | Result | Observed |
|---|---|---|
| Any 2.7 InstallPlan (install or update) while 2.6 instances exist | 2.6 EDA objects lose `spec.scheduler`; scheduler Deployment reverts to defaults on the next 2.6 reconcile. No condition or warning. Not undone by restoring the 2.6 schema. | Live (install) |
| Upgrading several namespace-scoped 2.6 installs to 2.7 one namespace at a time | The first namespace's 2.7 InstallPlan is the row above for every namespace still on 2.6. | Follows from the row above; not run |
| Any 2.6 InstallPlan (new tenant or patch update) after 2.7 wrote the CRDs | CRDs revert to the 2.6 schema. 2.7 EDA reconcile fails until the 2.7 schema is back. User-set 2.7 fields are removed from stored objects. | Live for a new install. An update takes the same code path; not run against a live 2.7 schema |
| `installPlanApproval: Automatic` on either subscription | The two cases above happen whenever a new build lands in either channel, at a time nobody chose. | Follows from the two rows above; not run with Automatic |
| An existing object anywhere on the cluster that fails the incoming schema | The other tenant's InstallPlan goes `Failed` and its CSV stays `Pending`. | Live, synthetic object (`15`) |
| A 2.6 operator writing a status the 2.7 schema rejects (hub, hub backup, hub restore conditions without `lastTransitionTime`, `status` or `type`) | The API server rejects the status update. | API level only (`19`); not seen from a real operator. The conditions these operators wrote in this run all carried the three fields (`10`) |
| A cluster-scoped install next to namespace-scoped ones | CSV fails with `InterOperatorGroupOwnerConflict`, but its InstallPlan had already rewritten the shared CRDs. | Live (`16`) |
| Uninstalling one operator (delete Subscription and CSV) | CRDs stay, at whatever schema was written last. That tenant's CRs and workloads stay, unmanaged. The surviving operator keeps running against the other version's schema. | Live (`17`) |
| Deleting a CRD to "finish" a tenant's cleanup | Every tenant's objects of that kind are deleted. | Live (`18`) |

Detail on the last three rows:

- **InstallPlan gate (`15`).** An inert `AutomationHub` object in a namespace no operator watches, with a hand-written status condition lacking `lastTransitionTime` (the command is in `manifests/06`), is valid under the 2.6 CRD and invalid under 2.7. A 2.7 install in a different namespace then failed with `error validating existing CRs against new CRD's schema for "automationhubs.automationhub.ansible.com" ... updated validation is too restrictive: [].status.conditions[0].lastTransitionTime: Required value`. No AAP, EDA or controller object hit this between the two tested builds, and hub, where the tightening is, was not deployed; the test shows the gate exists and that one tenant's object can block another tenant's install.
- **Cluster-scoped (`16`).** The CRDs had been set to the 2.6 schema beforehand. After the failed cluster-scoped 2.7 install they were at the 2.7 schema, with zero operator pods running in that namespace.
- **CRD deletion (`18`).** After the 2.7 operator was uninstalled, `edas.eda.ansible.com` was deleted. The healthy 2.6 tenant's EDA object was removed along with all 8 of its EDA pods, and its platform CR went `Failure=True` with `create not allowed while custom resource definition is terminating`. The CRD then hung in `Terminating`, because the orphaned 2.7 EDA object still carried `eda.ansible.com/finalizer` and its operator no longer existed to clear it.

What did not break between these two builds:

- No storage-version change, so OLM's `risk of data loss` gate never fires and no stored-version migration is involved.
- No `AnsibleAutomationPlatform`, `EDA` or `AutomationController` object failed validation in either direction. Hub, lightspeed, backup and restore objects did not exist on the cluster, so nothing is known about them.
- The controller CRD differs only by a removed default, and both controllers stayed healthy throughout.

### 5. What the documentation says

AAP 2.6 and 2.7, "Installing the Ansible Automation Platform Operator" (same wording in both):

> If you are managing multiple Ansible Automation Platform instances with the same Ansible Automation Platform Operator version, use the cluster-scoped operator, which uses a single operator to manage all Ansible Automation Platform custom resources in your cluster.
>
> If you need multiple operator versions in the same cluster, you must use the namespace-scoped operator.

<https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install-assembly_install_aap_operator>

The doc source is `downstream/modules/platform/proc-install-aap-operator.adoc` in `ansible/aap-docs` (attribute names expanded above). The text is identical on its `2.6` and `main` branches; that repository has no `2.7` branch.

Neither release's install or upgrade pages mention that the CRDs are shared between namespace-scoped installs, or give an ordering rule for coexisting versions.

OpenShift 4.20, Operators, "Limitations for multitenant Operator management":

> Operators are control plane extensions due to their usage of CustomResourceDefinition objects (CRDs), which are global resources in Kubernetes. Different major versions of an Operator often have incompatible CRDs. This makes them incompatible to install simultaneously in different namespaces on a cluster.

> The supported scenarios include the following:
>
> - Operators of different versions that ship the exact same CRD definition (in case of versioned CRDs, the exact same set of versions)
> - Operators of different versions that do not ship a CRD, and instead have their CRD available in a separate bundle in the software catalog
>
> All other scenarios are not supported, because the integrity of the cluster data cannot be guaranteed if there are multiple competing or overlapping CRDs from different Operator versions to be reconciled on the same cluster.

<https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/operators/understanding-operators#olm-operatorgroups-limitations>

The AAP guidance is accurate about what OLM will let you install. The OLM limitation is the one that describes what happens afterwards, and AAP 2.6 plus 2.7 falls outside both supported scenarios: 7 CRDs differ, and both bundles ship their own CRDs.

### 6. Practical consequences

- Namespace-scoped installs whose bundles ship **identical CRDs** do not overwrite each other's schema. That is a property of the two builds, not of the minor version: two namespaces on `stable-2.6` with Manual approval can sit on different 2.6 builds, and nothing guarantees those have the same CRDs. The two 2.6 builds used here did (`08`, `11`).
- Three rows of the table in section 4 do not depend on version skew and apply to any set of namespace-scoped AAP installs: one tenant's object can block another tenant's InstallPlan, uninstalling leaves CRDs and objects behind, and deleting a CRD removes every tenant's objects.
- Treat every InstallPlan approval in any AAP namespace as a cluster-wide CRD change, and set `installPlanApproval: Manual` on every AAP subscription so none happens unplanned.
- Keep the CR manifests in version control. Fields pruned from a top-level CR are not recoverable from the cluster.
- Never delete AAP CRDs while any AAP instance remains on the cluster.
- Check which schema is live with [`scripts/crd-state.sh`](scripts/crd-state.sh): CRD generations, a hash of each spec, and the CSVs OLM recorded against each CRD.

### 7. Solutions

Three facts limit what can work:

1. OLM writes CRDs only when an InstallPlan runs, and then replaces the whole object. Anything applied by hand lasts until the next InstallPlan in any AAP namespace.
2. Pruning is reversible only until the object is next written, and the operators write status on every reconcile (`19`, T1 and T4). A fix that re-applies a CRD after OLM has overwritten it will usually be too late for some objects.
3. Neither schema is harmless to the other side, so no install order avoids damage. Order only chooses which side takes it.

| # | Option | Removes the CRD damage | Supported | How it was checked |
|---|---|---|---|---|
| 1 | One AAP version per cluster | Yes | Yes | Follows from CRDs being per API server |
| 2 | Hosted control planes, one hosted cluster per AAP version | Yes | Confirm with Red Hat for AAP | Not tested |
| 3 | No coexistence: move every AAP namespace to 2.7 in one window | Avoids it, apart from the window itself | Yes | Not run; upgrade edge read from the bundle |
| 4 | Manual approval, pinned catalog, agreed order | No. Makes it predictable | Yes | Follows from the live results |
| 5 | `x-kubernetes-preserve-unknown-fields` on the 19 schema nodes that differ | Pruning only | No | API level (`20`); delivery mechanism not built |
| 6 | Hand-merged superset CRD | Pruning and validation differences | No | API level (`19`); operators not run against it |
| 7 | RFE: AAP CRDs in their own bundle, additive-only schema changes | Yes, for future releases | n/a | OLM documentation |
| 8 | RFE: versioned CRDs with conversion webhooks | Not for namespace-scoped bundles | n/a | OLM source |
| 9 | OLM v1 (`ClusterExtension`) | No. It refuses the second install | n/a | operator-controller source |
| 10 | vcluster | In principle | No | Not tested |
| 11 | One cluster-scoped operator for both | No. One operator is one version | n/a | Live (`16`) |

**1. One AAP version per cluster.** The only arrangement that needs nothing unsupported and has no residual damage. It costs a second cluster. Within a cluster, keep every AAP namespace on the same build.

**2. Hosted control planes.** Each hosted cluster has its own API server and etcd, so its own CRDs and its own OLM, while the workers can share the management cluster's hardware (for example as OpenShift Virtualization VMs). This is option 1 with less hardware. It is still two clusters to operate. It was not tested here, and whether AAP is supported on hosted clusters should be confirmed with Red Hat.

**3. No coexistence.** If the goal is migration and not running both releases indefinitely, switch every AAP subscription from `stable-2.6` to `stable-2.7` in one maintenance window. The 2.7 CSV's `olm.skipRange` (`>=2.6.0-0 <2.7.1`) covers every 2.6 build.

- Requires: Manual approval everywhere, backups and CR manifests taken beforehand, and all InstallPlans approved back to back.
- Does not fix: between the first and the last approval, the namespaces still on 2.6 run on the 2.7 schema (step 3). `spec.eda.scheduler` settings are dropped in any case, because 2.7 removed the field. It is not available if some tenants must stay on 2.6.

**4. Manual approval with an agreed order.** Set `installPlanApproval: Manual` on every AAP subscription and give one owner all AAP InstallPlan approvals. The least-bad order is to let the 2.7 schema win: under the 2.6 schema the 2.7 EDA cannot reconcile at all, while under the 2.7 schema 2.6 loses EDA scheduler settings but keeps reconciling.

- Requires: after 2.7 is installed, no 2.6 InstallPlan is approved. That means no new 2.6 tenants and no 2.6 patch updates. Serving the catalog from a mirrored index at a fixed digest keeps new builds from appearing as pending InstallPlans.
- Does not fix: any of the damage. 2.6 runs degraded and frozen, including for security updates, for as long as both exist. The hub validation tightening is untested.

**5. Preserve unknown fields on the nodes that differ.** Leave each bundle's CRD as shipped, but set `x-kubernetes-preserve-unknown-fields: true` on every schema node whose child properties differ between the two releases, so the API server stops pruning there. [`scripts/preserve_patch.py`](scripts/preserve_patch.py) derives the list from the bundles: 19 nodes across 6 CRDs for these builds (`20`). Setting it on `.spec` alone is not enough, because pruning resumes inside any child object that declares its own properties; `spec.event_stream` is one.

- Checked: with the EDA CRD patched this way, an object carrying `scheduler`, `event_persistence` and `event_stream.database_secret` kept all three across status writes under both the 2.6 and the 2.7 CRD (`20`).
- Requires: the patch has to be on the CRD at the moment OLM writes it, or fact 2 applies. A mutating admission webhook on `customresourcedefinitions` could do that; none was built or tested here. A controller or GitOps tool that re-applies afterwards leaves a window. The node list has to be regenerated for every build that is approved.
- Does not fix: validation, defaults and CEL rules still come from whichever bundle wrote last. OLM's existing-object gate (`15`) still uses the bundle's own schema. Uninstall and CRD deletion behave as before.
- Not tested: whether each operator tolerates the other release's fields on its objects. One case is known to arise: when the schema allows it, the 2.7 platform operator copies `spec.eda.scheduler` from the parent into the 2.7 EDA object (`09`), so the 2.7 EDA operator would receive a variable its release removed.
- Modifies operator-owned CRDs. Not supported.

**6. Hand-merged superset CRD.** One CRD per kind containing both releases' fields. [`scripts/superset_crd.py`](scripts/superset_crd.py) builds it: properties are merged, `required` lists are intersected, formats and other constraints that differ are dropped, and CEL rules are combined.

- Checked: under the superset EDA CRD, 2.6 and 2.7 fields both survive writes, the CEL rule 2.7 added is still enforced, and `spec.gateway_timeouts` survives on the parent (`19`, T3 and T5).
- Checked: it does not survive OLM. When a bundle CRD is written over the superset and the object is written once before the superset is re-applied, the other release's fields are gone for good (`19`, T4).
- Choices it forces: a field has one default. `ingress_type` defaults to `Route` in 2.6 and has no default in 2.7. The script keeps the 2.6 default; 2.7 objects were served with that default while the 2.6 schema was live and the 2.7 controller stayed healthy (`09`), but neither choice was tested with hub or with ingress settings that matter. Taking the looser validation everywhere means 2.7 loses checks it added.
- Compared with option 5: it also removes the validation differences, at the cost of maintaining a merged schema per pair of builds (about 1,200 nodes for the EDA CRD alone). The delivery problem, the untested operator tolerance and the support position are the same.

**7. RFE: CRDs in their own bundle.** The OLM documentation's second supported scenario is operators that "do not ship a CRD, and instead have their CRD available in a separate bundle". If the 29 CRDs moved to a CRD-only package that both operator versions require, and schema changes within a served version were additive only (deprecate fields, do not remove them), the newest CRD would always be a superset older operators can live with, and no older bundle could write it backwards. This needs new builds of both releases and does nothing for the current ones.

**8. RFE: versioned CRDs with conversion webhooks.** This does not fit namespace-scoped installs under OLM v0:

- OLM refuses conversion webhooks unless the CSV supports only AllNamespaces (`pkg/controller/install/webhook.go`: `CSVs with conversion webhooks must support only AllNamespaces`). The `stable-2.x` bundles support the three namespaced modes instead.
- An older bundle that lacks the new version would be stopped by the stored-versions gate (`risk of data loss updating`) once objects are stored at the new version. That is safer than today, since the install fails and nothing is corrupted, but the two releases still cannot coexist.

**9. OLM v1.** Read from `openshift/operator-framework-operator-controller`, branch `release-4.20`. It prevents the situation and does not solve it:

- Installing a bundle into a single namespace is behind the `SingleOwnNamespaceInstallSupport` feature gate, alpha and off by default. Without it only bundles that support AllNamespaces can be installed, which for AAP means the cluster-scoped channels.
- A second `ClusterExtension` whose bundle contains a CRD that another one already owns is refused as an install conflict.
- A CRD upgrade safety preflight runs before a CRD is changed. It was not run against these bundles.

**10. vcluster.** A virtual cluster has its own API server and so its own CRDs. It has no OLM, no Routes and no SCCs unless they are added, so the operator bundle would have to be unpacked and applied by hand. Not supported for AAP and not tested here. Hosted control planes give the same isolation with a real OpenShift API.

**11. One cluster-scoped operator.** A single operator deployment is one version; it would reconcile every instance as that version. Adding a cluster-scoped operator next to namespace-scoped ones fails with `InterOperatorGroupOwnerConflict` after its InstallPlan has already rewritten the CRDs (`16`).

In short: use option 1 or 2 if a second control plane is possible, option 3 if the goal is migration, and option 4 for a short overlap with one side degraded. Options 5 and 6 are for the case where both releases must share an API server for longer and unsupported CRD changes are acceptable; both still need a delivery mechanism and an operator-level test that this repo does not have.

### Reproducing

Requires cluster-admin on a disposable OpenShift cluster, plus `oc`, `jq`, `grpcurl`, `python3` with PyYAML.

```bash
# bundle teardown
scripts/extract-bundles.sh /tmp/aap-bundles
python3 scripts/crd_diff.py /tmp/aap-bundles/stable-2.6 /tmp/aap-bundles/stable-2.7 2.6 2.7
python3 scripts/bundle_checks.py /tmp/aap-bundles

# 2.6 in namespace A
oc apply -f manifests/01-ns-a-aap26.yaml && scripts/approve-installplan.sh aap-crdtest-26
oc apply -f manifests/02-aap-cr-26.yaml           # wait for the instance
scripts/crd-state.sh; scripts/snapshot.sh aap-crdtest-26 aap26

# 2.7 in namespace B: CRDs change, 2.6 EDA object loses spec.scheduler
oc apply -f manifests/03-ns-b-aap27.yaml && scripts/approve-installplan.sh aap-crdtest-27
oc apply -f manifests/04-aap-cr-27.yaml
scripts/crd-state.sh; scripts/snapshot.sh aap-crdtest-26 aap26
# restart the 2.6 EDA operator pod, then snapshot again to see the Deployment drift

# a 2.6 InstallPlan after 2.7: CRDs revert, 2.7 EDA reconcile fails
oc apply -f manifests/05-ns-c-aap26-after-27.yaml && scripts/approve-installplan.sh aap-crdtest-26b
scripts/crd-state.sh; scripts/snapshot.sh aap-crdtest-27 aap27
```

`manifests/06` to `08` are the InstallPlan-gate and cluster-scoped tests. Do not run any of this on a cluster with AAP instances you care about: the first 2.7 InstallPlan changes their CRDs.

The API-level tests (`19`, `20`) need only the extracted bundles and any disposable Kubernetes 1.33 cluster with no AAP installed, for example kind:

```bash
python3 scripts/superset_crd.py   /tmp/aap-bundles/stable-2.6 /tmp/aap-bundles/stable-2.7 /tmp/superset
python3 scripts/preserve_patch.py /tmp/aap-bundles/stable-2.6 /tmp/aap-bundles/stable-2.7 /tmp/preserve
scripts/api-level-test.sh /tmp/aap-bundles/stable-2.6 /tmp/aap-bundles/stable-2.7 /tmp/superset
scripts/preserve-test.sh /tmp/preserve
```

`crd-diff/crd-diff-2.6-vs-2.7.summary.txt` is a hand-trimmed excerpt of the full listing.

### Repository layout

| Path | Contents |
|---|---|
| `manifests/` | Namespaces, OperatorGroups, Subscriptions and AAP CRs used in the tests |
| `scripts/` | Bundle extraction, CRD diff and bundle checks, InstallPlan approval, CRD state and instance snapshot helpers, superset and preserve-unknown-fields CRD generators, API-level tests |
| `crd-diff/` | Field-level diff of the 2.6 and 2.7 CRDs (a summary and the full listing) and `bundle-checks.txt` |
| `evidence/` | Captured output from each step of the live run (`00` to `18`) and from the API-level tests (`19`, `20`) |
| `REVIEW.md` | Multi-model adversarial review of this repo: objections, proposed solutions and the verdict on each |
