"""Fetch pinned official Docker files; configure an isolated local demo."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import secrets
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
PIN = "cd47a47c3b4acc4d4457b73777722602c33bc950"  # Dify 1.9.1


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "grudge-shop-setup"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def download(item):
    relative = Path(item["path"])
    dest = (RUNTIME / relative).resolve()
    if not dest.is_relative_to(RUNTIME.resolve()):
        raise ValueError("Invalid upstream path")
    if dest.exists():
        data = dest.read_bytes()
        if hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == item["sha"]:
            return
    data = get(f"https://raw.githubusercontent.com/langgenius/dify/{PIN}/{item['path']}")
    if hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != item["sha"]:
        raise ValueError("Upstream blob hash mismatch")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def main():
    tree = json.loads(get(f"https://api.github.com/repos/langgenius/dify/git/trees/{PIN}?recursive=1"))
    items = [i for i in tree["tree"] if i["type"] == "blob" and (i["path"].startswith("docker/") or i["path"] == "LICENSE")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(download, items))
    env_file = RUNTIME / "docker" / ".env"
    # Secrets exist only in ignored local runtime files, never in the DSL.
    token = secrets.token_urlsafe(32)
    overrides = dict(COMPOSE_PROJECT_NAME="grudge-shop", EXPOSE_NGINX_PORT="127.0.0.1:8088", EXPOSE_NGINX_SSL_PORT="127.0.0.1:8448",
        APP_WEB_URL="http://127.0.0.1:8088", SERVICE_API_URL="http://127.0.0.1:8088/v1",
        SECRET_KEY=secrets.token_urlsafe(42), DB_PASSWORD=secrets.token_urlsafe(24), REDIS_PASSWORD=secrets.token_urlsafe(24),
        SANDBOX_API_KEY=secrets.token_urlsafe(24), PLUGIN_DAEMON_KEY=secrets.token_urlsafe(24),
        PLUGIN_DIFY_INNER_API_KEY=secrets.token_urlsafe(24), SHOP_API_TOKEN=token,
        HTTP_REQUEST_MAX_READ_TIMEOUT="120", CODE_MAX_STRING_LENGTH="160000",
        SHOP_DB_HOST="seekdb", SHOP_DB_PORT="2881", SHOP_DB_PASSWORD=secrets.token_urlsafe(24))
    overrides["POSTGRES_PASSWORD"] = overrides["DB_PASSWORD"]
    overrides["CODE_EXECUTION_API_KEY"] = overrides["SANDBOX_API_KEY"]
    if env_file.exists():
        existing = dict(l.split("=", 1) for l in env_file.read_text().splitlines() if "=" in l and not l.startswith("#"))
        token = existing["SHOP_API_TOKEN"]
        print("Existing runtime .env preserved")
    else:
        original = (env_file.parent / ".env.example").read_text(encoding="utf-8")
        lines = [l for l in original.splitlines() if l.split("=",1)[0] not in overrides]
        env_file.write_text("\n".join(lines) + "\n" + "\n".join(f"{k}={v}" for k,v in overrides.items()) + "\n", encoding="utf-8")
        env_file.chmod(0o600)
    extra = dict(services={
        "shop": dict(build={"context": "../../backend"}, restart="unless-stopped",
                     environment=dict(SEEKDB_HOST="${SHOP_DB_HOST}", SEEKDB_PORT="${SHOP_DB_PORT}", SEEKDB_DATABASE="dify_grudge_shop",
                                      SEEKDB_PASSWORD="${SHOP_DB_PASSWORD}", SHOP_API_TOKEN="${SHOP_API_TOKEN}"),
                     ports=["127.0.0.1:8099:8099"], extra_hosts=["host.docker.internal:host-gateway"]),
        "seekdb": dict(image="oceanbase/seekdb:1.4.0-100000172026082615", restart="unless-stopped",
                       environment=dict(ROOT_PASSWORD="${SHOP_DB_PASSWORD}", MEMORY_LIMIT="2G", DATAFILE_SIZE="512M", LOG_DISK_SIZE="2G"),
                       volumes=["shop_seekdb:/var/lib/seekdb"]),
        "ollama": dict(image="ollama/ollama:0.11.10", restart="unless-stopped", volumes=["shop_ollama:/root/.ollama"]),
        "ssrf_proxy": dict(volumes=["./shop-allow.conf:/etc/squid/conf.d/shop-allow.conf:ro"]),
    }, volumes={"shop_seekdb":{}, "shop_ollama":{}})
    (env_file.parent / "compose.shop.yaml").write_text(yaml.safe_dump(extra, sort_keys=False), encoding="utf-8")
    (env_file.parent / "shop-allow.conf").write_text("acl shop_backends dstdomain shop ollama\nhttp_access allow localnet shop_backends\n", encoding="utf-8")
    (RUNTIME / "import-settings.local.json").write_text(json.dumps({"SHOP_BASE_URL":"http://shop:8099", "SHOP_API_TOKEN":token,
        "OLLAMA_BASE_URL":"http://ollama:11434", "OLLAMA_MODEL":"qwen3:0.6b"}, indent=2), encoding="utf-8")
    print("Prepared pinned Dify 1.9.1; secret settings saved under ignored .runtime/. No secrets printed.")


if __name__ == "__main__":
    main()
