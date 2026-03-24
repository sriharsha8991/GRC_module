# Kubernetes Deployment Guide for the GRC Module

> **Audience:** You — a developer who knows Docker Compose but has never used Kubernetes.  
> **Goal:** Understand every K8s concept you need, see exactly how your current GRC architecture maps to it, and know which files matter before writing a single manifest.

---

## Table of Contents

1. [Part 1 — What Is Kubernetes and Why Use It?](#part-1--what-is-kubernetes-and-why-use-it)
2. [Part 2 — Core Kubernetes Concepts (The Building Blocks)](#part-2--core-kubernetes-concepts-the-building-blocks)
3. [Part 3 — Your Current Architecture (Docker Compose)](#part-3--your-current-architecture-docker-compose)
4. [Part 4 — Mapping Docker Compose → Kubernetes](#part-4--mapping-docker-compose--kubernetes)
5. [Part 5 — Files You Need to Understand in Your Codebase](#part-5--files-you-need-to-understand-in-your-codebase)
6. [Part 6 — The Kubernetes File Structure We'll Create](#part-6--the-kubernetes-file-structure-well-create)
7. [Part 7 — Multi-Environment Strategy (dev/staging/prod)](#part-7--multi-environment-strategy-devstaginprod)
8. [Part 8 — What Changes and What Stays the Same](#part-8--what-changes-and-what-stays-the-same)
9. [Part 9 — Prerequisites and Tools You Need](#part-9--prerequisites-and-tools-you-need)
10. [Part 10 — Glossary](#part-10--glossary)

---

## Part 1 — What Is Kubernetes and Why Use It?

### The Problem Docker Compose Solves
Right now you run `docker compose up` and it starts 4 containers on **one machine**. That's great for development. But:

- What happens if that machine dies? **Everything goes down.**
- What if your API gets 10× more traffic? You can't easily spin up more API containers and load-balance between them.
- What if you want zero-downtime deployments? Compose just stops and restarts containers.
- What if you need dev, staging, and prod environments with different configurations? You end up with multiple `.env` files and hacky scripts.

### What Kubernetes (K8s) Is
Kubernetes is a **container orchestration platform**. You tell it:
> *"I want 2 copies of my API running at all times, connected to Qdrant and Redis, accessible on port 8000 via the gateway, and if any container dies, restart it automatically."*

Kubernetes makes that happen — across one server or hundreds.

### The Mental Model

```
Docker Compose                          Kubernetes
─────────────────                       ──────────────────
You run everything on YOUR laptop       You describe what you WANT
You manually start/stop things          K8s continuously makes reality match your description
One machine                             One or many machines (a "cluster")
docker-compose.yml = the whole world    Multiple YAML files = each piece of your system
```

The key mindset shift: **Docker Compose is imperative** (you say "start this"), **Kubernetes is declarative** (you say "I want this to exist, you figure out how").

---

## Part 2 — Core Kubernetes Concepts (The Building Blocks)

Read this section carefully. Every concept below will map directly to something in your GRC architecture.

### 2.1 Cluster, Nodes, and the Control Plane

```
┌─────────────────────────────────────────────────────────┐
│                    KUBERNETES CLUSTER                     │
│                                                           │
│  ┌──────────────────┐    ┌──────────────┐ ┌──────────────┐│
│  │   Control Plane   │    │   Node 1     │ │   Node 2     ││
│  │ (the "brain")     │    │ (a server)   │ │ (a server)   ││
│  │                    │    │              │ │              ││
│  │ • API Server       │    │ • Pods run   │ │ • Pods run   ││
│  │ • Scheduler        │    │   here       │ │   here       ││
│  │ • etcd (database)  │    │              │ │              ││
│  │ • Controller Mgr   │    │              │ │              ││
│  └──────────────────┘    └──────────────┘ └──────────────┘│
└─────────────────────────────────────────────────────────┘
```

- **Cluster** = the entire Kubernetes installation (control plane + worker nodes)
- **Node** = a single machine (physical server or VM) that runs your containers
- **Control Plane** = the brain that watches your desired state and makes it happen
- **etcd** = a key-value database where K8s stores all cluster state

**You interact with the Control Plane via `kubectl`** (the K8s CLI). You never SSH into nodes or manually start containers.

### 2.2 Pod — The Smallest Deployable Unit

A **Pod** is a wrapper around one or more containers that share:
- The same network (they can talk via `localhost`)
- The same storage volumes
- The same lifecycle (they start and stop together)

```
┌─────────── Pod ───────────┐
│                            │
│  ┌──────────┐              │
│  │ Container│  (your app)  │
│  │ grc-api  │              │
│  └──────────┘              │
│                            │
│  IP: 10.244.1.5            │
└────────────────────────────┘
```

**Docker Compose equivalent:** One entry under `services:` ≈ one Pod.  
**Key difference:** You almost never create Pods directly. You create a Deployment (or StatefulSet) which creates Pods for you.

### 2.3 Deployment — "Keep N copies of this Pod running"

A **Deployment** tells K8s:
> *"I want 2 replicas of my API Pod. If one crashes, create a new one. If I update the image, roll out the new version gradually."*

```yaml
# Simplified — NOT a real file yet
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grc-api
spec:
  replicas: 2              # Keep 2 Pods running at all times
  template:
    spec:
      containers:
        - name: api
          image: youruser/grc-api:v1.0.0
          ports:
            - containerPort: 8080
```

What a Deployment gives you:
- **Self-healing:** Pod dies → K8s starts a new one automatically
- **Rolling updates:** Change the image tag → K8s gradually replaces old Pods with new ones (zero downtime)
- **Scaling:** Change `replicas: 2` to `replicas: 5` → K8s creates 3 more Pods
- **Rollback:** Bad deploy? `kubectl rollout undo deployment/grc-api` instantly reverts

**Docker Compose equivalent:** `restart: unless-stopped` gives you self-healing, but nothing else. No scaling, no rolling updates, no rollback.

**Use Deployment for:** Your API service, Gateway service, Redis (ephemeral cache).

### 2.4 StatefulSet — For Databases and Stateful Workloads

A **StatefulSet** is like a Deployment but designed for things that need:
- **Stable network identity:** Each Pod gets a predictable name (`qdrant-0`, `qdrant-1`, not random IDs)
- **Stable persistent storage:** Each Pod gets its own persistent disk that survives Pod restarts
- **Ordered startup/shutdown:** Pods are created one-by-one in order

```
Deployment Pods:     grc-api-7b9d4f6c8-xk2mn    (random suffix)
                      grc-api-7b9d4f6c8-p3r7q    (random suffix)

StatefulSet Pods:    qdrant-0                     (always "qdrant-0")
                      qdrant-1                     (always "qdrant-1")
```

**Use StatefulSet for:** Qdrant (your vector database — it stores data on disk that must survive restarts).

### 2.5 Service — Stable Networking Between Pods

Pods get random IP addresses and can be replaced at any time. So how does your Gateway find your API?

A **Service** provides a **stable DNS name and IP** that routes traffic to the right Pods.

```
┌──────────────────────────────────────────────────────────────┐
│                                                               │
│  Gateway Pod                  Service "grc-api"               │
│  ┌─────────┐                  ┌─────────────┐                 │
│  │ gateway  │──── http://grc-api:8080 ────►│  Load Balancer│  │
│  └─────────┘                  │             │                 │
│                                └──────┬──────┘                │
│                                       │                       │
│                           ┌───────────┼───────────┐           │
│                           ▼                       ▼           │
│                    ┌──────────┐             ┌──────────┐      │
│                    │ API Pod 1│             │ API Pod 2│      │
│                    └──────────┘             └──────────┘      │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

**Types of Services:**

| Type | Who Can Access | Use Case |
|------|---------------|----------|
| **ClusterIP** (default) | Only other Pods inside the cluster | Internal services (Qdrant, Redis, API) |
| **NodePort** | Anyone who can reach a cluster node on a specific port (30000-32767) | Quick dev/testing access |
| **LoadBalancer** | The internet (via cloud provider's load balancer) | Production on cloud (AWS/Azure/GCP) |

**Docker Compose equivalent:** When you write `http://api:8080` in Compose, Docker's internal DNS resolves `api` to the container's IP. K8s Services do the same thing but with load balancing across multiple Pods.

### 2.6 Ingress — The Front Door

While a Service gets traffic *inside* the cluster, an **Ingress** gets traffic *from the outside world into* the cluster. Think of it as a reverse proxy (like NGINX) that sits at the edge.

```
Internet ──► Ingress (NGINX) ──► Gateway Service ──► Gateway Pods
                │
                ├── grc.example.com/    → gateway:8000
                ├── grc.example.com/api → gateway:8000 (proxied to API)
```

An Ingress needs an **Ingress Controller** installed in the cluster (a one-time setup). The most common is NGINX Ingress Controller.

**Docker Compose equivalent:** The `ports: "8000:8000"` mapping in your Gateway. But Ingress adds SSL/TLS termination, path-based routing, and host-based routing.

### 2.7 ConfigMap — Non-Secret Configuration

A **ConfigMap** stores configuration data as key-value pairs that get injected into Pods as environment variables or mounted as files.

```yaml
# This is what your .env file becomes in Kubernetes
apiVersion: v1
kind: ConfigMap
metadata:
  name: grc-api-config
data:
  GRC_QDRANT__URL: "http://qdrant:6333"
  GRC_QDRANT__COLLECTION_NAME: "grc_controls"
  GRC_CHUNKING__SIZE: "256"
  GRC_CHUNKING__OVERLAP: "50"
  # ... all non-secret env vars
```

**Docker Compose equivalent:** Your `.env` file and the `environment:` block in each service.

### 2.8 Secret — Sensitive Configuration

A **Secret** is like a ConfigMap but for sensitive data (API keys, passwords, tokens). Values are base64-encoded (not encrypted by default, but K8s restricts access to them).

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: grc-secrets
type: Opaque
data:
  GRC_GEMINI__API_KEY: QUl6YVN5RH...   # base64-encoded
```

**Docker Compose equivalent:** The `GRC_GEMINI__API_KEY` entry in your `.env` file.

**Important:** Never commit Secret YAML files with real values to Git. We'll handle this with Kustomize's `secretGenerator` which reads from a gitignored `.env` file.

### 2.9 PersistentVolumeClaim (PVC) — Disk Storage

A **PVC** requests a piece of persistent disk storage from the cluster. It survives Pod restarts and can be re-attached to a new Pod.

```yaml
# "Give me 10Gi of disk space for Qdrant's data"
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: qdrant-data
spec:
  accessModes: [ "ReadWriteOnce" ]
  resources:
    requests:
      storage: 10Gi
```

**Docker Compose equivalent:** Your `volumes: qdrant_data:/qdrant/storage` named volume.

### 2.10 Namespace — Virtual Clusters

A **Namespace** is a way to divide one physical cluster into multiple virtual clusters. Each namespace has its own set of Pods, Services, ConfigMaps, etc.

```
Cluster
├── grc-dev        ← dev environment
│   ├── qdrant pods
│   ├── redis pods
│   ├── api pods
│   └── gateway pods
├── grc-staging    ← staging environment
│   ├── qdrant pods
│   ├── ...
└── grc-prod       ← production
    ├── qdrant pods
    ├── ...
```

This is how we get multi-environment support without separate clusters.

### 2.11 Health Checks (Probes)

K8s has three types of probes — you already know health checks from your Docker Compose `healthcheck:` blocks:

| Probe | Purpose | What Happens on Failure |
|-------|---------|------------------------|
| **Readiness Probe** | "Is the Pod ready to receive traffic?" | K8s stops sending traffic to it (but doesn't restart it) |
| **Liveness Probe** | "Is the Pod still alive?" | K8s kills and restarts the Pod |
| **Startup Probe** | "Has the Pod finished starting up?" | K8s waits longer before checking readiness/liveness |

Your `healthcheck` entries in docker-compose.yml map directly to these:
- API: `curl http://localhost:8080/health` → readiness + liveness HTTP probe on `/health`
- Gateway: `curl http://localhost:8000/health` → readiness + liveness HTTP probe on `/health`
- Redis: `redis-cli ping` → liveness exec probe
- Qdrant: TCP check on port 6333 → readiness TCP probe

### 2.12 Resource Requests and Limits

In K8s, you specify how much CPU and memory each container needs:

```yaml
resources:
  requests:          # "I need at least this much" (used for scheduling)
    memory: "256Mi"
    cpu: "250m"      # 250 millicores = 0.25 CPU cores
  limits:            # "Never exceed this" (container gets killed if it does)
    memory: "1Gi"
    cpu: "1000m"     # 1 full CPU core
```

- **Requests:** The scheduler uses this to decide which Node has enough room. Think "minimum guarantee."
- **Limits:** Hard ceiling. If your container tries to use more memory than its limit, K8s kills it (OOMKilled).

**Docker Compose equivalent:** None — Compose doesn't restrict resources by default.

### 2.13 Init Containers — Startup Dependencies

In Docker Compose, `depends_on: { qdrant: condition: service_healthy }` means "don't start the API until Qdrant is healthy."

K8s doesn't have `depends_on`. Instead, you use **init containers** — small containers that run before the main container and must complete successfully:

```yaml
initContainers:
  - name: wait-for-qdrant
    image: busybox:1.37
    command: ['sh', '-c', 'until nc -z qdrant 6333; do echo waiting for qdrant; sleep 2; done']
  - name: wait-for-redis
    image: busybox:1.37
    command: ['sh', '-c', 'until nc -z redis 6379; do echo waiting for redis; sleep 2; done']
```

This is exactly how we'll replicate your `depends_on` behavior.

---

## Part 3 — Your Current Architecture (Docker Compose)

Let's look at what you have today. Here's a visual representation of your `docker-compose.yml`:

```
                          ┌─────────────────────────┐
                          │     YOUR MACHINE         │
                          │                           │
    User ─── :8000 ────►  │  ┌─────────────────────┐ │
                          │  │    Gateway            │ │
                          │  │    (port 8000)        │ │
                          │  │    Reverse proxy +    │ │
                          │  │    health aggregator  │ │
                          │  └────────┬──────────────┘ │
                          │           │                 │
                          │           │ http://api:8080 │
                          │           ▼                 │
    (also :8080) ──────►  │  ┌─────────────────────┐  │
                          │  │    API                │  │
                          │  │    (port 8080)        │  │
                          │  │    RAG + Ingestion    │  │
                          │  └──┬─────────────┬─────┘  │
                          │     │             │         │
                          │     │             │         │
                          │     ▼             ▼         │
                          │  ┌────────┐  ┌─────────┐   │
                          │  │ Qdrant │  │  Redis   │   │
                          │  │ :6333  │  │  :6379   │   │
                          │  │ Vector │  │  Cache   │   │
                          │  │ DB     │  │          │   │
                          │  └────────┘  └─────────┘   │
                          │                             │
                          └─────────────────────────────┘
```

### Service-by-Service Breakdown

#### 1. Qdrant (Vector Database)
| Property | Value |
|----------|-------|
| Image | `qdrant/qdrant:v1.12.4` |
| Ports | 6333 (REST API), 6334 (gRPC) — internal only |
| Storage | Named volume `qdrant_data` → `/qdrant/storage` |
| Health check | TCP connect on port 6333 |
| Role | Stores embedded vectors of compliance control chunks for similarity search |

#### 2. Redis (Query Cache)
| Property | Value |
|----------|-------|
| Image | `redis:7-alpine` |
| Port | 6379 — internal only |
| Storage | None (ephemeral) |
| Config | `maxmemory 1024mb`, eviction policy `allkeys-lfu` |
| Health check | `redis-cli ping` |
| Role | Caches query responses to avoid re-running expensive RAG pipeline for repeated queries |

#### 3. API (Core RAG Service)
| Property | Value |
|----------|-------|
| Image | Built from `src/api/Dockerfile` (python:3.12-slim + uvicorn) |
| Port | 8080 (exposed to host) |
| Config | All `GRC_*` env vars from `.env` file |
| Dependencies | Qdrant (healthy) + Redis (healthy) |
| Health check | `GET /health` on port 8080 |
| Role | PDF ingestion, text extraction, chunking, embedding, Qdrant upsert, RAG queries, compliance mapping |

#### 4. Gateway (API Gateway)
| Property | Value |
|----------|-------|
| Image | Built from `src/gateway/Dockerfile` (python:3.12-slim + uvicorn) |
| Port | 8000 (exposed to host) — **the external entrypoint** |
| Config | `API_URL`, `QDRANT_URL`, `REDIS_URL`, `LOG_LEVEL` (set inline) |
| Dependencies | API (healthy) |
| Health check | `GET /health` on port 8000 |
| Role | Reverse-proxies all `/api/v1/*` requests to the API. Aggregates health status of all services |

### Data Flow

```
1. User uploads PDF → Gateway :8000 → /api/v1/ingestion/ingest → API :8080
   API extracts text (PyMuPDF) → chunks (LangChain) → embeds (Gemini) → stores in Qdrant

2. User queries a finding → Gateway :8000 → /api/v1/query → API :8080
   API embeds query (Gemini) → searches Qdrant → critique (Gemini) → cache result in Redis → return

3. Health check → Gateway :8000 → /health
   Gateway checks API + Qdrant + Redis health → aggregated response
```

---

## Part 4 — Mapping Docker Compose → Kubernetes

This is the key section. Here's a 1:1 comparison of every Docker Compose concept and its K8s equivalent **specific to your project**:

### 4.1 The Big Picture Mapping

```
docker-compose.yml            →    Multiple YAML files in k8s/ directory
                                    (one per resource, organized by service)

services:                           
  qdrant:                     →    StatefulSet + Service + PVC
  redis:                      →    Deployment + Service
  api:                        →    Deployment + Service + ConfigMap + Secret
  gateway:                    →    Deployment + Service + ConfigMap + Ingress

volumes:
  qdrant_data:                →    PersistentVolumeClaim (inside StatefulSet)

.env file                     →    ConfigMap (non-secrets) + Secret (API key)

ports: "8000:8000"            →    Ingress resource (routes external traffic)

depends_on:                   →    Init containers (wait for dependencies)

healthcheck:                  →    readinessProbe + livenessProbe

restart: unless-stopped       →    Built-in (K8s always restarts failed Pods)

environment:                  →    ConfigMap referenced via envFrom

env_file: .env                →    ConfigMap + Secret referenced via envFrom
```

### 4.2 Detailed Comparison Table

| Docker Compose | K8s Resource | Why |
|---|---|---|
| `image: qdrant/qdrant:v1.12.4` | `image:` field in StatefulSet Pod spec | Same — just a container image |
| `build: { dockerfile: src/api/Dockerfile }` | You build + push image to Docker Hub **first**, then reference it by name | K8s doesn't build images — it only pulls pre-built images |
| `expose: ["6333"]` | `containerPort: 6333` in Pod spec + ClusterIP Service | Internal-only port |
| `ports: ["8080:8080"]` | ClusterIP Service (for internal), or Ingress (for external) | K8s separates internal connectivity from external exposure |
| `volumes: [qdrant_data:/qdrant/storage]` | `volumeClaimTemplates` in StatefulSet | Persistent disk that survives Pod restarts |
| `command: ["redis-server", "--maxmemory", "1024mb"]` | `args:` field in container spec | Same — override the container's CMD |
| `env_file: .env` | ConfigMap + Secret with `envFrom:` | Splits config into non-secret (ConfigMap) and secret (Secret) |
| `environment: { API_URL: http://api:8080 }` | ConfigMap with `envFrom:` | Same key-value pairs, different storage mechanism |
| `depends_on: { qdrant: condition: service_healthy }` | `initContainers:` with connection check | K8s doesn't have native `depends_on` |
| `healthcheck: { test: curl /health }` | `readinessProbe:` + `livenessProbe:` | K8s probes are more granular (readiness vs liveness) |
| `restart: unless-stopped` | Always on — K8s default behavior | K8s always restarts crashed Pods |
| *N/A in Compose* | `resources: { requests, limits }` | K8s lets you set CPU/memory guarantees and caps |
| *N/A in Compose* | `replicas: 2` | K8s can run multiple copies and load-balance |

### 4.3 Service Name Resolution (DNS)

In Docker Compose, service names are DNS names automatically:
```
http://api:8080      ← "api" is the service name in docker-compose.yml
http://qdrant:6333   ← "qdrant" is the service name
redis://redis:6379   ← "redis" is the service name
```

In Kubernetes, **the K8s Service name** becomes the DNS name:
```
http://grc-api:8080      ← "grc-api" is the Service resource name
http://qdrant:6333       ← "qdrant" is the Service resource name
redis://redis:6379       ← "redis" is the Service resource name
```

So your ConfigMap values will reference these K8s Service names. We'll keep `qdrant` and `redis` as service names (same as Compose) and use `grc-api` for the API (to be more descriptive in K8s).

**Impact on your config:** Only `API_URL` changes from `http://api:8080` to `http://grc-api:8080`. Everything else stays the same.

### 4.4 Visual: Your Architecture in Kubernetes

```
┌─────────────────────── Kubernetes Cluster ───────────────────────────┐
│                                                                      │
│  ┌──────────── Namespace: grc-dev ──────────────────────────────┐   │
│  │                                                                │   │
│  │         NGINX Ingress Controller                               │   │
│  │  User ──────► ┌──────────┐                                     │   │
│  │               │ Ingress  │ grc.local → gateway:8000            │   │
│  │               └────┬─────┘                                     │   │
│  │                    │                                            │   │
│  │                    ▼                                            │   │
│  │  ┌──────────────────────────────────┐                          │   │
│  │  │ Service: grc-gateway (ClusterIP) │                          │   │
│  │  └────┬────────────────────────┬────┘                          │   │
│  │       ▼                        ▼                               │   │
│  │  ┌──────────┐            ┌──────────┐                          │   │
│  │  │ Gateway  │            │ Gateway  │   (2 replicas in prod)   │   │
│  │  │ Pod 1    │            │ Pod 2    │                          │   │
│  │  └────┬─────┘            └────┬─────┘                          │   │
│  │       │   http://grc-api:8080 │                                │   │
│  │       └──────────┬────────────┘                                │   │
│  │                  ▼                                              │   │
│  │  ┌──────────────────────────────────┐                          │   │
│  │  │ Service: grc-api (ClusterIP)     │                          │   │
│  │  └────┬────────────────────────┬────┘                          │   │
│  │       ▼                        ▼                               │   │
│  │  ┌──────────┐            ┌──────────┐                          │   │
│  │  │ API      │            │ API      │   (2 replicas in prod)   │   │
│  │  │ Pod 1    │            │ Pod 2    │                          │   │
│  │  └──┬────┬──┘            └──┬────┬──┘                          │   │
│  │     │    │                  │    │                              │   │
│  │     │    └──────┬───────────┘    │                              │   │
│  │     │           │                │                              │   │
│  │     ▼           ▼                ▼                              │   │
│  │  ┌────────────────┐  ┌──────────────────┐                      │   │
│  │  │ Service: qdrant│  │ Service: redis   │                      │   │
│  │  │ (ClusterIP)    │  │ (ClusterIP)      │                      │   │
│  │  └───────┬────────┘  └────────┬─────────┘                      │   │
│  │          ▼                    ▼                                 │   │
│  │  ┌──────────────┐    ┌──────────────┐                          │   │
│  │  │ Qdrant Pod   │    │ Redis Pod    │                          │   │
│  │  │ (StatefulSet)│    │ (Deployment) │                          │   │
│  │  │     + PVC    │    │ (ephemeral)  │                          │   │
│  │  └──────────────┘    └──────────────┘                          │   │
│  │                                                                │   │
│  │  Config:                                                       │   │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐      │   │
│  │  │ ConfigMap:    │  │ ConfigMap:    │  │ Secret:       │      │   │
│  │  │ grc-api-config│  │ grc-gw-config│  │ grc-secrets   │      │   │
│  │  │ (GRC_* vars)  │  │ (API_URL etc)│  │ (API key)     │      │   │
│  │  └───────────────┘  └───────────────┘  └───────────────┘      │   │
│  └────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Part 5 — Files You Need to Understand in Your Codebase

Before we create any K8s manifests, here are the existing files that inform the migration, **and why each matters:**

### 5.1 `docker-compose.yml` — The Source of Truth

**Why it matters:** This is the single file we're translating into Kubernetes. Every service definition, port, volume, health check, and environment variable in here will become a K8s resource.

**What to note:**
- 4 services: `qdrant`, `redis`, `api`, `gateway`
- `api` uses `env_file: .env` → all config comes from `.env`
- `gateway` uses inline `environment:` → 4 explicit env vars
- `qdrant` has a named volume → needs a PVC in K8s
- `redis` has no volume → ephemeral (just a cache)
- Dependency chain: `qdrant + redis → api → gateway`

### 5.2 `src/api/Dockerfile` — How the API Image Is Built

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ /app/src/
EXPOSE 8080
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Why it matters:** 
- This Dockerfile stays **exactly as-is**. K8s uses the same Docker images.
- You build this image with `docker build`, push to Docker Hub, then K8s pulls it.
- Port 8080 is the container port you'll reference in your K8s Deployment.

### 5.3 `src/gateway/Dockerfile` — How the Gateway Image Is Built

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY src/gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ /app/src/
EXPOSE 8000
CMD ["uvicorn", "src.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Why it matters:** Same as API. Port 8000. No changes needed to the Dockerfile.

### 5.4 `.env` — All API Configuration

This file contains every `GRC_*` environment variable that the API service needs. In K8s, we split it into:

| Env Var | Goes Into | Why |
|---------|-----------|-----|
| `GRC_GEMINI__API_KEY=AIza...` | **Secret** (`grc-secrets`) | It's a credential — must be protected |
| `GRC_QDRANT__URL=http://qdrant:6333` | **ConfigMap** (`grc-api-config`) | Non-sensitive service endpoint |
| `GRC_QDRANT__COLLECTION_NAME=grc_controls` | **ConfigMap** | Non-sensitive setting |
| `GRC_REDIS__URL=redis://redis:6379` | **ConfigMap** | Non-sensitive service endpoint |
| All other `GRC_*` vars | **ConfigMap** | Non-sensitive configuration |

**Key insight:** The URL values (`GRC_QDRANT__URL`, `GRC_REDIS__URL`) will work as-is in K8s because we'll name the K8s Services `qdrant` and `redis` — matching the Docker Compose service names.

### 5.5 `src/config/settings.py` — How the API Reads Configuration

```python
class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GRC_",
        env_nested_delimiter="__",
        env_file=".env",
    )
```

**Why it matters:**
- Uses Pydantic Settings with `GRC_` prefix and `__` nested delimiter
- Reads from environment variables OR `.env` file
- In K8s, environment variables are injected via ConfigMap and Secret (using `envFrom:`)
- **No code changes needed** — Pydantic reads env vars the same way regardless of where they come from

### 5.6 `src/gateway/config.py` — How the Gateway Reads Configuration

```python
class Settings(BaseSettings):
    api_url: str = "http://localhost:8080"
    qdrant_url: str = "http://localhost:6333"
    redis_url: str = "redis://localhost:6379"
    log_level: str = "info"
```

**Why it matters:**
- Reads `API_URL`, `QDRANT_URL`, `REDIS_URL`, `LOG_LEVEL` from env vars
- These are set inline in `docker-compose.yml` → will become a ConfigMap in K8s
- Has sensible defaults, but we override them explicitly

### 5.7 `src/api/main.py` — Health Check Endpoint

```python
@app.get("/health")
def health():
    return {"status": "healthy"}
```

**Why it matters:** This is the endpoint K8s probes will hit for readiness and liveness checks. Path: `/health`, port: `8080`.

### 5.8 `src/gateway/routes.py` — Gateway Health + Proxy

```python
@router.get("/health")
async def health_check():
    # checks API + Qdrant + Redis health
    
@router.api_route("/api/v1/{path:path}", methods=["GET", "POST", ...])
async def proxy_api(path, request):
    target = f"{settings.api_url}/api/v1/{path}"
    # forwards to API backend
```

**Why it matters:**
- Gateway's `/health` checks all 3 downstream services — this is the endpoint K8s will probe
- All `/api/v1/*` requests are reverse-proxied to the API using `settings.api_url` (which comes from `API_URL` env var)
- In K8s, `API_URL` will be `http://grc-api:8080` (the K8s Service name)

### 5.9 `requirements.txt` (root) — API Dependencies

**Why it matters:** Used inside `src/api/Dockerfile` to install Python packages. No changes needed for K8s.

### 5.10 `src/gateway/requirements.txt` — Gateway Dependencies

**Why it matters:** Used inside `src/gateway/Dockerfile`. No changes needed for K8s.

### Summary: What Changes vs. What Stays the Same

| File | Changes for K8s? | Details |
|------|:-:|---|
| `docker-compose.yml` | **Not used** | Replaced by K8s manifests (but kept for local dev) |
| `src/api/Dockerfile` | **No** | Same image, just pushed to Docker Hub |
| `src/gateway/Dockerfile` | **No** | Same image, just pushed to Docker Hub |
| `.env` | **Replaced** | Split into ConfigMap + Secret YAML files |
| `src/config/settings.py` | **No** | Pydantic reads env vars the same way |
| `src/gateway/config.py` | **No** | Same env vars, just different source |
| `src/api/main.py` | **No** | Health endpoint used as-is by K8s probes |
| `src/gateway/routes.py` | **No** | Proxy works the same via K8s DNS |
| `requirements.txt` | **No** | Installed during Docker build, unchanged |
| All Python source code | **No** | Zero code changes needed |

**Key takeaway: Migrating to Kubernetes requires zero changes to your application code or Dockerfiles. You only add new YAML files that describe how to run your existing containers.**

---

## Part 6 — The Kubernetes File Structure We'll Create

Here's exactly what files we'll create (using Kustomize for multi-environment support):

```
k8s/
├── base/                              ← Shared across all environments
│   ├── kustomization.yaml             ← "Index file" — lists all resources
│   ├── namespace.yaml                 ← Creates the namespace
│   │
│   ├── qdrant/
│   │   ├── statefulset.yaml           ← Qdrant container + persistent disk
│   │   └── service.yaml               ← DNS name "qdrant" pointing to the Pod
│   │
│   ├── redis/
│   │   ├── deployment.yaml            ← Redis container (ephemeral cache)
│   │   └── service.yaml               ← DNS name "redis" pointing to the Pod
│   │
│   ├── api/
│   │   ├── deployment.yaml            ← API container with probes + init containers
│   │   ├── service.yaml               ← DNS name "grc-api" pointing to API Pods
│   │   └── configmap.yaml             ← All non-secret GRC_* env vars
│   │
│   ├── gateway/
│   │   ├── deployment.yaml            ← Gateway container with probes + init container
│   │   ├── service.yaml               ← DNS name "grc-gateway" pointing to Gateway Pods
│   │   ├── configmap.yaml             ← API_URL, QDRANT_URL, REDIS_URL, LOG_LEVEL
│   │   └── ingress.yaml              ← External traffic routing (NGINX)
│   │
│   └── secrets/
│       └── grc-secrets.yaml           ← Template for Gemini API key (placeholder)
│
├── overlays/                          ← Per-environment customizations
│   ├── dev/
│   │   ├── kustomization.yaml         ← Patches for dev (1 replica, small resources)
│   │   ├── secrets.env                ← 🔒 GITIGNORED — real API key for dev
│   │   └── patches/
│   │       └── resource-limits.yaml   ← Smaller CPU/memory for dev
│   │
│   ├── staging/
│   │   ├── kustomization.yaml
│   │   ├── secrets.env                ← 🔒 GITIGNORED
│   │   └── patches/
│   │       └── resource-limits.yaml
│   │
│   └── prod/
│       ├── kustomization.yaml         ← 2+ replicas, larger resources, TLS
│       ├── secrets.env                ← 🔒 GITIGNORED
│       └── patches/
│           ├── replicas.yaml          ← Scale API + Gateway to 2 replicas
│           ├── resource-limits.yaml   ← Larger CPU/memory for prod
│           └── qdrant-storage.yaml    ← 20Gi disk instead of 10Gi
```

### What Is Kustomize?

**Kustomize** is a tool built into `kubectl` that lets you customize K8s YAML files without templating. Instead of one giant YAML file with `${VARIABLE}` placeholders, you have:

1. **Base** — the default manifests that work for any environment
2. **Overlays** — small patches that change specific values per environment

```
Base:     replicas: 1,  memory: 256Mi,  PVC: 10Gi,  no TLS
   └── Dev overlay:     (uses base as-is)
   └── Staging overlay: namespace: grc-staging
   └── Prod overlay:    replicas: 2,  memory: 1Gi,  PVC: 20Gi,  TLS enabled
```

**Why Kustomize over Helm?**
- Kustomize is simpler — no template syntax to learn, just YAML patches
- Built into `kubectl` — no extra tools to install
- Helm is more powerful but overkill for 4 services
- Perfect for your "I need dev/staging/prod" use case

---

## Part 7 — Multi-Environment Strategy (dev/staging/prod)

Each environment is a **Kustomize overlay** that patches the base:

| Setting | Dev | Staging | Prod |
|---------|:---:|:-------:|:----:|
| Namespace | `grc-dev` | `grc-staging` | `grc-prod` |
| API replicas | 1 | 1 | 2+ |
| Gateway replicas | 1 | 1 | 2+ |
| API memory limit | 512Mi | 1Gi | 1Gi |
| Qdrant PVC size | 5Gi | 10Gi | 20Gi |
| Ingress TLS | No | No | Yes |
| Ingress host | `grc.local` | `grc-staging.example.com` | `grc.example.com` |
| Image tag | `latest` or `dev` | `v1.0.0-rc1` | `v1.0.0` |
| Gemini API key | Dev key | Staging key | Prod key |

### Deploy Commands

```bash
# Deploy to dev
kubectl apply -k k8s/overlays/dev

# Deploy to staging
kubectl apply -k k8s/overlays/staging

# Deploy to prod
kubectl apply -k k8s/overlays/prod
```

That's it. One command per environment. Kustomize merges bases + overlays automatically.

---

## Part 8 — What Changes and What Stays the Same

### What You Keep (No Changes)
- ✅ All Python source code (`src/`)
- ✅ Both Dockerfiles (`src/api/Dockerfile`, `src/gateway/Dockerfile`)
- ✅ Both `requirements.txt` files
- ✅ `docker-compose.yml` (kept for local development)
- ✅ Configuration reading logic (Pydantic settings)

### What You Add (New Files)
- 📁 `k8s/` directory with ~20 YAML manifest files
- 📁 Per-environment `secrets.env` files (gitignored)
- 📄 `.dockerignore` (if not present, to speed up Docker builds)

### New Workflow (Build → Push → Deploy)

**Today (Docker Compose):**
```bash
docker compose up --build     # builds + runs everything locally
```

**With Kubernetes:**
```bash
# Step 1: Build images (same Dockerfiles, just tag them)
docker build -t youruser/grc-api:v1.0.0 -f src/api/Dockerfile .
docker build -t youruser/grc-gateway:v1.0.0 -f src/gateway/Dockerfile .

# Step 2: Push to Docker Hub (one-time login: docker login)
docker push youruser/grc-api:v1.0.0
docker push youruser/grc-gateway:v1.0.0

# Step 3: Deploy to Kubernetes
kubectl apply -k k8s/overlays/dev

# Step 4: Check status
kubectl get pods -n grc-dev
kubectl logs deployment/grc-api -n grc-dev
```

### What You Gain

| Capability | Docker Compose | Kubernetes |
|-----------|:-:|:-:|
| Run on one machine | ✅ | ✅ |
| Run across multiple machines | ❌ | ✅ |
| Auto-restart crashed containers | ✅ | ✅ |
| Scale to N replicas | ❌ | ✅ |
| Zero-downtime deployments | ❌ | ✅ (rolling updates) |
| Instant rollback | ❌ | ✅ (`kubectl rollout undo`) |
| CPU/memory limits per service | ❌ | ✅ |
| Load balancing across replicas | ❌ | ✅ (built into Services) |
| Multi-environment (dev/staging/prod) | Hacky | ✅ (native with Kustomize) |
| SSL/TLS termination | Manual | ✅ (Ingress + cert-manager) |
| Self-healing health checks | Basic | ✅ (readiness + liveness + startup probes) |
| Config/secret management | `.env` files | ✅ (ConfigMap + Secret with RBAC) |

---

## Part 9 — Prerequisites and Tools You Need

Before we create any K8s files, make sure you have:

### 9.1 On Your Machine

| Tool | What It Does | Install |
|------|-------------|---------|
| **Docker** | Build container images | Already installed ✅ |
| **kubectl** | CLI to talk to Kubernetes clusters | [Install guide](https://kubernetes.io/docs/tasks/tools/) |
| **Docker Hub account** | Store your built images | [hub.docker.com](https://hub.docker.com) |

### 9.2 A Kubernetes Cluster (On-Prem)

Since you're going self-managed / on-prem, you need a K8s cluster running on your servers. Common options:

| Tool | Best For | Complexity |
|------|----------|:----------:|
| **kubeadm** | Production on-prem clusters | Medium |
| **k3s** (by Rancher) | Lightweight, single-node or small clusters | Low |
| **microk8s** (by Canonical) | Single-node, Ubuntu-based | Low |
| **minikube** | Local development / learning | Very Low |
| **kind** (K8s in Docker) | CI/CD testing, runs K8s inside Docker | Very Low |

**Recommendation for learning:** Start with **minikube** on your laptop to test the manifests before deploying to your on-prem cluster.

```bash
# Install minikube (Windows)
winget install Kubernetes.minikube

# Start a local cluster
minikube start

# Verify
kubectl get nodes
# NAME       STATUS   ROLES           AGE   VERSION
# minikube   Ready    control-plane   1m    v1.28.0
```

### 9.3 NGINX Ingress Controller

Required for the Ingress resource to work. One-time installation:

```bash
# For minikube:
minikube addons enable ingress

# For on-prem (bare-metal):
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.0/deploy/static/provider/baremetal/deploy.yaml
```

---

## Part 10 — Glossary

Quick reference for every K8s term used in this document:

| Term | One-Line Definition |
|------|-------------------|
| **Cluster** | A set of machines (nodes) running Kubernetes |
| **Node** | A single machine in the cluster that runs Pods |
| **Control Plane** | The "brain" — API server, scheduler, etcd, controllers |
| **Pod** | The smallest unit — a wrapper around one or more containers |
| **Deployment** | Manages Pods — ensures N replicas are always running |
| **StatefulSet** | Like Deployment but for stateful workloads (stable names + storage) |
| **Service** | Stable DNS name + IP that routes traffic to Pods |
| **ClusterIP** | Service type — accessible only inside the cluster |
| **NodePort** | Service type — accessible on every node's IP + a high port |
| **LoadBalancer** | Service type — provisions a cloud load balancer |
| **Ingress** | Routes external HTTP/HTTPS traffic into the cluster |
| **Ingress Controller** | The actual reverse proxy (NGINX) that implements Ingress rules |
| **ConfigMap** | Stores non-secret configuration as key-value pairs |
| **Secret** | Stores sensitive data (base64-encoded, access-controlled) |
| **PersistentVolumeClaim (PVC)** | A request for persistent disk storage |
| **Namespace** | Virtual partition of a cluster (for multi-environment isolation) |
| **Probe** | Health check — readiness, liveness, or startup |
| **Init Container** | Container that runs before the main container (for dependency checks) |
| **Kustomize** | Built-in tool for customizing YAML manifests (base + overlays) |
| **kubectl** | CLI tool to interact with a Kubernetes cluster |
| **Replica** | One copy of a Pod — `replicas: 3` means 3 identical Pods |
| **Rolling Update** | Gradually replaces old Pods with new ones (zero downtime) |
| **Rollback** | Revert a Deployment to a previous version |
| **envFrom** | Inject all keys from a ConfigMap/Secret as environment variables |

---

## Next Steps

Once you've read and understood this document, let me know and we can:

1. **Create all the K8s manifest files** (the `k8s/` directory with base + overlays)
2. **Walk through each file** explaining every line
3. **Test locally** with minikube
4. **Deploy to your on-prem cluster**

Take your time — no rush. Understanding the concepts first will make the implementation trivial.
