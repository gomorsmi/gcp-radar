"""gcp-radar: GPU fleet savings module.

Reproduces the Pump "GPU fleet" page from the CLI:
  KPIs        -> GPU spend, % busy, idle GPU-hours / idle spend, fleet-mix savings
  per type    -> allocated / requested / busy GPU-hours, cost per allocated vs busy hour, markup
  recs        -> stop idle VMs, shrink node pools, downshift GPU type, time-share / MIG

Data sources (read-only):
  * BigQuery detailed billing export      -> GPU node cost (whole node: GPU + vCPU + RAM)
  * Cloud Monitoring                      -> uptime, duty cycle, GPU memory, GPU requests
  * Compute Engine + GKE APIs             -> inventory (machine type, accelerators, node counts)

pip install google-cloud-bigquery google-cloud-monitoring google-cloud-compute google-cloud-container
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, asdict

from google.cloud import bigquery, compute_v1, monitoring_v3

HOURS_PER_MONTH = 730
IDLE_DUTY_PCT = 2.0          # below this average duty cycle a VM counts as idle
DOWNSHIFT_MAX_DUTY = 40.0    # workload must be under this busy % ...
DOWNSHIFT_MEM_HEADROOM = 0.8 # ... and peak memory under 80% of the smaller GPU's memory

ACCEL_NAME = {
    "nvidia-h100-80gb": "H100", "nvidia-h100-mega-80gb": "H100",
    "nvidia-tesla-a100": "A100", "nvidia-a100-80gb": "A100-80",
    "nvidia-l4": "L4", "nvidia-tesla-t4": "T4", "nvidia-tesla-v100": "V100",
}
FAMILY_GPU = {"a3": "H100", "a2": "A100", "g2": "L4"}  # accelerator-optimized families
GPU_MEM_GB = {"H100": 80, "A100-80": 80, "A100": 40, "V100": 16, "L4": 24, "T4": 16}
# Cheapest-first ladder used for downshift recommendations
LADDER = ["T4", "L4", "A100", "A100-80", "H100"]


@dataclass
class GpuResource:
    name: str
    kind: str                 # "vm" | "nodepool" | "workload"
    location: str
    gpu_type: str
    gpus: int                 # GPUs per VM/node (workload: GPUs requested per pod)
    nodes: int = 1
    alloc_hours: float = 0.0
    requested_hours: float = 0.0
    busy_hours: float = 0.0   # sum(duty_cycle/100 * GPU-hours)
    peak_mem_gb: float = 0.0
    cost: float = 0.0

    @property
    def busy_pct(self) -> float:
        return 100 * self.busy_hours / self.alloc_hours if self.alloc_hours else 0.0

    @property
    def idle_hours(self) -> float:
        return max(self.alloc_hours - self.busy_hours, 0.0)


# ---------------------------------------------------------------- collectors
def gpu_from_machine(machine_type: str, accelerators) -> tuple[str, int] | None:
    if accelerators:
        acc = accelerators[0]
        return ACCEL_NAME.get(acc.accelerator_type.split("/")[-1], acc.accelerator_type), acc.accelerator_count
    fam = machine_type.split("-")[0]
    if fam in FAMILY_GPU:  # e.g. a2-highgpu-8g -> 8, g2-standard-4 -> 1
        tail = machine_type.split("-")[-1]
        count = int(tail[:-1]) if tail.endswith("g") and tail[:-1].isdigit() else 1
        gpu = "A100-80" if "ultragpu" in machine_type else FAMILY_GPU[fam]
        return gpu, count
    return None


def list_gpu_vms(project: str) -> dict[str, GpuResource]:
    """Standalone GPU VMs + GKE GPU nodes (rolled up to node pools via labels)."""
    out: dict[str, GpuResource] = {}
    for zone, scoped in compute_v1.InstancesClient().aggregated_list(project=project):
        for inst in scoped.instances or []:
            mt = inst.machine_type.split("/")[-1]
            gpu = gpu_from_machine(mt, inst.guest_accelerators)
            if not gpu:
                continue
            pool = inst.labels.get("goog-k8s-node-pool-name")
            key, kind = (pool, "nodepool") if pool else (inst.name, "vm")
            if key in out:
                out[key].nodes += 1
            else:
                out[key] = GpuResource(key, kind, zone.split("/")[-1], gpu[0], gpu[1])
    return out


def billing_cost(bq_table: str, project: str, start: dt.datetime, end: dt.datetime) -> dict[str, float]:
    """Net cost per VM name or GKE node pool (whole node cost, credits applied)."""
    sql = f"""
    SELECT
      COALESCE((SELECT value FROM UNNEST(labels) WHERE key = 'goog-k8s-node-pool-name'),
               resource.name) AS res,
      SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS net
    FROM `{bq_table}`
    WHERE project.id = @project AND service.description = 'Compute Engine'
      AND usage_start_time >= @start AND usage_start_time < @end
    GROUP BY res"""
    job = bigquery.Client().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("project", "STRING", project),
        bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
        bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
    ]))
    return {r.res: float(r.net) for r in job}


def _series_sum(project, metric_filter, group_by, start, end, reducer, aligner):
    client = monitoring_v3.MetricServiceClient()
    interval = monitoring_v3.TimeInterval(start_time=start, end_time=end)
    agg = monitoring_v3.Aggregation(
        alignment_period={"seconds": 3600}, per_series_aligner=aligner,
        cross_series_reducer=reducer, group_by_fields=group_by)
    out = defaultdict(list)
    for ts in client.list_time_series(name=f"projects/{project}", filter=metric_filter,
                                      interval=interval, aggregation=agg):
        labels = {**ts.metric.labels, **ts.resource.labels, **ts.metadata.user_labels}
        key = next((labels[k.split(".")[-1].strip('"')] for k in group_by
                    if labels.get(k.split(".")[-1].strip('"'))), "?")
        out[key] += [p.value.double_value or p.value.int64_value for p in ts.points]
    return out


def collect_utilization(project, res: dict[str, GpuResource], start, end) -> None:
    A, R = monitoring_v3.Aggregation.Aligner, monitoring_v3.Aggregation.Reducer
    # Allocated GPU-hours = node uptime hours * GPUs per node
    up = _series_sum(project, 'metric.type="compute.googleapis.com/instance/uptime"',
                     ['metadata.user_labels."goog-k8s-node-pool-name"', "metric.label.instance_name"],
                     start, end, R.REDUCE_SUM, A.ALIGN_SUM)
    for key, pts in up.items():
        if key in res:
            res[key].alloc_hours = sum(pts) / 3600 * res[key].gpus
    # Standalone VMs: Ops Agent NVML metric (requires Ops Agent on the VM)
    util = _series_sum(project, 'metric.type="agent.googleapis.com/gpu/utilization"',
                       ["metric.label.instance_name"], start, end, R.REDUCE_MEAN, A.ALIGN_MEAN)
    for key, pts in util.items():
        if key in res and pts:
            res[key].busy_hours = res[key].alloc_hours * (sum(pts) / len(pts)) / 100


def collect_gke_workloads(project, start, end) -> dict[str, GpuResource]:
    """Per-workload requests, duty cycle and peak memory from GKE system metrics."""
    A, R = monitoring_v3.Aggregation.Aligner, monitoring_v3.Aggregation.Reducer
    grp = ['metadata.system_labels."top_level_controller_name"']
    req = _series_sum(project, 'metric.type="kubernetes.io/container/accelerator/request"',
                      grp, start, end, R.REDUCE_SUM, A.ALIGN_MEAN)
    duty = _series_sum(project, 'metric.type="kubernetes.io/container/accelerator/duty_cycle"',
                       grp, start, end, R.REDUCE_MEAN, A.ALIGN_MEAN)
    mem = _series_sum(project, 'metric.type="kubernetes.io/container/accelerator/memory_used"',
                      grp, start, end, R.REDUCE_MAX, A.ALIGN_MAX)
    out = {}
    for name, pts in req.items():
        hours = sum(pts)  # hourly samples of GPUs requested -> GPU-hours
        d = duty.get(name, [0])
        w = GpuResource(name, "workload", "", "?", round(max(pts)) or 1)
        w.requested_hours = w.alloc_hours = hours
        w.busy_hours = hours * (sum(d) / len(d)) / 100
        w.peak_mem_gb = max(mem.get(name, [0])) / 1e9
        out[name] = w
    return out


# ---------------------------------------------------------------- analysis
def unit_costs(resources) -> dict[str, float]:
    """Observed $/allocated GPU-hour per type (fallback: pass list prices via overrides)."""
    cost, hours = defaultdict(float), defaultdict(float)
    for r in resources:
        cost[r.gpu_type] += r.cost
        hours[r.gpu_type] += r.alloc_hours
    return {t: cost[t] / hours[t] for t in cost if hours[t]}


def summarize(resources) -> dict:
    by = defaultdict(lambda: defaultdict(float))
    for r in resources:
        b = by[r.gpu_type]
        b["gpus"] += r.gpus * r.nodes
        for f in ("cost", "alloc_hours", "requested_hours", "busy_hours"):
            b[f] += getattr(r, f)
    for t, b in by.items():
        b["busy_pct"] = 100 * b["busy_hours"] / b["alloc_hours"] if b["alloc_hours"] else 0
        b["idle_hours"] = b["alloc_hours"] - b["busy_hours"]
        b["cost_per_alloc_hr"] = b["cost"] / b["alloc_hours"] if b["alloc_hours"] else 0
        b["cost_per_busy_hr"] = b["cost"] / b["busy_hours"] if b["busy_hours"] else 0
        b["markup"] = b["cost_per_busy_hr"] / b["cost_per_alloc_hr"] if b["cost_per_alloc_hr"] else 0
        b["idle_spend"] = b["idle_hours"] * b["cost_per_alloc_hr"]
    return {t: dict(b) for t, b in by.items()}


def recommend(resources, workloads, price: dict[str, float]) -> list[dict]:
    recs = []
    for r in resources:
        hourly = price.get(r.gpu_type, 0) * r.gpus
        # 1. Idle standalone VM -> stop
        if r.kind == "vm" and r.alloc_hours and r.busy_pct < IDLE_DUTY_PCT:
            recs.append(_rec(r, f"{r.gpus}x {r.gpu_type} -> stopped", "Stop VM", "Low",
                             hourly * HOURS_PER_MONTH))
        # 2. Node pool allocated well above requested -> shrink
        if r.kind == "nodepool" and r.alloc_hours and r.requested_hours:
            needed = max(1, -(-r.requested_hours // (r.alloc_hours / r.nodes)))  # ceil
            if needed < r.nodes:
                recs.append(_rec(r, f"{r.nodes} -> {int(needed)} nodes", "Shrink node pool / autoscale",
                                 "Low", (r.nodes - needed) * hourly * HOURS_PER_MONTH))
    for w in workloads:
        # 3. Low duty + memory fits a cheaper GPU -> downshift type
        cur = w.gpu_type if w.gpu_type in LADDER else None
        if cur and w.busy_pct < DOWNSHIFT_MAX_DUTY:
            for cand in LADDER[:LADDER.index(cur)]:
                if w.peak_mem_gb < GPU_MEM_GB[cand] * DOWNSHIFT_MEM_HEADROOM and cand in price:
                    new_gpus = 1
                    saving = (price[cur] * w.gpus - price[cand] * new_gpus) * HOURS_PER_MONTH
                    if saving > 0:
                        recs.append(_rec(w, f"{w.gpus}x {cur} per pod -> {new_gpus}x {cand} per pod",
                                         f"Move to {cand}", "Medium", saving))
                    break
        # 4. Requests a whole GPU but barely uses it -> time-sharing / MIG
        elif w.busy_pct < 15 and w.gpus == 1:
            recs.append(_rec(w, "1 GPU -> shared (time-sharing / MIG)", "Share GPU", "Medium",
                             0.5 * price.get(w.gpu_type, 0) * HOURS_PER_MONTH))
    return sorted(recs, key=lambda x: -x["savings_mo"])


def _rec(r, change, action, risk, saving):
    return {"resource": r.name, "kind": r.kind, "location": r.location, "change": change,
            "action": action, "risk": risk, "busy_pct": round(r.busy_pct, 1),
            "savings_mo": round(saving, 2)}


# ---------------------------------------------------------------- attention / FlashAttention
# Standard attention materialises the full S = QK^T score matrix in HBM (seq^2 per head);
# FlashAttention tiles it through on-chip SRAM with an online softmax and never writes S.
# Kernel support by GPU: FA3 = Hopper, FA2 = Ampere/Ada/Hopper, pre-Ampere = xformers/SDPA only.
FLASH_SUPPORT = {"H100": "FA3", "A100": "FA2", "A100-80": "FA2", "L4": "FA2", "T4": None, "V100": None}

INFERENCE_IMAGES = ("vllm", "sglang", "text-generation-inference", "tgi", "tensorrt-llm", "triton")
# Settings that force a non-flash attention path (engine defaults are flash on Ampere+)
NON_FLASH_FLAGS = {
    "VLLM_ATTENTION_BACKEND": {"XFORMERS", "TORCH_SDPA"},   # vLLM env override
    "--attention-backend": {"triton", "torch_native"},       # SGLang flag
    "attn_implementation": {"eager"},                        # HF transformers
    "USE_FLASH_ATTENTION": {"false", "0"},                   # TGI
}
CONTEXT_FLAGS = ("--max-model-len", "--context-length", "--max-input-length", "MAX_INPUT_TOKENS")


def score_matrix_bytes(seq_len: int, heads: int = 1, batch: int = 1, dtype_bytes: int = 2) -> int:
    """HBM bytes standard attention writes for S in ONE layer. 8192 tokens, 1 head, fp16 = 128 MiB."""
    return seq_len * seq_len * dtype_bytes * heads * batch


def detect_attention(container) -> dict:
    """Classify one container spec (kubernetes V1Container) -> flash / non-flash / unknown."""
    image = (container.image or "").lower()
    tokens = list(container.args or []) + list(container.command or [])
    env = {e.name: (e.value or "") for e in (container.env or [])}
    flat = {**env}
    for i, t in enumerate(tokens):
        if "=" in t:
            k, v = t.split("=", 1)
            flat[k] = v
        elif t.startswith("--") and i + 1 < len(tokens):
            flat[t] = tokens[i + 1]
    context = next((int(flat[k]) for k in CONTEXT_FLAGS if str(flat.get(k, "")).isdigit()), None)
    for key, bad in NON_FLASH_FLAGS.items():
        if str(flat.get(key, "")).lower() in {b.lower() for b in bad}:
            return {"status": "non-flash", "reason": f"{key}={flat[key]}", "context": context}
    if any(x in image for x in INFERENCE_IMAGES):
        return {"status": "flash-default", "reason": f"engine image {image.split('/')[-1]}", "context": context}
    return {"status": "unknown", "reason": "custom image; check attn_implementation in code", "context": context}


def collect_pod_specs(kube_api) -> dict[str, list]:
    """GPU containers per top-level controller. kube_api = kubernetes.client.CoreV1Api()."""
    out = defaultdict(list)
    for pod in kube_api.list_pod_for_all_namespaces(field_selector="status.phase=Running").items:
        gpu_containers = [c for c in pod.spec.containers
                          if c.resources and c.resources.limits and "nvidia.com/gpu" in c.resources.limits]
        if not gpu_containers:
            continue
        owner = (pod.metadata.owner_references or [None])[0]
        name = owner.name if owner else pod.metadata.name
        if owner and owner.kind == "ReplicaSet":
            name = name.rsplit("-", 1)[0]  # strip pod-template hash -> Deployment name
        out[name] += gpu_containers
    return out


def attention_findings(workloads, pod_specs: dict[str, list]) -> list[dict]:
    """Findings are throughput/memory levers, reported separately from $ fleet-mix savings."""
    findings = []
    for w in workloads:
        for c in pod_specs.get(w.name, []):
            a = detect_attention(c)
            support = FLASH_SUPPORT.get(w.gpu_type)
            hbm = (f"~{score_matrix_bytes(a['context']) / 2**20:,.0f} MiB of scores per head per layer"
                   if a["context"] else "seq^2 x 2 B per head per layer")
            if a["status"] == "non-flash" and support:
                findings.append({"resource": w.name, "gpu": w.gpu_type, "finding": "FlashAttention disabled",
                                 "evidence": a["reason"], "action": f"Remove override; {support} is supported",
                                 "impact": f"stops writing {hbm} to HBM; frees KV-cache room for larger batches",
                                 "risk": "Low"})
            elif a["status"] != "unknown" and support is None:
                findings.append({"resource": w.name, "gpu": w.gpu_type, "finding": "No FA2 kernels on this GPU",
                                 "evidence": f"{w.gpu_type} is pre-Ampere; {a['reason']}",
                                 "action": "Move inference to L4 (FA2) or consolidate",
                                 "impact": f"long prompts pay {hbm}; pair with fleet-mix recs", "risk": "Medium"})
            elif a["status"] == "unknown" and w.busy_pct < DOWNSHIFT_MAX_DUTY:
                findings.append({"resource": w.name, "gpu": w.gpu_type, "finding": "Attention path unverified",
                                 "evidence": a["reason"],
                                 "action": "Confirm attn_implementation='flash_attention_2' or 'sdpa'",
                                 "impact": "eager attention is HBM-bound; may explain low duty cycle", "risk": "Low"})
            break  # one finding per workload
    return findings


# ---------------------------------------------------------------- entry point
def run(project: str, bq_table: str, days: int = 30, price_overrides: dict | None = None,
        kube_api=None) -> dict:
    """kube_api: optional kubernetes.client.CoreV1Api() for the FlashAttention check."""
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    res = list_gpu_vms(project)
    for k, c in billing_cost(bq_table, project, start, end).items():
        if k in res:
            res[k].cost = c
    collect_utilization(project, res, start, end)
    workloads = collect_gke_workloads(project, start, end)
    # Workload GPU type: take the pool type when there's one GPU type in the fleet; otherwise
    # join via pod -> node -> pool (kubernetes.io/node labels) in your inventory step.
    types = {r.gpu_type for r in res.values() if r.kind == "nodepool"}
    for w in workloads.values():
        if len(types) == 1:
            w.gpu_type = next(iter(types))
    price = {**unit_costs(res.values()), **(price_overrides or {})}
    recs = recommend(res.values(), workloads.values(), price)
    attention = attention_findings(workloads.values(), collect_pod_specs(kube_api)) if kube_api else []
    by_type = summarize(res.values())
    total_alloc = sum(b["alloc_hours"] for b in by_type.values())
    total_busy = sum(b["busy_hours"] for b in by_type.values())
    return {
        "kpis": {
            "gpu_spend": round(sum(b["cost"] for b in by_type.values()), 2),
            "gpus": int(sum(b["gpus"] for b in by_type.values())),
            "busy_pct": round(100 * total_busy / total_alloc, 1) if total_alloc else 0,
            "idle_gpu_hours": round(total_alloc - total_busy),
            "idle_spend": round(sum(b["idle_spend"] for b in by_type.values()), 2),
            "fleet_mix_savings_mo": round(sum(r["savings_mo"] for r in recs), 2),
            "recommendations": len(recs),
            "attention_findings": len(attention),
        },
        "by_type": by_type,
        "resources": [asdict(r) for r in res.values()],
        "recommendations": recs,
        "attention": attention,
    }
