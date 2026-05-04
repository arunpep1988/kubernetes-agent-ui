import yaml
import tempfile
from langchain_ollama import OllamaLLM
from kubernetes import client, config
from kubernetes.utils import create_from_yaml

# ---------------- LOAD K8S CONFIG ----------------
try:
    config.load_kube_config()
except:
    config.load_incluster_config()

k8s_client = client.ApiClient()

# ---------------- LLM ----------------
llm = OllamaLLM(model="llama3")
chat_history = []

# ---------------- CLEAN YAML ----------------
def clean_yaml(text):
    text = text.strip()

    if "```" in text:
        parts = text.split("```")
        for p in parts:
            if "apiVersion:" in p:
                text = p.replace("yaml", "").strip()
                break

    if "apiVersion:" in text:
        text = text[text.index("apiVersion:"):]

    return text.strip()

# ---------------- PARSE YAML ----------------
def parse_yaml(text):
    try:
        docs = list(yaml.safe_load_all(text))
        if not docs:
            return None, "Empty YAML"
        return docs, None
    except Exception as e:
        return None, str(e)

# ---------------- SAFETY ----------------
def is_safe(text):
    blocked = [
        "privileged: true",
        "hostnetwork: true",
        "hostpid: true",
        "hostipc: true",
        "cluster-admin",
        "nodeName:",
    ]
    for b in blocked:
        if b.lower() in text.lower():
            return False, b
    return True, None

# ---------------- VALIDATION ----------------
def validate_yaml(docs):
    try:
        for doc in docs:
            client.ApiClient().sanitize_for_serialization(doc)
        return "Validation successful"
    except Exception as e:
        return str(e)

# ---------------- APPLY ----------------
def apply_yaml(path):
    try:
        created = create_from_yaml(k8s_client, path)
        return str(created)
    except Exception as e:
        return str(e)

# ---------------- LLM PROMPT ----------------
def generate(user_input, history, error=None):
    prompt = f"""
You are a Kubernetes expert AI.

Decide request type:

1. MANIFEST GENERATION
- If user asks to deploy/create something → output ONLY YAML

2. KUBERNETES KNOWLEDGE
- If conceptual → output TEXT

3. NON-KUBERNETES
- If unrelated → output EXACTLY:
INVALID REQUEST: ONLY KUBERNETES SUPPORTED

MANIFEST RULES:
- MUST include apiVersion, kind, metadata, spec
- Valid Kubernetes structure
- No markdown
- No ``` blocks

BEST PRACTICES:
- Use labels
- Use latest stable apiVersion
- Keep it minimal but correct

Conversation:
{history}

User:
{user_input}
"""

    if error:
        prompt += f"\nFix this error:\n{error}"

    return llm.invoke(prompt).strip()

# ---------------- AUTO REPAIR ----------------
def generate_with_repair(user_input, history):
    error = None

    for _ in range(4):
        raw = generate(user_input, history, error)

        print("\n=== RAW LLM OUTPUT ===\n", raw)

        # Non-K8s
        if "INVALID REQUEST" in raw:
            return None, raw

        # Not YAML → treat as explanation
        if not raw.strip().startswith("apiVersion:"):
            return None, raw

        cleaned = clean_yaml(raw)

        docs, err = parse_yaml(cleaned)
        if err:
            error = err
            continue

        final_yaml = yaml.dump_all(docs, sort_keys=False)

        safe, pattern = is_safe(final_yaml)
        if not safe:
            return None, f"Blocked unsafe config: {pattern}"

        validation = validate_yaml(docs)
        if "error" in validation.lower():
            error = validation
            continue

        # write temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".yml", mode="w") as f:
            f.write(final_yaml)
            path = f.name

        return final_yaml, path

    return None, f"Failed after retries:\n{error}"

# ---------------- AGENT ----------------
def agent(user_input):
    global chat_history

    chat_history.append(f"User: {user_input}")
    history = "\n".join(chat_history[-5:])

    yaml_text, result = generate_with_repair(user_input, history)

    # TEXT response
    if not yaml_text:
        return result

    path = result

    apply_result = apply_yaml(path)

    return (
        "===== FINAL YAML =====\n"
        + yaml_text
        + "\n\n===== APPLY RESULT =====\n"
        + apply_result
    )

# ---------------- CLI ----------------
if __name__ == "__main__":
    print("Kubernetes AI Agent Ready")
    while True:
        user_input = input(">> ")
        if user_input.lower() in ["exit", "quit"]:
            break
        print(agent(user_input))
