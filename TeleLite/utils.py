import json

def safe_print(obj):
    try:
        print(json.dumps(obj, indent=4, ensure_ascii=False))
    except Exception:
        print(obj)
