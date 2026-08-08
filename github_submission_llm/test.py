import requests

base = "http://127.0.0.1:8000/claims/process"
filename = "09_llm_unstructured_test.txt"

with open(f"data/demo_cases/{filename}", "rb") as f:
    r1 = requests.post(base, files={"file": (filename, f, "text/plain")})
    print("Default:", r1.status_code, r1.json())

with open(f"data/demo_cases/{filename}", "rb") as f:
    r2 = requests.post(base + "?use_llm=true", files={"file": (filename, f, "text/plain")})
    print("LLM enabled:", r2.status_code, r2.json())