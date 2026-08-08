import sys, tempfile
from pathlib import Path

sdk_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "openai-sentinel-demo" / "20260219f9f6" / "sdk.js"
sdk = sdk_path.read_text(encoding="utf-8")
m = sdk.find("function Dt(){const t=")
print("Dt @", m)
i = m + len("function Dt(){const t=")
depth = 0
in_str = False
esc = False
while i < len(sdk):
    ch = sdk[i]
    if in_str:
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = False
    else:
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
            print(f"[ @ {i} depth->{depth} ctx={sdk[max(0,i-8):i+16]!r}")
        elif ch == "]":
            depth -= 1
            print(f"] @ {i} depth->{depth} ctx={sdk[max(0,i-8):i+20]!r}")
            if depth == 0:
                print("ARRAY END @", i)
                break
    i += 1
