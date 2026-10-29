"""Download fixed public ONNX artifacts; no inference or vectors are generated here."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL = "Xenova/all-MiniLM-L6-v2"
REVISION = "751bff37182d3f1213fa05d7196b954e230abad9"
FILES = {
    "onnx/model.onnx": (90387606, "759c3cd2b7fe7e93933ad23c4c9181b7396442a2ed746ec7c1d46192c469c46e"),
    "onnx/model_fp16.onnx": (45297825, "2cdb5e58291813b6d6e248ed69010100246821a367fa17b1b81ae9483744533d"),
    "onnx/model_quantized.onnx": (22972370, "afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1"),
    "config.json": (650, "72147e4ff4426ebedbfa2146c4a0999def51a313"),
    "tokenizer.json": (711661, "c17ed520ed8438736732a54957a69306b8822215"),
    "tokenizer_config.json": (366, "37fca74771bc76a8e01178ce3a6055a0995f8093"),
    "special_tokens_map.json": (125, "a8b3208c2884c4efb86e49300fdd3dc877220cdf"),
    "vocab.txt": (231508, "fb140275c155a9c7c5a3b3e0e77a9e839594a938"),
}


def valid(path, size, digest):
    if not path.exists() or path.stat().st_size != size:
        return False
    if len(digest) == 64:
        with path.open("rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest() == digest
    return hashlib.sha1(b"blob " + str(size).encode() + b"\0" + path.read_bytes()).hexdigest() == digest


def download(item):
    name, (size, digest) = item
    target = ROOT / "public/models" / MODEL / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not valid(target, size, digest):
        for attempt in range(5):
            try:
                with urllib.request.urlopen(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", timeout=90) as r, target.with_suffix(".part").open("wb") as f:
                    while chunk := r.read(1024 * 1024):
                        f.write(chunk)
                target.with_suffix(".part").replace(target)
                if not valid(target, size, digest):
                    raise ValueError("Hash mismatch: " + name)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2)
    print("VERIFIED", name, size, flush=True)
    return {"file": name, "bytes": size, "hash": digest}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(download, FILES.items()))
    (ROOT / "evidence").mkdir(exist_ok=True)
    common = sum(size for name, (size, _) in FILES.items() if not name.startswith("onnx/"))
    sizes = {dtype: common + FILES[file][0] for dtype,file in
             [("fp32","onnx/model.onnx"),("fp16","onnx/model_fp16.onnx"),("q8","onnx/model_quantized.onnx")]}
    (ROOT / "evidence/model-files.json").write_text(json.dumps({"model":MODEL,"revision":REVISION,
        "files":records,"totalBytesIncludingTokenizer":sizes,
        "compressionRatio":{k:sizes['fp32']/v for k,v in sizes.items()}},indent=2)+"\n",encoding="utf-8")
