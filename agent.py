# ================= CONFIG =================
MONGO_URI = "mongodb+srv://linuxtut2024_db_user:vz1l1VLPdwKbPB0L@demo.gcwmlt4.mongodb.net/?appName=demo"
VOYAGE_API_KEY = "al-Ly5ASI7tQFTJMloAMsC5JrONqihCPezUw6hfw2XMGfn"
SIM_THRESHOLD = 0.70
# ==========================================

import re
import random
import numpy as np
from voyageai import Client
from pymongo import MongoClient
from kubernetes import client, config

voyage = Client(api_key=VOYAGE_API_KEY)

# ---------------- K8S ----------------
try:
    config.load_kube_config()
except:
    config.load_incluster_config()

core = client.CoreV1Api()
apps = client.AppsV1Api()

# ---------------- MONGO ----------------
mongo = MongoClient(MONGO_URI)
db = mongo["k8s_agent"]
col = db["intents"]

# ---------------- MEMORY ----------------
INTENTS = []
INTENT_EMB = []
LAST_PODS = []

# ---------------- COSINE ----------------
def cosine(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

# ---------------- GENERATE MANY PHRASES ----------------
def generate_variations(base_words):
    verbs = ["get", "show", "list", "fetch", "display"]
    suffix = ["", "now", "please", "quickly"]

    phrases = []
    for _ in range(200):  # generates many
        v = random.choice(verbs)
        w = random.choice(base_words)
        s = random.choice(suffix)
        phrases.append(f"{v} {w} {s}".strip())

    return list(set(phrases))

# ---------------- INIT DB ----------------
def init_mongo():
    if col.count_documents({}) > 0:
        print("✅ Intents already exist")
        return

    print("⚡ Generating ~1000 intents...")

    dataset = {
        "get_pods": ["pods", "containers", "running containers", "workloads"],
        "get_deployments": ["deployments", "apps", "deploy"],
        "get_services": ["services", "svc"],
        "logs": ["logs", "container logs", "pod logs"],
        "delete_pod": ["delete pod", "remove pod", "kill pod"],
    }

    docs = []

    for intent, words in dataset.items():
        phrases = generate_variations(words)

        emb = voyage.embed(phrases, model="voyage-3").embeddings

        for p, e in zip(phrases, emb):
            docs.append({
                "intent": intent,
                "text": p,
                "embedding": e
            })

    col.insert_many(docs)

    print(f"Inserted {len(docs)} intents")

# ---------------- LOAD INTO MEMORY ----------------
def load_memory():
    print("⚡ Loading intents into memory...")

    docs = list(col.find())

    for d in docs:
        INTENTS.append(d["intent"])
        INTENT_EMB.append(d["embedding"])

    print(f"Loaded {len(INTENTS)} intents")

# ---------------- FIND INTENT ----------------
def find_intent(text):
    q = voyage.embed([text], model="voyage-3").embeddings[0]

    best_i = -1
    best_score = -1

    for i, e in enumerate(INTENT_EMB):
        score = cosine(q, e)
        if score > best_score:
            best_score = score
            best_i = i

    if best_score < SIM_THRESHOLD:
        return None, best_score

    return INTENTS[best_i], best_score

# ---------------- FORMAT ----------------
def format_pods(pods):
    global LAST_PODS
    LAST_PODS = pods

    out = f"{'IDX':<4}{'NAMESPACE':<18}{'NAME':<45}{'STATUS'}\n"
    out += "-" * 90 + "\n"

    for i, p in enumerate(pods):
        out += f"{i+1:<4}{p.metadata.namespace:<18}{p.metadata.name:<45}{p.status.phase}\n"

    return out

# ---------------- HELPERS ----------------
def find_pod(name):
    name = name.lower()
    for p in LAST_PODS:
        if name in p.metadata.name.lower():
            return p
    return None

# ---------------- ACTIONS ----------------
def get_pods(ns=None):
    pods = core.list_namespaced_pod(ns).items if ns else core.list_pod_for_all_namespaces().items
    return format_pods(pods)

def get_deployments():
    deps = apps.list_deployment_for_all_namespaces().items
    return "\n".join([f"{d.metadata.namespace} {d.metadata.name}" for d in deps])

def get_services():
    svcs = core.list_service_for_all_namespaces().items
    return "\n".join([f"{s.metadata.namespace} {s.metadata.name}" for s in svcs])

def logs_idx(i):
    try:
        p = LAST_PODS[i]
        return core.read_namespaced_pod_log(p.metadata.name, p.metadata.namespace, tail_lines=100)
    except:
        return "❌ Invalid index"

def logs_name(name):
    p = find_pod(name)
    if not p:
        return "❌ Pod not found"
    return core.read_namespaced_pod_log(p.metadata.name, p.metadata.namespace, tail_lines=100)

def delete_idx(i):
    try:
        p = LAST_PODS[i]
        core.delete_namespaced_pod(p.metadata.name, p.metadata.namespace)
        return f"🗑️ Deleted {p.metadata.name}"
    except:
        return "❌ Invalid index"

def delete_name(name):
    p = find_pod(name)
    if not p:
        return "Pod not found"
    core.delete_namespaced_pod(p.metadata.name, p.metadata.namespace)
    return f"Deleted {p.metadata.name}"

# ---------------- AGENT ----------------
def agent(q):
    text = q.lower().strip()

    if text.isdigit():
        return logs_idx(int(text)-1)

    if "log" in text:
        parts = text.split()
        return logs_idx(int(parts[-1])-1) if parts[-1].isdigit() else logs_name(parts[-1])

    if "delete" in text:
        parts = text.split()
        return delete_idx(int(parts[-1])-1) if parts[-1].isdigit() else delete_name(parts[-1])

    ns_match = re.search(r"namespace\s+(\S+)", text)
    ns = ns_match.group(1) if ns_match else None

    intent, score = find_intent(text)
    print(f"DEBUG → {intent} ({score:.2f})")

    if intent == "get_pods":
        return get_pods(ns)
    if intent == "get_deployments":
        return get_deployments()
    if intent == "get_services":
        return get_services()

    # fallback
    if any(x in text for x in ["pod", "container", "running"]):
        return get_pods(ns)

    return "❌ Not understood"

# ---------------- MAIN ----------------
if __name__ == "__main__":
    print("Mongo + AI Kubernetes Agent")

    init_mongo()   # run once
    load_memory()  # always load to RAM

    while True:
        q = input(">> ")
        if q in ["exit", "quit"]:
            break
        print(agent(q))
